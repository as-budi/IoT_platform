"""Logika snapshot yang dipakai endpoint device, halaman web, aturan, dan housekeeping."""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from devices import presence
from devices.models import Command, Device
from realtime import events

from . import jpeg, thumbs
from .models import Camera, CaptureRequest, Snapshot

logger = logging.getLogger(__name__)


class SnapshotError(Exception):
    """Unggahan ditolak. `status` = kode HTTP untuk device."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


SOURCES = {"periodic": Snapshot.Source.PERIODIC, "command": Snapshot.Source.COMMAND, "device": Snapshot.Source.DEVICE}


def max_bytes():
    return settings.CAMERA_MAX_SNAPSHOT_KB * 1024


def quota_bytes():
    """Kuota penyimpanan gambar per pemilik device (0 = tanpa batas)."""
    return settings.CAMERA_STORAGE_QUOTA_MB * 1024 * 1024


def owner_snapshots(owner):
    return Snapshot.objects.filter(camera__device__owner=owner)


def storage_used(owner):
    """Byte yang dipakai gambar (asli) semua kamera milik `owner`."""
    return owner_snapshots(owner).aggregate(total=Sum("size"))["total"] or 0


def make_room(owner, incoming):
    """Kuota penuh: hapus gambar TERTUA milik `owner` sampai `incoming` byte muat.
    Seperti kamera CCTV yang menimpa rekaman lama. Mengembalikan jumlah gambar yang dihapus."""
    quota = quota_bytes()
    if not quota:
        return 0
    over = storage_used(owner) + incoming - quota
    removed = 0
    while over > 0:
        batch = list(owner_snapshots(owner).order_by("ts", "pk")[:50])
        if not batch:
            break
        for snapshot in batch:
            if over <= 0:
                break
            over -= snapshot.size
            snapshot.delete()          # sinyal post_delete menghapus file-nya
            removed += 1
    return removed


def start_thread(target):
    """Pekerjaan lambat (Telegram) setelah commit, di thread terpisah, supaya device yang
    mengunggah tidak ikut menunggu. Tes menggantinya dengan pemanggilan langsung."""
    threading.Thread(target=target, daemon=True).start()


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

    command = request = None
    if command_id and str(command_id).isdigit():
        command = Command.objects.filter(pk=int(command_id), device=device).first()
        request = CaptureRequest.objects.filter(command=command).select_related("rule").first() if command else None
    if request is not None:
        kind = Snapshot.Source.RULE
    elif command is not None:
        kind = Snapshot.Source.COMMAND
    else:
        kind = SOURCES.get(str(source).lower(), Snapshot.Source.PERIODIC)

    with transaction.atomic():
        camera, _ = Camera.objects.get_or_create(device=device, name=name)
        # Batas laju: device yang salah (atau token bocor) tidak boleh membanjiri disk.
        # Gambar atas permintaan (command) selalu diterima.
        gap = settings.CAMERA_MIN_INTERVAL_S
        if not command and gap and camera.snapshots.filter(ts__gt=now - timedelta(seconds=gap)).exists():
            raise SnapshotError(429, f"Too many images: at most one every {gap} s.")
        make_room(device.owner, len(data))
        width, height = jpeg.dimensions(data)
        snapshot = Snapshot(camera=camera, ts=now, size=len(data), width=width, height=height,
                            source=kind, command=command)
        if request is not None and request.rule_id:
            snapshot.rule = request.rule
            snapshot.alarm = rule_alarm(request.rule, now)
        snapshot.file.save("snapshot.jpg", ContentFile(data), save=False)
        small = thumbs.make_thumbnail(data)
        if small:
            snapshot.thumb.save("thumb.jpg", ContentFile(small), save=False)
        snapshot.save()
        if request is not None and request.done_at is None:
            CaptureRequest.objects.filter(pk=request.pk).update(done_at=now)
            if request.telegram_chats:
                transaction.on_commit(lambda: start_thread(lambda: send_to_telegram(request, data)))
        # Gambar juga "data dari device": catat waktunya dan tandai online (seperti telemetri HTTP).
        Device.objects.filter(pk=device.pk).update(last_seen_at=now, last_protocol=Device.Protocol.HTTP)
        presence.seen(device, now, reason="Snapshot received")
        events.emit(device.pk, {
            "type": "snapshot",
            "device": device.pk,
            "id": snapshot.pk,
            "camera": camera.name,
            "url": reverse("cameras:image", args=[snapshot.pk]),
            "thumb": thumb_url(snapshot),
            "ts": events.to_ms(now),
            "size": snapshot.size,
            "resolution": snapshot.resolution,
            "source": snapshot.get_source_display(),
        })
    return snapshot


def thumb_url(snapshot):
    """URL gambar kecil: thumbnail bila ada, selain itu gambar aslinya."""
    url = reverse("cameras:image", args=[snapshot.pk])
    return url + "?thumb=1" if snapshot.thumb else url


def rule_alarm(rule, now):
    """Alarm aktif aturan ini yang terbaru (dibuat sebelum gambar tiba), untuk ditautkan."""
    from rules.models import Alarm

    return Alarm.objects.active().filter(rule=rule, created_at__lte=now).order_by("-created_at").first()


def send_to_telegram(request, data):
    """Kirim gambar jawaban aksi aturan ke chat Telegram yang dicatat di CaptureRequest."""
    from notifications import telegram

    for chat in [c.strip() for c in request.telegram_chats.split(",") if c.strip()]:
        try:
            telegram.send_photo(chat, data, request.caption)
        except telegram.TelegramError as exc:
            logger.warning("Snapshot for capture #%s not sent to Telegram %s: %s", request.command_id, chat, exc)


def make_missing_thumbnails(limit=None):
    """Buat thumbnail untuk gambar lama yang belum punya (mis. setelah Pillow dipasang)."""
    made = 0
    if not thumbs.available():
        return made
    for snapshot in Snapshot.objects.filter(thumb="").iterator():
        if limit is not None and made >= limit:
            break
        try:
            with snapshot.file.open("rb") as handle:
                small = thumbs.make_thumbnail(handle.read())
        except OSError:
            continue
        if small:
            snapshot.thumb.save("thumb.jpg", ContentFile(small), save=True)
            made += 1
    return made


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


def rule_capture(device, rule, telegram_chats=(), caption="", ttl=120):
    """Aksi aturan "Capture snapshot": command "capture" + catatan apa yang dilakukan saat
    gambarnya tiba (tautkan ke aturan/alarm, kirim ke Telegram)."""
    with transaction.atomic():
        command = Command.objects.create(
            device=device, method="capture", params={},
            expires_at=timezone.now() + timedelta(seconds=ttl),
        )
        CaptureRequest.objects.create(
            command=command, rule=rule, telegram_chats=", ".join(telegram_chats)[:255], caption=caption[:1024],
        )
        events.command_changed(command)
    return command


def device_snapshots(device):
    return Snapshot.objects.filter(camera__device=device).select_related("camera", "rule", "alarm")
