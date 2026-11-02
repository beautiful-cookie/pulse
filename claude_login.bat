@echo off
cd /d "%~dp0"
chcp 65001 >nul
python -X utf8 -m pulse claude-login
echo.
pause
