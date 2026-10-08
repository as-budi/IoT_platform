"""
Model data device dan telemetri.

Aturan rancangan: SATU device = SATU variabel. Sebuah device mengukur tepat
satu besaran (mis. suhu), yang nama dan satuannya dicatat di model Device.
Alat fisik yang mengukur beberapa besaran didaftarkan sebagai beberapa device.
Telemetri cukup menyimpan deret waktu (waktu, nilai) milik device itu.

Ini berbeda dari ThingsBoard, yang mengizinkan banyak "key" per device.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Device(models.Model):
    # ForeignKey = relasi banyak-ke-satu: satu pengguna punya banyak device.
    # settings.AUTH_USER_MODEL dipakai (bukan import User langsung) karena
    # itulah cara baku merujuk model user kustom.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="owner",
        on_delete=models.CASCADE,  # pengguna dihapus -> device-nya ikut terhapus
        related_name="devices",    # memungkinkan: user.devices.all()
    )
    name = models.CharField("name", max_length=100)
    device_type = models.CharField(
        "type",
        max_length=50,
        blank=True,
        help_text="Example: temperature sensor, power meter.",
    )
    # Variabel tunggal yang diukur device ini, beserta satuannya.
    variable = models.CharField(
        "variable",
        max_length=50,
        help_text="The measured quantity. Example: temperature.",
    )
    unit = models.CharField(
        "unit",
        max_length=20,
        blank=True,
        help_text="Example: °C, %, V. May be left blank.",
    )
    description = models.TextField("description", blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("created", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "device"
        verbose_name_plural = "devices"

    def __str__(self):
        return self.name

    @property
    def latest(self):
        """Baris telemetri terbaru device ini (None bila belum ada data).

        Dipakai untuk card "nilai terkini" di halaman detail.
        """
        return self.telemetry.order_by("-ts").first()


class Telemetry(models.Model):
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="telemetry",  # memungkinkan: device.telemetry.all()
    )
    # Tidak ada kolom nama variabel di sini: variabelnya sudah ditentukan
    # oleh device. Tahap ini hanya mendukung nilai angka.
    value = models.FloatField("value")
    ts = models.DateTimeField("time", default=timezone.now)

    class Meta:
        ordering = ["-ts"]  # terbaru lebih dulu
        verbose_name = "telemetry"
        verbose_name_plural = "telemetry"
        indexes = [
            # Index mempercepat pencarian "data device X, urut waktu".
            models.Index(fields=["device", "ts"], name="telemetry_dev_ts"),
        ]

    def __str__(self):
        return f"{self.device}: {self.value}"
