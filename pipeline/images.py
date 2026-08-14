"""Phase 3 — graphical-abstract thumbnails, license-first best-effort.

There is no per-DOI graphical-abstract API, hotlinking publisher CDNs is fragile
+ infringing, and re-hosting a paywalled figure is not ours to do. So we ONLY
cache images for open-access papers under a Creative Commons license:

    license (OpenAlex, else Unpaywall)  ->  CC?  ->  fetch landing-page og:image
    ->  validate + downscale (Pillow)   ->  cache to site/img + attribution

Everything else keeps the material-coloured placeholder. Expect a minority of
papers (mostly ACS/RSC/Nature gold-OA) to get a real thumbnail — that's honest.

    python -m pipeline.images --date 2026-08-14
"""
from __future__ import annotations

import argparse
import io
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

from .config import load_config
from .models import Paper
from .store import Store

IMG_DIR = Path(__file__).resolve().parent.parent / "site" / "img"
CONTACT = os.environ.get("CONTACT_EMAIL", "qmrk123@hanyang.ac.kr")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 "
              f"battery-paper-daily (mailto:{CONTACT})")

MAX_EDGE = 640          # downscale so the longer side <= this
MIN_EDGE = 120          # reject anything smaller (logos/icons)
MAX_BYTES = 5_000_000

_OG = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\']'
    r'[^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
_OG_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']'
    r'(?:og:image(?::secure_url)?|twitter:image(?::src)?)["\']', re.IGNORECASE)


def is_reusable_license(license_: Optional[str]) -> bool:
    """True for any Creative Commons license (attribution is provided). This is a
    non-commercial academic digest, so cc-by / -sa / -nc / -nd / cc0 are all OK."""
    if not license_:
        return False
    return license_.lower().startswith("cc")


def parse_og_image(html: str, base_url: str) -> Optional[str]:
    m = _OG.search(html) or _OG_REV.search(html)
    if not m:
        return None
    return urljoin(base_url, m.group(1).strip())


def _slug(paper_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", paper_id)


def unpaywall_license(doi: str, session: requests.Session) -> Optional[str]:
    """Best-OA-location license from Unpaywall (fallback when OpenAlex lacks it)."""
    try:
        r = session.get(f"https://api.unpaywall.org/v2/{doi}",
                        params={"email": CONTACT}, timeout=15)
        if r.status_code != 200:
            return None
        loc = (r.json() or {}).get("best_oa_location") or {}
        return loc.get("license")
    except Exception:
        return None


def _validate_and_downscale(data: bytes) -> Optional[bytes]:
    """Return JPEG bytes if the image looks like a real figure, else None."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    w, h = im.size
    if w < MIN_EDGE or h < MIN_EDGE:      # logos / spacer / icon
        return None
    if not (0.2 <= w / h <= 5.0):         # banners / rules
        return None
    if max(w, h) > MAX_EDGE:
        scale = MAX_EDGE / max(w, h)
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
        im = bg
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=82, optimize=True)
    return out.getvalue()


def fetch_og_image(landing_url: str, session: requests.Session) -> Optional[str]:
    try:
        r = session.get(landing_url, timeout=15, headers={"User-Agent": BROWSER_UA})
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return None
        return parse_og_image(r.text, r.url)
    except Exception:
        return None


def download_image(url: str, session: requests.Session) -> Optional[bytes]:
    try:
        r = session.get(url, timeout=20, headers={"User-Agent": BROWSER_UA}, stream=True)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("content-type", "")
        if "image" not in ctype:
            return None
        data = r.raw.read(MAX_BYTES + 1, decode_content=True)
        return data if 0 < len(data) <= MAX_BYTES else None
    except Exception:
        return None


def process_paper(paper: Paper, session: requests.Session, img_dir: Path) -> str:
    """Try to attach a cached thumbnail. Returns a short status string."""
    if paper.image and paper.image.get("cached"):
        return "cached"
    if not paper.doi or (paper.oa_status in (None, "closed")):
        return "no-oa"

    license_ = paper.license or unpaywall_license(paper.doi, session)
    paper.license = license_
    if not is_reusable_license(license_):
        return "non-cc"

    landing = paper.url or (f"https://doi.org/{paper.doi}")
    og = fetch_og_image(landing, session)
    if not og:
        return "no-og"
    data = download_image(og, session)
    if not data:
        return "dl-fail"
    jpeg = _validate_and_downscale(data)
    if not jpeg:
        return "invalid"

    img_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_slug(paper.id)}.jpg"
    (img_dir / fname).write_bytes(jpeg)
    paper.image = {
        "src": og,
        "cached": f"img/{fname}",
        "license": license_,
        "attribution": f"{paper.venue or 'source'} — https://doi.org/{paper.doi}",
    }
    return "ok"


def process_papers(papers: list[Paper], img_dir: Path = IMG_DIR,
                   delay: float = 0.7, log=print) -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = BROWSER_UA
    stats: dict[str, int] = {}
    for i, p in enumerate(papers, 1):
        status = process_paper(p, session, img_dir)
        stats[status] = stats.get(status, 0) + 1
        if status == "ok":
            log(f"  [{i}/{len(papers)}] ✓ {p.title[:50]}")
        if status in ("no-og", "dl-fail", "invalid", "non-cc"):
            time.sleep(delay)          # be polite only when we actually hit a site
    return stats


def run(date: str, log=print) -> dict:
    cfg = load_config()
    store = Store()
    papers = store.load_day(date)
    if not papers:
        log(f"no data for {date}")
        return {}
    log(f"== images {date}: {len(papers)} papers ==")
    stats = process_papers(papers, log=log)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.save_day(date, papers, generated_at=now)
    store.write_index(cfg.topic_meta(), updated_at=now)
    log(f"== {stats} -> saved data/{date}.json ==")
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Graphical-abstract thumbnails (CC only)")
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    run(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
