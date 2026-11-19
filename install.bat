@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.12 from python.org and tick "Add python.exe to PATH".
  echo See INSTRUKCIYA, step 1.
  pause
  exit /b 1
)
python -X utf8 -m pulse install
echo.
pause
