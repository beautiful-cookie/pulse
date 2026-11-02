"""Команды: serve, collect, telegram-login, mcp."""
import json
import sys


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "serve":
        from .server import serve
        serve()
    elif command == "claude-login":
        from .claude_ai import login
        login()
    elif command == "install-model":
        from .setup_model import run
        run()
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
