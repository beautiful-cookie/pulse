"""Запуск коннектора из любой папки: Claude стартует его не из корня проекта."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pulse.mcp_server import main

main()
