@echo off
rem One-button Tailscale setup for the android-ci live device.
rem Double-click to install (to E:\ if that drive exists), suppress restore
rem points, and log in. Re-run any time; done steps skip themselves.
rem Full story in TAILSCALE.md.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tailscale-setup.ps1"
pause
