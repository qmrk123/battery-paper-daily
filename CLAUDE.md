# battery-paper-daily — repo guide for Claude

Static site + Python pipeline that gathers newly-indexed battery-materials papers
daily into 4 topic tabs (Li metal / Na metal / High-Ni NCM / Li-rich) with title,
link, graphical abstract (best-effort), and a **Korean** summary.

**Not** a PyQt app — this is a data pipeline + static web frontend. No tem-core.

## Run / test / preview
- `run.bat` — bootstrap `.venv` (Python 3.12), fetch, build `public/`, serve on :8765.
- `update.bat` — fetch + build only (for scheduler/CI).
- Tests: `.venv\Scripts\python -m pytest tests/ -q` (pure logic, no network).
- Live dry-run: `python -m pipeline.main --topic <id> --dry-run --show-filtered`.
- The site (`site/`) is fetch-based; `scripts/build_site.py` assembles `public/`
  (site + `data/`) for both local preview and Pages. `scripts/snapshot.py` makes a
  single self-contained HTML (data inlined) for offline/side-panel preview.

## Key facts (see DESIGN.md for full rationale)
- OpenAlex **free tier**: `from_publication_date` + `sort=publication_date:desc`
  work; `from_created_date`/`from_updated_date`/`sort=created_date` are **paid** —
  do not use them. No API key needed (mailto polite pool); `OPENALEX_API_KEY` optional.
- Abstracts are inverted-index → reconstructed; some closed papers have none.
- Precision = 3-stage funnel: search → regex (`include`/`exclude`/`context` in
  `config/topics.yaml`) → LLM relevance gate (Phase 4). Edit topics.yaml to tune.
- Cross-source dedup by canonical DOI (`models.canonical_key`); arXiv preprint and
  its OpenAlex copy collapse.

## Phases
1. ✅ Fetch pipeline → `data/*.json`
2. ✅ Static site (tabs/cards/date archive, dark-mode, instrument-panel design)
3. ✅ Graphical-abstract best-effort (`pipeline/images.py`) — license-first (CC-OA
   only; publisher bot-blocks mean most papers keep the placeholder — by design)
4. ✅ Korean summaries + LLM relevance gate (`pipeline/summarize.py`) — Anthropic
   Haiku 4.5; needs `ANTHROPIC_API_KEY`. Runs from run.bat/update.bat when set.
5. ✅ GitHub Actions daily cron → commit-back + Pages deploy (`.github/workflows/daily.yml`).

Remaining: user creates the GitHub repo, sets Pages source + `ANTHROPIC_API_KEY`
secret, runs the workflow once. Optional: Batch API for -50% summary cost.

## Conventions
- `data/` **is committed** (it's the site content). Never commit API keys.
- Only re-host CC-licensed graphical abstracts; others get placeholder + link-out.
- Keep the `Paper` field names stable — `site/app.js` reads them directly.
