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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"

_PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _score(p: dict) -> tuple[bool, bool]:
    return bool(p.get("summary_ko")), bool(p.get("image") and p["image"].get("cached"))


def write_corpus(public_data: Path) -> int:
    """Union every per-day / per-month file into data/corpus.json (visible papers
    only, deduped by id, keeping the richest record) so the site can search and
    filter the whole archive client-side, not one date at a time."""
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
    return len(papers)


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
    n_corpus = write_corpus(PUBLIC / "data")

    img_src = ROOT / "site" / "img"
    if img_src.exists():
        shutil.copytree(img_src, PUBLIC / "img")

    print(f"built {PUBLIC} (corpus.json: {n_corpus} papers)")
    return PUBLIC


if __name__ == "__main__":
    build()
