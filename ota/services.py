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
    device restart, menyala dengan firmware baru, melapor {"title": "Greenhouse", "version": "1.2.0"}
      └─ handle_report() -> report_version()   nama + versi cocok -> UPDATED

Firmware dikenali dari NAMA + versi (seperti fw_title + fw_version di ThingsBoard): device
dengan board yang sama bisa menjalankan program berbeda dengan nomor versi yang sama.
Firmware lama yang belum melaporkan "title" hanya bisa dibandingkan versinya.
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
TITLE_MAX = 80
# MAC address (tahap 24): 12 digit heksadesimal, boleh dipisah ":", "-", atau ".".
MAC_DIGITS_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
MAC_SEPARATORS_RE = re.compile(r"[:\-.]")


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
                why = f"The device did not come back with version {update.package_version}"
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


# --- nama + versi ------------------------------------------------------------------------

def same_title(a, b):
    """Nama firmware dibandingkan tanpa beda huruf besar-kecil dan spasi di ujung."""
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def firmware_label(title, version):
    """"Greenhouse 1.2.0", atau "1.2.0" bila namanya tidak diketahui."""
    return f"{title} {version}" if title else (version or "?")


def runs_package(device, package):
    """Apakah device SUDAH menjalankan paket ini (nama + versi; tanpa nama: versi saja)?"""
    if not device.firmware_version or package.version != device.firmware_version:
        return False
    return not device.firmware_title or same_title(device.firmware_title, package.title)


def confirmation_needed(device, package):
    """Alasan pengguna harus menegaskan pembaruan ini, atau "" bila tidak perlu.

    - device menjalankan program LAIN (nama berbeda): programnya akan diganti;
    - device belum melaporkan nama dan versinya sama: platform tidak bisa membedakan
      firmware baru dari firmware lama, jadi hasil pembaruan tidak bisa dipastikan.
    """
    if device.firmware_title and not same_title(device.firmware_title, package.title):
        return (f"This device runs {firmware_label(device.firmware_title, device.firmware_version)}. "
                f"Replace it with {firmware_label(package.title, package.version)}?")
    if not device.firmware_title and device.firmware_version and device.firmware_version == package.version:
        return (f"This device already runs version {package.version} and does not report its firmware name, "
                f"so the platform cannot tell {package.title} {package.version} from the firmware it runs now. "
                f"The update may be marked as successful even if the old firmware comes back.")
    return ""


# --- memulai dan membatalkan -------------------------------------------------------------

def compatible_packages(packages, device):
    """Paket untuk board device ini, kecuali firmware (nama + versi) yang sedang berjalan."""
    if not device.board:
        return packages.none()
    packages = packages.filter(board=device.board)
    if device.firmware_version and device.firmware_title:
        return packages.exclude(title__iexact=device.firmware_title.strip(), version=device.firmware_version)
    return packages


def start_update(device, package, user, base_url, confirmed=False):
    """Buat OtaUpdate + command "ota". Melempar OtaError bila tidak boleh.

    Bila confirmation_needed() memberi alasan, pembaruan hanya dimulai dengan confirmed=True
    (halaman web menampilkan halaman konfirmasi lebih dulu)."""
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
    if device.firmware_title and runs_package(device, package):
        raise OtaError(f"The device already runs {firmware_label(package.title, package.version)}.")
    if device.ota_updates.active().exists():
        raise OtaError("Another update for this device is still running. Wait for it, or cancel it.")
    if not confirmed and confirmation_needed(device, package):
        raise OtaError(confirmation_needed(device, package))

    now = timezone.now()
    with transaction.atomic():
        update = OtaUpdate.objects.create(
            device=device,
            package=package,
            previous_title=device.firmware_title,
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
            message=(f"Update {firmware_label(device.firmware_title, device.firmware_version)} → "
                     f"{firmware_label(package.title, package.version)} queued by {who}"),
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

        {"title": "Greenhouse", "version": "1.0.0", "board": "esp32", "mac": "24:6F:28:AA:BB:CC"}   saat menyala
        {"update": 7, "state": "DOWNLOADING", "progress": 40}      selama pembaruan
        {"update": 7, "state": "FAILED", "error": "MD5 mismatch"}

    Keduanya boleh digabung dalam satu pesan. Mengembalikan teks ringkas untuk
    log/jawaban; melempar PayloadError bila isinya salah.
    """
    if not isinstance(data, dict):
        raise PayloadError('Report must be a JSON object, e.g. {"version": "1.0.0"}.')
    if not any(key in data for key in ("version", "state", "mac")):
        raise PayloadError('Report needs "version" (and "mac"), and/or "update" + "state".')
    # Periksa MAC lebih dulu: laporan dengan MAC yang salah ditolak utuh, tidak setengah tersimpan.
    mac = normalize_mac(data["mac"]) if "mac" in data else None
    expire_due(device)
    done = []
    if "state" in data:
        done.append(report_state(device, data, protocol))
    if "version" in data:
        done.append(report_version(device, data.get("version"), data.get("board"), protocol, data.get("title")))
    if mac is not None:
        done.append(report_mac(device, mac))
    return "; ".join(done)


def normalize_mac(value):
    """Seragamkan MAC address: "aa-bb-cc-dd-ee-ff", "aabb.ccdd.eeff", atau
    "aabbccddeeff" -> "AA:BB:CC:DD:EE:FF". Melempar PayloadError bila bentuknya salah.
    """
    message = '"mac" must be text like "AA:BB:CC:DD:EE:FF" (12 hexadecimal digits).'
    if not isinstance(value, str):
        raise PayloadError(message)
    digits = MAC_SEPARATORS_RE.sub("", value.strip())
    if not MAC_DIGITS_RE.match(digits) or int(digits, 16) == 0:
        raise PayloadError(message)
    digits = digits.upper()
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


def report_mac(device, mac):
    """Simpan MAC address yang dilaporkan device (tahap 24). `mac` sudah dinormalisasi."""
    changed = device.mac_address != mac
    device.mac_address = mac
    device.mac_reported_at = timezone.now()
    device.save(update_fields=["mac_address", "mac_reported_at"])
    if changed:
        events.emit(device.pk, {"type": "firmware", "mac": mac})
    return f"MAC {mac}"


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


def report_version(device, version, board, protocol, title=None):
    """Device memberi tahu firmware apa yang SEDANG berjalan (biasanya saat menyala).

    Inilah satu-satunya bukti bahwa pembaruan berhasil: file sudah ditulis dan
    device benar-benar menyala dengan firmware baru. Dengan "title", berhasil berarti
    nama DAN versi sama dengan paket; tanpa "title" (firmware lama) hanya versinya.
    """
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise PayloadError('"version" must be text like "1.2.0" (letters, digits, . _ + -).')
    if board is not None and board not in Board.values:
        raise PayloadError(f'"board" must be one of {", ".join(Board.values)}.')
    if title is not None and (not isinstance(title, str) or not title.strip() or len(title.strip()) > TITLE_MAX):
        raise PayloadError(f'"title" must be the firmware name, e.g. "Greenhouse node" (at most {TITLE_MAX} characters).')
    # Laporan tanpa "title" berarti firmware yang tidak mengenal nama: nama lama dihapus.
    title = title.strip() if title else ""

    changed = (device.firmware_version != version or device.firmware_title != title
               or (board and device.board != board))
    device.firmware_version = version
    device.firmware_title = title
    device.firmware_reported_at = timezone.now()
    fields = ["firmware_version", "firmware_title", "firmware_reported_at"]
    if board:
        device.board = board
        fields.append("board")
    device.save(update_fields=fields)

    running = firmware_label(title, version)
    message = f"running {running}"
    with transaction.atomic():
        update = device.ota_updates.select_for_update().active().select_related("package").first()
        if update is not None:
            target = firmware_label(update.package_title, update.package_version)
            if version == update.package_version and (not title or same_title(title, update.package_title)):
                record(update, OtaStatus.UPDATED, f"The device restarted with {running}", source=protocol)
                message += f"; update {update.pk}: UPDATED"
            elif update.status == OtaStatus.UPDATING:
                # Restart setelah UPDATING tetapi firmware-nya lain: firmware baru gagal
                # menyala dan bootloader kembali ke partisi lama (rollback).
                why = "the new firmware did not start"
                if version == update.package_version:
                    # Versi cocok, nama tidak: mungkin FW_TITLE di sketch berbeda dengan nama di platform.
                    why += f", or FW_TITLE in the sketch is not \"{update.package_title}\""
                fail(update, f"The device restarted with {running}, not {target} ({why}).", source=protocol)
                message += f"; update {update.pk}: FAILED"
    if changed and update is None:
        events.emit(device.pk, {"type": "firmware", "version": version, "title": title})
    return message
