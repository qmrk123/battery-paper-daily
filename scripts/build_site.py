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
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"


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

    img_src = ROOT / "site" / "img"
    if img_src.exists():
        shutil.copytree(img_src, PUBLIC / "img")

    print(f"built {PUBLIC}")
    return PUBLIC


if __name__ == "__main__":
    build()
