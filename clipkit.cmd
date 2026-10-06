@echo off
rem ClipKit Team commands:  clipkit doctor | clipkit update | clipkit repair
setlocal
set "ROOT=%~dp0"
if /i "%~1"=="doctor" ( python "%ROOT%scripts\doctor.py" & exit /b %errorlevel% )
if /i "%~1"=="update" (
  git -C "%ROOT%." pull --ff-only || exit /b 1
  python -m pip install -q -r "%ROOT%requirements.txt" || exit /b 1
  powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\app_icon.ps1"
  python "%ROOT%scripts\doctor.py" & exit /b %errorlevel%
)
if /i "%~1"=="app" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\app_icon.ps1"
  powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%app\start.ps1" & exit /b %errorlevel%
)
if /i "%~1"=="report" ( python "%ROOT%scripts\cutlog.py" report & exit /b %errorlevel% )
if /i "%~1"=="sheet" ( python "%ROOT%scripts\cutlog.py" sheet "%~2" & exit /b %errorlevel% )
if /i "%~1"=="repair" ( powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\setup.ps1" & exit /b %errorlevel% )
echo usage: clipkit app ^| doctor ^| update ^| repair ^| report
echo   app     open the ClipKit window (and put its icon on the desktop)
echo   doctor  check this machine is ready
echo   update  get the newest ClipKit Team and its packages
echo   repair  run the setup again (installs what is missing)
echo   report  zip the cut log to send back (no video)
echo   sheet   clipkit sheet ^<URL^>  send each clip's check to the team Google Sheet
exit /b 1
