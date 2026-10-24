@echo off
rem One button for the redroid phone farm: opens the control window.
rem Nothing is installed; python + the portable adb/scrcpy in the repo do everything.
setlocal
cd /d "%~dp0"
python "android-headless\phone\farm_app.py" %*
if errorlevel 1 pause
