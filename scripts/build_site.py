"""Assemble the deployable site into ./public.

    public/
      index.html app.js style.css   (from site/)
      data/                          (from data/)
      img/                           (from site/img, if present — Phase 3 thumbs)

Both local preview and the GitHub Pages deploy serve this single folder, so
paths like `data/index.json` resolve the same everywhere.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"


def build() -> Path:
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)

    for name in ("index.html", "app.js", "style.css"):
        shutil.copy2(ROOT / "site" / name, PUBLIC / name)

    shutil.copytree(ROOT / "data", PUBLIC / "data",
                    ignore=shutil.ignore_patterns("*.tmp"))

    img_src = ROOT / "site" / "img"
    if img_src.exists():
        shutil.copytree(img_src, PUBLIC / "img")

    print(f"built {PUBLIC}")
    return PUBLIC


if __name__ == "__main__":
    build()
