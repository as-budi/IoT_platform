"""
Status online/offline device (tahap 16, docs/tahap-16-ringkasan-dan-status-device.md).

Dua cara platform tahu device masih hidup:

1. DATA MASUK. save_telemetry() memanggil seen(): device yang offline menjadi
   online. Worker `manage.py housekeeping` memanggil check_timeouts() setiap
   beberapa detik: device yang tidak mengirim data selama DEVICE_OFFLINE_SECONDS
   menjadi offline. Berlaku untuk semua protokol (HTTP, MQTT, CoAP).

2. SESI MQTT + LAST WILL. Device MQTT boleh mengumumkan sesinya di topik
   <prefix>/<token>/status:
       saat tersambung      publish "online"  (retained)
       Last Will            "offline" (retained), dikirim BROKER bila koneksi
                            putus tanpa pamit (listrik mati, Wi-Fi hilang),
                            terdeteksi lewat keepalive (±1,5 × keepalive detik)
   Selama sesi "online", batas waktu tanpa data tidak dipakai (mqtt_connected),
   jadi device yang jarang mengirim data (mis. tiap 30 menit) tidak berkedip
   offline-online.

Setiap perubahan:
    - Device.is_online & status_changed_at diperbarui dengan UPDATE BERSYARAT
      (... WHERE is_online = <lama>), sehingga perubahan yang sama tidak dicatat
      dua kali walaupun dua proses melihatnya bersamaan;
    - satu baris DeviceStatusEvent (riwayat);
    - kabar WebSocket {"type": "status", "online": ..., "reason": ...}.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from realtime import events

from .models import Device, DeviceStatusEvent

ONLINE_WORDS = {"online", "connected", "1", "true"}
OFFLINE_WORDS = {"offline", "disconnected", "lost", "0", "false"}


def _change(device_id, online, reason, now, **extra):
    """Ubah status bila memang berubah. Mengembalikan True bila berubah."""
    changed = Device.objects.filter(pk=device_id, is_online=not online).update(
        is_online=online, status_changed_at=now, **extra
    )
    if extra and not changed:
        Device.objects.filter(pk=device_id).update(**extra)   # mis. mqtt_connected tetap dicatat
    if changed:
        DeviceStatusEvent.objects.create(device_id=device_id, online=online, reason=reason[:200], at=now)
        events.status_changed(device_id, online, reason, now)
    return bool(changed)


def seen(device, now=None, reason="Data received"):
    """Device baru saja mengirim data. Dipanggil oleh api.services.save_telemetry()."""
    if device.is_online:
        return False        # sudah online: tanpa query tambahan per pesan
    device.is_online = True
    return _change(device.pk, True, reason, now or timezone.now())


def check_timeouts(now=None):
    """Device online tanpa data selama DEVICE_OFFLINE_SECONDS -> offline. Mengembalikan jumlahnya."""
    now = now or timezone.now()
    limit = settings.DEVICE_OFFLINE_SECONDS
    cutoff = now - timedelta(seconds=limit)
    silent = Device.objects.filter(is_online=True, mqtt_connected=False).filter(
        Q(last_seen_at__lt=cutoff) | Q(last_seen_at__isnull=True)
    )
    return sum(_change(pk, False, f"No data for {limit} s", now) for pk in silent.values_list("pk", flat=True))


def parse_status(payload):
    """Isi topik status -> True (online) / False (offline) / None (tidak dikenal).

    Menerima teks ("online", "offline") maupun JSON ("online", {"online": false}).
    """
    try:
        text = payload.decode() if isinstance(payload, bytes) else str(payload)
    except UnicodeDecodeError:
        return None
    text = text.strip()
    if text.startswith("{"):
        import json

        try:
            value = json.loads(text).get("online")
        except (ValueError, AttributeError):
            return None
        return value if isinstance(value, bool) else None
    word = text.strip('"').lower()
    if word in ONLINE_WORDS:
        return True
    if word in OFFLINE_WORDS:
        return False
    return None


def mqtt_status(device, online, now=None):
    """Pesan di topik status MQTT (dari device sendiri, atau Last Will dari broker)."""
    now = now or timezone.now()
    if online:
        return _change(device.pk, True, "MQTT session started", now, mqtt_connected=True)
    return _change(device.pk, False, "MQTT session ended (disconnect or Last Will)", now, mqtt_connected=False)
