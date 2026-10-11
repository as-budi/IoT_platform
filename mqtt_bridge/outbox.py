"""
Pesan KELUAR dari platform ke device (arah platform -> device).

Pola "outbox": view Django TIDAK mengirim MQTT sendiri. Saat pengguna menekan
ON atau mengirim command, view hanya menyimpan permintaan ke database (seperti
di tahap 1b). Proses `mqtt_bridge` lalu memeriksa database setiap ~1 detik dan
mengirim yang belum terkirim.

Keuntungannya:
- Halaman web tetap cepat dan tidak error walaupun broker sedang tidak bisa
  dihubungi; permintaan menunggu di database dan dikirim saat koneksi pulih.
- Hanya ada SATU koneksi MQTT milik platform, yaitu milik bridge.
Harganya: jeda paling lama satu putaran pemeriksaan (~1 detik).
"""
import json
from collections import defaultdict

from devices.control import command_payload, plain
from devices.models import Command, CommandStatus, Variable

from . import topics




class Outbox:
    def __init__(self, prefix):
        self.prefix = prefix
        # device_id -> "sidik jari" desired state yang terakhir dikirim.
        # Disimpan di memori: bila bridge di-restart, semua dikirim ulang.
        # Itu aman karena desired state bersifat "keadaan", bukan "aksi".
        self.sent_desired = {}

    def reset(self):
        """Dipanggil setiap kali (ulang) tersambung ke broker: kirim ulang semuanya."""
        self.sent_desired.clear()

    def flush(self, publish):
        """Kirim semua yang tertunda. `publish(topic, payload_dict, retain)` -> True bila berhasil.

        Mengembalikan (jumlah pesan desired, jumlah command) yang terkirim.
        """
        return self.flush_desired(publish), self.flush_commands(publish)

    # --- Pola 1: desired state ----------------------------------------------

    def flush_desired(self, publish):
        """Satu pesan per device berisi desired state SEMUA aktuatornya:

            <prefix>/<token>/desired   {"lamp": 1, "fan_speed": 40}

        Pesan dikirim dengan flag RETAIN: broker menyimpan pesan terakhir di
        topik ini, dan langsung memberikannya kepada device yang baru
        tersambung. Device yang mati lalu hidup kembali pun tetap tahu
        keadaan yang diminta, tanpa harus bertanya.
        """
        actuators = (
            Variable.objects.filter(
                kind=Variable.Kind.ACTUATOR,
                desired_value__isnull=False,
                device__is_active=True,
            )
            .select_related("device")
            .order_by("device_id", "key")
        )
        by_device = defaultdict(list)
        for variable in actuators:
            by_device[variable.device].append(variable)

        sent = 0
        for device, variables in by_device.items():
            # Berubah bila ada desired_version yang naik, atau token diganti.
            signature = (device.access_token,) + tuple((v.key, v.desired_version) for v in variables)
            if self.sent_desired.get(device.pk) == signature:
                continue
            payload = {v.key: plain(v.desired_value) for v in variables}
            if not publish(topics.desired(self.prefix, device.access_token), payload, retain=True):
                break  # koneksi putus; coba lagi di putaran berikutnya
            self.sent_desired[device.pk] = signature
            sent += 1
        return sent

    # --- Pola 2: command ------------------------------------------------------

    def flush_commands(self, publish):
        """Kirim command berstatus QUEUED, lalu ubah statusnya menjadi SENT.

            <prefix>/<token>/commands/12   {"id": 12, "method": "reboot", "params": {}}

        Command TIDAK di-retain: aksi sekali jalan tidak boleh diulang hanya
        karena device tersambung ulang.
        """
        # Command kedaluwarsa tidak boleh dikirim sama sekali.
        Command.objects.expire_due()
        queued = (
            Command.objects.filter(status=CommandStatus.QUEUED, device__is_active=True)
            .select_related("device")
            .order_by("created_at", "pk")
        )
        sent = 0
        for command in queued:
            payload = command_payload(command)
            topic = topics.command(self.prefix, command.device.access_token, command.pk)
            if not publish(topic, payload, retain=False):
                break
            command.mark_sent()
            sent += 1
        return sent


def encode(payload):
    """dict -> teks JSON yang ringkas (tanpa spasi), hemat untuk device kecil."""
    return json.dumps(payload, separators=(",", ":"))
