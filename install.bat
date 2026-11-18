@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Ставлю библиотеки Python для Пульса...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo Не получилось. Проверьте, что Python 3.12 установлен и при установке была отмечена галочка "Add python.exe to PATH".
  pause
  exit /b 1
)
echo.
echo Готово. Дальше запустите telegram_login.bat, а потом start.bat.
pause
