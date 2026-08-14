@echo off
REM  Local run: bootstrap venv, fetch today's papers, build the site, serve it.
setlocal
cd /d %~dp0

if not exist .venv (
  echo [setup] creating .venv ...
  py -3.12 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

echo [fetch] gathering new papers ...
python -m pipeline.main %*

if defined ANTHROPIC_API_KEY (
  echo [summarize] generating Korean summaries ...
  python -m pipeline.summarize
) else (
  echo [summarize] skipped ^(set ANTHROPIC_API_KEY to enable Korean summaries^)
)

echo [build] assembling public\ ...
python scripts\build_site.py

echo.
echo [serve] http://localhost:8765  (Ctrl+C to stop)
start "" http://localhost:8765
python -m http.server 8765 --directory public
