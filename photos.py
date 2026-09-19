#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pillow>=10.0",
#   "pillow-heif>=0.16",
#   "jinja2>=3.1",
# ]
# ///
"""
photos.py — manage the photo gallery.

  ./photos.py add IMG_1234.HEIC        ingest a photo (EXIF → date/place), rebuild site
  ./photos.py build                    regenerate docs/ from data/photos.json + templates/
  ./photos.py list                     show every photo with its NFC URL
  ./photos.py remove <slug>            delete a photo and rebuild
  ./photos.py serve                    preview docs/ at http://127.0.0.1:8000/

Run with `uv run photos.py ...` (or `./photos.py ...`); uv installs the
dependencies on first use.  Without uv: `pip install -r requirements.txt`
and run with python3.11+.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import ExifTags, Image, ImageOps

try:  # HEIC/HEIF support (iPhone photos)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent
SITE_FILE = ROOT / "site.json"
DATA_FILE = ROOT / "data" / "photos.json"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
DOCS = ROOT / "docs"
IMG_DIR = DOCS / "img"
PAGES_DIR = DOCS / "p"

SITE_DEFAULTS = {
    "title": "Our Photos",
    "subtitle": "",
    "base_url": "",
    "lang": "en",
    "order": "newest",  # or "oldest"
    "allow_search_engines": False,
    "large_px": 2560,
    "thumb_px": 900,
    "jpeg_quality": 85,
}

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
           "agosto", "settembre", "ottobre", "novembre", "dicembre"],
}

STRINGS = {
    "en": {
        "back": "All photos",
        "prev": "Previous",
        "next": "Next",
        "open_full": "Open full size",
        "empty": "No photos yet.",
        "empty_hint": "Add one with ./photos.py add <file>",
        "photos": "photos",
        "photo": "photo",
        "not_found": "This photo isn't here.",
        "not_found_hint": "Maybe it moved. Have a look at the gallery instead.",
    },
    "it": {
        "back": "Tutte le foto",
        "prev": "Precedente",
        "next": "Successiva",
        "open_full": "Apri a piena risoluzione",
        "empty": "Ancora nessuna foto.",
        "empty_hint": "Aggiungine una con ./photos.py add <file>",
        "photos": "foto",
        "photo": "foto",
        "not_found": "Questa foto non è qui.",
        "not_found_hint": "Forse è stata spostata. Dai un'occhiata alla galleria.",
    },
}


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_site() -> dict:
    site = {**SITE_DEFAULTS, **load_json(SITE_FILE, {})}
    site["base_url"] = site["base_url"].rstrip("/")
    path = urlparse(site["base_url"]).path if site["base_url"] else ""
    site["base_path"] = (path.rstrip("/") + "/") if path else "/"
    if site["lang"] not in STRINGS:
        site["lang"] = "en"
    return site


def load_photos() -> list[dict]:
    return load_json(DATA_FILE, [])


def save_photos(photos: list[dict]) -> None:
    save_json(DATA_FILE, photos)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text


def unique_slug(base: str, taken: set[str]) -> str:
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def format_date(iso: str, lang: str) -> str:
    dt = datetime.fromisoformat(iso)
    months = MONTHS.get(lang, MONTHS["en"])
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def sort_photos(photos: list[dict], order: str) -> list[dict]:
    key = lambda p: (p.get("date") or "", p.get("added") or "")  # noqa: E731
    return sorted(photos, key=key, reverse=(order != "oldest"))


def photo_url(site: dict, slug: str) -> str:
    base = site["base_url"] or f"https://<user>.github.io{site['base_path'].rstrip('/')}"
    return f"{base}/p/{slug}/"


def ask(label: str, default: str | None = None, *, skip: bool = False) -> str:
    """Prompt on the terminal. Enter accepts the default (or leaves it empty)."""
    if skip:
        return default or ""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{label}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value or (default or "")


# ----------------------------------------------------------------------------
# EXIF
# ----------------------------------------------------------------------------

def _dms_to_deg(dms, ref) -> float:
    d, m, s = (float(x) for x in dms)
    deg = d + m / 60 + s / 3600
    return -deg if ref in ("S", "W") else deg


def read_exif(img: Image.Image) -> tuple[datetime | None, tuple[float, float] | None]:
    """Return (date taken, (lat, lon)) from EXIF, each None when missing."""
    exif = img.getexif()
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)

    date = None
    for raw in (sub.get(ExifTags.Base.DateTimeOriginal),
                sub.get(ExifTags.Base.DateTimeDigitized),
                exif.get(ExifTags.Base.DateTime)):
        if raw:
            try:
                date = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
                break
            except ValueError:
                continue

    coords = None
    lat, lon = gps.get(ExifTags.GPS.GPSLatitude), gps.get(ExifTags.GPS.GPSLongitude)
    if lat and lon:
        try:
            coords = (
                round(_dms_to_deg(lat, gps.get(ExifTags.GPS.GPSLatitudeRef, "N")), 5),
                round(_dms_to_deg(lon, gps.get(ExifTags.GPS.GPSLongitudeRef, "E")), 5),
            )
        except (TypeError, ValueError, ZeroDivisionError):
            coords = None
    return date, coords


def reverse_geocode(lat: float, lon: float, lang: str) -> tuple[str, str] | None:
    """Return (full place string, short locality) via OpenStreetMap Nominatim."""
    query = urllib.parse.urlencode({
        "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2",
        "zoom": 12, "accept-language": lang,
    })
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{query}",
        headers={"User-Agent": "photos-gallery-tool/1.0 (personal static site)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  (reverse geocoding failed: {exc})")
        return None
    addr = data.get("address") or {}

    def first(*keys):
        return next((addr[k] for k in keys if addr.get(k)), None)

    locality = first("village", "town", "city", "municipality", "hamlet", "suburb", "county")
    region = first("state", "region", "province")
    country = addr.get("country")
    parts = list(dict.fromkeys(p for p in (locality, region, country) if p))
    if not parts:
        return None
    return ", ".join(parts), (locality or parts[0])


# ----------------------------------------------------------------------------
# images
# ----------------------------------------------------------------------------

def make_derivatives(img: Image.Image, slug: str, site: dict) -> dict:
    """Write docs/img/<slug>.jpg and <slug>-thumb.jpg (EXIF stripped, ICC kept)."""
    icc = img.info.get("icc_profile")
    base = ImageOps.exif_transpose(img) or img
    if base.mode != "RGB":
        base = base.convert("RGB")
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, px, filename in (("image", site["large_px"], f"{slug}.jpg"),
                               ("thumb", site["thumb_px"], f"{slug}-thumb.jpg")):
        im = base.copy()
        im.thumbnail((px, px), Image.Resampling.LANCZOS)
        save_kwargs = dict(quality=site["jpeg_quality"], optimize=True, progressive=True)
        if icc:
            save_kwargs["icc_profile"] = icc
        im.save(IMG_DIR / filename, "JPEG", **save_kwargs)
        out[name] = f"img/{filename}"
        out[f"{name}_width"], out[f"{name}_height"] = im.size
    return out


# ----------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------

def build(site: dict | None = None, photos: list[dict] | None = None) -> list[dict]:
    site = site or load_site()
    photos = photos if photos is not None else load_photos()
    ordered = sort_photos(photos, site["order"])
    t = STRINGS[site["lang"]]

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["fmtdate"] = lambda iso: format_date(iso, site["lang"])

    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").touch()
    for asset in STATIC.iterdir():
        if asset.is_file():
            shutil.copy2(asset, DOCS / asset.name)

    def render(template: str, dest: Path, **ctx) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(env.get_template(template).render(site=site, t=t, **ctx), encoding="utf-8")

    render("index.html", DOCS / "index.html", photos=ordered, root="")
    render("404.html", DOCS / "404.html", root=site["base_path"])

    for i, photo in enumerate(ordered):
        render(
            "photo.html", PAGES_DIR / photo["slug"] / "index.html",
            photo=photo,
            prev=ordered[i - 1] if i > 0 else None,
            next=ordered[i + 1] if i + 1 < len(ordered) else None,
            root="../../",
        )

    # drop pages for photos that no longer exist
    keep = {p["slug"] for p in photos}
    if PAGES_DIR.exists():
        for d in PAGES_DIR.iterdir():
            if d.is_dir() and d.name not in keep:
                shutil.rmtree(d)
    return ordered


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------

def cmd_add(args) -> None:
    site = load_site()
    photos = load_photos()
    src = Path(args.file).expanduser()
    if not src.is_file():
        die(f"file not found: {src}")

    print(f"Reading {src.name} …")
    try:
        img = Image.open(src)
        img.load()
    except Exception as exc:  # noqa: BLE001
        die(f"cannot open image: {exc}")

    exif_date, coords = read_exif(img)
    if args.no_coords:
        coords = None

    # --- date -----------------------------------------------------------------
    if args.date:
        try:
            date = datetime.fromisoformat(args.date)
        except ValueError:
            die("--date must be ISO, e.g. 2026-06-14 or 2026-06-14T17:30")
    elif exif_date:
        date = exif_date
        print(f"  date from EXIF: {date:%Y-%m-%d %H:%M}")
    else:
        fallback = datetime.fromtimestamp(src.stat().st_mtime).replace(second=0, microsecond=0)
        print("  no date in EXIF")
        raw = ask("Date (YYYY-MM-DD)", fallback.strftime("%Y-%m-%d"), skip=args.yes)
        try:
            date = datetime.fromisoformat(raw)
        except ValueError:
            die(f"invalid date: {raw}")

    # --- place ----------------------------------------------------------------
    locality = None
    place = args.place
    if place is None:
        suggestion = None
        if coords:
            print(f"  GPS from EXIF: {coords[0]:.4f}, {coords[1]:.4f} — looking up place name …")
            geo = reverse_geocode(coords[0], coords[1], site["lang"])
            if geo:
                suggestion, locality = geo
        else:
            print("  no GPS in EXIF")
        place = ask("Place", suggestion, skip=args.yes)

    place_short = locality or (place.split(",")[0].strip() if place else "")

    # --- title / description --------------------------------------------------
    title = args.title if args.title is not None else ask("Title (optional)", skip=args.yes)
    description = (args.description if args.description is not None
                   else ask("Description", skip=args.yes))

    # --- slug -----------------------------------------------------------------
    taken = {p["slug"] for p in photos}
    if args.slug:
        slug = slugify(args.slug)
        if not slug:
            die("--slug is empty after normalisation")
        if slug in taken:
            die(f"slug already exists: {slug}")
    else:
        stem = slugify(title) or slugify(locality or place or "") or slugify(src.stem) or "photo"
        base_slug = f"{stem}-{date:%Y-%m-%d}" if not slugify(title) else stem
        slug = unique_slug(base_slug, taken)

    # --- write ----------------------------------------------------------------
    print(f"  resizing → {site['large_px']}px / {site['thumb_px']}px …")
    derived = make_derivatives(img, slug, site)
    img.close()

    entry = {
        "slug": slug,
        "title": title,
        "description": description,
        "date": date.replace(microsecond=0).isoformat(),
        "place": place,
        "place_short": place_short,
        "lat": coords[0] if coords else None,
        "lon": coords[1] if coords else None,
        **derived,
        "source": src.name,
        "added": datetime.now().replace(microsecond=0).isoformat(),
    }
    photos.append(entry)
    save_photos(photos)
    build(site, photos)

    print()
    print(f"Added  {slug}")
    print(f"  title:       {title or '—'}")
    print(f"  place:       {place or '—'}")
    print(f"  date:        {format_date(entry['date'], site['lang'])}")
    print(f"  image:       docs/{derived['image']} ({derived['image_width']}×{derived['image_height']})")
    print(f"  page:        docs/p/{slug}/index.html")
    print()
    print(f"NFC URL →  {photo_url(site, slug)}")
    if not site["base_url"]:
        print("  (set base_url in site.json to get the real URL)")


def cmd_build(_args) -> None:
    ordered = build()
    print(f"Built docs/ — {len(ordered)} photo(s)")


def cmd_list(_args) -> None:
    site = load_site()
    photos = sort_photos(load_photos(), site["order"])
    if not photos:
        print("No photos yet.")
        return
    w = max(len(p["slug"]) for p in photos)
    for p in photos:
        date = format_date(p["date"], site["lang"]) if p.get("date") else "—"
        print(f"{p['slug']:<{w}}  {date:<18}  {p.get('place') or '—'}")
        print(f"{'':<{w}}  {photo_url(site, p['slug'])}")


def cmd_remove(args) -> None:
    photos = load_photos()
    match = [p for p in photos if p["slug"] == args.slug]
    if not match:
        die(f"no photo with slug {args.slug!r}")
    photo = match[0]
    for key in ("image", "thumb"):
        f = DOCS / photo[key]
        if f.exists():
            f.unlink()
    photos = [p for p in photos if p["slug"] != args.slug]
    save_photos(photos)
    build(photos=photos)
    print(f"Removed {args.slug}")


def cmd_serve(args) -> None:
    import functools
    import http.server

    if not (DOCS / "index.html").exists():
        build()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"Serving docs/ at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="photos.py", description="Photo gallery tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="add a photo and rebuild the site")
    p.add_argument("file", help="JPEG / HEIC / PNG to add")
    p.add_argument("--title", help="short title (optional)")
    p.add_argument("--description", help="your text for this photo")
    p.add_argument("--place", help="override the place name (skips geocoding)")
    p.add_argument("--date", help="override the date, ISO format")
    p.add_argument("--slug", help="URL slug (default: from title, or place + date)")
    p.add_argument("--no-coords", action="store_true", help="do not store GPS coordinates")
    p.add_argument("-y", "--yes", action="store_true", help="never prompt; accept defaults")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("build", help="regenerate docs/ from data + templates")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("list", help="list photos and their URLs")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="remove a photo by slug")
    p.add_argument("slug")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("serve", help="preview the site locally")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
