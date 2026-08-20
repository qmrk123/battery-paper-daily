"""Orchestrate sources -> regex filter -> dedup.

`gather_candidates` is side-effect free (good for --dry-run). `select_new`
compares against the seen ledger to find genuinely new papers for a run date.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .arxiv import ArxivClient
from .config import Config, Topic
from .models import Paper, canonical_key, normalize_title
from .openalex import OpenAlexClient

_ROOT = Path(__file__).resolve().parent.parent
SOURCES_CACHE = _ROOT / "data" / "journal_sources.json"

# `Nature` is an allowlist PREFIX (the gate keeps any "Nature …" venue). For
# journal-FIRST we must name the battery-relevant sisters explicitly by source.
NATURE_SISTERS = [
    "Nature", "Nature Communications", "Nature Energy", "Nature Materials",
    "Nature Nanotechnology", "Nature Chemistry", "Nature Reviews Materials",
]


def _prefer(a: Paper, b: Paper) -> Paper:
    """Pick the better of two records for the same work (cross-source dup).
    Prefer a real abstract, then the journal-of-record (openalex) over arXiv."""
    if bool(a.abstract_en) != bool(b.abstract_en):
        return a if a.abstract_en else b
    if a.source != b.source:
        return a if a.source == "openalex" else b
    return a


def _prefer_edition(a: Paper, b: Paper) -> Paper:
    """Choose which of two same-title editions to keep: one with an abstract,
    then the English 'International Edition' over a language edition."""
    if bool(a.abstract_en) != bool(b.abstract_en):
        return a if a.abstract_en else b
    ai = "international edition" in (a.venue or "").lower()
    bi = "international edition" in (b.venue or "").lower()
    if ai != bi:
        return a if ai else b
    return a


def dedup_by_title(cands: dict[str, Paper]) -> dict[str, Paper]:
    """Collapse candidates that share an identical normalized title (different
    DOIs), e.g. a paper's German + International editions."""
    seen: dict[str, tuple[str, Paper]] = {}
    for key, p in cands.items():
        tk = normalize_title(p.title)
        if not tk:
            seen[f"__{key}"] = (key, p)
            continue
        if tk in seen:
            k0, p0 = seen[tk]
            win = _prefer_edition(p0, p)
            los = p if win is p0 else p0
            win.merge_topics(los.topics)
            seen[tk] = ((k0 if win is p0 else key), win)
        else:
            seen[tk] = (key, p)
    return {k: p for k, p in seen.values()}


@dataclass
class TopicStat:
    topic: str
    raw: int = 0          # returned by search (pre-filter)
    kept: int = 0         # survived regex post-filter
    new: int = 0          # not previously seen


@dataclass
class GatherResult:
    candidates: dict[str, Paper] = field(default_factory=dict)
    stats: list[TopicStat] = field(default_factory=list)


def resolve_journal_sources(cfg: Config, oa: OpenAlexClient, log=print) -> dict[str, str]:
    """{journal name -> OpenAlex source id} for the allowlist + Nature sisters,
    cached on disk (data/journal_sources.json) so each name resolves only once."""
    names = list(dict.fromkeys((cfg.allow_journals or []) + NATURE_SISTERS))
    cache: dict[str, Optional[str]] = {}
    if SOURCES_CACHE.exists():
        try:
            cache = json.loads(SOURCES_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    changed = False
    for name in names:
        if name in cache:
            continue
        sid = oa.resolve_source(name)      # may be None; cached so we don't retry each run
        cache[name] = sid
        changed = True
        log(f"  [source] {name} -> {sid}")
    if changed:
        SOURCES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SOURCES_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return {n: s for n, s in cache.items() if s}


def add_journal_candidates(cfg: Config, from_date: str, sources: dict[str, str],
                           result: GatherResult, oa: OpenAlexClient,
                           max_per_source: int = 3000, log=print) -> int:
    """Journal-first: pull every recent work from each allowlisted source, keep the
    ones passing the shared battery/electrochem context gate, and add them (topics
    empty — the LLM classifies later) to the candidate set. Closes the gap where a
    journal's battery papers don't match our topic search phrasing."""
    ctx = cfg.journal_gate or cfg.context
    added = 0
    for name, sid in sources.items():
        got = kept = 0
        try:
            for p in oa.search_source(sid, from_date, max_results=max_per_source,
                                      types=cfg.types):
                got += 1
                if ctx and not any(pat.search(p.search_text) for pat in ctx):
                    continue
                kept += 1
                key = canonical_key(p.doi, p.id)
                if key not in result.candidates:   # never clobber a topic-matched record
                    result.candidates[key] = p
                    added += 1
        except Exception as e:
            log(f"  [journal:{name}] failed: {e}")
            continue
        log(f"  [journal:{name}] fetched={got} kept={kept}")
    log(f"  journal-first: +{added} candidates")
    return added


def gather_candidates(
    cfg: Config,
    from_date: str,
    topic_ids: Optional[list[str]] = None,
    use_arxiv: bool = True,
    oa_client: Optional[OpenAlexClient] = None,
    ax_client: Optional[ArxivClient] = None,
    journal_first: bool = False,
    log=print,
) -> GatherResult:
    """Search all sources for each topic, apply the regex post-filter, and
    accumulate a de-duplicated (by paper id) candidate set with merged topics."""
    oa = oa_client or OpenAlexClient()
    want_arxiv = use_arxiv and cfg.include_preprints
    ax = ax_client if ax_client is not None else (ArxivClient() if want_arxiv else None)
    topics = [t for t in cfg.topics if not topic_ids or t.id in topic_ids]

    result = GatherResult()
    for topic in topics:
        stat = TopicStat(topic=topic.id)
        raw_by_id: dict[str, Paper] = {}

        # --- OpenAlex: one request per query phrase, merged ---
        for q in topic.queries:
            for p in oa.search(q, from_date, max_results=cfg.max_per_topic,
                               types=cfg.types):
                raw_by_id.setdefault(p.id, p)

        # --- arXiv preprints (optional) ---
        if ax is not None and topic.arxiv:
            try:
                for p in ax.search(topic.arxiv, from_date,
                                   max_results=min(cfg.max_per_topic, 50)):
                    raw_by_id.setdefault(p.id, p)
            except Exception as e:  # arXiv outages shouldn't kill the run
                log(f"  [arxiv] {topic.id} failed: {e}")

        stat.raw = len(raw_by_id)

        # --- regex precision filter + accumulate into candidate set ---
        # Keyed by canonical DOI so an arXiv preprint and its OpenAlex-indexed
        # copy collapse into one record.
        for p in raw_by_id.values():
            if not topic.matches(p.search_text):
                continue
            stat.kept += 1
            key = canonical_key(p.doi, p.id)
            existing = result.candidates.get(key)
            if existing:
                winner = _prefer(existing, p)
                winner.merge_topics(existing.topics + [topic.id])
                result.candidates[key] = winner
            else:
                p.merge_topics([topic.id])
                result.candidates[key] = p

        log(f"  [{topic.id}] raw={stat.raw} kept={stat.kept}")
        result.stats.append(stat)

    # journal-first: add allowlisted-journal battery papers the topic queries missed
    if journal_first:
        sources = resolve_journal_sources(cfg, oa, log=log)
        add_journal_candidates(cfg, from_date, sources, result, oa, log=log)

    # collapse same-title editions (Angew German vs International, etc.)
    before = len(result.candidates)
    result.candidates = dedup_by_title(result.candidates)
    if len(result.candidates) < before:
        log(f"  title-dedup: {before} -> {len(result.candidates)}")

    # journal gate: allowlist (if set) else metric threshold; drops preprints too
    if cfg.min_journal_metric > 0 or cfg.allow_journals:
        from .journals import enrich_and_filter
        before = len(result.candidates)
        result.candidates, dropped = enrich_and_filter(
            result.candidates, cfg.min_journal_metric, cfg.include_preprints,
            allow_journals=cfg.allow_journals or None, log=log)
        mode = (f"allowlist({len(cfg.allow_journals)})" if cfg.allow_journals
                else f">={cfg.min_journal_metric}")
        log(f"  journal-gate {mode}: {before} -> {len(result.candidates)} "
            f"(dropped {dropped})")

    return result


def select_new(
    candidates: dict[str, Paper],
    seen: dict[str, str],
    run_date: str,
) -> list[Paper]:
    """Return candidates not in the seen ledger, stamped with first_seen.
    Mutates `seen` in place to register the new ids."""
    new: list[Paper] = []
    for pid, paper in candidates.items():
        if pid in seen:
            continue
        paper.first_seen = run_date
        seen[pid] = run_date
        new.append(paper)
    # newest publication first, then title for stability
    new.sort(key=lambda p: (p.published or "", p.title), reverse=True)
    return new
