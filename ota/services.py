"""
Logika OTA yang dipakai halaman web, endpoint unduhan, dan ketiga protokol device.

Alur satu pembaruan (lihat juga docs/tahap-09-ota.md):

    pengguna klik "Update firmware"
      └─ start_update()   OtaUpdate QUEUED + command "ota" berisi URL unduhan bertoken
    device menerima command (MQTT / CoAP), menjawab {"success": true}
      └─ command_finished()          (gagal -> FAILED)
    device mengunduh GET /api/v1/ota/<token>.bin
      └─ claim_download()            token dipakai SEKALI; QUEUED -> DOWNLOADING
    device melapor {"update": 7, "state": "DOWNLOADING", "progress": 40} ... "UPDATING"
      └─ handle_report() -> report_state()
    device restart, menyala dengan firmware baru, melapor {"version": "1.2.0"}
      └─ handle_report() -> report_version()   versi cocok -> UPDATED
    tidak ada kabar dalam OTA_UPDATE_TIMEOUT
      └─ expire_due()                -> FAILED

Fungsi-fungsi di sini tidak tahu apa-apa tentang HTTP/MQTT/CoAP: masukannya
objek Python biasa, sama seperti devices/control.py.
"""
import ipaddress
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.payload import PayloadError
from devices.models import Board, Command, CommandStatus
from realtime import events

from .models import FINAL, STEP, OtaEvent, OtaStatus, OtaUpdate

# Status yang boleh DILAPORKAN device. UPDATED tidak termasuk: hanya server
# yang menyatakan berhasil, yaitu saat device menyala dan melaporkan versi barunya.
REPORTABLE = {
    OtaStatus.DOWNLOADING, OtaStatus.DOWNLOADED, OtaStatus.VERIFIED, OtaStatus.UPDATING, OtaStatus.FAILED,
}
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+\-]{0,39}$")


class OtaError(Exception):
    """Pembaruan tidak bisa dimulai/dibatalkan; pesannya ditampilkan ke pengguna."""


# --- kabar & pencatatan ------------------------------------------------------------

def emit(update):
    """Kabar WebSocket ke halaman device: progres tampil tanpa memuat ulang."""
    events.emit(update.device_id, {
        "type": "ota",
        "update": update.pk,
        "status": update.status,
        "status_label": update.get_status_display(),
        "progress": update.progress,
        "final": update.is_final,
        "error": update.error,
    })


def record(update, status, message="", source="server", progress=None, error=""):
    """Ganti status, catat satu baris riwayat (OtaEvent), lalu kabari browser."""
    now = timezone.now()
    update.status = status
    if status in (OtaStatus.DOWNLOADED, OtaStatus.VERIFIED, OtaStatus.UPDATING, OtaStatus.UPDATED):
        update.progress = 100
    elif progress is not None:
        update.progress = progress
    if error:
        update.error = error[:255]
    update.updated_at = now
    if status in FINAL:
        update.finished_at = now
    update.save()
    OtaEvent.objects.create(update=update, status=status, message=message[:255], source=source, created_at=now)
    emit(update)


def note(update, message, source="server"):
    """Catatan riwayat tanpa ganti status, mis. "download started from 192.168.1.23"."""
    OtaEvent.objects.create(update=update, status=update.status, message=message[:255], source=source)


def fail(update, message, source="server"):
    record(update, OtaStatus.FAILED, message, source=source, error=message)


# --- batas waktu --------------------------------------------------------------------

def expire_due(device=None, now=None):
    """Tandai FAILED pembaruan yang macet. Dipanggil setiap kali data OTA dibaca
    (pola "lazy", sama seperti Command.expire_due): tidak perlu proses terpisah.

    - QUEUED, tetapi command "ota"-nya kedaluwarsa: device tidak pernah mengambilnya.
    - Belum selesai setelah OTA_UPDATE_TIMEOUT detik sejak dimulai.
    Mengembalikan jumlah pembaruan yang digagalkan.
    """
    now = now or timezone.now()
    Command.objects.expire_due()
    updates = OtaUpdate.objects.active().select_related("command", "package")
    if device is not None:
        updates = updates.filter(device=device)
    timeout = settings.OTA_UPDATE_TIMEOUT
    failed = 0
    for update in updates:
        if update.status == OtaStatus.QUEUED and update.command and update.command.status == CommandStatus.EXPIRED:
            fail(update, "The device did not pick up the update command in time (is it online?).")
        elif (now - update.created_at).total_seconds() > timeout:
            if update.status == OtaStatus.UPDATING:
                why = f"The device did not come back with version {update.package.version}"
            else:
                why = f"No progress after {update.get_status_display().lower()}"
            fail(update, f"{why} within {timeout // 60} minutes.")
        else:
            continue
        failed += 1
    return failed


# --- alamat unduhan -------------------------------------------------------------------

def base_url_for(request):
    """Alamat server untuk device: OTA_BASE_URL (.env), atau alamat di address bar."""
    if settings.OTA_BASE_URL:
        return settings.OTA_BASE_URL
    return f"{request.scheme}://{request.get_host()}"


def download_url(base_url, update):
    return f"{base_url}/api/v1/ota/{update.download_token}.bin"


def reach(base_url):
    """Siapa yang bisa menjangkau alamat ini (sama dengan CoAP, devices/views.coap_info):
    loopback = hanya komputer ini, private = jaringan yang sama, public = internet."""
    host = (urlsplit(base_url).hostname or "").strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "loopback" if host == "localhost" else "unknown"
    return "loopback" if address.is_loopback else "private" if address.is_private else "public"


# --- memulai dan membatalkan -------------------------------------------------------------

def compatible_packages(packages, device):
    """Paket untuk board device ini, yang versinya bukan versi yang sedang berjalan."""
    if not device.board:
        return packages.none()
    return packages.filter(board=device.board).exclude(version=device.firmware_version)


def start_update(device, package, user, base_url):
    """Buat OtaUpdate + command "ota". Melempar OtaError bila tidak boleh."""
    expire_due(device)
    if not device.is_active:
        raise OtaError("This device is inactive.")
    if not device.board:
        raise OtaError("The device's board is unknown. Set it with Edit, or let the device report it.")
    if package.board != device.board:
        raise OtaError(
            f"This firmware is for {package.get_board_display()}, but the device is "
            f"{device.get_board_display()}."
        )
    if device.firmware_version and package.version == device.firmware_version:
        raise OtaError(f"The device already runs version {package.version}.")
    if device.ota_updates.active().exists():
        raise OtaError("Another update for this device is still running. Wait for it, or cancel it.")

    now = timezone.now()
    with transaction.atomic():
        update = OtaUpdate.objects.create(
            device=device,
            package=package,
            previous_version=device.firmware_version,
            token_expires_at=now + timedelta(seconds=settings.OTA_DOWNLOAD_TTL),
            created_by=user,
            created_at=now,
            updated_at=now,
        )
        # Isi command: semua yang dibutuhkan firmware untuk memperbarui dirinya.
        command = Command.objects.create(
            device=device,
            method="ota",
            params={
                "update": update.pk,
                "title": package.title,
                "version": package.version,
                "url": download_url(base_url, update),
                "md5": package.md5,
                "size": package.size,
            },
            created_by=user,
            expires_at=now + timedelta(seconds=settings.OTA_COMMAND_TTL),
        )
        update.command = command
        update.save(update_fields=["command"])
        who = user.username if user else "system"
        OtaEvent.objects.create(
            update=update, status=OtaStatus.QUEUED, source="web", created_at=now,
            message=f"Update {device.firmware_version or '?'} → {package.version} queued by {who}",
        )
    events.command_changed(command)
    emit(update)
    return update


def cancel(update, user):
    """Pengguna membatalkan. Device yang SUDAH mulai menulis firmware tidak bisa
    dihentikan dari server; pembatalan hanya berarti server berhenti menunggu dan
    link unduhan tidak berlaku lagi."""
    if update.is_final:
        raise OtaError("This update has already finished.")
    command = update.command
    if command is not None and not command.is_final:
        command.status = CommandStatus.EXPIRED
        command.completed_at = timezone.now()
        command.save(update_fields=["status", "completed_at"])
        events.command_changed(command)
    record(update, OtaStatus.CANCELED, f"Canceled by {user.username}", source="web")


# --- dari device -------------------------------------------------------------------------

def command_finished(command):
    """Dipanggil devices.control.record_command_result() untuk command "ota".

    Jawaban command hanya berarti "perintah diterima" (atau ditolak). Unduhan dan
    pemasangan dilaporkan terpisah lewat handle_report().
    """
    update = OtaUpdate.objects.filter(command=command).first()
    if update is None or update.is_final:
        return
    response = command.response or {}
    if command.status == CommandStatus.FAILED:
        fail(update, f"The device refused the update: {response.get('error') or 'no reason given'}",
             source="device")
    else:
        note(update, "The device accepted the update command", source="device")


def claim_download(token, address):
    """Endpoint unduhan memanggil ini. Mengembalikan (update, None) atau (None, (kode HTTP, pesan))."""
    update = OtaUpdate.objects.select_related("package", "device").filter(download_token=token).first()
    if update is None:
        return None, (404, "Unknown download link.")
    expire_due(update.device)
    update.refresh_from_db()
    if update.is_final:
        return None, (410, f"This update is {update.get_status_display().lower()}.")
    now = timezone.now()
    if now > update.token_expires_at:
        return None, (410, "This download link has expired.")
    if not update.device.is_active:
        return None, (403, "Device is inactive.")
    # SEKALI pakai: hanya satu request yang berhasil mengisi token_used_at.
    # UPDATE ... WHERE token_used_at IS NULL bersifat atomik di database, jadi dua
    # request bersamaan tidak bisa sama-sama lolos.
    claimed = OtaUpdate.objects.filter(pk=update.pk, token_used_at__isnull=True).update(
        token_used_at=now, downloaded_by=address
    )
    if not claimed:
        return None, (410, "This download link was already used. Start a new update to try again.")
    update.refresh_from_db()
    message = f"Download started from {address or 'unknown address'}"
    if update.status == OtaStatus.QUEUED:
        record(update, OtaStatus.DOWNLOADING, message, source="HTTP", progress=0)
    else:
        note(update, message, source="HTTP")
    return update, None


def handle_report(device, data, protocol):
    """Laporan firmware dari device (HTTP, MQTT, atau CoAP; payload sama):

        {"version": "1.0.0", "board": "esp32"}                     saat menyala
        {"update": 7, "state": "DOWNLOADING", "progress": 40}      selama pembaruan
        {"update": 7, "state": "FAILED", "error": "MD5 mismatch"}

    Keduanya boleh digabung dalam satu pesan. Mengembalikan teks ringkas untuk
    log/jawaban; melempar PayloadError bila isinya salah.
    """
    if not isinstance(data, dict):
        raise PayloadError('Report must be a JSON object, e.g. {"version": "1.0.0"}.')
    if "version" not in data and "state" not in data:
        raise PayloadError('Report needs "version" and/or "update" + "state".')
    expire_due(device)
    done = []
    if "state" in data:
        done.append(report_state(device, data, protocol))
    if "version" in data:
        done.append(report_version(device, data.get("version"), data.get("board"), protocol))
    return "; ".join(done)


def report_state(device, data, protocol):
    update_id = data.get("update")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        raise PayloadError('"update" must be the update number from the ota command.')
    state = data.get("state")
    if state not in REPORTABLE:
        raise PayloadError(f'"state" must be one of {", ".join(sorted(REPORTABLE))}.')
    progress = data.get("progress")
    if progress is not None and (not isinstance(progress, (int, float)) or isinstance(progress, bool)
                                 or not 0 <= progress <= 100):
        raise PayloadError('"progress" must be a number from 0 to 100.')
    progress = None if progress is None else int(progress)
    error = str(data.get("error") or "")[:200]

    with transaction.atomic():
        # Dikunci: laporan MQTT dan unduhan HTTP bisa datang hampir bersamaan.
        update = OtaUpdate.objects.select_for_update().filter(device=device, pk=update_id).first()
        if update is None:
            raise PayloadError(f"Update {update_id} does not exist for this device.")
        if update.is_final:
            raise PayloadError(f"Update {update_id} is already {update.get_status_display().lower()}.")
        if state == OtaStatus.FAILED:
            fail(update, f"Device: {error or 'update failed'}", source=protocol)
            return f"update {update_id}: FAILED"
        if STEP[state] < STEP[update.status]:
            raise PayloadError(f"Update {update_id} is already {update.status}; cannot go back to {state}.")
        if state == update.status:
            # Hanya progres yang berubah: tanpa baris riwayat, cukup kabar ke browser.
            if progress is not None and progress != update.progress:
                update.progress = progress
                update.updated_at = timezone.now()
                update.save(update_fields=["progress", "updated_at"])
                emit(update)
            return f"update {update_id}: {state} {update.progress}%"
        record(update, state, f"Reported by the device{f' ({progress}%)' if progress is not None else ''}",
               source=protocol, progress=progress)
        return f"update {update_id}: {state}"


def report_version(device, version, board, protocol):
    """Device memberi tahu firmware apa yang SEDANG berjalan (biasanya saat menyala).

    Inilah satu-satunya bukti bahwa pembaruan berhasil: file sudah ditulis dan
    device benar-benar menyala dengan firmware baru.
    """
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise PayloadError('"version" must be text like "1.2.0" (letters, digits, . _ + -).')
    if board is not None and board not in Board.values:
        raise PayloadError(f'"board" must be one of {", ".join(Board.values)}.')

    changed = device.firmware_version != version or (board and device.board != board)
    device.firmware_version = version
    device.firmware_reported_at = timezone.now()
    fields = ["firmware_version", "firmware_reported_at"]
    if board:
        device.board = board
        fields.append("board")
    device.save(update_fields=fields)

    message = f"running {version}"
    with transaction.atomic():
        update = device.ota_updates.select_for_update().active().select_related("package").first()
        if update is not None:
            target = update.package.version
            if version == target:
                record(update, OtaStatus.UPDATED, f"The device restarted with version {version}", source=protocol)
                message += f"; update {update.pk}: UPDATED"
            elif update.status == OtaStatus.UPDATING:
                # Restart setelah UPDATING tetapi versinya lama: firmware baru gagal
                # menyala dan bootloader kembali ke partisi lama (rollback).
                fail(update, f"The device restarted with version {version}, not {target} "
                             f"(the new firmware did not start).", source=protocol)
                message += f"; update {update.pk}: FAILED"
    if changed and update is None:
        events.emit(device.pk, {"type": "firmware", "version": version})
    return message
