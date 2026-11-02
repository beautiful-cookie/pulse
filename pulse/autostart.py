"""Запуск Пульса при входе в Windows. Включается только кнопкой в настройках."""
import os
import sys
from pathlib import Path

from .config import ROOT

STARTUP = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Pulse.vbs"


def script(python, root):
    """VBS запускает сервер без окна. Кириллица в пути требует файла в UTF-16."""
    return (f'Set shell = CreateObject("WScript.Shell")\r\n'
            f'shell.CurrentDirectory = "{root}"\r\n'
            f'shell.Run """{python}"" -X utf8 -m pulse serve", 0, False\r\n')


def is_enabled():
    return STARTUP.exists()


def set_enabled(on):
    if on:
        STARTUP.parent.mkdir(parents=True, exist_ok=True)
        STARTUP.write_text(script(sys.executable, ROOT), "utf-16")
    else:
        STARTUP.unlink(missing_ok=True)
    return is_enabled()
