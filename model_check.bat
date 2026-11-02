@echo off
cd /d "%~dp0"
chcp 65001 >nul
python -X utf8 tests\model_exam.py
echo.
pause
