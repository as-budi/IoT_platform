"""
Model data device, variabel, dan telemetri.

Struktur (mengikuti pola platform IoT pada umumnya, mis. ThingsBoard):

    Device      = alat fisik yang terhubung ke jaringan (mis. ESP32 + DHT22).
                  Satu token, satu koneksi, satu status online.
      Variable  = besaran yang diukur alat itu (temperature, humidity, ...),
                  lengkap dengan satuan dan label tampilannya.
        Telemetry = deret waktu (waktu, nilai) untuk satu variabel.

Jadi satu device bisa punya banyak variabel, dan setiap variabel punya
riwayat datanya sendiri.
"""
from django.conf import settings
from django.core.validators import RegexValidator
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
    # Lokasi pemasangan, teks bebas. blank=True: boleh dikosongkan, sehingga
    # device lama otomatis berisi "" saat kolom ini ditambahkan lewat migrasi.
    location = models.CharField(
        "location",
        max_length=100,
        blank=True,
        help_text="Where the device is installed. Example: Server room, Building A.",
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



# Nama variabel dipakai di payload JSON dan nanti di topic MQTT, jadi dibatasi
# ke huruf, angka, dan garis bawah (tanpa spasi), mis. "soil_moisture".
key_validator = RegexValidator(
    r"^[A-Za-z][A-Za-z0-9_]*$",
    "Use letters, digits and underscores only, starting with a letter.",
)


class Variable(models.Model):
    device = models.ForeignKey(
        Device,
        verbose_name="device",
        on_delete=models.CASCADE,   # device dihapus -> variabelnya ikut terhapus
        related_name="variables",   # memungkinkan: device.variables.all()
    )
    key = models.CharField(
        "key",
        max_length=50,
        validators=[key_validator],
        help_text="Name used in the data the device sends. Example: temperature.",
    )
    label = models.CharField(
        "label",
        max_length=100,
        blank=True,
        help_text="Display name. Leave blank to show the key.",
    )
    unit = models.CharField(
        "unit",
        max_length=20,
        blank=True,
        help_text="Example: °C, %, V. May be left blank.",
    )
    created_at = models.DateTimeField("created", auto_now_add=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "variable"
        verbose_name_plural = "variables"
        constraints = [
            # Dalam satu device, nama variabel tidak boleh kembar.
            models.UniqueConstraint(fields=["device", "key"], name="unique_variable_key_per_device"),
        ]

    def __str__(self):
        return f"{self.device} · {self.key}"

    @property
    def display_name(self):
        """Label bila diisi, kalau tidak pakai key."""
        return self.label or self.key

    @property
    def latest(self):
        """Baris telemetri terbaru variabel ini (None bila belum ada data)."""
        return self.telemetry.order_by("-ts").first()


class Telemetry(models.Model):
    variable = models.ForeignKey(
        Variable,
        verbose_name="variable",
        on_delete=models.CASCADE,
        related_name="telemetry",  # memungkinkan: variable.telemetry.all()
    )
    # Tahap ini hanya mendukung nilai angka.
    value = models.FloatField("value")
    ts = models.DateTimeField("time", default=timezone.now)

    class Meta:
        ordering = ["-ts"]  # terbaru lebih dulu
        verbose_name = "telemetry"
        verbose_name_plural = "telemetry"
        indexes = [
            # Index mempercepat pencarian "data variabel X, urut waktu".
            models.Index(fields=["variable", "ts"], name="telemetry_var_ts"),
        ]

    def __str__(self):
        return f"{self.variable}: {self.value}"
