"""Daily battery-paper tracker pipeline.

Stages:
    openalex / arxiv  -> raw records
    filters           -> topic post-filtering (regex precision)
    fetch             -> orchestrate sources + dedup against seen.json
    images            -> (Phase 3) best-effort graphical-abstract thumbnails
    summarize         -> (Phase 4) Korean summary + LLM relevance gate (Anthropic)
    build             -> write data/*.json for the static site
"""

__version__ = "0.1.0"
