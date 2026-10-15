"""
Logika kontrol device yang SAMA untuk semua protokol (MQTT, CoAP, ...).

Sebelumnya fungsi-fungsi ini berada di dalam mqtt_bridge. Dipindah ke sini
supaya MQTT dan CoAP dijamin berperilaku identik:

    desired_state(device)            -> {"pump": 1, "fan_speed": 40}
    pending_commands(device)         -> command yang belum selesai & belum kedaluwarsa
    command_payload(command)         -> {"id": 12, "method": "...", "params": {...}}
    record_command_result(device, id, data)  -> catat hasil dari device

Fungsi di sini tidak tahu apa-apa tentang jaringan; masukan dan keluarannya
berupa objek Python biasa.
"""
from api.payload import PayloadError

from .models import CommandStatus, Variable


def plain(value):
    """1.0 -> 1 supaya JSON-nya rapi bagi firmware; 22.5 tetap 22.5."""
    return int(value) if float(value).is_integer() else value


def actuators_with_desired(device):
    return device.variables.filter(
        kind=Variable.Kind.ACTUATOR, desired_value__isnull=False
    ).order_by("key")


def desired_state(device):
    """Desired state SEMUA aktuator device dalam satu dict."""
    return {v.key: plain(v.desired_value) for v in actuators_with_desired(device)}


def desired_signature(device):
    """Berubah setiap kali ada desired state baru (versi naik) pada device ini."""
    return tuple(actuators_with_desired(device).values_list("key", "desired_version"))


def command_payload(command):
    return {"id": command.pk, "method": command.method, "params": command.params}


def pending_commands(device):
    """Command yang masih berjalan (QUEUED atau SENT), urut dari yang terlama.

    Command yang sudah lewat batas waktu ditandai EXPIRED lebih dulu, sehingga
    tidak pernah dikirim ke device.
    """
    device.commands.expire_due()
    return list(device.commands.pending().order_by("created_at", "pk"))


def mark_sent(commands):
    """QUEUED -> SENT untuk command yang baru saja dikirim ke device."""
    for command in commands:
        if command.status == CommandStatus.QUEUED:
            command.mark_sent()


def record_command_result(device, command_id, data):
    """Device melaporkan hasil command. Mengembalikan Command yang sudah diperbarui.

    Payload berupa objek JSON. Kunci "success" (true/false) menentukan status;
    bila tidak ada, dianggap berhasil. Seluruh payload disimpan sebagai
    Command.response, sehingga device bebas menambahkan informasi lain:
        {"success": true, "uptime_ms": 123456}
        {"success": false, "error": "Valve is jammed"}

    Melempar PayloadError bila payload salah atau command tidak bisa diubah lagi.
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
    if command.method == "ota":
        # Jawaban command "ota" juga mengubah status pembaruan firmware (tahap 9).
        from ota.services import command_finished

        command_finished(command)
    return command
