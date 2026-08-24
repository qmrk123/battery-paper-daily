@echo off
setlocal
REM Graphical abstracts via Wiley/Elsevier TDM (LOCAL, run on the CAMPUS network).
REM Subscribed TDM access is IP-gated to campus, so this cannot run in CI.
REM Setup once (cmd): setx WILEY_TDM_TOKEN "..."  and  setx ELSEVIER_API_KEY "..."
REM Scheduled as task battery-paper-images (StartWhenAvailable catches missed runs).
set GIT_TERMINAL_PROMPT=0
cd /d C:\dev\Coding\battery-paper-daily
set "LOG=C:\dev\Coding\battery-paper-daily\_imagetask.log"
echo ==================== %DATE% %TIME% ==================== >> "%LOG%"
if defined WILEY_TDM_TOKEN (echo wiley-token=yes>> "%LOG%") else (echo wiley-token=NO>> "%LOG%")
if defined ELSEVIER_API_KEY (echo elsevier-key=yes>> "%LOG%") else (echo elsevier-key=NO>> "%LOG%")
if "%WILEY_TDM_TOKEN%"=="" if "%ELSEVIER_API_KEY%"=="" (
  echo [!] Neither WILEY_TDM_TOKEN nor ELSEVIER_API_KEY set. >> "%LOG%"
  exit /b 0
)
for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set "BR=%%b"
if not "%BR%"=="main" git checkout main >> "%LOG%" 2>&1
echo == git pull (get today's new papers from CI) == >> "%LOG%"
git pull --rebase >> "%LOG%" 2>&1
echo == Wiley/Elsevier TDM graphical abstracts to data/ + site/img, then push == >> "%LOG%"
.venv\Scripts\python.exe -m pipeline.tdm_images --push >> "%LOG%" 2>&1
echo == done (python rc=%errorlevel%) == >> "%LOG%"
exit /b 0