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
from datetime import timedelta

from devices.control import command_payload, plain
from devices.models import Command, CommandStatus, Variable
from django.conf import settings
from django.utils import timezone

from . import topics

# Jeda setelah device menjadi online sebelum command dikirim: device yang baru tersambung
# mengirim "online" dan subscribe hampir bersamaan; tanpa jeda, command bisa tiba sebelum
# langganannya terpasang dan hilang (clean session).
ONLINE_GRACE = timedelta(seconds=2)


class Outbox:
    def __init__(self, prefix, desired_mode=None):
        self.prefix = prefix
        # "changes" (bawaan): pesan desired hanya berisi aktuator yang berubah;
        # "all": selalu semua aktuator device (perilaku lama). Lihat flush_desired().
        self.desired_mode = desired_mode or getattr(settings, "MQTT_DESIRED_MESSAGE", "changes")
        # device_id -> apa yang terakhir dikirim: {"token", "session", "versions": {key: desired_version}}.
        # Disimpan di memori: bila bridge di-restart, semua dikirim ulang.
        # Itu aman karena desired state bersifat "keadaan", bukan "aksi".
        self.sent_desired = {}

    def reset(self):
        """Dipanggil setiap kali (ulang) tersambung ke broker: kirim ulang semuanya."""
        self.sent_desired.clear()

    def flush(self, publish):
        """Kirim semua yang tertunda. `publish(topic, payload, retain, expiry=None)` -> True bila berhasil.
        `expiry` = Message Expiry Interval MQTT 5 dalam detik (hanya command).

        Mengembalikan (jumlah pesan desired, jumlah command) yang terkirim.
        """
        desired, commands = self.flush_desired(publish), self.flush_commands(publish)
        self.flush_rules(publish)
        return desired, commands

    # --- Pola 1: desired state ----------------------------------------------

    def flush_desired(self, publish):
        """Pesan desired state per device:

            <prefix>/<token>/desired   {"lamp": 1}                    (MQTT_DESIRED_MESSAGE=changes)
            <prefix>/<token>/desired   {"lamp": 1, "fan_speed": 40}   (semua aktuator)

        Mode "changes" (bawaan): saat pengguna mengubah satu aktuator, hanya aktuator itu
        yang dikirim; aktuator lain tidak "diperintah ulang". SEMUA aktuator dikirim bila:
        - device ini belum pernah dikirimi sejak bridge (ulang) tersambung,
        - access token device diganti,
        - device baru saja online (sesi baru, setelah ONLINE_GRACE): device yang baru
          menyala mendapat keadaan lengkap, walaupun pesan retained di broker hanya
          berisi perubahan terakhir.
        Firmware cukup membaca kunci yang ADA di pesan (kunci yang tidak ada = tidak berubah).

        Mode "all": setiap perubahan mengirim semua aktuator (perilaku sebelum tahap 26).

        Pesan dikirim dengan flag RETAIN: broker menyimpan pesan terakhir di
        topik ini, dan langsung memberikannya kepada device yang baru tersambung.
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

        now = timezone.now()
        sent = 0
        for device, variables in by_device.items():
            versions = {v.key: v.desired_version for v in variables}
            entry = self.sent_desired.get(device.pk)
            # Sesi online = waktu status terakhir berubah menjadi online.
            session = device.status_changed_at if device.is_online else None
            if entry is None or entry["token"] != device.access_token or self.desired_mode == "all":
                keys = set(versions)
            elif device.is_online and session != entry["session"]:
                # Baru online: tunggu sebentar supaya device sempat subscribe, lalu kirim semua.
                if session and session > now - ONLINE_GRACE:
                    continue
                keys = set(versions)
            else:
                keys = {key for key, version in versions.items() if entry["versions"].get(key) != version}
            if self.desired_mode == "all" and entry and entry["token"] == device.access_token \
                    and entry["versions"] == versions:
                continue   # mode "all": tidak ada yang berubah
            if not keys:
                continue
            payload = {v.key: plain(v.desired_value) for v in variables if v.key in keys}
            if not publish(topics.desired(self.prefix, device.access_token), payload, retain=True):
                break  # koneksi putus; coba lagi di putaran berikutnya
            self.sent_desired[device.pk] = {
                "token": device.access_token,
                # Selama offline, sesi lama diingat: begitu online lagi (sesi berbeda) semua dikirim.
                "session": session if device.is_online else (entry["session"] if entry else None),
                "versions": versions,
            }
            sent += 1
        return sent

    # --- Pola 2: command ------------------------------------------------------

    def flush_commands(self, publish):
        """Kirim command berstatus QUEUED ke device yang ONLINE, lalu ubah statusnya menjadi SENT.

            <prefix>/<token>/commands/12
            {"id": 12, "method": "reboot", "params": {}, "expires_at": 1762412345, "ttl": 297}

        Command TIDAK di-retain: aksi sekali jalan tidak boleh diulang hanya
        karena device tersambung ulang.

        Device yang offline (Device.is_online False) tidak dikirimi: device memakai clean session,
        jadi pesan yang dikirim saat ia offline hilang begitu saja padahal statusnya sudah SENT.
        Command tetap QUEUED dan dikirim begitu device online lagi (selama belum kedaluwarsa).
        Setelah device online, ditunggu ONLINE_GRACE detik supaya device sempat subscribe.

        Pesan diberi batas umur MQTT 5 (Message Expiry Interval = sisa detik): broker membuang
        pesan yang tertahan di antrean (device dengan persistent session) setelah batas itu.
        """
        # Command kedaluwarsa tidak boleh dikirim sama sekali.
        Command.objects.expire_due()
        now = timezone.now()
        queued = (
            Command.objects.filter(status=CommandStatus.QUEUED, device__is_active=True, device__is_online=True)
            .exclude(device__status_changed_at__gt=now - ONLINE_GRACE)
            .select_related("device")
            .order_by("created_at", "pk")
        )
        sent = 0
        for command in queued:
            payload = command_payload(command, now)
            if payload["ttl"] < 1:
                continue                     # kurang dari 1 detik lagi: biarkan kedaluwarsa
            topic = topics.command(self.prefix, command.device.access_token, command.pk)
            if not publish(topic, payload, retain=False, expiry=payload["ttl"]):
                break
            command.mark_sent()
            sent += 1
        return sent


    # --- JSON aturan (tombol Publish di halaman aturan) ----------------------------------

    def flush_rules(self, publish):
        """Kirim RuleDelivery berstatus QUEUED, retained, ke <prefix>/<token>/rules/<id>.

        payload berupa teks JSON yang sudah jadi (dikirim apa adanya, urutan kunci tetap).
        payload None = pesan retained dihapus (Withdraw atau aturan dihapus): broker menerima
        pesan kosong retained sebagai "hapus pesan yang tersimpan di topik ini".
        """
        from rules.models import RuleDelivery

        queued = RuleDelivery.objects.filter(status=RuleDelivery.Status.QUEUED).order_by("pk")
        sent = 0
        for delivery in queued:
            if not publish(delivery.topic, delivery.payload, retain=True):
                break
            delivery.status = RuleDelivery.Status.SENT if delivery.payload is not None else RuleDelivery.Status.CLEARED
            delivery.sent_at = timezone.now()
            delivery.save(update_fields=["status", "sent_at"])
            sent += 1
        return sent


def encode(payload):
    """dict -> teks JSON yang ringkas (tanpa spasi), hemat untuk device kecil."""
    return json.dumps(payload, separators=(",", ":"))
