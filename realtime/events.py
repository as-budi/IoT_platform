"""
Mengirim kabar "ada data baru" ke browser yang sedang membuka halaman device.

Siapa pun yang mengubah data (API HTTP, mqtt_bridge, tombol di dashboard,
simulator) cukup memanggil salah satu fungsi di bawah. Fungsi itu tidak tahu
siapa yang sedang membuka halaman; ia hanya mengirim pesan ke sebuah GROUP
di channel layer:

    save_telemetry() ──► group "device-3" ──► setiap DeviceConsumer yang
                                              berlangganan device 3 ──► browser

Channel layer adalah "papan pengumuman" bersama:
- InMemoryChannelLayer: hanya di dalam SATU proses. Cukup untuk API HTTP dan
  tombol dashboard (keduanya berjalan di proses runserver).
- RedisChannelLayer: lintas proses. WAJIB bila data datang dari proses lain,
  yaitu `manage.py mqtt_bridge`. Aktif bila REDIS_URL diisi di .env.

Bentuk pesan yang sampai di browser (JSON):
    {"type": "telemetry", "points": [{"variable": 7, "key": "temperature",
        "kind": "SENSOR", "boolean": false, "value": 25.1, "ts": 1791436800000}, ...]}
    {"type": "desired", "variable": 9}
    {"type": "command", "id": 12, "status": "SUCCEEDED"}
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)

# "device.event" -> Channels memanggil method DeviceConsumer.device_event().
HANDLER = "device.event"


def group_name(device_id):
    """Nama group untuk satu device. Hanya boleh huruf, angka, '-', '_', '.'."""
    return f"device-{device_id}"


def deliver(device_id, event):
    """Kirim langsung ke channel layer. Kegagalan tidak boleh menggagalkan penyimpanan data."""
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        # group_send adalah fungsi async; async_to_sync memanggilnya dari kode biasa.
        async_to_sync(layer.group_send)(group_name(device_id), {"type": HANDLER, "event": event})
    except Exception:
        # Mis. Redis mati. Data sudah tersimpan; browser akan menangkapnya
        # lewat pemeriksaan cadangan, jadi cukup dicatat di log.
        logger.warning("Could not deliver a live event for device %s", device_id, exc_info=True)


def emit(device_id, event):
    """Kirim SETELAH transaksi database berhasil (commit).

    Bila dikirim sebelum commit, browser bisa menerima kabar tentang data yang
    ternyata batal disimpan, atau data yang belum terlihat oleh query lain.
    """
    transaction.on_commit(lambda: deliver(device_id, event))


def to_ms(dt):
    return int(dt.timestamp() * 1000)


def telemetry_saved(device, rows, protocol=""):
    """`rows` = list (variable, value, ts) yang baru disimpan.

    `protocol` ("HTTP", "MQTT", "COAP", atau kosong) ikut dikirim supaya
    halaman bisa menampilkan "via CoAP" tanpa memuat ulang.
    """
    points = [
        {
            "variable": variable.pk,
            "key": variable.key,
            "kind": variable.kind,
            "boolean": variable.is_boolean,
            "value": value,
            "ts": to_ms(ts),
        }
        for variable, value, ts in rows
    ]
    if points:
        emit(device.pk, {"type": "telemetry", "protocol": protocol, "points": points})


def desired_changed(variable):
    emit(variable.device_id, {"type": "desired", "variable": variable.pk})


def command_changed(command):
    emit(command.device_id, {"type": "command", "id": command.pk, "status": command.status})
