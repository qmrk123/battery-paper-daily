@echo off
REM  Daily update only (no server): fetch -> data\*.json -> build public\.
REM  Point Windows Task Scheduler here for local automation, or use GitHub Actions.
setlocal
cd /d %~dp0

if not exist .venv (
  py -3.12 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt

python -m pipeline.main %*

REM Korean summaries + relevance gate (only if a key is available)
if defined ANTHROPIC_API_KEY (
  echo [summarize] generating Korean summaries ...
  python -m pipeline.summarize
) else (
  echo [summarize] skipped ^(ANTHROPIC_API_KEY not set^)
)

python scripts\build_site.py
