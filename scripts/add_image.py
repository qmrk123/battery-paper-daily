"""Attach a graphical abstract to a paper from an image YOU saved (or a URL).

You have legitimate institutional access to these papers. Open the paper in your
own browser, save its graphical abstract (right-click -> Save image), then run this
to put it on the site. This does NO scraping and defeats NO bot-protection — it just
ingests a file you already have. Matches the paper by OpenAlex id, DOI, or title.

    python scripts/add_image.py W7203532084 "C:/Downloads/ga.png"
    python scripts/add_image.py 10.1002/anie.4146793 "C:/Downloads/ga.jpg"
    python scripts/add_image.py "cobalt speciation" "C:/Downloads/ga.jpg"   # title match
    python scripts/add_image.py --url "https://.../ga.jpg" --for 10.1002/anie.4146793
    python scripts/add_image.py --scan "C:/Downloads/ga"   # files named <doi>.<ext>

After it runs, commit + push (or just `git add data/ site/img && git commit && git
push`) and the card shows the image on the next deploy.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.images import _validate_and_downscale, _slug, IMG_DIR, BROWSER_UA  # noqa: E402
from pipeline.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{4}-\d{2}$")


def _norm_doi(d: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (d or "").lower())


def _orig_generated_at(basename: str) -> str | None:
    try:
        return json.load(open(DATA / f"{basename}.json", encoding="utf-8")).get("generated_at")
    except Exception:
        return None


def build_index(store: Store):
    """by_id: id->Paper (first seen); by_doi: normdoi->id; files_of: id->{basenames}."""
    by_id, by_doi, files_of = {}, {}, {}
    for f in sorted(glob.glob(str(DATA / "*.json"))):
        b = os.path.basename(f)[:-5]
        if not _DATE.match(b):
            continue
        for p in store.load_day(b):
            by_id.setdefault(p.id, p)
            if p.doi:
                by_doi.setdefault(_norm_doi(p.doi), p.id)
            files_of.setdefault(p.id, set()).add(b)
    return by_id, by_doi, files_of


def resolve_id(ident: str, by_id, by_doi) -> str | None:
    ident = ident.strip()
    if ident in by_id:
        return ident
    nd = _norm_doi(ident)
    if nd and nd in by_doi:
        return by_doi[nd]
    # title substring (case-insensitive), unique match only
    low = ident.lower()
    hits = [pid for pid, p in by_id.items() if p.title and low in p.title.lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"  ! '{ident}' matches {len(hits)} papers by title — use the DOI or id instead")
    return None


def load_bytes(source: str, session: requests.Session) -> bytes | None:
    if re.match(r"^https?://", source, re.I):
        try:
            r = session.get(source, timeout=30, headers={"User-Agent": BROWSER_UA})
        except Exception as e:
            print(f"  fetch error: {type(e).__name__}"); return None
        if r.status_code != 200:
            print(f"  fetch failed ({r.status_code}) — the image CDN may be blocked too; "
                  f"save the file in your browser and pass its path instead")
            return None
        return r.content
    p = Path(source)
    if not p.exists():
        print(f"  file not found: {source}"); return None
    return p.read_bytes()


def ingest(pid: str, data: bytes, store: Store, by_id, files_of) -> bool:
    jpeg = _validate_and_downscale(data)
    if not jpeg:
        print(f"  x {pid}: not a usable image (too small <120px, odd aspect, or unreadable)")
        return False
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{_slug(pid)}.jpg"
    (IMG_DIR / fname).write_bytes(jpeg)
    p0 = by_id[pid]
    attribution = (p0.venue or "source") + (f" - https://doi.org/{p0.doi}" if p0.doi else "")
    for b in sorted(files_of[pid]):
        papers = store.load_day(b)
        changed = False
        for p in papers:
            if p.id == pid:
                p.image = {"src": p0.url, "cached": f"img/{fname}", "manual": True,
                           "license": p0.license, "attribution": attribution}
                changed = True
        if changed:
            store.save_day(b, papers,
                           generated_at=_orig_generated_at(b) or p0.published or "")
    print(f"  OK {pid}: {fname}  ({(p0.title or '')[:52]})")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Attach a saved graphical abstract to a paper.")
    ap.add_argument("ident", nargs="?", help="paper OpenAlex id, DOI, or title substring")
    ap.add_argument("image", nargs="?", help="path to the saved image (or a URL)")
    ap.add_argument("--url", help="image URL (alternative to positional image)")
    ap.add_argument("--for", dest="for_", help="paper id/DOI when using --url")
    ap.add_argument("--scan", help="folder of images named <doi>.<ext> to ingest in bulk")
    args = ap.parse_args(argv)

    store = Store()
    by_id, by_doi, files_of = build_index(store)
    session = requests.Session()
    n_ok = 0

    if args.scan:
        folder = Path(args.scan)
        files = [f for f in folder.iterdir() if f.suffix.lower() in
                 (".jpg", ".jpeg", ".png", ".webp", ".gif")] if folder.is_dir() else []
        if not files:
            print(f"no image files in {folder}"); return 1
        for f in sorted(files):
            pid = resolve_id(f.stem, by_id, by_doi)
            if not pid:
                print(f"  ? {f.name}: no paper matches stem '{f.stem}' (name it <doi>.jpg)")
                continue
            data = f.read_bytes()
            n_ok += ingest(pid, data, store, by_id, files_of)
        print(f"\nscan: {n_ok} image(s) attached.")
        return 0 if n_ok else 1

    ident = args.for_ or args.ident
    source = args.url or args.image
    if not ident or not source:
        ap.print_help(); return 2
    pid = resolve_id(ident, by_id, by_doi)
    if not pid:
        print(f"no paper matches '{ident}' (try the OpenAlex id like W7203532084, or the DOI)")
        return 1
    data = load_bytes(source, session)
    if not data:
        return 1
    ok = ingest(pid, data, store, by_id, files_of)
    if ok:
        n_ok += 1
        print("\nDone. Now:  git add data/ site/img && git commit -m \"img: manual GA\" && git push")
    return 0 if n_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
