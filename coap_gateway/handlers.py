"""
Logika setiap permintaan CoAP, TANPA bergantung pada pustaka aiocoap.

Masukan: metode ("GET"/"POST"), potongan alamat setelah /api/v1/, dan isi pesan.
Keluaran: Reply(kode, isi JSON). resources.py yang menerjemahkannya menjadi
pesan CoAP sungguhan. Pemisahan ini membuat aturan mudah dites tanpa jaringan,
sama seperti mqtt_bridge/handlers.py.

Alamat (setelah coap://<server>:5683/api/v1/):

    POST  <token>/telemetry                payload sama dengan HTTP & MQTT
    GET   <token>/desired                  {"pump": 1, ...}          (bisa di-Observe)
    GET   <token>/commands                 {"commands": [...]}        (bisa di-Observe)
    POST  <token>/commands/<id>/response   {"success": true, ...}
"""
import json
from dataclasses import dataclass, field

from django.utils import timezone

from api.payload import PayloadError, parse_telemetry
from api.services import save_telemetry
from devices import control
from devices.models import Device

# Kode respons CoAP ditulis "kelas.detail", mirip kode HTTP:
#   2.xx berhasil, 4.xx kesalahan pengirim, 5.xx kesalahan server.
CHANGED = "2.04"
CONTENT = "2.05"
BAD_REQUEST = "4.00"
UNAUTHORIZED = "4.01"
FORBIDDEN = "4.03"
NOT_FOUND = "4.04"
METHOD_NOT_ALLOWED = "4.05"

TELEMETRY = "telemetry"
DESIRED = "desired"
COMMANDS = "commands"
COMMAND_RESPONSE = "command_response"

# Jenis alamat -> metode yang diizinkan.
ALLOWED = {
    TELEMETRY: "POST",
    DESIRED: "GET",
    COMMANDS: "GET",
    COMMAND_RESPONSE: "POST",
}
# Alamat yang boleh di-Observe (server mengirim kabar saat isinya berubah).
OBSERVABLE = {DESIRED, COMMANDS}


@dataclass
class Reply:
    code: str
    body: dict = field(default_factory=dict)
    device: Device | None = None

    @property
    def ok(self):
        return self.code.startswith("2.")


def error(code, message, device=None):
    return Reply(code, {"error": message}, device)


def route(segments):
    """["TOKEN", "commands", "12", "response"] -> ("command_response", "TOKEN", 12).

    Alamat yang tidak dikenal -> None.
    """
    segments = list(segments)
    if len(segments) == 2 and segments[0] and segments[1] in (TELEMETRY, DESIRED, COMMANDS):
        return segments[1], segments[0], None
    if (
        len(segments) == 4
        and segments[0]
        and segments[1] == COMMANDS
        and segments[2].isdigit()
        and segments[3] == "response"
    ):
        return COMMAND_RESPONSE, segments[0], int(segments[2])
    return None


def reject_constant(name):
    raise ValueError(f"{name} is not allowed")


def decode_json(payload):
    try:
        return json.loads(payload, parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError):
        raise PayloadError("Payload must be valid JSON.")


def find_device(token):
    """(device, Reply penolakan atau None)."""
    device = Device.objects.filter(access_token=token).first()
    if device is None:
        return None, error(UNAUTHORIZED, "Invalid access token.")
    if not device.is_active:
        return device, error(FORBIDDEN, "Device is inactive.", device)
    return device, None


def handle(method, segments, payload=b""):
    """Proses satu permintaan CoAP. Selalu mengembalikan Reply."""
    target = route(segments)
    if target is None:
        return error(NOT_FOUND, "Unknown resource.")
    kind, token, command_id = target
    if method != ALLOWED[kind]:
        return error(METHOD_NOT_ALLOWED, f"Use {ALLOWED[kind]} for this resource.")

    device, rejected = find_device(token)
    if rejected:
        return rejected

    try:
        if kind == TELEMETRY:
            rows = parse_telemetry(decode_json(payload), now=timezone.now())
            created = save_telemetry(device, rows, protocol=Device.Protocol.COAP)
            return Reply(CHANGED, {"saved": len(rows), "new_variables": created}, device)

        if kind == DESIRED:
            return Reply(CONTENT, control.desired_state(device), device)

        if kind == COMMANDS:
            commands = control.pending_commands(device)
            # Sudah dikirim ke device -> SENT. Command yang sudah SENT tetap ikut
            # dikirim sampai device melaporkan hasilnya, sehingga device yang
            # tersambung ulang tidak kehilangan command. Karena itu device harus
            # mengingat id yang sudah dijalankan (lihat fake_coap_device.py).
            control.mark_sent(commands)
            return Reply(CONTENT, {"commands": [control.command_payload(c) for c in commands]}, device)

        command = control.record_command_result(device, command_id, decode_json(payload))
        return Reply(CHANGED, {"id": command.pk, "status": command.status}, device)
    except PayloadError as exc:
        message = str(exc)
        return error(BAD_REQUEST, message[:1].upper() + message[1:], device)


def observe_signature(kind, token):
    """Ringkasan isi resource yang di-Observe. Bila berubah, pengamat diberi kabar.

    None bila token tidak (lagi) valid.
    """
    device = Device.objects.filter(access_token=token, is_active=True).first()
    if device is None:
        return None
    if kind == DESIRED:
        return control.desired_signature(device)
    # COMMANDS: id command yang masih berjalan (setelah yang kedaluwarsa dibereskan).
    return tuple(c.pk for c in control.pending_commands(device))
