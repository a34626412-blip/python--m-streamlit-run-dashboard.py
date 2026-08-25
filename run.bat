@echo off
setlocal
cd /d "%~dp0"
title CHIPS BOOST LIVE v12

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" not found.
  echo Install Python 3.11+ and enable the Python launcher.
  pause
  exit /b 1
)

echo Installing/checking dependencies...
py -3 -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  pause
  exit /b 1
)

echo.
echo ==========================================
echo        CHIPS BOOST LIVE v12
echo ==========================================
echo API-only scanner - chips.gg sports page is NOT requested.
echo Local dashboard: http://127.0.0.1:8765
echo.
start "" http://127.0.0.1:8765
py -3 app.py
pause
