"""
Model data device, variabel, dan telemetri.

Struktur (mengikuti pola platform IoT pada umumnya, mis. ThingsBoard):

    Device      = alat fisik yang terhubung ke jaringan (mis. ESP32 + DHT22).
                  Satu token, satu koneksi, satu status online.
      Variable  = besaran yang diukur alat itu (temperature, humidity, ...),
                  lengkap dengan satuan dan label tampilannya.
        Telemetry = deret waktu (waktu, nilai) untuk satu variabel.

    Location    = tempat pemasangan (nama + koordinat). Sejak tahap 12 setiap
                  lokasi punya PEMILIK: pengguna mengelola lokasinya sendiri
                  dan saat menambah device hanya MEMILIH dari lokasi miliknya.

Jadi satu device bisa punya banyak variabel, dan setiap variabel punya
riwayat datanya sendiri.

Variabel ada dua jenis (Variable.kind):
- SENSOR   : hanya dibaca. Nilainya datang dari device.
- ACTUATOR : bisa dikontrol, memakai pola DESIRED / REPORTED STATE:
             * desired  = keadaan yang DIMINTA pengguna (disimpan di Variable),
             * reported = keadaan yang DILAPORKAN device (= telemetri terbaru).
             Keduanya sengaja dipisah: mengirim perintah belum tentu terlaksana.

Untuk aksi sekali jalan yang tidak meninggalkan keadaan (restart, buka katup
5 detik, ...) dipakai model Command (setara "RPC" di ThingsBoard).
"""
from django.conf import settings
import secrets
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.utils import timezone

from realtime import events


def generate_access_token():
    """Token acak untuk autentikasi device, mis. "q3Zr8-kP2mXv0aLt9yBc".

    secrets (bukan random) dipakai karena menghasilkan angka acak yang aman
    untuk keperluan keamanan. 15 byte acak -> 20 karakter URL-safe.
    """
    return secrets.token_urlsafe(15)


class Location(models.Model):
    """Tempat pemasangan device, lengkap dengan titik koordinatnya.

    Lokasi milik satu pengguna (owner), sama seperti device. Nama lokasi unik
    PER PEMILIK: budi dan siti boleh sama-sama punya "Server room", tetapi
    budi tidak boleh punya dua "Server room".
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="owner",
        on_delete=models.CASCADE,   # pengguna dihapus -> lokasinya ikut terhapus
        related_name="locations",   # memungkinkan: user.locations.all()
    )
    name = models.CharField("name", max_length=100)
    description = models.TextField("description", blank=True)
    # DecimalField (bukan FloatField) supaya koordinat tersimpan persis seperti
    # yang diketik. 6 angka di belakang koma ≈ ketelitian 10 cm.
    latitude = models.DecimalField(
        "latitude",
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("-90")), MaxValueValidator(Decimal("90"))],
        help_text="Between -90 and 90. Example: -7.952600",
    )
    longitude = models.DecimalField(
        "longitude",
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("-180")), MaxValueValidator(Decimal("180"))],
        help_text="Between -180 and 180. Example: 112.614500",
    )
    created_at = models.DateTimeField("created", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "location"
        verbose_name_plural = "locations"
        constraints = [
            # Dijaga database: kombinasi (pemilik, nama) tidak boleh kembar.
            models.UniqueConstraint(fields=["owner", "name"], name="location_unique_name_per_owner"),
        ]

    def __str__(self):
        return self.name


class Board(models.TextChoices):
    """Jenis board/mikrokontroler. Dipakai OTA untuk menolak firmware yang salah board
    (firmware ESP32-S3 tidak boleh ditulis ke ESP32 biasa)."""

    ESP32 = "esp32", "ESP32"
    ESP32S2 = "esp32s2", "ESP32-S2"
    ESP32S3 = "esp32s3", "ESP32-S3"
    ESP32C3 = "esp32c3", "ESP32-C3"
    ESP8266 = "esp8266", "ESP8266"
    SIMULATED = "sim", "Simulated device"


class Device(models.Model):
    class Protocol(models.TextChoices):
        """Jalur yang dipakai device saat terakhir mengirim telemetri."""

        HTTP = "HTTP", "HTTP"
        MQTT = "MQTT", "MQTT"
        COAP = "COAP", "CoAP"

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
    # Lokasi dipilih dari tabel Location (bukan diketik bebas), sehingga
    # "Server room", "server room", dan "Server Room " tidak menjadi tiga lokasi.
    # RESTRICT: lokasi yang masih dipakai device tidak bisa dihapus. Bedanya
    # dengan PROTECT (dipakai sampai tahap 11): bila PEMILIK dihapus, lokasi
    # dan device-nya ikut terhapus bersama (CASCADE dari owner), dan RESTRICT
    # mengizinkannya karena device itu juga sedang dihapus. PROTECT akan
    # menolak penghapusan pengguna tersebut.
    location = models.ForeignKey(
        Location,
        verbose_name="location",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="devices",   # memungkinkan: location.devices.all()
        help_text="Choose one of your locations. New locations are added on the Locations page.",
    )
    description = models.TextField("description", blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("created", auto_now_add=True)
    # Kredensial device untuk API (seperti "access token" di ThingsBoard).
    # Device mengirim data ke /api/v1/<access_token>/telemetry; siapa pun yang
    # tahu token ini bisa mengirim data atas nama device, jadi perlakukan
    # seperti password. editable=False: tidak muncul di form biasa.
    access_token = models.CharField(
        "access token",
        max_length=40,
        unique=True,
        default=generate_access_token,
        editable=False,
    )
    # Diisi otomatis oleh api.services.save_telemetry() setiap kali telemetri
    # masuk, sehingga halaman bisa menampilkan "via CoAP · 3 s ago" tanpa
    # menghitung ulang dari tabel telemetri.
    last_seen_at = models.DateTimeField("last data", null=True, blank=True, editable=False)
    last_protocol = models.CharField(
        "last protocol", max_length=4, choices=Protocol.choices, blank=True, editable=False
    )
    # --- Firmware (tahap 9, OTA) ---
    # Board diisi pengguna di form device, atau dilaporkan device sendiri saat menyala.
    board = models.CharField(
        "board", max_length=10, choices=Board.choices, blank=True,
        help_text="Used by firmware updates to refuse firmware built for another board.",
    )
    # Versi firmware yang SEDANG berjalan, menurut laporan device (bukan menurut
    # server): hanya device yang tahu pasti firmware mana yang menyala.
    firmware_version = models.CharField("firmware version", max_length=40, blank=True, editable=False)
    firmware_reported_at = models.DateTimeField("firmware reported", null=True, blank=True, editable=False)
    # --- Status koneksi (tahap 16, devices/presence.py) ---
    # Online = mengirim data dalam DEVICE_OFFLINE_SECONDS terakhir, ATAU punya sesi
    # MQTT yang diumumkan lewat topik <prefix>/<token>/status (Last Will).
    # Disimpan (bukan dihitung saat menampilkan), supaya PERUBAHANNYA bisa dicatat
    # dan dikabarkan ke browser saat itu juga.
    is_online = models.BooleanField("online", default=False, editable=False)
    status_changed_at = models.DateTimeField("status changed", null=True, blank=True, editable=False)
    # True selama device mengumumkan sesi MQTT "online" dan Last Will-nya belum
    # dikirim broker. Selama itu batas waktu tanpa data tidak dipakai: broker
    # sendiri yang mendeteksi koneksi putus (keepalive).
    mqtt_connected = models.BooleanField("MQTT session", default=False, editable=False)

    class Meta:
        ordering = ["name"]
        verbose_name = "device"
        verbose_name_plural = "devices"

    def __str__(self):
        return self.name

    def regenerate_token(self):
        """Ganti token (mis. bila bocor). Token lama langsung tidak berlaku."""
        self.access_token = generate_access_token()
        self.save(update_fields=["access_token"])


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
    class Kind(models.TextChoices):
        SENSOR = "SENSOR", "Sensor (read only)"
        ACTUATOR = "ACTUATOR", "Actuator (controllable)"

    class ValueType(models.TextChoices):
        NUMBER = "NUMBER", "Number"
        BOOLEAN = "BOOLEAN", "On / off"

    kind = models.CharField("kind", max_length=10, choices=Kind.choices, default=Kind.SENSOR)
    # Nilai boolean tetap disimpan sebagai angka di telemetri: 1 = ON, 0 = OFF.
    value_type = models.CharField(
        "value type", max_length=10, choices=ValueType.choices, default=ValueType.NUMBER
    )
    # Batas nilai yang boleh diminta untuk aktuator angka (mis. servo 0–180).
    # null=True: kolom boleh kosong di database (artinya "tanpa batas").
    min_value = models.FloatField("minimum", null=True, blank=True)
    max_value = models.FloatField("maximum", null=True, blank=True)

    # --- Desired state (hanya untuk aktuator) ---
    desired_value = models.FloatField("desired value", null=True, blank=True)
    desired_at = models.DateTimeField("desired at", null=True, blank=True)
    desired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="desired by",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,  # pengguna dihapus -> kolom ini jadi kosong
        related_name="+",           # "+" = tidak perlu relasi balik dari User
    )
    # Naik 1 setiap kali desired diubah. Nanti device memakainya untuk tahu
    # apakah ada permintaan baru sejak terakhir kali ia mengecek.
    desired_version = models.PositiveIntegerField("desired version", default=0)
    created_at = models.DateTimeField("created", auto_now_add=True)

    class SyncStatus(models.TextChoices):
        PENDING = "PENDING", "Waiting for device"
        SYNCED = "SYNCED", "In sync"
        MISMATCH = "MISMATCH", "Mismatch"

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

    @property
    def is_actuator(self):
        return self.kind == self.Kind.ACTUATOR

    @property
    def is_boolean(self):
        return self.value_type == self.ValueType.BOOLEAN

    def _reported(self):
        """(nilai, waktu) laporan terakhir device.

        Bila view sudah menambahkan last_value/last_seen lewat annotate(),
        pakai itu supaya tidak ada query tambahan; kalau belum, ambil sendiri.
        """
        if hasattr(self, "last_seen"):
            return self.last_value, self.last_seen
        latest = self.latest
        return (latest.value, latest.ts) if latest else (None, None)

    @property
    def sync_status(self):
        """Bandingkan desired dengan reported.

        - None     : bukan aktuator, atau belum pernah ada permintaan.
        - PENDING  : device belum melapor apa pun SETELAH permintaan terakhir.
        - SYNCED   : laporan setelah permintaan nilainya sama dengan desired.
        - MISMATCH : device sudah melapor, tetapi nilainya berbeda
                     (mis. relay macet, atau perintah ditolak device).
        """
        if not self.is_actuator or self.desired_value is None:
            return None
        value, reported_at = self._reported()
        if reported_at is None or reported_at < self.desired_at:
            return self.SyncStatus.PENDING
        if abs(value - self.desired_value) < 1e-9:
            return self.SyncStatus.SYNCED
        return self.SyncStatus.MISMATCH

    def set_desired(self, value, user):
        """Simpan permintaan baru dan catat di log. Mengembalikan variabel terbaru.

        transaction.atomic(): semua langkah di dalamnya berhasil bersama atau
        batal bersama. select_for_update() mengunci baris variabel selama
        transaksi, sehingga dua klik yang hampir bersamaan tidak menghasilkan
        nomor versi yang sama.
        """
        with transaction.atomic():
            variable = Variable.objects.select_for_update().get(pk=self.pk)
            variable.desired_value = value
            variable.desired_at = timezone.now()
            variable.desired_by = user
            variable.desired_version += 1
            variable.save(
                update_fields=["desired_value", "desired_at", "desired_by", "desired_version"]
            )
            DesiredStateLog.objects.create(
                variable=variable,
                value=value,
                version=variable.desired_version,
                changed_by=user,
            )
            # Kabari dashboard lain yang sedang terbuka (WebSocket).
            events.desired_changed(variable)
        return variable


class DesiredStateLog(models.Model):
    """Jejak audit: siapa meminta nilai berapa, kapan."""

    variable = models.ForeignKey(
        Variable, on_delete=models.CASCADE, related_name="desired_log", verbose_name="variable"
    )
    value = models.FloatField("value")
    version = models.PositiveIntegerField("version")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="changed by",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField("time", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-version"]
        verbose_name = "desired state change"
        verbose_name_plural = "desired state log"

    def __str__(self):
        return f"{self.variable} v{self.version} = {self.value}"


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
            # Tahap 16: tabel ringkasan membaca "semua data antara pukul A dan B"
            # tanpa menyebut variabel; tanpa index ini MySQL membaca seluruh tabel.
            models.Index(fields=["ts"], name="telemetry_ts"),
        ]

    def __str__(self):
        return f"{self.variable}: {self.value}"


# --- Tabel ringkasan telemetri (tahap 16, devices/rollups.py) -------------------

class TelemetryRollup(models.Model):
    """Ringkasan telemetri satu variabel dalam satu "ember" waktu.

        resolution = 60    -> satu baris per variabel per MENIT
        resolution = 3600  -> satu baris per variabel per JAM (dihitung dari baris menit)

    Disimpan jumlah (total) dan banyaknya data (count), bukan rata-rata: rata-rata
    beberapa ember bisa digabung dengan benar (sum total / sum count), sedangkan
    rata-rata dari rata-rata salah bila jumlah data tiap ember berbeda.
    Data asli di tabel Telemetry tidak diubah atau dihapus.
    """

    class Resolution(models.IntegerChoices):
        MINUTE = 60, "1 minute"
        HOUR = 3600, "1 hour"

    variable = models.ForeignKey(Variable, on_delete=models.CASCADE, related_name="rollups")
    resolution = models.PositiveIntegerField("resolution (s)", choices=Resolution.choices)
    bucket = models.DateTimeField("bucket start")
    count = models.PositiveIntegerField("readings")
    total = models.FloatField("sum")
    min_value = models.FloatField("min")
    max_value = models.FloatField("max")

    class Meta:
        ordering = ["bucket"]
        verbose_name = "telemetry rollup"
        verbose_name_plural = "telemetry rollups"
        constraints = [
            # Juga berfungsi sebagai index untuk chart: (variabel, resolusi, waktu).
            models.UniqueConstraint(fields=["variable", "resolution", "bucket"], name="rollup_unique_bucket"),
        ]
        indexes = [
            # Untuk menghitung ulang satu rentang waktu untuk SEMUA variabel.
            models.Index(fields=["resolution", "bucket"], name="rollup_res_bucket"),
        ]

    def __str__(self):
        return f"{self.variable} {self.bucket:%Y-%m-%d %H:%M} ({self.get_resolution_display()})"

    @property
    def average(self):
        return self.total / self.count if self.count else None


class RollupProgress(models.Model):
    """Sampai kapan ringkasan suatu resolusi sudah lengkap (eksklusif).

    Chart memakai ringkasan untuk waktu < done_until, dan data asli setelahnya.
    """

    resolution = models.PositiveIntegerField(primary_key=True, choices=TelemetryRollup.Resolution.choices)
    done_until = models.DateTimeField()

    class Meta:
        verbose_name = "rollup progress"
        verbose_name_plural = "rollup progress"

    def __str__(self):
        return f"{self.get_resolution_display()} until {self.done_until:%Y-%m-%d %H:%M}"


class DeviceStatusEvent(models.Model):
    """Riwayat perubahan online/offline sebuah device (tahap 16)."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="status_events")
    online = models.BooleanField("online")
    reason = models.CharField("reason", max_length=200)
    at = models.DateTimeField("time", default=timezone.now)

    class Meta:
        ordering = ["-at", "-pk"]
        verbose_name = "device status event"
        verbose_name_plural = "device status events"
        indexes = [models.Index(fields=["device", "at"], name="status_event_device_at")]

    def __str__(self):
        return f"{self.device} {'online' if self.online else 'offline'} at {self.at:%Y-%m-%d %H:%M:%S}"


# --- Command (setara "RPC" di ThingsBoard) ------------------------------------

class CommandStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"          # dibuat, belum diambil device
    SENT = "SENT", "Sent"                # sudah diambil device
    SUCCEEDED = "SUCCEEDED", "Succeeded" # device melapor berhasil
    FAILED = "FAILED", "Failed"          # device melapor gagal
    EXPIRED = "EXPIRED", "Expired"       # tidak selesai sebelum batas waktu


class CommandQuerySet(models.QuerySet):
    """Method tambahan untuk Command.objects.* (custom QuerySet)."""

    def pending(self):
        """Command yang masih berjalan (belum punya hasil akhir)."""
        return self.filter(status__in=[CommandStatus.QUEUED, CommandStatus.SENT])

    def expire_due(self):
        """Tandai EXPIRED semua command berjalan yang sudah lewat batas waktu.

        Command yang kedaluwarsa TIDAK boleh dijalankan belakangan: katup yang
        tiba-tiba terbuka tiga jam kemudian justru berbahaya.
        Mengembalikan jumlah baris yang diubah.
        """
        now = timezone.now()
        due = list(self.pending().filter(expires_at__lte=now).select_related("device__owner"))
        if not due:
            return 0
        # UPDATE bersyarat (masih pending): command yang baru saja selesai tidak ikut ditimpa.
        changed = self.pending().filter(pk__in=[c.pk for c in due]).update(
            status=CommandStatus.EXPIRED, completed_at=now
        )
        # update() tidak memicu sinyal post_save; kirim sendiri supaya log aktivitas
        # (tahap 18, activity/signals.py) mencatat command yang kedaluwarsa.
        expired = set(self.filter(pk__in=[c.pk for c in due], status=CommandStatus.EXPIRED, completed_at=now)
                      .values_list("pk", flat=True))
        for command in (c for c in due if c.pk in expired):
            command.status, command.completed_at = CommandStatus.EXPIRED, now
            models.signals.post_save.send(
                sender=Command, instance=command, created=False, raw=False,
                using=self.db, update_fields={"status", "completed_at"},
            )
        return changed


class Command(models.Model):
    """Aksi sekali jalan untuk sebuah device, mis. restart atau buka katup 5 detik."""

    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="commands", verbose_name="device"
    )
    method = models.CharField(
        "method",
        max_length=50,
        validators=[key_validator],
        help_text="Name of the action. Example: reboot, open_valve.",
    )
    # JSONField menyimpan dict/list Python sebagai JSON di database.
    params = models.JSONField("parameters", default=dict, blank=True)
    status = models.CharField(
        "status", max_length=10, choices=CommandStatus.choices, default=CommandStatus.QUEUED
    )
    response = models.JSONField("response", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="created by",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField("created", auto_now_add=True)
    expires_at = models.DateTimeField("expires")
    sent_at = models.DateTimeField("sent", null=True, blank=True)
    completed_at = models.DateTimeField("completed", null=True, blank=True)

    objects = CommandQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "command"
        verbose_name_plural = "commands"
        indexes = [models.Index(fields=["device", "status"], name="command_device_status")]

    def __str__(self):
        return f"{self.device} · {self.method} ({self.status})"

    @property
    def is_final(self):
        return self.status in (CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.EXPIRED)

    def mark_sent(self):
        self.status = CommandStatus.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "sent_at"])
        events.command_changed(self)

    def complete(self, succeeded, response=None):
        """Catat hasil dari device. Command yang sudah final tidak diubah lagi."""
        if self.is_final:
            return False
        self.status = CommandStatus.SUCCEEDED if succeeded else CommandStatus.FAILED
        self.response = response
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "response", "completed_at"])
        events.command_changed(self)
        return True
