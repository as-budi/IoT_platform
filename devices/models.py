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
        verbose_name="pemilik",
        on_delete=models.CASCADE,  # pengguna dihapus -> device-nya ikut terhapus
        related_name="devices",    # memungkinkan: user.devices.all()
    )
    name = models.CharField("nama", max_length=100)
    device_type = models.CharField(
        "tipe",
        max_length=50,
        blank=True,
        help_text="Contoh: sensor suhu, power meter.",
    )
    # Variabel tunggal yang diukur device ini, beserta satuannya.
    variable = models.CharField(
        "variabel",
        max_length=50,
        help_text="Besaran yang diukur. Contoh: temperature.",
    )
    unit = models.CharField(
        "satuan",
        max_length=20,
        blank=True,
        help_text="Contoh: °C, %, V. Boleh dikosongkan.",
    )
    description = models.TextField("deskripsi", blank=True)
    is_active = models.BooleanField("aktif", default=True)
    created_at = models.DateTimeField("dibuat", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "device"
        verbose_name_plural = "device"

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
    value = models.FloatField("nilai")
    ts = models.DateTimeField("waktu", default=timezone.now)

    class Meta:
        ordering = ["-ts"]  # terbaru lebih dulu
        verbose_name = "telemetri"
        verbose_name_plural = "telemetri"
        indexes = [
            # Index mempercepat pencarian "data device X, urut waktu".
            models.Index(fields=["device", "ts"], name="telemetry_dev_ts"),
        ]

    def __str__(self):
        return f"{self.device}: {self.value}"
