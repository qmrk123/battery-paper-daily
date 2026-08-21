"""Assemble the deployable site into ./public.

    public/
      index.html app.js style.css   (from site/)
      data/                          (from data/)
      img/                           (from site/img, if present — Phase 3 thumbs)

Both local preview and the GitHub Pages deploy serve this single folder, so
paths like `data/index.json` resolve the same everywhere.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
SITE_URL = "https://qmrk123.github.io/battery-paper-daily/"

_PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _score(p: dict) -> tuple[bool, bool]:
    return bool(p.get("summary_ko")), bool(p.get("image") and p["image"].get("cached"))


def write_corpus(public_data: Path) -> list[dict]:
    """Union every per-day / per-month file into data/corpus.json (visible papers
    only, deduped by id, keeping the richest record) so the site can search and
    filter the whole archive client-side, not one date at a time. Returns the
    deduped papers (newest publication first) for downstream use (e.g. the feed)."""
    pool: dict[str, dict] = {}
    for f in sorted(public_data.glob("*.json")):
        if f.stem in ("index", "seen", "journals", "corpus") or not _PERIOD.match(f.stem):
            continue
        for p in json.loads(f.read_text(encoding="utf-8")).get("papers", []):
            pid = p.get("id")
            if not pid or p.get("relevant") is False:   # skip hidden/off-topic
                continue
            cur = pool.get(pid)
            if cur is None or _score(p) > _score(cur):
                pool[pid] = p
    papers = sorted(pool.values(),
                    key=lambda p: (p.get("published") or "", p.get("title") or ""),
                    reverse=True)
    (public_data / "corpus.json").write_text(
        json.dumps({"count": len(papers), "papers": papers}, ensure_ascii=False),
        encoding="utf-8")
    return papers


def _rfc822(published: str) -> str:
    try:
        dt = datetime.strptime((published or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return format_datetime(dt)


def write_feed(public: Path, papers: list[dict], limit: int = 60) -> int:
    """Emit feed.xml (RSS 2.0) of the newest papers with their Korean summary, so the
    site can be followed in any feed reader without opening it. Regenerated each build."""
    items = []
    for p in papers[:limit]:
        link = escape(p.get("url") or SITE_URL)
        venue = p.get("venue") or ""
        body = p.get("summary_ko") or (p.get("abstract_en") or "")[:300]
        desc = f"{venue} — {body}" if venue else body
        cats = "".join(f"<category>{escape(t)}</category>" for t in (p.get("topics") or []))
        items.append(
            f"<item><title>{escape(p.get('title') or '(제목 없음)')}</title>"
            f"<link>{link}</link>"
            f"<guid isPermaLink=\"false\">{escape(p.get('doi') or p.get('id') or link)}</guid>"
            f"<pubDate>{_rfc822(p.get('published') or '')}</pubDate>"
            f"{cats}<description>{escape(desc)}</description></item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        '<title>전지 소재 논문 데일리</title>'
        f'<link>{SITE_URL}</link>'
        '<description>리튬·소듐 금속 음극과 High-Ni·Li-rich 양극재 신규 논문 (한국어 요약)</description>'
        '<language>ko</language>'
        f'<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>'
        + "".join(items) +
        '</channel></rss>'
    )
    (public / "feed.xml").write_text(xml, encoding="utf-8")
    return len(items)


def build() -> Path:
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)

    shutil.copy2(ROOT / "site" / "app.js", PUBLIC / "app.js")
    shutil.copy2(ROOT / "site" / "style.css", PUBLIC / "style.css")

    # Cache-bust: GitHub Pages serves assets with a 10-min cache and no way to set
    # headers, so a returning visitor can get fresh index.html but a STALE app.js/
    # style.css (buttons render, handlers missing). Stamp each asset URL with a hash
    # of its content so any change yields a new URL the browser must re-fetch.
    ver = hashlib.sha1(
        (PUBLIC / "app.js").read_bytes() + (PUBLIC / "style.css").read_bytes()
    ).hexdigest()[:8]
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    html = (html.replace('href="style.css"', f'href="style.css?v={ver}"')
                .replace('src="app.js"', f'src="app.js?v={ver}"'))
    (PUBLIC / "index.html").write_text(html, encoding="utf-8")

    shutil.copytree(ROOT / "data", PUBLIC / "data",
                    ignore=shutil.ignore_patterns("*.tmp"))
    corpus = write_corpus(PUBLIC / "data")
    n_feed = write_feed(PUBLIC, corpus)

    img_src = ROOT / "site" / "img"
    if img_src.exists():
        shutil.copytree(img_src, PUBLIC / "img")

    print(f"built {PUBLIC} (corpus.json: {len(corpus)} papers, feed.xml: {n_feed} items)")
    return PUBLIC


if __name__ == "__main__":
    build()
