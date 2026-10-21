@echo off
REM ============================================================================
REM  One-button launcher for the Android device.
REM
REM  Double-click this file for a phone on THIS PC (local emulator + live window).
REM  Or run it with arguments for a phone on a cloud VM:
REM
REM      Phone.cmd remote --vm ubuntu@1.2.3.4
REM
REM  First run of local mode needs the SDK once:
REM      python android-headless\setup_sdk.py
REM
REM  On the first run it also downloads portable adb and scrcpy into
REM  android-headless\phone\tools\ -- no system install, no admin rights.
REM ============================================================================

setlocal
set "HERE=%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python is not on PATH. Install it from https://python.org and reopen this window.
  pause
  exit /b 1
)

if "%~1"=="" (
  python "%HERE%android-headless\phone\launch.py" local
) else (
  python "%HERE%android-headless\phone\launch.py" %*
)

echo.
echo Session ended. If the window closed, the device may still be running.
pause
