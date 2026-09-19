# photos

A small static photo gallery for GitHub Pages. Each photo has its own page
(high-resolution image, a short text, the place and the date taken from EXIF)
whose URL is written on an NFC tag glued to the printed card.

```
photos.py            the tool: add / build / list / remove / serve
site.json            title, subtitle, base URL, language, sizes
data/photos.json     the manifest — the single source of truth
templates/           Jinja2 templates (gallery, photo page, 404)
static/              style.css + favicon, copied into docs/ on build
docs/                the generated site — this is what GitHub Pages serves
```

## Setup

The tool runs with [uv](https://docs.astral.sh/uv/); dependencies (Pillow,
pillow-heif, Jinja2) are declared inline and installed on first run.

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

- writes `docs/img/<slug>.jpg` (long edge 2560 px) and `<slug>-thumb.jpg`
  (900 px), correctly rotated, **with all EXIF metadata stripped**;
- appends the entry to `data/photos.json`;
- regenerates every page in `docs/`;
- prints the URL to write on the NFC tag.

Every prompt has a flag, so this works non-interactively too:

```sh
./photos.py add IMG_1234.HEIC \
  --title "Christmas Eve in Florence" \
  --description "Cold hands, warm chestnuts." \
  --place "Florence, Italy" --date 2025-12-24 --slug florence-xmas --yes
```

`--no-coords` keeps the place name but does not store the coordinates
(which otherwise power the small map link under the photo).

The slug becomes the URL: `<base_url>/p/<slug>/`. It defaults to the title,
or to `<place>-<date>` when there is no title. Slugs never change once a tag
is written, so pick them with care or pass `--slug`.

## Other commands

```sh
./photos.py list             # every photo with its NFC URL
./photos.py build            # after editing templates/ or static/style.css
./photos.py remove <slug>    # delete manifest entry, images and page
./photos.py serve            # preview at http://127.0.0.1:8000/
```

`data/photos.json` is plain JSON: fix a typo in a description there and run
`build`.

## Publishing

1. Push to GitHub.
2. Repository **Settings → Pages → Build and deployment**: Source
   *Deploy from a branch*, branch `main`, folder `/docs`.
3. The site is live at `base_url` a minute later.

The whole `docs/` folder is committed, including the images, so no CI is
needed. Keep an eye on size: GitHub recommends staying under 1 GB per site.
At the default settings a photo weighs roughly 1–3 MB.

## Writing the NFC tags

Any NTAG215 works (504 bytes of user memory; a URL record is ~50 bytes).
With the **NFC Tools** app (iOS/Android): *Write → Add a record → URL/URI*,
paste the URL printed by `add` (or shown by `list`), then *Write*. Consider
locking the tag afterwards so it cannot be rewritten.

Phones open the URL directly when the card is tapped; no app is required.

## Privacy notes

- GitHub Pages sites are public. Pages carry `noindex` by default
  (`allow_search_engines` in `site.json`), which keeps search engines away
  but does not stop anyone who has the link.
- Published images have EXIF removed, so no GPS or device data leaks through
  the files. Coordinates live only in `data/photos.json` and the map link;
  use `--no-coords` for places you would rather not pin.
