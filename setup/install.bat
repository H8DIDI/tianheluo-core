@echo off
rem Tianheluo (THL) installer launcher. Pure ASCII on purpose: all real work is in install.ps1.
setlocal
cd /d "%~dp0"

echo.
echo   Tianheluo THL installer
echo.

rem Running directly from inside the zip? Then install.ps1 / net8 / net10 folders are missing.
if not exist "%~dp0install.ps1" goto notextracted
if not exist "%~dp0net10\THL\THL.Host.dll" goto notextracted

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo   Install did not finish. Exit code: %RC%
  echo   Please send the file below to the supplier:
  echo     %TEMP%\thl-install.log
)
pause
exit /b %RC%

:notextracted
echo   [!] Please extract the WHOLE zip to a folder first, then double-click install.bat again.
echo       Do not run it from inside the zip window.
echo.
pause
exit /b 1
