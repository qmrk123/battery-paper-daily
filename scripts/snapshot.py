"""Build a single self-contained snapshot HTML (CSS+JS+data inlined).

Useful for: an offline one-file preview, sharing a look at the site, or viewing
inside a sandbox that blocks fetch(). Not the deployed artifact — that's build_site.py.

    python scripts/snapshot.py [out.html]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = ROOT / "data"


def build(out: Path) -> Path:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    css = (SITE / "style.css").read_text(encoding="utf-8")
    js = (SITE / "app.js").read_text(encoding="utf-8")

    index = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
    days = {
        d: json.loads((DATA / f"{d}.json").read_text(encoding="utf-8"))
        for d in index.get("dates", [])
        if (DATA / f"{d}.json").exists()
    }
    embedded = json.dumps({"index": index, "days": days}, ensure_ascii=False)

    html = html.replace(
        '<link rel="stylesheet" href="style.css" />',
        f"<style>\n{css}\n</style>",
    )
    html = html.replace(
        '<link rel="preconnect" href="https://api.openalex.org" />', "",
    )
    html = html.replace(
        '<script src="app.js" defer></script>',
        f"<script>window.__BPD__ = {embedded};</script>\n<script>\n{js}\n</script>",
    )

    out.write_text(html, encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"wrote {out} ({kb:.0f} KB, {sum(len(d.get('papers', [])) for d in days.values())} papers)")
    return out


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else (ROOT / "public" / "snapshot.html")
    dest.parent.mkdir(parents=True, exist_ok=True)
    build(dest)
