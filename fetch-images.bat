@echo off
setlocal
REM ── Graphical abstracts via Wiley/Elsevier TDM (LOCAL, run on the CAMPUS network) ──
REM Subscribed TDM access is IP-gated to campus, so this can't run in CI.
REM One-time setup (in cmd):  setx WILEY_TDM_TOKEN "..."  &  setx ELSEVIER_API_KEY "..."
REM Scheduled via Task Scheduler (task: battery-paper-images, daily; StartWhenAvailable
REM so a missed run — e.g. laptop off at the scheduled time — catches up on next wake).

REM Fail fast instead of popping/hanging on a git credential prompt in a headless run.
set GIT_TERMINAL_PROMPT=0
cd /d C:\dev\Coding\battery-paper-daily
set "LOG=%TEMP%\battery-images.log"
echo ==================== %DATE% %TIME% ==================== >> "%LOG%"

if "%WILEY_TDM_TOKEN%"=="" if "%ELSEVIER_API_KEY%"=="" (
  echo [!] Neither WILEY_TDM_TOKEN nor ELSEVIER_API_KEY set. >> "%LOG%"
  echo       setx WILEY_TDM_TOKEN "your-wiley-token"  ^&  setx ELSEVIER_API_KEY "your-elsevier-key" >> "%LOG%"
  exit /b 0
)

REM stay on main (the task must not run on a stray/detached checkout)
for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set "BR=%%b"
if not "%BR%"=="main" git checkout main >> "%LOG%" 2>&1

echo == git pull (get today's new papers from CI) == >> "%LOG%"
git pull --rebase >> "%LOG%" 2>&1

echo == Wiley/Elsevier TDM graphical abstracts -> data/ + site/img, then push == >> "%LOG%"
.venv\Scripts\python.exe -m pipeline.tdm_images --push >> "%LOG%" 2>&1
echo == done (python rc=%errorlevel%) == >> "%LOG%"

REM Always report success to Task Scheduler — image fetching is best-effort and must
REM not leave a scary non-zero "Last Result". Real errors are in %LOG%.
exit /b 0
