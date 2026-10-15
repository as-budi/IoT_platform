"""
Model data pembaruan firmware over-the-air (OTA), tahap 9.

    FirmwarePackage   satu file firmware (.bin) beserta versi, board, dan checksum-nya
    OtaUpdate         SATU percobaan memperbarui SATU device ke SATU paket
      OtaEvent        riwayat langkah percobaan itu (QUEUED → DOWNLOADING → ... → UPDATED)

Status sebuah OtaUpdate mengikuti pola ThingsBoard (fw_state):

    QUEUED ─► DOWNLOADING ─► DOWNLOADED ─► VERIFIED ─► UPDATING ─► UPDATED
       │            │             │            │           │
       └────────────┴─────────────┴────────────┴───────────┴──► FAILED  (beserta alasannya)
                                                                 CANCELED (dibatalkan pengguna)

Status hanya boleh MAJU (lihat STEP). UPDATED, FAILED, dan CANCELED adalah
status akhir: percobaan itu selesai, dan device bisa diperbarui lagi dengan
percobaan baru.

File firmware TIDAK disimpan di folder media publik. Ia disimpan di
OTA_STORAGE_DIR, dan hanya bisa diunduh lewat URL bertoken sekali pakai
(ota/views.py: download). Alasannya ada di docs/tahap-09-ota.md bagian 6.
"""
import secrets

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.signals import setting_changed
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import LazyObject, empty

from devices.models import Board, Command, Device


class FirmwareStorage(LazyObject):
    """Tempat file firmware: OTA_STORAGE_DIR, BUKAN MEDIA_ROOT.

    LazyObject: lokasinya baru dibaca saat dipakai, sehingga tes bisa
    mengganti OTA_STORAGE_DIR dengan folder sementara.
    """

    def _setup(self):
        self._wrapped = FileSystemStorage(location=settings.OTA_STORAGE_DIR, base_url=None)


firmware_storage = FirmwareStorage()


@receiver(setting_changed)
def reset_firmware_storage(setting, **kwargs):
    # Tes yang memakai override_settings(OTA_STORAGE_DIR=...) mendapat folder baru.
    if setting == "OTA_STORAGE_DIR":
        firmware_storage._wrapped = empty


def firmware_storage_callable():
    return firmware_storage


class FirmwarePackage(models.Model):
    title = models.CharField(
        "title", max_length=80, help_text="What the firmware is, e.g. Greenhouse node."
    )
    version = models.CharField(
        "version", max_length=40,
        help_text="Must match the version the firmware reports after it starts, e.g. 1.2.0.",
    )
    board = models.CharField("board", max_length=10, choices=Board.choices)
    file = models.FileField("file", upload_to="%Y/%m/", storage=firmware_storage_callable)
    size = models.PositiveIntegerField("size (bytes)", editable=False)
    # Dihitung server saat upload. MD5 dikirim ke device (HTTPUpdate memeriksanya
    # lewat header x-MD5); SHA-256 untuk dicocokkan manusia dengan hasil build.
    md5 = models.CharField("MD5", max_length=32, editable=False)
    sha256 = models.CharField("SHA-256", max_length=64, editable=False)
    notes = models.TextField("release notes", blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="uploaded by",
        on_delete=models.CASCADE, related_name="firmware_packages",
    )
    created_at = models.DateTimeField("uploaded", auto_now_add=True)

    class Meta:
        ordering = ["board", "-created_at"]
        verbose_name = "firmware package"
        verbose_name_plural = "firmware packages"
        constraints = [
            models.UniqueConstraint(
                fields=["uploaded_by", "board", "version"], name="firmware_unique_version_per_board"
            ),
        ]

    def __str__(self):
        return f"{self.title} {self.version} ({self.get_board_display()})"


class OtaStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"                # command "ota" dibuat, belum diambil device
    DOWNLOADING = "DOWNLOADING", "Downloading" # device sedang mengunduh
    DOWNLOADED = "DOWNLOADED", "Downloaded"    # file lengkap diterima
    VERIFIED = "VERIFIED", "Verified"          # checksum cocok, sudah ditulis ke partisi cadangan
    UPDATING = "UPDATING", "Updating"          # device restart ke firmware baru
    UPDATED = "UPDATED", "Updated"             # device menyala dan melaporkan versi baru
    FAILED = "FAILED", "Failed"
    CANCELED = "CANCELED", "Canceled"


# Urutan langkah: laporan device hanya boleh menaikkan angka ini.
STEP = {
    OtaStatus.QUEUED: 0,
    OtaStatus.DOWNLOADING: 1,
    OtaStatus.DOWNLOADED: 2,
    OtaStatus.VERIFIED: 3,
    OtaStatus.UPDATING: 4,
    OtaStatus.UPDATED: 5,
}
FINAL = {OtaStatus.UPDATED, OtaStatus.FAILED, OtaStatus.CANCELED}
ACTIVE = [status for status in OtaStatus.values if status not in FINAL]


def generate_download_token():
    """32 karakter acak, TERPISAH dari access token device.

    URL unduhan ikut terkirim di command lewat broker MQTT publik. Bila yang
    terkirim access token, siapa pun yang membaca broker bisa menyamar sebagai
    device. Token unduhan hanya membuka SATU file, SEKALI, dalam waktu terbatas.
    """
    return secrets.token_urlsafe(24)


class OtaUpdateQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=ACTIVE)


class OtaUpdate(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="ota_updates")
    # PROTECT: paket yang pernah dipakai tidak bisa dihapus, supaya riwayat tetap utuh.
    package = models.ForeignKey(FirmwarePackage, on_delete=models.PROTECT, related_name="updates")
    status = models.CharField("status", max_length=12, choices=OtaStatus.choices, default=OtaStatus.QUEUED)
    progress = models.PositiveSmallIntegerField("download progress (%)", default=0)
    error = models.CharField("error", max_length=255, blank=True)
    previous_version = models.CharField("version before", max_length=40, blank=True)
    # Command "ota" yang membawa perintah ini ke device (lihat ota/services.py).
    command = models.OneToOneField(
        Command, null=True, blank=True, on_delete=models.SET_NULL, related_name="ota_update"
    )
    download_token = models.CharField(
        "download token", max_length=40, unique=True, default=generate_download_token, editable=False
    )
    token_expires_at = models.DateTimeField("download link expires")
    token_used_at = models.DateTimeField("downloaded at", null=True, blank=True)
    downloaded_by = models.GenericIPAddressField("downloaded from", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField("started", default=timezone.now)
    updated_at = models.DateTimeField("last change", default=timezone.now)
    finished_at = models.DateTimeField("finished", null=True, blank=True)

    objects = OtaUpdateQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "firmware update"
        verbose_name_plural = "firmware updates"
        indexes = [models.Index(fields=["device", "status"], name="ota_device_status")]

    def __str__(self):
        return f"{self.device} → {self.package.version} ({self.status})"

    @property
    def is_final(self):
        return self.status in FINAL

    @property
    def step(self):
        return STEP.get(self.status)

    def steps(self):
        """Langkah untuk tampilan: [(label, "done"/"current"/"todo"), ...]."""
        current = self.step
        if current is None:  # FAILED/CANCELED: tandai langkah terakhir yang tercapai
            current = max((STEP[e.status] for e in self.events.all() if e.status in STEP), default=0)
        result = []
        for status, number in STEP.items():
            if number < current or (number == current and self.status == OtaStatus.UPDATED):
                state = "done"
            elif number == current:
                state = "failed" if self.status in (OtaStatus.FAILED, OtaStatus.CANCELED) else "current"
            else:
                state = "todo"
            result.append((OtaStatus(status).label, state))
        return result


class OtaEvent(models.Model):
    """Satu baris riwayat: status berganti, atau catatan (mis. "download started")."""

    update = models.ForeignKey(OtaUpdate, on_delete=models.CASCADE, related_name="events")
    status = models.CharField("status", max_length=12, choices=OtaStatus.choices)
    message = models.CharField("message", max_length=255, blank=True)
    source = models.CharField("source", max_length=20, blank=True)  # "web", "MQTT", "HTTP", "server", ...
    created_at = models.DateTimeField("time", default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "firmware update event"
        verbose_name_plural = "firmware update events"

    def __str__(self):
        return f"{self.update_id} {self.status} {self.message}"
