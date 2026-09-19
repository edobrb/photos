# photos

A small static photo gallery for GitHub Pages, played as a collecting game.
Every photo starts **locked**: the gallery shows numbered blank cards. Each
printed card carries an NFC tag with that photo's URL; tapping it opens the
photo, unlocks it, and the browser remembers it from then on. Each photo page
shows the high-resolution image, a short text, and the place and date taken
from EXIF.

```
photos.py            the tool: add / build / list / remove / serve
site.json            title, subtitle, base URL, language, sizes
data/photos.json     the manifest: plaintext + keys — PRIVATE, not in git
templates/           Jinja2 templates (gallery, photo page, 404)
static/              style.css, app.js, favicon — copied into docs/ on build
docs/                the generated site — this is what GitHub Pages serves
```

## How the lock works

Each photo gets a random 128-bit key. The tool encrypts the image, the
thumbnail and the metadata (title, text, place, date, coordinates) with
AES-GCM and only the ciphertext goes into `docs/`. The key travels in the
NFC URL fragment:

```
https://edobrb.github.io/photos/p/k7m2xq/#k=Xy9…22 chars…
```

The fragment never reaches the server. The page decrypts with WebCrypto,
stores the key in `localStorage` (`photos.unlocked`), and strips it from the
address bar. The gallery reads the same storage and shows a progress line,
"3 of 12 unlocked". Everything public is opaque: random ids, blobs, a count.

Consequences worth knowing:

- Unlocks live in one browser on one device. A different phone starts over.
- On a local preview (`./photos.py serve`) the gallery footer shows a
  *Reset unlocks* button, and `#reset` on the gallery URL does the same.
  Neither exists on the published site.
- `data/photos.json` is the only plaintext copy and holds the keys. It is
  git-ignored; **back it up** (iCloud, a private repo, anywhere private).
  If it is ever lost, the URLs on the cards still contain the keys.
- Someone who reads the repository sees only ciphertext.

## Setup

The tool runs with [uv](https://docs.astral.sh/uv/); dependencies (Pillow,
pillow-heif, Jinja2, cryptography) are declared inline and installed on
first run.

```sh
uv run photos.py --help        # or simply: ./photos.py --help
```

Without uv: `pip install -r requirements.txt` and run with Python 3.11+.

Edit `site.json` first: `title`, `subtitle`, `base_url`
(`https://<user>.github.io/<repo>`), `lang` (`en` or `it`).

## Adding a photo

```sh
./photos.py add ~/Pictures/IMG_1234.HEIC
```

The tool reads the EXIF date and GPS position, looks the position up on
OpenStreetMap to propose a place name, and then asks for the things it cannot
guess: place (Enter accepts the suggestion), an optional title, and your
description. It then

- resizes to 2560 px (long edge) plus a 900 px thumbnail, rotated correctly,
  all EXIF removed, and writes them **encrypted** to `docs/img/<id>.enc`;
- appends the entry to `data/photos.json`;
- regenerates every page in `docs/`;
- prints the URL to write on the NFC tag.

Every prompt has a flag, so this works non-interactively too:

```sh
./photos.py add IMG_1234.HEIC \
  --title "Christmas Eve in Florence" \
  --description "Cold hands, warm chestnuts." \
  --place "Florence, Italy" --date 2025-12-24 --yes
```

`--no-coords` keeps the place name but drops the coordinates (which power
the Google Maps link under the photo). `--id` sets a custom URL id if you do
not want a random one; ids never change once a tag is written.

## Other commands

```sh
./photos.py list             # every photo with its number and NFC URL
./photos.py build            # after editing templates/ or static/
./photos.py remove <id>      # delete manifest entry, files and page
./photos.py serve            # preview at http://127.0.0.1:8000/
```

`data/photos.json` is plain JSON: fix a typo in a description there and run
`build`. The gallery order follows `order` in `site.json` (`newest` or
`oldest` by date), which is also the numbering — a locked card's position
between two unlocked ones is the only hint the game gives away.

## Publishing

1. Push to GitHub.
2. Repository **Settings → Pages → Build and deployment**: Source
   *Deploy from a branch*, branch `main`, folder `/docs`.
3. The site is live at `base_url` a minute later.

The whole `docs/` folder is committed, so no CI is needed. Keep an eye on
size: GitHub recommends staying under 1 GB per site. At the default settings
a photo weighs roughly 1–3 MB.

## Writing the NFC tags

Any NTAG215 works (504 bytes of user memory; these URLs are ~70 bytes).
With the **NFC Tools** app (iOS/Android): *Write → Add a record → URL/URI*,
paste the URL printed by `add` (or shown by `list`), then *Write*. Lock the
tag afterwards so it cannot be rewritten.

Phones open the URL directly when the card is tapped; no app is required.
The page needs HTTPS (GitHub Pages) or `localhost` for WebCrypto, so
previewing works through `./photos.py serve` but not by opening the HTML
file directly.

## Privacy notes

- GitHub Pages sites are public. Pages carry `noindex` by default
  (`allow_search_engines` in `site.json`).
- Published images have EXIF removed and are encrypted; the plaintext exists
  only in `data/` on your machine and in the browsers that unlocked them.
