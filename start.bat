@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Браузер открывается через три секунды, когда Пульс уже готов отвечать
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8765"
python -m pulse serve
echo.
echo Пульс остановился. Если выше есть сообщение об ошибке, сфотографируйте его.
pause
