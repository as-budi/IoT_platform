"""
Mengolah pesan MQTT yang MASUK dari device (arah device -> platform).

Fungsi di sini tidak tahu apa-apa tentang koneksi ke broker: masukannya hanya
nama topik dan isi pesan (bytes). Karena itu bisa dites tanpa broker, dan
perintah `mqtt_bridge` cukup meneruskan setiap pesan ke handle_message().

Perbedaan penting dengan HTTP: pada HTTP, device langsung menerima jawaban
(200, 400, 401, ...). MQTT bersifat "kirim lalu lupa": tidak ada jawaban
otomatis. Karena itu hasil pengolahan dikembalikan sebagai Result, lalu
bridge mencatatnya di log dan, bila ditolak, mengirim pesan ke topik
<prefix>/<token>/errors supaya pembuat firmware tahu kesalahannya.
"""
import json
from dataclasses import dataclass

from django.utils import timezone

from api.payload import PayloadError, parse_telemetry
from api.services import save_telemetry
from devices.models import Device

from . import topics


@dataclass
class Result:
    ok: bool
    message: str
    # Device pengirim, bila token-nya dikenal. Pesan error hanya dikirim balik
    # ke device yang dikenal.
    device: Device | None = None


def reject_constant(name):
    # json.loads Python menerima NaN/Infinity, padahal itu bukan JSON standar.
    raise ValueError(f"{name} is not allowed")


def decode_json(payload):
    """bytes -> objek Python. Melempar PayloadError bila bukan JSON yang valid."""
    try:
        return json.loads(payload, parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError):
        raise PayloadError("Payload must be valid JSON.")


def handle_message(topic, payload, prefix):
    """Proses satu pesan masuk. Mengembalikan Result, atau None bila topiknya bukan milik kita."""
    parsed = topics.parse(topic, prefix)
    if parsed is None:
        return None
    kind, token, command_id = parsed

    # Autentikasi: sama dengan HTTP, device dikenali dari access token.
    # Bedanya, token ada di nama topik, bukan di URL.
    device = Device.objects.filter(access_token=token).first()
    if device is None:
        return Result(False, "Unknown access token.")
    if not device.is_active:
        return Result(False, "Device is inactive.", device)

    try:
        data = decode_json(payload)
        if kind == topics.TELEMETRY:
            return handle_telemetry(device, data)
        return handle_command_response(device, command_id, data)
    except PayloadError as exc:
        message = str(exc)
        return Result(False, message[:1].upper() + message[1:], device)


def handle_telemetry(device, data):
    """Payload sama persis dengan HTTP API (lihat api/payload.py)."""
    rows = parse_telemetry(data, now=timezone.now())
    created = save_telemetry(device, rows)
    message = f"saved {len(rows)} value(s)"
    if created:
        message += f", new variable(s): {', '.join(created)}"
    return Result(True, message, device)


def handle_command_response(device, command_id, data):
    """Device melaporkan hasil command.

    Payload berupa objek JSON. Kunci "success" (true/false) menentukan status;
    bila tidak ada, dianggap berhasil. Seluruh payload disimpan sebagai
    Command.response, sehingga device bebas menambahkan informasi lain:
        {"success": true, "uptime_ms": 123456}
        {"success": false, "error": "Valve is jammed"}
    """
    if not isinstance(data, dict):
        raise PayloadError('Response must be a JSON object, e.g. {"success": true}.')
    success = data.get("success", True)
    if not isinstance(success, bool):
        raise PayloadError('"success" must be true or false.')

    # Dicari di command milik device INI saja: device tidak bisa menjawab
    # command device lain walaupun menebak nomornya.
    command = device.commands.filter(pk=command_id).first()
    if command is None:
        raise PayloadError(f"Command {command_id} does not exist for this device.")

    # Bereskan yang kedaluwarsa dulu: jawaban yang terlambat tidak dihitung.
    device.commands.expire_due()
    command.refresh_from_db()
    if not command.complete(success, data):
        raise PayloadError(
            f"Command {command_id} is already {command.get_status_display().lower()}; response ignored."
        )
    return Result(True, f"command {command_id} ({command.method}): {command.status}", device)
