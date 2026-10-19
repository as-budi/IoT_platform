"""
Notifikasi Telegram (tahap 17, docs/tahap-17-notifikasi-telegram.md).

    TelegramLink      akun platform <-> chat Telegram pengguna (1-1)
    TelegramLinkCode  kode sekali pakai untuk menyambungkan, berlaku 15 menit
    TelegramBotState  posisi terakhir pembacaan pesan bot (offset getUpdates)

Mengapa butuh kode? Bot Telegram tidak tahu siapa pengguna platform. Pengguna
membuka tautan t.me/<bot>?start=<kode> dari halaman profilnya; Telegram lalu
mengirim pesan "/start <kode>" ke bot dari chat milik pengguna itu. Kode
menghubungkan "chat ini" dengan "akun yang membuat kode ini".
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class TelegramLink(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram")
    # Id chat bisa lebih besar dari int 32-bit dan negatif (grup), jadi disimpan sebagai teks.
    chat_id = models.CharField("chat id", max_length=32)
    chat_name = models.CharField("chat name", max_length=100, blank=True)
    linked_at = models.DateTimeField("linked", default=timezone.now)

    class Meta:
        verbose_name = "Telegram link"
        verbose_name_plural = "Telegram links"

    def __str__(self):
        return f"{self.user} -> {self.chat_name or self.chat_id}"


class TelegramLinkCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_codes")
    code = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Telegram link code"
        verbose_name_plural = "Telegram link codes"

    def __str__(self):
        return f"{self.user}: {self.code}"


class TelegramBotState(models.Model):
    """Satu baris saja (pk=1): update_id berikutnya yang belum dibaca."""

    next_update_id = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = "Telegram bot state"
        verbose_name_plural = "Telegram bot state"
