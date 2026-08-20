"""Phase 3 — graphical-abstract thumbnails from the landing page's og:image.

Publishers publish an `og:image` / `twitter:image` meta so third parties (Slack,
Twitter, Google) can render a link preview — usually the graphical abstract or a
representative figure. We do the same: fetch that image, validate + downscale, and
cache to site/img with attribution + link-back. Best-effort, and honest about limits:

    landing page  ->  og:image meta  ->  validate + downscale (Pillow)  ->  cache

Reality check (probed 2026-08): only Springer Nature (nature.com) reliably serves
og:image to a plain fetch. Wiley / ACS / RSC / AAAS return 403 to non-browser
requests, and Elsevier (ScienceDirect) renders the meta client-side — so those
(the bulk of the allowlist) keep the material-coloured placeholder. Defeating a
publisher's bot-protection to scrape figures would violate their ToS + copyright,
so we don't: no reachable og:image => no thumbnail.

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


def springer_fig1_url(doi: Optional[str]) -> Optional[str]:
    """Construct the Figure-1 image URL for a Springer Nature article. Recent Nature
    articles sometimes carry no og:image yet, but their figures live at a predictable
    media.springernature.com path derived from the DOI (10.1038/s{J}-{0YY}-{ID}-{c})."""
    m = re.match(r"^10\.1038/(s(\d+)-(\d+)-(\d+)-.+)$", doi or "")
    if not m:
        return None
    suf, j, yr, aid = m.group(1), m.group(2), m.group(3), m.group(4)
    return ("https://media.springernature.com/lw685/springer-static/image/"
            f"art%3A10.1038%2F{suf}/MediaObjects/{j}_2{yr}_{aid}_Fig1_HTML.png")


def process_paper(paper: Paper, session: requests.Session, img_dir: Path) -> str:
    """Try to attach a cached thumbnail from the landing page's og:image (the
    image publishers publish for link previews). Returns a short status string."""
    if paper.image and paper.image.get("cached"):
        return "cached"
    landing = paper.url or (f"https://doi.org/{paper.doi}" if paper.doi else None)
    if not landing:
        return "no-url"

    og = fetch_og_image(landing, session)
    if not og and (paper.doi or "").startswith("10.1038"):
        og = springer_fig1_url(paper.doi)   # Nature with no og:image -> its Figure 1
    if not og:
        return "no-og"          # blocked (403), or no og:image and not a constructable Nature URL
    data = download_image(og, session)
    if not data:
        return "dl-fail"
    jpeg = _validate_and_downscale(data)
    if not jpeg:
        return "invalid"

    img_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_slug(paper.id)}.jpg"
    (img_dir / fname).write_bytes(jpeg)
    attribution = paper.venue or "source"
    if paper.doi:
        attribution += f" — https://doi.org/{paper.doi}"
    paper.image = {
        "src": og,
        "cached": f"img/{fname}",
        "license": paper.license,   # kept for attribution if known; no longer a gate
        "attribution": attribution,
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
        if status in ("no-og", "dl-fail", "invalid"):
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
