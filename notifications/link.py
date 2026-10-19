"""
Menyambungkan akun platform dengan chat Telegram (tahap 17).

    1. Profil -> "Connect Telegram": new_code(user) membuat kode acak, halaman
       menampilkan tautan https://t.me/<bot>?start=<kode>.
    2. Pengguna membuka tautan, menekan START. Telegram mengirim "/start <kode>"
       ke bot, dari chat milik pengguna.
    3. Pengguna menekan "I pressed Start". check_updates() membaca pesan baru
       bot (getUpdates), mencocokkan kode, lalu menyimpan chat_id.

Mengapa getUpdates saat tombol ditekan, bukan webhook atau worker? Webhook butuh
alamat HTTPS publik (tidak ada di laptop), worker berarti satu proses lagi.
Menyambungkan akun hanya terjadi sesekali, jadi cukup dibaca saat dibutuhkan.
Semua pesan /start dalam satu bacaan diproses sekaligus, jadi pengguna lain
yang sedang menyambungkan juga ikut beres.
"""
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import telegram
from .models import TelegramBotState, TelegramLink, TelegramLinkCode

CODE_MINUTES = 15


def new_code(user):
    TelegramLinkCode.objects.filter(user=user).delete()
    return TelegramLinkCode.objects.create(user=user, code=secrets.token_urlsafe(16)).code


def deep_link(code):
    return f"https://t.me/{telegram.bot_username()}?start={code}"


def chat_name(chat):
    name = " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part)
    username = chat.get("username")
    return (name or chat.get("title") or "") + (f" (@{username})" if username else "")


def handle_start(message):
    """Satu pesan "/start <kode>". Mengembalikan user yang tersambung, atau None."""
    chat = message.get("chat", {})
    parts = (message.get("text") or "").split()
    if len(parts) != 2 or parts[0] != "/start" or not chat.get("id"):
        return None
    fresh = timezone.now() - timedelta(minutes=CODE_MINUTES)
    entry = TelegramLinkCode.objects.filter(code=parts[1], created_at__gte=fresh).select_related("user").first()
    if entry is None:
        _reply(chat["id"], "This link has expired. Open your profile on the IoT platform and press "
                           "\"Connect Telegram\" again.")
        return None
    user = entry.user
    TelegramLink.objects.update_or_create(user=user, defaults={
        "chat_id": str(chat["id"]), "chat_name": chat_name(chat)[:100], "linked_at": timezone.now(),
    })
    entry.delete()
    _reply(chat["id"], f"Connected to the IoT platform account \"{user.username}\". "
                       "Rule notifications will arrive in this chat.")
    return user


def _reply(chat_id, text):
    try:
        telegram.send_message(chat_id, text)
    except telegram.TelegramError:
        pass     # balasan hanya basa-basi; sambungan tetap tersimpan


def check_updates():
    """Baca pesan baru bot dan proses semua "/start <kode>". Mengembalikan daftar user yang tersambung."""
    with transaction.atomic():
        state, _ = TelegramBotState.objects.select_for_update().get_or_create(pk=1)
        updates = telegram.get_updates(state.next_update_id)
        linked = []
        for update in updates:
            user = handle_start(update.get("message") or {})
            if user:
                linked.append(user)
            state.next_update_id = max(state.next_update_id, update["update_id"] + 1)
        state.save()
    return linked
