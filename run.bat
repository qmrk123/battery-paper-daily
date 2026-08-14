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

set DOSUM=
if defined ANTHROPIC_API_KEY set DOSUM=1
if defined CLAUDE_CODE_OAUTH_TOKEN set DOSUM=1
if defined DOSUM (
  echo [summarize] generating Korean summaries ...
  python -m pipeline.summarize
) else (
  echo [summarize] skipped ^(run: python -m pipeline.summarize --setup-token, then set CLAUDE_CODE_OAUTH_TOKEN^)
)

echo [images] collecting graphical abstracts ^(CC-OA only^) ...
python -m pipeline.images

echo [build] assembling public\ ...
python scripts\build_site.py

echo.
echo [serve] http://localhost:8765  (Ctrl+C to stop)
start "" http://localhost:8765
python -m http.server 8765 --directory public
