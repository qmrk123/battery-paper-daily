@echo off
REM ── Graphical abstracts via Wiley TDM (LOCAL, run on the CAMPUS network) ──
REM Wiley TDM subscribed access is IP-gated to campus, so this can't run in CI.
REM One-time setup (in cmd):   setx WILEY_TDM_TOKEN "your-token-uuid"
REM Then run this file (double-click, or schedule it in Task Scheduler).
cd /d C:\dev\Coding\battery-paper-daily

if "%WILEY_TDM_TOKEN%"=="" if "%ELSEVIER_API_KEY%"=="" (
  echo [!] Neither WILEY_TDM_TOKEN nor ELSEVIER_API_KEY set. Run once (then new cmd):
  echo       setx WILEY_TDM_TOKEN "your-wiley-token"
  echo       setx ELSEVIER_API_KEY "your-elsevier-key"
  exit /b 2
)

echo == git pull (get today's new papers from CI) ==
git pull --rebase

echo == Wiley TDM graphical abstracts -> data/ + site/img, then push ==
.venv\Scripts\python.exe -m pipeline.tdm_images --push

echo == done ==
