"""Logika snapshot yang dipakai endpoint device, halaman web, dan housekeeping."""
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from devices import presence
from devices.models import Command, Device
from realtime import events

from . import jpeg
from .models import Camera, Snapshot


class SnapshotError(Exception):
    """Unggahan ditolak. `status` = kode HTTP untuk device."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


SOURCES = {"periodic": Snapshot.Source.PERIODIC, "command": Snapshot.Source.COMMAND, "device": Snapshot.Source.DEVICE}


def max_bytes():
    return settings.CAMERA_MAX_SNAPSHOT_KB * 1024


def save_snapshot(device, data, source="", command_id="", camera_name="", now=None):
    """Simpan satu JPEG dari `device`. Melempar SnapshotError bila ditolak; mengembalikan Snapshot."""
    now = now or timezone.now()
    if not data:
        raise SnapshotError(400, "The request body is empty; send the JPEG image as the body.")
    if len(data) > max_bytes():
        raise SnapshotError(413, f"The image is larger than {settings.CAMERA_MAX_SNAPSHOT_KB} KB.")
    if not jpeg.is_jpeg(data):
        raise SnapshotError(415, "The body is not a JPEG image (it must start with FF D8 FF).")
    name = (camera_name or Camera.DEFAULT_NAME).strip()[:40] or Camera.DEFAULT_NAME

    command = None
    if command_id and str(command_id).isdigit():
        command = Command.objects.filter(pk=int(command_id), device=device).first()
    kind = Snapshot.Source.COMMAND if command else SOURCES.get(str(source).lower(), Snapshot.Source.PERIODIC)

    with transaction.atomic():
        camera, _ = Camera.objects.get_or_create(device=device, name=name)
        # Batas laju: device yang salah (atau token bocor) tidak boleh membanjiri disk.
        # Gambar atas permintaan (command) selalu diterima.
        gap = settings.CAMERA_MIN_INTERVAL_S
        if not command and gap and camera.snapshots.filter(ts__gt=now - timedelta(seconds=gap)).exists():
            raise SnapshotError(429, f"Too many images: at most one every {gap} s.")
        width, height = jpeg.dimensions(data)
        snapshot = Snapshot(camera=camera, ts=now, size=len(data), width=width, height=height,
                            source=kind, command=command)
        snapshot.file.save("snapshot.jpg", ContentFile(data), save=False)
        snapshot.save()
        # Gambar juga "data dari device": catat waktunya dan tandai online (seperti telemetri HTTP).
        Device.objects.filter(pk=device.pk).update(last_seen_at=now, last_protocol=Device.Protocol.HTTP)
        presence.seen(device, now, reason="Snapshot received")
        events.emit(device.pk, {
            "type": "snapshot",
            "id": snapshot.pk,
            "camera": camera.name,
            "url": reverse("cameras:image", args=[snapshot.pk]),
            "ts": events.to_ms(now),
            "size": snapshot.size,
            "resolution": snapshot.resolution,
            "source": snapshot.get_source_display(),
        })
    return snapshot


def delete_expired(now=None):
    """Hapus snapshot yang lebih tua dari masa simpan kameranya (housekeeping, tiap jam).
    Mengembalikan jumlah gambar yang dihapus. File ikut terhapus (cameras/signals.py)."""
    now = now or timezone.now()
    removed = 0
    for camera in Camera.objects.all():
        days = camera.keep_days
        if not days:
            continue
        old = camera.snapshots.filter(ts__lt=now - timedelta(days=days))
        for snapshot in old.iterator():     # satu per satu: sinyal post_delete menghapus file-nya
            snapshot.delete()
            removed += 1
    return removed


def request_capture(device, user, ttl=60):
    """Command "capture" (tombol Capture now). Device memotret lalu mengunggah dengan X-Command-Id."""
    now = timezone.now()
    command = Command.objects.create(
        device=device, method="capture", params={}, created_by=user,
        expires_at=now + timedelta(seconds=ttl),
    )
    events.command_changed(command)
    return command


def device_snapshots(device):
    return Snapshot.objects.filter(camera__device=device).select_related("camera")
