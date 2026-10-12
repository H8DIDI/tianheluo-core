@echo off
rem Tianheluo (THL) uninstaller launcher (pure ASCII). Real work is in uninstall.ps1.
setlocal
cd /d "%~dp0"
if not exist "%~dp0uninstall.ps1" (
  echo   [!] Please extract the whole zip first.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
pause
