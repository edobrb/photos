/* Unlocking logic. Each photo is AES-GCM encrypted with its own key; the key
   arrives once in the URL fragment (#k=…) from the NFC card and is then kept
   in localStorage, so the photo stays unlocked on this device. */
(function () {
  'use strict';

  var STORE_KEY = 'photos.unlocked';
  var lang = document.documentElement.lang || 'en';
  // developer conveniences (reset button, #reset) exist only on a local preview
  var isLocal = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  var i18n = {};
  try { i18n = JSON.parse(document.getElementById('i18n').textContent); } catch (e) { /* defaults below */ }

  function t(key, vars) {
    var s = i18n[key] || key;
    return s.replace(/\{(\w+)\}/g, function (_, v) { return vars && v in vars ? vars[v] : ''; });
  }

  // ---- storage -----------------------------------------------------------
  function loadStore() {
    try {
      var s = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
      return s && typeof s === 'object' ? s : {};
    } catch (e) { return {}; }
  }
  function saveStore(s) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch (e) { /* private mode etc. */ }
  }

  // ---- crypto ------------------------------------------------------------
  function fromB64u(s) {
    s = String(s).replace(/-/g, '+').replace(/_/g, '/');
    s += '='.repeat((4 - (s.length % 4)) % 4);
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function importKey(k) {
    return crypto.subtle.importKey('raw', fromB64u(k), 'AES-GCM', false, ['decrypt']);
  }
  function decrypt(key, bytes) {
    var iv = bytes.slice(0, 12), data = bytes.slice(12);
    return crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv }, key, data)
      .then(function (plain) { return new Uint8Array(plain); });
  }
  function decryptMeta(key, b64) {
    return decrypt(key, fromB64u(b64)).then(function (plain) {
      return JSON.parse(new TextDecoder().decode(plain));
    });
  }
  function decryptFile(key, url, type) {
    return fetch(url).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status + ' for ' + url);
      return res.arrayBuffer();
    }).then(function (buf) {
      return decrypt(key, new Uint8Array(buf));
    }).then(function (plain) {
      return URL.createObjectURL(new Blob([plain], { type: type }));
    });
  }

  // ---- formatting --------------------------------------------------------
  // "2026-06-14T17:32:00" or "2024-03-02" (date only), read as local time
  function parseLocal(iso) {
    var d = iso.slice(0, 10).split('-').map(Number);
    var t = iso.length > 10 ? iso.slice(11, 16).split(':').map(Number) : [0, 0];
    return new Date(d[0], d[1] - 1, d[2], t[0] || 0, t[1] || 0);
  }
  function fmtDate(iso) {
    if (!iso) return '';
    try {
      return new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'long', year: 'numeric' }).format(parseLocal(iso));
    } catch (e) { return iso.slice(0, 10); }
  }
  function fmtTime(iso) {
    if (!iso || iso.length <= 10) return '';  // date-only entries have no time to show
    try {
      return new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(parseLocal(iso));
    } catch (e) { return iso.slice(11, 16); }
  }
  function fmtDateTime(iso) {
    var time = fmtTime(iso);
    return fmtDate(iso) + (time ? ' \u00b7 ' + time : '');
  }
  function mapUrl(lat, lon) {
    return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(lat + ',' + lon);
  }
  function countUnlocked(store, ids) {
    return ids.filter(function (id) { return Boolean(store[id]); }).length;
  }

  // ---- gallery -----------------------------------------------------------
  function resetAll() {
    saveStore({});
    location.replace(location.pathname + location.search);  // reload without the hash
  }

  function gallery() {
    if (isLocal) {
      if (location.hash === '#reset') {
        saveStore({});
        history.replaceState(null, '', location.pathname + location.search);
      }
      // typing #reset into the address bar while already on the gallery does not reload the page
      window.addEventListener('hashchange', function () {
        if (location.hash === '#reset') resetAll();
      });
      var devTools = document.querySelector('.dev-tools');
      if (devTools) {
        devTools.hidden = false;
        devTools.querySelector('[data-reset]').addEventListener('click', resetAll);
      }
    }
    var store = loadStore();
    var tiles = Array.prototype.slice.call(document.querySelectorAll('.tile[data-id]'));
    var total = tiles.length;
    var ids = tiles.map(function (el) { return el.dataset.id; });
    var count = 0;

    var progress = document.querySelector('[data-progress]');
    function setProgress() {
      if (!progress) return;
      progress.querySelector('.progress-text').textContent = t('progress', { n: count, total: total });
      progress.querySelector('.progress-fill').style.width = total ? (100 * count / total) + '%' : '0';
      progress.classList.toggle('is-complete', total > 0 && count === total);
    }
    setProgress();
    masonry(document.querySelector('.grid'), tiles);
    if (!window.crypto || !crypto.subtle) return;

    tiles.forEach(function (tile) {
      var k = store[tile.dataset.id];
      if (!k) return;
      var key;
      importKey(k)
        .then(function (ck) { key = ck; return decryptMeta(key, tile.dataset.meta); })
        .then(function (meta) {
          return decryptFile(key, tile.dataset.thumb, 'image/jpeg').then(function (src) {
            renderTile(tile, meta, src);
            count++;
            setProgress();
          });
        })
        .catch(function (err) { console.warn('[photos] cannot unlock', tile.dataset.id, err); });
    });
  }

  function renderTile(tile, meta, src) {
    var img = document.createElement('img');
    img.src = src;
    img.alt = meta.title || meta.place_short || '';
    img.decoding = 'async';
    if (meta.thumb_width && meta.thumb_height) { img.width = meta.thumb_width; img.height = meta.thumb_height; }
    tile.querySelector('.tile-locked').replaceWith(img);
    tile.querySelector('.tile-title').textContent = meta.title || meta.place_short || meta.place || '';
    var time = tile.querySelector('time');
    if (meta.date && time) { time.dateTime = meta.date; time.textContent = fmtDate(meta.date); time.hidden = false; }
    tile.classList.add('is-unlocked');
  }

  /* On phones the grid is two columns wide, and photos keep their own aspect
     ratio — so a plain grid would align every row to its tallest tile and leave
     holes. Give each tile a row span proportional to its height instead, and the
     two columns stagger. Heights arrive late (a thumbnail is decrypted, then
     decoded), hence the ResizeObserver. */
  function masonry(grid, tiles) {
    if (!grid || !window.ResizeObserver || !window.matchMedia) return;
    var UNIT = 4, GAP = 24;  // keep in sync with .grid.is-masonry in style.css
    var narrow = window.matchMedia('(max-width: 640px)');

    function layout() {
      if (!narrow.matches) {
        grid.classList.remove('is-masonry');
        tiles.forEach(function (tile) {
          tile.style.gridRowEnd = '';
          delete tile.dataset.span;
        });
        return;
      }
      grid.classList.add('is-masonry');  // align-items: start, or tiles stretch and grow without bound
      tiles.forEach(function (tile) {
        var span = Math.ceil((tile.getBoundingClientRect().height + GAP) / UNIT);
        if (tile.dataset.span === String(span)) return;
        tile.dataset.span = span;
        tile.style.gridRowEnd = 'span ' + span;
      });
    }

    var ro = new ResizeObserver(layout);
    tiles.forEach(function (tile) { ro.observe(tile); });
    if (narrow.addEventListener) narrow.addEventListener('change', layout);
    else narrow.addListener(layout);  // Safari < 14
    layout();
  }

  // ---- single photo ------------------------------------------------------
  function photo() {
    var main = document.querySelector('main[data-id]');
    if (!main) return;
    var id = main.dataset.id;
    var n = Number(main.dataset.n) || 0;
    var ids = (main.dataset.ids || '').split(',').filter(Boolean);
    var total = Number(main.dataset.total) || ids.length;
    var store = loadStore();
    var hashKey = new URLSearchParams(location.hash.slice(1)).get('k');

    if (!window.crypto || !crypto.subtle) {
      showLocked(main, { noCrypto: true });
      return;
    }

    var candidates = [];
    if (hashKey) candidates.push(hashKey);
    if (store[id] && store[id] !== hashKey) candidates.push(store[id]);

    // try each key against the metadata; AES-GCM authenticates, so a wrong key fails cleanly
    function tryNext(i) {
      if (i >= candidates.length) {
        showLocked(main, { wrongCard: Boolean(hashKey) });
        return;
      }
      var k = candidates[i], key;
      importKey(k)
        .then(function (ck) { key = ck; return decryptMeta(key, main.dataset.meta); })
        .then(function (meta) { onUnlocked(k, key, meta); },
              function (err) { console.warn('[photos] key rejected', err); tryNext(i + 1); });
    }

    function onUnlocked(k, key, meta) {
      var isNew = store[id] !== k;
      if (isNew) { store[id] = k; saveStore(store); }
      if (hashKey) history.replaceState(null, '', location.pathname + location.search);
      showUnlocked(main, meta);
      if (isNew) toast(n, countUnlocked(store, ids), total);
      var image = decryptFile(key, main.dataset.image, 'image/jpeg').then(function (src) {
        var img = main.querySelector('.photo img');
        var link = main.querySelector('.photo-link');
        img.src = src;
        link.href = src;
        link.target = '_blank';
      }).catch(function (err) {
        console.error('[photos] image failed', err);
        main.querySelector('.photo').classList.add('image-failed');
      });
      // the song is fetched once the photo is in, so it never slows the photo down
      if (meta.music) music(key, main.dataset.root + meta.music, meta, image);
    }
    tryNext(0);
  }

  function showUnlocked(main, meta) {
    var fig = main.querySelector('.state-unlocked');
    var img = fig.querySelector('img');
    if (meta.image_width && meta.image_height) { img.width = meta.image_width; img.height = meta.image_height; }
    img.alt = meta.title || meta.place_short || '';

    var h1 = fig.querySelector('h1');
    if (meta.title) { h1.textContent = meta.title; h1.hidden = false; }
    var desc = fig.querySelector('.description');
    if (meta.description) { desc.textContent = meta.description; desc.hidden = false; }

    var metaEl = fig.querySelector('.meta');
    var place = fig.querySelector('.place'), sep = fig.querySelector('.sep'), time = fig.querySelector('time');
    if (meta.place) {
      if (meta.lat != null && meta.lon != null) {
        var a = document.createElement('a');
        a.href = mapUrl(meta.lat, meta.lon); a.rel = 'noopener'; a.textContent = meta.place;
        place.appendChild(a);
      } else {
        place.textContent = meta.place;
      }
      place.hidden = false;
    }
    if (meta.date) { time.dateTime = meta.date; time.textContent = fmtDateTime(meta.date); time.hidden = false; }
    if (meta.place && meta.date) sep.hidden = false;
    if (meta.place || meta.date) metaEl.hidden = false;

    if (meta.title) document.title = meta.title + ' · ' + document.title;
    main.querySelector('.state-locked').hidden = true;
    fig.hidden = false;
    document.body.classList.add('is-unlocked');
  }

  function showLocked(main, opts) {
    var card = main.querySelector('.state-locked');
    if (opts && opts.wrongCard) card.querySelector('.wrong-card').hidden = false;
    if (opts && opts.noCrypto) card.querySelector('.no-crypto').hidden = false;
    main.querySelector('.state-unlocked').hidden = true;
    card.hidden = false;
    document.body.classList.add('is-locked');
  }

  // ---- music -------------------------------------------------------------
  /* A photo can carry a song, encrypted with the same key as the image, and
     played by a small player above the description. Browsers refuse to start
     sound before the viewer has touched the page, so when autoplay is blocked
     the first tap anywhere starts it. Once the viewer stops a song, songs no
     longer start by themselves on any photo until they press play again. */
  var MUSIC_KEY = 'photos.music';

  function musicWanted() {
    try { return localStorage.getItem(MUSIC_KEY) !== 'off'; } catch (e) { return true; }
  }
  function setMusicWanted(on) {
    try { localStorage.setItem(MUSIC_KEY, on ? 'on' : 'off'); } catch (e) { /* private mode etc. */ }
  }

  function music(key, url, meta, after) {
    var player = document.querySelector('.player');
    if (!player) return;
    var btn = player.querySelector('.player-toggle');
    var line = player.querySelector('.player-line');
    var fill = player.querySelector('.player-fill');
    var title = meta.music_title ? ' · ' + meta.music_title : '';
    // shown right away so the caption doesn't jump; usable once the song is decrypted
    player.querySelector('.player-title').textContent = meta.music_title || '';
    btn.setAttribute('aria-label', t('music_play') + title);
    btn.disabled = true;
    player.hidden = false;

    after.then(function () {
      return decryptFile(key, url, meta.music_type || 'audio/mpeg');
    }).then(function (src) {
      var audio = new Audio(src);
      var gestures = ['click', 'touchend', 'keydown'];
      var frame = 0;

      function progress() {
        cancelAnimationFrame(frame);
        var d = audio.duration;
        fill.style.transform = 'scaleX(' + (d && !audio.ended ? audio.currentTime / d : 0) + ')';
        if (!audio.paused) frame = requestAnimationFrame(progress);
      }
      function sync() {
        var playing = !audio.paused;
        // 'play' fires as soon as playback is allowed; play()'s promise can lag behind by seconds
        if (playing) disarm();
        var label = t(playing ? 'music_stop' : 'music_play') + title;
        player.classList.toggle('is-playing', playing);
        btn.setAttribute('aria-label', label);
        btn.title = label;
        progress();
      }
      function onGesture(e) {
        if (btn.contains(e.target)) return;  // the button has its own handler
        var a = e.target.closest && e.target.closest('a');
        if (a && a.target !== '_blank') return;  // leaving the page: no blip of sound
        audio.play().catch(function () { /* not a user activation (a scroll, Esc): wait for the next */ });
      }
      function arm() {
        player.classList.add('is-waiting');
        gestures.forEach(function (ev) { document.addEventListener(ev, onGesture, true); });
      }
      function disarm() {
        player.classList.remove('is-waiting');
        gestures.forEach(function (ev) { document.removeEventListener(ev, onGesture, true); });
      }

      ['play', 'pause', 'ended'].forEach(function (ev) { audio.addEventListener(ev, sync); });
      ['seeked', 'durationchange'].forEach(function (ev) { audio.addEventListener(ev, progress); });
      btn.addEventListener('click', function () {
        disarm();
        var on = audio.paused;
        setMusicWanted(on);
        if (on) audio.play().catch(function (err) { console.warn('[photos] music refused', err); });
        else audio.pause();
      });
      line.addEventListener('click', function (e) {  // tap the line to jump
        var r = line.getBoundingClientRect();
        if (!audio.duration || !r.width) return;
        audio.currentTime = audio.duration * Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1);
        progress();
      });
      if ('mediaSession' in navigator && window.MediaMetadata && meta.music_title) {
        navigator.mediaSession.metadata = new MediaMetadata({ title: meta.music_title });
      }
      btn.disabled = false;
      sync();
      if (musicWanted()) {
        audio.play().catch(function (err) {
          if (err.name === 'NotAllowedError') arm();
          else console.warn('[photos] music refused', err);
        });
      }
    }).catch(function (err) {
      console.error('[photos] music failed', err);
      player.hidden = true;
    });
  }

  function toast(n, count, total) {
    var el = document.createElement('div');
    el.className = 'toast';
    el.setAttribute('role', 'status');
    var strong = document.createElement('strong');
    strong.textContent = t('unlocked') + ' · ' + t('number', { n: n });
    var span = document.createElement('span');
    span.textContent = t('of', { n: count, total: total });
    el.appendChild(strong); el.appendChild(span);
    document.body.appendChild(el);
    void el.offsetWidth;  // flush styles so the transition runs (rAF may be paused in hidden windows)
    el.classList.add('show');
    setTimeout(function () {
      el.classList.remove('show');
      setTimeout(function () { el.remove(); }, 400);
    }, 4500);
  }

  // ---- boot --------------------------------------------------------------
  if (document.body.classList.contains('gallery-page')) gallery();
  else if (document.body.classList.contains('photo-page')) photo();
})();
