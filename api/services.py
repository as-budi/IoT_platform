"""Menyimpan telemetri yang sudah divalidasi ke database."""
import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from devices import presence
from devices.models import Device, Telemetry, Variable
from realtime import events
from rules import queue as rule_queue

from .payload import PayloadError


# Kelonggaran batas interval: device yang mengirim tepat setiap 10 detik bisa tiba 9,8 detik
# setelah pesan sebelumnya (antrean jaringan, Wi-Fi). Tanpa kelonggaran, setiap pesan kedua
# ditolak dan device itu efektif mengirim setiap 20 detik.
INTERVAL_SLACK_SECONDS = 1


class TelemetryTooFrequent(PayloadError):
    """Telemetri datang lebih cepat dari TELEMETRY_MIN_INTERVAL_SECONDS.

    Turunan PayloadError: pesannya dikirim balik ke device seperti kesalahan payload
    (MQTT: topik errors). HTTP menjawab 429 dan CoAP 4.29 dengan `retry_after` detik.
    """

    def __init__(self, interval, retry_after):
        self.interval = interval
        self.retry_after = retry_after
        super().__init__(
            f"Telemetry is limited to one message every {interval} s per device; "
            f"try again in {retry_after} s."
        )


def only_actuators(device, keys):
    """True bila SEMUA key adalah variabel aktuator device yang sudah ada.

    Laporan seperti itu adalah "reported state" setelah device menjalankan desired
    state (template firmware: reportLamp()). Laporan itu harus selalu sampai, supaya
    dashboard tahu aktuatornya sudah berubah, jadi tidak ikut batas interval.
    """
    keys = set(keys)
    actuators = Variable.objects.filter(device=device, key__in=keys, kind=Variable.Kind.ACTUATOR)
    return actuators.count() == len(keys)


def claim_interval(device, rows, now):
    """Catat telemetri yang diterima, atau tolak bila terlalu cepat (TelemetryTooFrequent).

    Satu UPDATE bersyarat, jadi aman walaupun pesan dari satu device datang bersamaan
    ke proses berbeda (web untuk HTTP, mqtt_bridge, coap_server):
        UPDATE devices_device SET last_telemetry_at = now
        WHERE id = 7 AND (last_telemetry_at IS NULL OR last_telemetry_at <= now - 9 s)
    1 baris berubah = diterima; 0 = pesan sebelumnya terlalu baru. Dipanggil di DALAM
    transaksi save_telemetry(): bila penyimpanan gagal, catatan waktunya ikut batal.
    """
    interval = settings.TELEMETRY_MIN_INTERVAL_SECONDS
    if interval <= 0 or only_actuators(device, (key for key, _, _ in rows)):
        return
    allowed_after = now - timedelta(seconds=max(0, interval - INTERVAL_SLACK_SECONDS))
    accepted = (
        Device.objects.filter(pk=device.pk)
        .filter(Q(last_telemetry_at__isnull=True) | Q(last_telemetry_at__lte=allowed_after))
        .update(last_telemetry_at=now)
    )
    if not accepted:
        last = Device.objects.filter(pk=device.pk).values_list("last_telemetry_at", flat=True).first() or now
        wait = interval - (now - last).total_seconds()
        raise TelemetryTooFrequent(interval, max(1, math.ceil(wait)))


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

    Dari device (`protocol` diisi), telemetri dibatasi satu pesan setiap
    TELEMETRY_MIN_INTERVAL_SECONDS (claim_interval); pesan yang terlalu cepat
    melempar TelemetryTooFrequent dan tidak disimpan sama sekali.

    Mengembalikan daftar key yang baru dibuat.
    """
    created_keys = []
    with transaction.atomic():
        if protocol:
            claim_interval(device, rows, timezone.now())
        variables = {}
        # dict.fromkeys: daftar key unik dengan urutan tetap.
        for key in dict.fromkeys(key for key, _, _ in rows):
            variable, created = Variable.objects.get_or_create(device=device, key=key)
            variables[key] = variable
            if created:
                created_keys.append(key)
        # Kategori: nama atau kode -> kode 0, 1, ... Kesalahan membatalkan seluruh
        # transaksi (termasuk variabel yang baru dibuat): payload diterima utuh atau ditolak utuh.
        rows = [(key, encode_value(variables[key], key, value, created_keys), ts) for key, value, ts in rows]
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


def encode_value(variable, key, value, created_keys=()):
    """Nilai dari payload -> angka yang disimpan (lihat Variable.encode)."""
    if not isinstance(value, str) and not variable.is_categorical:
        return value

    try:
        return variable.encode(value)
    except ValueError as exc:
        hint = ""
        if isinstance(value, str) and not variable.is_categorical:
            hint = (" To send names, set the variable's value type to Categories first." if key not in created_keys
                    else " To send names, add the variable with the value type Categories first.")
        raise PayloadError(f"value of '{key}' {exc}.{hint}")
