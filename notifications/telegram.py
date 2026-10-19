"""
Klien kecil Telegram Bot API (tahap 17). Hanya pustaka standar Python (urllib),
tanpa paket tambahan.

    send_message(chat_id, text)   kirim teks ke satu chat
    get_updates(offset)           ambil pesan yang masuk ke bot (untuk menyambungkan akun)
    bot_username()                nama bot, untuk tautan t.me/<nama>

Setiap panggilan = HTTPS POST ke
    https://api.telegram.org/bot<TOKEN>/<method>
dengan badan JSON. Jawaban Telegram selalu {"ok": true, "result": ...} atau
{"ok": false, "description": "..."}.

TOKEN bot adalah rahasia (siapa pun yang tahu bisa mengendalikan bot): hanya
di .env (TELEGRAM_BOT_TOKEN), tidak pernah ditulis ke log atau pesan error.
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = 10
MAX_TEXT = 4096      # batas panjang satu pesan Telegram
_username_cache = {}


class TelegramError(Exception):
    """Panggilan gagal; pesannya aman ditampilkan (tidak memuat token)."""


def enabled():
    return bool(settings.TELEGRAM_BOT_TOKEN)


def api(method, **params):
    if not enabled():
        raise TelegramError("Telegram is not configured on this server (TELEGRAM_BOT_TOKEN).")
    url = f"{settings.TELEGRAM_API_URL.rstrip('/')}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    request = urllib.request.Request(
        url, data=json.dumps(params).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # Telegram menjawab 4xx dengan JSON berisi alasan, mis. "Forbidden: bot was blocked by the user".
        try:
            body = json.loads(exc.read().decode())
        except (ValueError, OSError):
            raise TelegramError(f"Telegram answered HTTP {exc.code}.") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TelegramError(f"Cannot reach Telegram: {reason}") from None
    if not body.get("ok"):
        raise TelegramError(f"Telegram: {body.get('description', 'unknown error')}")
    return body.get("result")


def send_message(chat_id, text):
    """Kirim teks biasa (tanpa format Markdown/HTML, jadi tidak ada yang perlu di-escape)."""
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1] + "…"
    return api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)


def get_updates(offset):
    return api("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])


def bot_username():
    """Nama bot (dari .env TELEGRAM_BOT_USERNAME, atau ditanyakan ke Telegram sekali)."""
    if settings.TELEGRAM_BOT_USERNAME:
        return settings.TELEGRAM_BOT_USERNAME.lstrip("@")
    token = settings.TELEGRAM_BOT_TOKEN
    if token not in _username_cache:
        _username_cache[token] = api("getMe")["username"]
    return _username_cache[token]
