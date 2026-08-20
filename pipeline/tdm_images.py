"""Graphical-abstract fetch via publisher TDM (Text & Data Mining) APIs — LOCAL.

Publishers bot-block automated web fetches, but offer OFFICIAL TDM APIs for
subscribers to download articles programmatically. This is the publisher's
sanctioned channel — not scraping, not bot-evasion.

Wiley TDM subscribed access is IP-gated to the institutional network, so this runs
LOCALLY on the campus network (not in CI). It fetches each paper's PDF via the TDM
API and extracts the graphical abstract (a first-page figure), then caches a
thumbnail exactly like pipeline/images.py does.

Token (do NOT commit): set WILEY_TDM_TOKEN in the environment.

    set WILEY_TDM_TOKEN=xxxxxxxx-xxxx-...        (cmd, ON the campus network)
    python -m pipeline.tdm_images --test 10.1002/anie.202522034   # inspect one paper
    python -m pipeline.tdm_images                                  # batch all Wiley papers
    python -m pipeline.tdm_images --limit 30 --push               # cap + auto commit/push
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests

from .images import _validate_and_downscale, _slug, IMG_DIR, BROWSER_UA
from .store import Store

WILEY_TDM = "https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}"
ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "_tdm_test"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{4}-\d{2}$")

# DOI-prefix -> fetcher (both need only their token/key + a subscribing campus IP).
WILEY_PREFIX = "10.1002"                # Wiley TDM -> PDF -> extract first figure
ELS_PREFIX = "10.1016"                  # Elsevier/ScienceDirect -> Object API -> 'ga1'
ELS_OBJECT = "https://api.elsevier.com/content/object/doi/{doi}"

# Wiley TDM occasionally 500/503s under load (or for the odd article) — treat these
# as transient and retry with backoff. 403 = not entitled; 404 = not in TDM (permanent).
TRANSIENT = {"429", "500", "502", "503", "504"}


def _fetch_with_retry(doi: str, token: str, session: requests.Session, log, tag=""):
    """fetch_wiley_pdf, waiting out Wiley TDM's rate-limit windows on 5xx/429.

    Measured: Wiley TDM allows ~60-request bursts then throttles (HTTP 500) for a
    few MINUTES before the window resets. Short retries can't outlast that, so the
    waits escalate into minutes; the first paper of a throttled block eats the wait
    and refills the bucket, so the next ~60 papers fly through.
    """
    pdf, status = fetch_wiley_pdf(doi, token, session)
    for wait in (30, 90, 240):          # ~6 min total — long enough to clear a window
        if pdf is not None or status not in TRANSIENT:
            break
        log(f"    {status} on {doi}{tag} — Wiley rate-limited; waiting {wait}s for the window to reset")
        time.sleep(wait)
        pdf, status = fetch_wiley_pdf(doi, token, session)
    return pdf, status


def fetch_wiley_pdf(doi: str, token: str, session: requests.Session):
    """Return (pdf_bytes|None, status_str). Follows the TDM 302 -> PDF redirect."""
    try:
        r = session.get(
            WILEY_TDM.format(doi=doi),
            headers={"Wiley-TDM-Client-Token": token, "User-Agent": BROWSER_UA},
            timeout=90, allow_redirects=True,
        )
    except Exception as e:
        return None, f"error:{type(e).__name__}"
    if r.status_code == 429:
        return None, "429"
    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and (r.content[:5] == b"%PDF-" or "pdf" in ctype):
        return r.content, "200"
    return None, str(r.status_code)


def _pdf_image_candidates(pdf_bytes: bytes, max_pages: int = 6):
    """Embedded raster images on the first pages, ranked (page asc, area desc).

    Returns list of dicts: {page, w, h, ext, data}. We take the earliest page that
    has a real raster, largest first — usually the graphical abstract or Figure 1.
    Scans the first ~6 pages because many Wiley PDFs place the first figure on
    page 2-4 (title/abstract/references fill the earlier pages).
    """
    import pymupdf  # PyMuPDF
    out = []
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return out
    seen = set()
    for pno in range(min(max_pages, doc.page_count)):
        try:
            imgs = doc[pno].get_images(full=True)
        except Exception:
            continue
        for img in imgs:
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                d = doc.extract_image(xref)
            except Exception:
                continue
            w, h = d.get("width", 0), d.get("height", 0)
            if w < 150 or h < 150:
                continue
            ar = (w / h) if h else 0
            if not (0.2 <= ar <= 5.0):
                continue
            out.append({"page": pno, "w": w, "h": h, "ext": d.get("ext", "png"),
                        "data": d["image"], "area": w * h})
    out.sort(key=lambda c: (c["page"], -c["area"]))
    return out


def extract_graphical_abstract(pdf_bytes: bytes):
    """Best-guess GA thumbnail (validated+downscaled JPEG bytes) or None."""
    for c in _pdf_image_candidates(pdf_bytes):
        jpeg = _validate_and_downscale(c["data"])
        if jpeg:
            return jpeg, c
    return None, None


def fetch_elsevier_ga(doi: str, apikey: str, session: requests.Session):
    """Graphical abstract via the ScienceDirect Object Retrieval API (needs only the
    API key, from a subscribing IP — no full-text entitlement). Returns
    (jpeg_bytes|None, status). The manifest lists image objects: 'ga1' IS the
    graphical abstract; 'gr1' (Figure 1) is the fallback."""
    try:
        r = session.get(ELS_OBJECT.format(doi=doi),
                        headers={"X-ELS-APIKey": apikey, "Accept": "application/json"},
                        timeout=40)
    except Exception as e:
        return None, f"error:{type(e).__name__}"
    if r.status_code not in (200, 300):     # 300 = the "multiple choices" object list
        return None, str(r.status_code)
    try:
        choices = r.json()["choices"]["choice"]
        if isinstance(choices, dict):
            choices = [choices]
    except Exception:
        return None, "no-manifest"
    ds = [c for c in choices if c.get("@type") == "IMAGE-DOWNSAMPLED"]
    cand = next((c for c in ds if c.get("@ref") == "ga1"), None) \
        or next((c for c in ds if c.get("@ref") == "gr1"), None) \
        or (ds[0] if ds else None)
    if not cand or not cand.get("$"):
        return None, "no-image"
    try:
        ir = session.get(cand["$"], headers={"X-ELS-APIKey": apikey}, timeout=40)
    except Exception as e:
        return None, f"error:{type(e).__name__}"
    if ir.status_code != 200 or ir.content[:3] != b"\xff\xd8\xff":
        return None, str(ir.status_code)
    jpeg = _validate_and_downscale(ir.content)
    return (jpeg, "ok") if jpeg else (None, "invalid")


def _iter_papers(store: Store):
    """Yield (basename, papers list) for each day/month data file."""
    import glob
    for f in sorted(glob.glob(str(ROOT / "data" / "*.json"))):
        b = os.path.basename(f)[:-5]
        if _DATE.match(b):
            yield b, store.load_day(b)


def _orig_generated_at(basename: str):
    import json
    try:
        return json.load(open(ROOT / "data" / f"{basename}.json", encoding="utf-8")).get("generated_at")
    except Exception:
        return None


def run_batch(wiley_token: str, els_key: str, limit: int | None, delay: float,
              do_push: bool, log=print) -> dict:
    store = Store()
    session = requests.Session()
    targets = {}   # id -> (publisher, doi, venue, url, license)
    files_of = {}  # id -> set(basenames)
    for b, papers in _iter_papers(store):
        for p in papers:
            files_of.setdefault(p.id, set()).add(b)
            if p.id in targets or (p.image and p.image.get("cached")):
                continue
            doi = p.doi or ""
            if doi.startswith(WILEY_PREFIX) and wiley_token:
                targets[p.id] = ("wiley", doi, p.venue, p.url, p.license)
            elif doi.startswith(ELS_PREFIX) and els_key:
                targets[p.id] = ("elsevier", doi, p.venue, p.url, p.license)
    ids = list(targets)
    if limit:
        ids = ids[:limit]
    nw = sum(1 for i in ids if targets[i][0] == "wiley")
    log(f"== TDM images: {len(ids)} papers ({nw} Wiley, {len(ids) - nw} Elsevier) ==")
    stats = {}
    for i, pid in enumerate(ids, 1):
        pub, doi, venue, url, lic = targets[pid]
        if pub == "wiley":
            pdf, status = _fetch_with_retry(doi, wiley_token, session, log)
            jpeg = extract_graphical_abstract(pdf)[0] if pdf is not None else None
            if pdf is not None:
                status = "ok" if jpeg is not None else "no-ga"
        else:
            jpeg, status = fetch_elsevier_ga(doi, els_key, session)
        if jpeg is None:
            stats[status] = stats.get(status, 0) + 1
            log(f"  [{i}/{len(ids)}] {status:6} {pub[0]} {doi}")
            time.sleep(delay)
            continue
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{_slug(pid)}.jpg"
        (IMG_DIR / fname).write_bytes(jpeg)
        attribution = (venue or "source") + (f" - https://doi.org/{doi}" if doi else "")
        for b in files_of[pid]:
            ps = store.load_day(b)
            changed = False
            for p in ps:
                if p.id == pid:
                    p.image = {"src": url, "cached": f"img/{fname}", "tdm": True,
                               "license": lic, "attribution": attribution}
                    changed = True
            if changed:
                store.save_day(b, ps, generated_at=_orig_generated_at(b) or "")
        stats["ok"] = stats.get("ok", 0) + 1
        log(f"  [{i}/{len(ids)}] OK     {pub[0]} {doi}")
        time.sleep(delay)
    log(f"== done: {stats} ==")
    if do_push and stats.get("ok"):
        _git_push(log)
    return stats


def _git_push(log=print):
    import subprocess
    try:
        subprocess.run(["git", "add", "data/", "site/img/"], cwd=ROOT, check=True)
        r = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=ROOT)
        if r.returncode == 0:
            log("  nothing to commit"); return
        subprocess.run(["git", "commit", "-m", "img: TDM graphical abstracts (Wiley/Elsevier)"], cwd=ROOT, check=True)
        subprocess.run(["git", "push"], cwd=ROOT, check=True)
        log("  committed + pushed")
    except Exception as e:
        log(f"  push failed: {e} (commit/push manually)")


def run_test(doi: str, token: str, log=print) -> int:
    """Fetch one paper and dump ALL first-page image candidates for inspection."""
    session = requests.Session()
    pdf, status = fetch_wiley_pdf(doi, token, session)
    if pdf is None:
        log(f"fetch {doi}: {status} (need WILEY_TDM_TOKEN set + campus network)")
        return 1
    log(f"fetch {doi}: OK ({len(pdf)//1024} KB PDF)")
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(doi)
    cands = _pdf_image_candidates(pdf)
    log(f"first-page image candidates: {len(cands)}")
    for idx, c in enumerate(cands):
        raw = TEST_DIR / f"{slug}__p{c['page']}_{idx}_{c['w']}x{c['h']}.{c['ext']}"
        raw.write_bytes(c["data"])
        log(f"  #{idx}: page {c['page']}  {c['w']}x{c['h']}  {c['ext']}  -> {raw.name}")
    jpeg, cand = extract_graphical_abstract(pdf)
    if jpeg:
        chosen = TEST_DIR / f"{slug}__CHOSEN.jpg"
        chosen.write_bytes(jpeg)
        log(f"CHOSEN -> {chosen.name}  (from page {cand['page']}, {cand['w']}x{cand['h']})")
    else:
        log("CHOSEN -> none (no usable figure)")
    log(f"\nInspect the files in: {TEST_DIR}")
    return 0


def run_sample(token: str, n: int, delay: float, log=print) -> int:
    """Fetch N Wiley papers and dump the CHOSEN thumbnail for each to _tdm_test/
    (no data changes) so extraction quality can be eyeballed across papers."""
    store = Store()
    session = requests.Session()
    picked, seen = [], set()
    for _b, papers in _iter_papers(store):
        for p in papers:
            if p.id in seen:
                continue
            seen.add(p.id)
            if (p.doi or "").startswith(WILEY_PREFIX) and not (p.image and p.image.get("cached")):
                picked.append((p.id, p.doi, p.venue))
    picked = picked[:n]
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    log(f"== sample {len(picked)} Wiley papers -> _tdm_test/ ==")
    ok = 0
    for i, (pid, doi, venue) in enumerate(picked, 1):
        pdf, status = _fetch_with_retry(doi, token, session, log)
        if pdf is None:
            log(f"  [{i}/{len(picked)}] {status:6} {doi}"); time.sleep(delay); continue
        jpeg, cand = extract_graphical_abstract(pdf)
        if not jpeg:
            log(f"  [{i}/{len(picked)}] no-ga  {doi}"); time.sleep(delay); continue
        vslug = re.sub(r"[^A-Za-z0-9]+", "_", (venue or "")[:16]).strip("_")
        fn = TEST_DIR / f"{_slug(pid)}__{vslug}.jpg"
        fn.write_bytes(jpeg)
        ok += 1
        log(f"  [{i}/{len(picked)}] OK  p{cand['page']} {cand['w']}x{cand['h']}  {doi}  -> {fn.name}")
        time.sleep(delay)
    log(f"== {ok}/{len(picked)} extracted. Inspect: {TEST_DIR} ==")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch graphical abstracts via Wiley TDM + Elsevier API (local, campus network).")
    ap.add_argument("--test", metavar="DOI", help="fetch one paper, dump image candidates for inspection")
    ap.add_argument("--sample", type=int, metavar="N", help="fetch N Wiley papers, dump CHOSEN thumbnails to _tdm_test/ (no data changes)")
    ap.add_argument("--limit", type=int, default=None, help="cap number of papers (batch)")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests (be polite)")
    ap.add_argument("--push", action="store_true", help="git commit + push after a successful batch")
    args = ap.parse_args(argv)

    wiley = os.environ.get("WILEY_TDM_TOKEN", "").strip()
    els = os.environ.get("ELSEVIER_API_KEY", "").strip()
    if not wiley and not els:
        print("Set WILEY_TDM_TOKEN and/or ELSEVIER_API_KEY (campus network).")
        return 2

    if args.test:
        return run_test(args.test, wiley)
    if args.sample:
        return run_sample(wiley, args.sample, args.delay)
    run_batch(wiley, els, args.limit, args.delay, args.push)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
