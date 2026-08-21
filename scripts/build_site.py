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
import math
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
SITE_URL = "https://qmrk123.github.io/battery-paper-daily/"

_PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")

# Enough English/academic filler that the very-common-term cap doesn't have to; real
# ubiquitous words (battery, electrode…) are dropped by the document-frequency cap.
_STOP = set(
    "the a an and or of for to in on with by from as at is are was were be been being "
    "this that these those we our their its it can could may not have has had which "
    "such via using used use study studies show shows shown report reports here also "
    "than then thus into over under both more most high low new novel between during "
    "toward towards results result method methods approach based due within without".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower())
            if len(t) >= 3 and t not in _STOP and not t.isdigit()]


def compute_related(papers: list[dict], k: int = 6, top_terms: int = 25) -> None:
    """Attach `related` (top-k similar paper ids) to each paper via TF-IDF cosine
    over title+abstract. Pure-Python (no numpy/model/API) so it runs in any build.
    Gives a lexical-semantic 'more like this' — clusters by material/method wording."""
    n = len(papers)
    if n < 2:
        for p in papers:
            p["related"] = []
        return
    tfs, df = [], Counter()
    for p in papers:
        tf = Counter(_tokens(f"{p.get('title','')} {p.get('abstract_en','')}"))
        tfs.append(tf)
        for t in tf:
            df[t] += 1
    lo, hi = 2, max(3, int(n * 0.30))                       # drop unique + ubiquitous terms
    idf = {t: math.log(n / d) for t, d in df.items() if lo <= d <= hi}
    vecs: list[dict[str, float]] = []
    for tf in tfs:
        w = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items() if t in idf}
        top = sorted(w.items(), key=lambda kv: kv[1], reverse=True)[:top_terms]
        norm = math.sqrt(sum(v * v for _, v in top)) or 1.0
        vecs.append({t: v / norm for t, v in top})
    inv: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for i, v in enumerate(vecs):
        for t, w in v.items():
            inv[t].append((i, w))
    for i, v in enumerate(vecs):
        sims: dict[int, float] = defaultdict(float)
        for t, w in v.items():
            for j, w2 in inv[t]:
                if j != i:
                    sims[j] += w * w2
        best = sorted(sims.items(), key=lambda kv: kv[1], reverse=True)[:k]
        papers[i]["related"] = [papers[j].get("id") for j, s in best if s > 0.03]


# Faceted tags — orthogonal to the 6 material topics. Precise technical jargon, so
# keyword rules (no LLM/cost) tag the whole corpus deterministically. Each tag needs
# ALL of its regex groups to match (each group is an any-of alternation).
FACETS: dict[str, tuple[str, list[str]]] = {
    # 기법 (method)
    "insitu-tem":     ("기법", [r"in[ -]?situ|operando", r"\bs?tem\b|microscop|electron microsc"]),
    "cryo":           ("기법", [r"\bcryo"]),
    "stem-haadf":     ("기법", [r"\bhaadf\b|\babf\b|\bstem\b|scanning transmission"]),
    "eels":           ("기법", [r"\beels\b|energy[- ]loss"]),
    "xrd":            ("기법", [r"\bxrd\b|x-ray diffraction|rietveld"]),
    "xas":            ("기법", [r"\bxas\b|\bxanes\b|\bexafs\b|x-ray absorption|\bxps\b"]),
    "nmr":            ("기법", [r"\bnmr\b|nuclear magnetic"]),
    "dft":            ("기법", [r"\bdft\b|first[- ]principles|density functional|ab initio"]),
    "ml":             ("기법", [r"machine learning|neural network|deep learning|interatomic potential|\bmlip\b"]),
    # 문제·현상 (problem / phenomenon)
    "cation-mixing":  ("문제", [r"cation mixing|rock[- ]?salt|antisite"]),
    "cracking":       ("문제", [r"\bcrack|fracture|pulveriz|chemo[- ]?mechanic|particle break"]),
    "oxygen-redox":   ("문제", [r"oxygen redox|anion(ic)? redox|lattice oxygen|oxygen release"]),
    "dendrite":       ("문제", [r"dendrit"]),
    "sei-cei":        ("문제", [r"\bsei\b|\bcei\b|electrolyte interphase|passivat"]),
    "thermal-gas":    ("문제", [r"thermal runaway|gas(sing| evolution| generation| release)|exotherm"]),
    "fast-charge":    ("문제", [r"fast[- ]?charg|high[- ]?rate|rate capability|extreme fast"]),
    "coating-doping": ("문제", [r"\bcoating|surface modif|\bdoping\b|dopant"]),
    "single-crystal": ("문제", [r"single[- ]?crystal|monocrystal"]),
}
_FACET_RX = {k: [re.compile(g, re.I) for g in groups] for k, (_, groups) in FACETS.items()}


def tag_facets(papers: list[dict]) -> None:
    """Attach `facets` (list of tag keys) to each paper from title+abstract."""
    for p in papers:
        text = f"{p.get('title','')} {p.get('abstract_en','')}"
        p["facets"] = [k for k, groups in _FACET_RX.items()
                       if all(g.search(text) for g in groups)]


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
    compute_related(papers)                                # attach 'related' ids in place
    tag_facets(papers)                                     # attach 'facets' tag keys in place
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
