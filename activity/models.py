"""
Log aktivitas platform (tahap 18, docs/tahap-18-log-aktivitas-dan-pencarian-aturan.md).

Satu tabel untuk SEMUA kejadian penting: device dibuat/dihapus/online/offline,
aktuator diubah, command dikirim/selesai, aturan disimpan/aktif/pulih, alarm,
pembaruan firmware, login, dan seterusnya. Setiap baris berdiri sendiri: nama
device/aturan disalin ke kolom teks, sehingga log tetap terbaca walaupun
device atau aturannya sudah dihapus.

Siapa boleh melihat baris mana ditentukan kolom `owner` (pemilik sumber daya
yang dibicarakan, mis. pemilik device), BUKAN `actor` (siapa yang melakukan).
Contoh: Admin mematikan pompa milik siti -> owner = siti, actor = admin.
Siti melihatnya di log-nya; Admin melihat semua baris.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ActivityEvent(models.Model):
    class Category(models.TextChoices):
        DEVICE = "DEVICE", "Device"
        CONTROL = "CONTROL", "Control"          # aktuator dan command
        RULE = "RULE", "Rule"
        ALARM = "ALARM", "Alarm"
        FIRMWARE = "FIRMWARE", "Firmware"
        ACCOUNT = "ACCOUNT", "Account"
        SHARING = "SHARING", "Sharing"          # berbagi device & grup (tahap 19)

    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        ERROR = "ERROR", "Error"

    at = models.DateTimeField("time", default=timezone.now)
    category = models.CharField("category", max_length=10, choices=Category.choices)
    level = models.CharField("level", max_length=8, choices=Level.choices, default=Level.INFO)
    # Kode singkat yang stabil, mis. "device.offline", "rule.activated" (untuk filter dan tes).
    verb = models.CharField("event", max_length=40)
    message = models.CharField("message", max_length=300)

    # Pemilik sumber daya -> menentukan siapa yang boleh melihat baris ini.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="owner", null=True, blank=True,
        on_delete=models.CASCADE, related_name="+",
    )
    # Pelaku: pengguna yang login, atau kosong untuk sistem/device/aturan (lihat actor_name).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="by", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    actor_name = models.CharField("by", max_length=100, blank=True)
    device = models.ForeignKey(
        "devices.Device", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    device_name = models.CharField("device", max_length=100, blank=True)
    rule = models.ForeignKey("rules.Rule", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    rule_name = models.CharField("rule", max_length=100, blank=True)

    class Meta:
        ordering = ["-at", "-pk"]
        verbose_name = "activity event"
        verbose_name_plural = "activity events"
        indexes = [
            models.Index(fields=["owner", "at"], name="activity_owner_at"),
            models.Index(fields=["at"], name="activity_at"),
            models.Index(fields=["device", "at"], name="activity_device_at"),
        ]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M:%S} {self.verb}: {self.message}"
