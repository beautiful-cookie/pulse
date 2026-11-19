"""Команды: serve, install, install-model, collect, doctor, telegram-login, claude-login, mcp."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def install():
    """Библиотеки из requirements.txt. Сообщения здесь, а не в .bat: там допустима только латиница."""
    print("Ставлю библиотеки Python для Пульса…", flush=True)
    code = subprocess.call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                            "-r", str(ROOT / "requirements.txt")])
    if code:
        print("\nНе получилось. Проверьте интернет и запустите install ещё раз.")
        sys.exit(code)
    print("\nГотово. Дальше запустите telegram_login, а потом start.")


def install_model():
    from .setup_model import run
    print("Скачиваю локальную модель: программу llama.cpp и модель Gemma, всего около 3 ГБ.")
    print("Это может занять от 5 минут до часа. Окно не закрывайте.")
    print("Если загрузка прервётся, запустите install_model ещё раз: она продолжится с того же места.\n", flush=True)
    try:
        run()
    except (OSError, ValueError) as error:
        print(f"\nНе получилось: {error}\nПроверьте интернет и запустите install_model ещё раз.")
        sys.exit(1)
    print("\nМодель на месте. Перезапустите Пульс: закройте его чёрное окно и снова откройте start.")


def serve():
    from .server import serve as run
    try:
        run()
    except BaseException:
        print("\nПульс остановился. Если выше есть сообщение об ошибке, сфотографируйте его.", file=sys.stderr)
        raise


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "serve":
        serve()
    elif command == "install":
        install()
    elif command == "claude-login":
        from .claude_ai import login
        login()
    elif command == "install-model":
        install_model()
    elif command == "demo":
        from .demo import run
        run()
    elif command == "collect":
        from .pipeline import run_collection
        print(json.dumps(run_collection(), ensure_ascii=False))
    elif command == "doctor":
        from .doctor import run
        run()
    elif command == "telegram-login":
        from .telegram import login
        login()
    elif command == "mcp":
        from .mcp_server import main as run_mcp
        run_mcp()
    else:
        sys.exit(f"Неизвестная команда: {command}")


main()
