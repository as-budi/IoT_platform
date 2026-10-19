"""Menyimpan telemetri yang sudah divalidasi ke database."""
from django.db import transaction
from django.utils import timezone

from devices import presence
from devices.models import Device, Telemetry, Variable
from realtime import events
from rules import queue as rule_queue


def save_telemetry(device, rows, protocol=""):
    """Simpan baris (key, value, ts) milik `device`.

    Key yang belum dikenal otomatis dibuat sebagai variabel SENSOR baru
    (mirip ThingsBoard), sehingga device baru bisa langsung mengirim data
    tanpa harus mendaftarkan variabelnya satu per satu.

    Untuk variabel AKTUATOR, telemetri ini adalah "reported state": laporan
    keadaan sebenarnya dari device. Karena itu status desired/reported di
    dashboard otomatis ikut diperbarui.

    Setelah tersimpan, browser yang sedang membuka halaman device ini diberi
    kabar lewat WebSocket (realtime/events.py), dan data diteruskan ke rule
    engine (rules/queue.py). Karena HTTP API, mqtt_bridge, coap_server, dan
    simulator sama-sama menyimpan lewat fungsi ini, semuanya ikut tampil
    seketika di dashboard dan ikut dinilai oleh aturan.

    `protocol` (Device.Protocol.HTTP/MQTT/COAP) dicatat di device sebagai
    jalur pengiriman terakhir; kosong untuk sumber lain (simulator).

    Mengembalikan daftar key yang baru dibuat.
    """
    created_keys = []
    with transaction.atomic():
        variables = {}
        # dict.fromkeys: daftar key unik dengan urutan tetap.
        for key in dict.fromkeys(key for key, _, _ in rows):
            variable, created = Variable.objects.get_or_create(device=device, key=key)
            variables[key] = variable
            if created:
                created_keys.append(key)
        Telemetry.objects.bulk_create(
            [Telemetry(variable=variables[key], value=value, ts=ts) for key, value, ts in rows]
        )
        # Catat kapan dan lewat jalur apa device terakhir mengirim data.
        # update() langsung ke database: tidak menimpa kolom device lain.
        changes = {"last_seen_at": timezone.now()}
        if protocol:
            changes["last_protocol"] = protocol
        Device.objects.filter(pk=device.pk).update(**changes)
        # Tahap 16: device yang tadinya offline kini online (devices/presence.py).
        presence.seen(device, changes["last_seen_at"])
        # Dikirim setelah transaksi selesai (lihat events.emit).
        saved = [(variables[key], value, ts) for key, value, ts in rows]
        events.telemetry_saved(device, saved, protocol)
        # Serahkan juga ke rule engine (setelah commit; lihat rules/queue.py).
        rule_queue.publish(rule_queue.telemetry_message(device, saved, protocol))
    return created_keys
