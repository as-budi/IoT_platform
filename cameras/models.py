"""
Kamera dan snapshot (tahap 27a, docs/tahap-27a-snapshot-esp32cam.md).

Camera    kamera milik sebuah device. ESP32-CAM punya satu kamera ("camera"), dibuat otomatis
          saat snapshot pertama tiba. Model terpisah dari Device supaya kelak IP camera dan
          device berkamera lebih dari satu bisa ditambahkan tanpa mengubah data.
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

    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="snapshots")
    ts = models.DateTimeField("time", default=timezone.now)
    file = models.FileField("image", upload_to=snapshot_path, storage=camera_storage_callable)
    size = models.PositiveIntegerField("size (bytes)")
    width = models.PositiveSmallIntegerField("width", null=True, blank=True)
    height = models.PositiveSmallIntegerField("height", null=True, blank=True)
    source = models.CharField("source", max_length=10, choices=Source.choices, default=Source.PERIODIC)
    # Command "capture" yang memintanya (header X-Command-Id), bila ada.
    command = models.ForeignKey(Command, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-ts", "-pk"]
        indexes = [models.Index(fields=["camera", "ts"], name="snapshot_camera_ts")]

    def __str__(self):
        return f"{self.camera} @ {self.ts:%Y-%m-%d %H:%M:%S}"

    @property
    def resolution(self):
        return f"{self.width}×{self.height}" if self.width and self.height else ""
