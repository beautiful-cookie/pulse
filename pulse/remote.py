"""Доступ с телефона в домашней сети. Выключен по умолчанию.

Здесь видны личные чаты, поэтому без ключа не отдаётся ни одна страница, а запросы
принимаются только с адресов локальной сети. Соединение не шифруется: в общей сети
доступ надо выключать.
"""
import hmac
import ipaddress
import secrets
import socket
from http.cookies import SimpleCookie

COOKIE = "pulse_key"
COOKIE_DAYS = 30
PORT_SHIFT = 10
LOGIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Пульс</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui,sans-serif;padding:16px;text-align:center}</style>
</head><body><p>Откройте ссылку с QR-кода:<br>Пульс на компьютере, Настройки, Телефон.</p></body></html>"""


def remote_port(port):
    return port + PORT_SHIFT


def new_key():
    return secrets.token_urlsafe(24)


def key_matches(key, given):
    return bool(key and given) and hmac.compare_digest(key, given)


def cookie_key(header):
    cookie = SimpleCookie()
    try:
        cookie.load(header or "")
    except Exception:
        return ""
    return cookie[COOKIE].value if COOKIE in cookie else ""


def cookie_header(key):
    return f"{COOKIE}={key}; Path=/; Max-Age={COOKIE_DAYS * 86400}; HttpOnly; SameSite=Strict"


def client_allowed(address):
    """Только локальная сеть. Адреса Tailscale тоже не публичные, поэтому пройдут."""
    try:
        return not ipaddress.ip_address(address.split("%")[0]).is_global
    except ValueError:
        return False


def host_allowed(host, port):
    """Только адрес вида IP:порт: так обычный сайт не обратится сюда по своему домену."""
    name, _, given_port = host.rpartition(":")
    if given_port != str(port):
        return False
    try:
        return not ipaddress.ip_address(name).is_global
    except ValueError:
        return False


def lan_ip():
    """Адрес компьютера в сети. Пакет не отправляется, система лишь выбирает маршрут."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def qr_svg(url):
    import qrcode
    from qrcode.image.svg import SvgPathImage
    return qrcode.make(url, image_factory=SvgPathImage, box_size=8, border=3).to_string(encoding="unicode")


def status(cfg, port):
    settings = cfg["remote"]
    if not settings["enabled"]:
        return {"enabled": False}
    address = lan_ip()
    if not address:
        return {"enabled": True, "url": "", "qr": ""}
    url = f"http://{address}:{remote_port(port)}/?key={settings['key']}"
    return {"enabled": True, "url": url, "address": f"{address}:{remote_port(port)}", "qr": qr_svg(url)}
