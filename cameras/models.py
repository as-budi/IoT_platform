"""
Kamera dan snapshot (tahap 27a, docs/tahap-27a-snapshot-esp32cam.md).

Camera    kamera milik sebuah device. ESP32-CAM punya satu kamera ("camera"), dibuat otomatis
          saat snapshot pertama tiba. Model terpisah dari Device supaya kelak device berkamera
          lebih dari satu bisa ditambahkan tanpa mengubah data.
Snapshot  satu gambar JPEG dari kamera, dikirim device lewat POST /api/v1/<token>/snapshot.

File gambar TIDAK disimpan di folder media publik, tetapi di CAMERA_STORAGE_DIR (sama seperti file
firmware OTA). Gambar hanya bisa dibuka lewat view yang memeriksa hak akses (cameras/views.py).
"""
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.signals import setting_changed
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import LazyObject, empty

from devices.models import Command, Device


class CameraStorage(LazyObject):
    """Tempat file gambar: CAMERA_STORAGE_DIR. Lazy supaya tes bisa menggantinya."""

    def _setup(self):
        self._wrapped = FileSystemStorage(location=settings.CAMERA_STORAGE_DIR, base_url=None)


camera_storage = CameraStorage()


@receiver(setting_changed)
def reset_camera_storage(setting, **kwargs):
    if setting == "CAMERA_STORAGE_DIR":
        camera_storage._wrapped = empty


def camera_storage_callable():
    return camera_storage


def snapshot_path(snapshot, filename):
    """<device>/<yyyy>/<mm>/<dd>/<HHMMSS>-<acak>.jpg (nama asli dari device diabaikan)."""
    ts = timezone.localtime(snapshot.ts)
    return f"{snapshot.camera.device_id}/{ts:%Y/%m/%d}/{ts:%H%M%S}.jpg"


def thumb_path(snapshot, filename):
    """Thumbnail di sebelah gambarnya: .../<HHMMSS>-t.jpg"""
    ts = timezone.localtime(snapshot.ts)
    return f"{snapshot.camera.device_id}/{ts:%Y/%m/%d}/{ts:%H%M%S}-t.jpg"


class Camera(models.Model):
    DEFAULT_NAME = "camera"

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="cameras")
    name = models.CharField("name", max_length=40, default=DEFAULT_NAME)
    # Kosong = CAMERA_RETENTION_DAYS (.env). 0 = simpan selamanya.
    retention_days = models.PositiveIntegerField(
        "keep snapshots (days)", null=True, blank=True,
        help_text="Empty: the platform default. 0: keep forever.",
    )
    created_at = models.DateTimeField("created", auto_now_add=True)

    class Meta:
        ordering = ["device", "name"]
        constraints = [models.UniqueConstraint(fields=["device", "name"], name="camera_unique_name_per_device")]

    def __str__(self):
        return f"{self.device} · {self.name}"

    @property
    def keep_days(self):
        return settings.CAMERA_RETENTION_DAYS if self.retention_days is None else self.retention_days


class Snapshot(models.Model):
    class Source(models.TextChoices):
        PERIODIC = "PERIODIC", "Periodic"
        COMMAND = "COMMAND", "Capture command"
        DEVICE = "DEVICE", "Device"               # mis. tombol di device, gerak, dsb.
        RULE = "RULE", "Rule"                     # aksi aturan "Capture snapshot"

    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="snapshots")
    ts = models.DateTimeField("time", default=timezone.now)
    file = models.FileField("image", upload_to=snapshot_path, storage=camera_storage_callable)
    # Gambar kecil (lebar THUMB_WIDTH) untuk daftar dan dashboard; kosong bila Pillow tidak ada
    # atau gambar aslinya sudah kecil. Tampilan memakai gambar asli sebagai gantinya.
    thumb = models.FileField("thumbnail", upload_to=thumb_path, storage=camera_storage_callable, blank=True)
    size = models.PositiveIntegerField("size (bytes)")
    width = models.PositiveSmallIntegerField("width", null=True, blank=True)
    height = models.PositiveSmallIntegerField("height", null=True, blank=True)
    source = models.CharField("source", max_length=10, choices=Source.choices, default=Source.PERIODIC)
    # Command "capture" yang memintanya (header X-Command-Id), bila ada.
    command = models.ForeignKey(Command, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # Aksi aturan "Capture snapshot": aturannya, dan alarm aktif aturan itu saat gambar tiba.
    rule = models.ForeignKey("rules.Rule", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    alarm = models.ForeignKey("rules.Alarm", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="snapshots")

    class Meta:
        ordering = ["-ts", "-pk"]
        indexes = [models.Index(fields=["camera", "ts"], name="snapshot_camera_ts")]

    def __str__(self):
        return f"{self.camera} @ {self.ts:%Y-%m-%d %H:%M:%S}"

    @property
    def resolution(self):
        return f"{self.width}×{self.height}" if self.width and self.height else ""


class CaptureRequest(models.Model):
    """Apa yang harus dilakukan saat gambar jawaban sebuah command "capture" tiba.

    Dibuat oleh aksi aturan "Capture snapshot" (rules/actions.py). Command-nya dikirim ke
    device seperti command biasa; device mengunggah gambar dengan X-Command-Id, lalu
    services.save_snapshot() menautkan gambar ke aturan/alarm dan, bila diminta,
    mengirimnya ke Telegram.
    """

    command = models.OneToOneField(Command, on_delete=models.CASCADE, related_name="capture_request")
    rule = models.ForeignKey("rules.Rule", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    # Id chat Telegram tujuan (dipisah koma), sudah ditentukan saat aksi berjalan. Kosong = tidak dikirim.
    telegram_chats = models.CharField("Telegram chats", max_length=255, blank=True)
    caption = models.CharField("caption", max_length=1024, blank=True)
    created_at = models.DateTimeField("created", auto_now_add=True)
    done_at = models.DateTimeField("photo received", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"capture #{self.command_id}"
