"""
Halaman dan endpoint kamera (tahap 27a).

    POST /api/v1/<token>/snapshot                 device mengunggah JPEG (api/urls.py)
    GET  /cameras/snapshots/<id>.jpg[?thumb=1]    gambar (atau thumbnail-nya), setelah hak akses diperiksa
    GET  /cameras/devices/<id>/                   galeri snapshot satu device
    POST /cameras/devices/<id>/capture/           tombol "Capture now" -> command "capture"
    POST /cameras/snapshots/<id>/delete/          hapus satu snapshot
    POST /cameras/devices/<id>/settings/          masa simpan snapshot kamera

Hak akses mengikuti berbagi device (tahap 19): Viewer melihat, Operator memotret, Editor menghapus
dan mengubah masa simpan. Memotret, menghapus, dan mengubah masa simpan dicatat di log aktivitas.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from activity.log import Category, record
from devices.models import Device
from devices.views import visible_devices
from sharing import access

from . import services
from .models import Camera, Snapshot

GALLERY_PAGE_SIZE = 24
RECENT = 6


# --- Device -----------------------------------------------------------------------------

@csrf_exempt
@require_POST
def upload(request, token):
    """POST /api/v1/<token>/snapshot — body = gambar JPEG (Content-Type: image/jpeg).

    Header opsional:  X-Source: periodic | command | device,  X-Command-Id: <id command capture>,
                      X-Camera: <nama kamera> (bawaan "camera").
    Balasan: 201 {"id", "size", "width", "height"} | 400 kosong | 401 token | 403 nonaktif |
             413 terlalu besar | 415 bukan JPEG | 429 terlalu sering.
    """
    device = Device.objects.filter(access_token=token).first()
    if device is None:
        return JsonResponse({"error": "Invalid access token."}, status=401)
    if not device.is_active:
        return JsonResponse({"error": "Device is inactive."}, status=403)
    length = request.META.get("CONTENT_LENGTH") or "0"
    if length.isdigit() and int(length) > services.max_bytes():
        return JsonResponse({"error": f"The image is larger than {settings.CAMERA_MAX_SNAPSHOT_KB} KB."}, status=413)
    try:
        snapshot = services.save_snapshot(
            device, request.body,
            source=request.headers.get("X-Source", ""),
            command_id=request.headers.get("X-Command-Id", ""),
            camera_name=request.headers.get("X-Camera", ""),
        )
    except services.SnapshotError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    return JsonResponse({"id": snapshot.pk, "size": snapshot.size, "width": snapshot.width,
                         "height": snapshot.height}, status=201)


# --- Pengguna ---------------------------------------------------------------------------

def device_for(request, pk, level=access.VIEWER):
    device = get_object_or_404(visible_devices(request.user), pk=pk)
    access.require(request.user, device, level)
    return device


@login_required
def image(request, pk):
    """Gambar snapshot. Produksi: nginx yang mengirim file (X-Accel-Redirect, CAMERA_X_ACCEL_PREFIX)."""
    snapshot = get_object_or_404(Snapshot.objects.select_related("camera"), pk=pk)
    device_for(request, snapshot.camera.device_id)
    filename = f"{snapshot.camera.device_id}-{snapshot.ts:%Y%m%d-%H%M%S}.jpg"
    disposition = "attachment" if request.GET.get("download") else "inline"
    # ?thumb=1: gambar kecil bila ada (gambar lama / tanpa Pillow: gambar asli).
    field = snapshot.thumb if request.GET.get("thumb") and snapshot.thumb else snapshot.file
    if settings.CAMERA_X_ACCEL_PREFIX:
        response = HttpResponse(content_type="image/jpeg")
        response["X-Accel-Redirect"] = settings.CAMERA_X_ACCEL_PREFIX.rstrip("/") + "/" + field.name
    else:
        try:
            response = FileResponse(field.open("rb"), content_type="image/jpeg")
        except FileNotFoundError:
            raise Http404("The image file is missing.")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    # Gambar tidak pernah berubah (nama baru untuk gambar baru): boleh disimpan browser,
    # tetapi hanya browser (private), karena perlu login.
    response["Cache-Control"] = "private, max-age=86400"
    return response


@login_required
def gallery(request, pk):
    from django.template.response import TemplateResponse

    device = device_for(request, pk)
    snapshots = services.device_snapshots(device)
    day = request.GET.get("day", "")
    if day:
        snapshots = snapshots.filter(ts__date=day) if _valid_day(day) else snapshots.none()
    page = Paginator(snapshots, GALLERY_PAGE_SIZE).get_page(request.GET.get("page"))
    camera = device.cameras.first()
    quota = services.quota_bytes()
    used = services.storage_used(device.owner) if quota else 0
    return TemplateResponse(request, "cameras/gallery.html", {
        "quota_mb": settings.CAMERA_STORAGE_QUOTA_MB,
        "used_mb": round(used / 1048576, 1),
        "used_percent": min(100, round(used * 100 / quota)) if quota else 0,
        "is_owner": device.owner_id == request.user.pk,
        "device": device,
        "page_obj": page,
        "day": day,
        "camera": camera,
        "can_operate": access.can(request.user, device, access.OPERATOR),
        "can_edit": access.can(request.user, device, access.EDITOR),
        "default_keep_days": settings.CAMERA_RETENTION_DAYS,
    })


def _valid_day(text):
    from datetime import date

    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


@login_required
@require_POST
def capture(request, pk):
    device = device_for(request, pk, access.OPERATOR)
    command = services.request_capture(device, request.user)
    record("camera.capture", f"Capture requested (command #{command.pk})",
           category=Category.CONTROL, owner=device.owner, device=device)
    messages.success(request, f"Capture requested. {device.name} sends the image as soon as it receives the command.")
    return redirect(request.POST.get("next") or reverse("devices:detail", args=[device.pk]) + "#camera")


@login_required
@require_POST
def delete(request, pk):
    snapshot = get_object_or_404(Snapshot.objects.select_related("camera"), pk=pk)
    device = device_for(request, snapshot.camera.device_id, access.EDITOR)
    label = f"{snapshot.ts.astimezone():%Y-%m-%d %H:%M:%S}"
    snapshot.delete()
    record("camera.snapshot_deleted", f"Snapshot of {label} deleted",
           category=Category.DEVICE, owner=device.owner, device=device)
    messages.success(request, "Snapshot deleted.")
    return redirect(request.POST.get("next") or reverse("cameras:gallery", args=[device.pk]))


@login_required
@require_POST
def camera_settings(request, pk):
    device = device_for(request, pk, access.EDITOR)
    camera, _ = Camera.objects.get_or_create(device=device, name=Camera.DEFAULT_NAME)
    value = request.POST.get("retention_days", "").strip()
    if value == "":
        camera.retention_days = None
    elif value.isdigit() and int(value) <= 3650:
        camera.retention_days = int(value)
    else:
        messages.error(request, "Keep snapshots: a number of days from 0 to 3650, or empty for the default.")
        return redirect(reverse("cameras:gallery", args=[device.pk]))
    camera.save(update_fields=["retention_days"])
    shown = "platform default" if camera.retention_days is None else (
        "forever" if camera.retention_days == 0 else f"{camera.retention_days} days")
    record("camera.settings", f"Snapshots kept: {shown}", category=Category.DEVICE, owner=device.owner, device=device)
    messages.success(request, "Camera settings saved.")
    return redirect(reverse("cameras:gallery", args=[device.pk]))


def device_camera_context(request, device):
    """Data kartu "Camera" di halaman device (devices/views.DeviceDetailView).
    Kartu tampil untuk board ESP32-CAM, atau device yang pernah mengirim snapshot."""
    recent = list(services.device_snapshots(device)[:RECENT])
    show = bool(recent) or device.board == "esp32cam"
    return {
        "camera_show": show,
        "camera_recent": recent,
        "camera_latest": recent[0] if recent else None,
        "camera_count": services.device_snapshots(device).count() if show else 0,
        "camera_upload_url": request.build_absolute_uri(reverse("api:snapshot", args=[device.access_token])),
    }
