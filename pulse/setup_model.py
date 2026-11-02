"""Установка локальной модели: python -m pulse install-model

Версии и контрольные суммы закреплены. Файл с другой суммой не будет использован.
"""
import hashlib
import ssl
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from .config import ROOT

MODEL_LABEL = "Gemma 4 E2B"
MODELS = ROOT / "models"
TOOLS = ROOT / "tools" / "llama"
MODEL_FILE = MODELS / "gemma-4-E2B-it-Q4_K_M.gguf"
SERVER_FILE = TOOLS / "llama-server.exe"
MODEL_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
MODEL_SHA256 = "740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8"
LLAMA_TAG = "b12048"
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/llama-{LLAMA_TAG}-bin-win-cpu-x64.zip"
LLAMA_SHA256 = "e05b3d640ddc44a4cc8f032c262aceedaec1252a398bee8f4246b1bffaf161be"
CHUNK = 1 << 20
RETRIES = 40


def context():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    return ctx


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download(url, target, expected):
    """Докачивает файл с места обрыва и проверяет сумму."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256(target) == expected:
        print(f"{target.name}: уже на месте", flush=True)
        return
    part = target.with_name(target.name + ".part")
    total = None
    for attempt in range(1, RETRIES + 1):
        done = part.stat().st_size if part.exists() else 0
        if total is not None and done >= total:
            break
        try:
            total = fetch_range(url, part, done, target.name)
        except OSError as error:
            print(f"{target.name}: обрыв, попытка {attempt}: {type(error).__name__}", flush=True)
            time.sleep(3)
    if total is None or part.stat().st_size != total:
        sys.exit(f"{target.name}: не удалось докачать, запустите команду ещё раз, загрузка продолжится")
    if sha256(part) != expected:
        part.unlink()
        sys.exit(f"Контрольная сумма {target.name} не совпала, файл удалён")
    part.replace(target)
    print(f"{target.name}: проверен", flush=True)


def fetch_range(url, part, done, name):
    """Качает с позиции done. Конец потока ещё не значит конец файла: размер сверяет вызывающий код."""
    request = urllib.request.Request(url, headers={"User-Agent": "pulse", "Range": f"bytes={done}-"})
    with urllib.request.urlopen(request, timeout=60, context=context()) as response:
        resumed = response.status == 206
        total = (done if resumed else 0) + int(response.headers.get("Content-Length", 0))
        done = done if resumed else 0
        reported = -1
        with part.open("ab" if resumed else "wb") as fh:
            while block := response.read(CHUNK):
                fh.write(block)
                done += len(block)
                percent = done * 100 // max(total, 1)
                if percent // 5 != reported:
                    reported = percent // 5
                    print(f"{name}: {percent}% из {total / 1e9:.2f} ГБ", flush=True)
    return total


def install_server():
    if SERVER_FILE.exists():
        print("llama-server: уже на месте", flush=True)
        return
    archive = TOOLS / f"llama-{LLAMA_TAG}.zip"
    download(LLAMA_URL, archive, LLAMA_SHA256)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name and Path(member).suffix.lower() in (".exe", ".dll") and name.startswith(("llama-server", "llama", "ggml", "mtmd", "lib", "vcomp", "msvc")):
                (TOOLS / name).write_bytes(zf.read(member))
    archive.unlink()
    if not SERVER_FILE.exists():
        sys.exit("В архиве не нашёлся llama-server.exe")


def run():
    install_server()
    download(MODEL_URL, MODEL_FILE, MODEL_SHA256)
    print("Готово", flush=True)


def installed():
    return MODEL_FILE.exists() and SERVER_FILE.exists()
