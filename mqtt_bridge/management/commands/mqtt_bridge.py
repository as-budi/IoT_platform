"""
Perintah kustom:  python manage.py mqtt_bridge

Jembatan antara broker MQTT dan database platform. Jalankan di terminal
TERPISAH dari `runserver`, dan biarkan menyala.

Mengapa proses terpisah? Server web Django bekerja per-request: menerima
request, menjawab, selesai. MQTT butuh koneksi yang TERUS tersambung ke broker
untuk mendengarkan pesan kapan pun datang. Karena itu ia dijalankan sebagai
proses "worker" tersendiri, seperti yang juga dilakukan platform IoT lain.

    device ──publish──► broker ──► mqtt_bridge ──► database ──► dashboard
    device ◄─subscribe─ broker ◄── mqtt_bridge ◄── database ◄── tombol ON / command

Dua "jalur" berjalan bersamaan:
- Thread jaringan paho-mqtt: menerima pesan masuk -> handlers.handle_message().
- Thread utama: setiap --interval detik memeriksa outbox (desired state dan
  command yang belum terkirim) -> publish.

Pengaturan broker dibaca dari .env (lihat config/settings.py, bagian MQTT).
"""
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from mqtt_bridge import topics
from mqtt_bridge.handlers import handle_message
from mqtt_bridge.outbox import Outbox, encode

# Batas waktu menunggu broker mengonfirmasi pesan QoS 1 (PUBACK).
PUBLISH_TIMEOUT = 5


class Command(BaseCommand):
    help = "Connect to the MQTT broker: store incoming telemetry and send desired states and commands."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval", type=float, default=1.0,
            help="Seconds between checks for desired states and commands to send (default 1).",
        )

    def log(self, message, style=None):
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.stdout.write(style(line) if style else line)

    def handle(self, *args, **options):
        # Diimpor di sini (bukan di atas file) supaya `manage.py test` dan
        # perintah lain tetap jalan walaupun paho-mqtt belum dipasang.
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise CommandError("paho-mqtt is not installed. Run: pip install paho-mqtt")

        prefix = settings.MQTT_TOPIC_PREFIX
        outbox = Outbox(prefix)
        # Penanda dari thread jaringan ke thread utama: "baru saja tersambung".
        just_connected = threading.Event()

        # CallbackAPIVersion.VERSION2 = bentuk callback paho-mqtt 2.x.
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.MQTT_CLIENT_ID,  # kosong = dibuat acak
            clean_session=True,
        )
        if settings.MQTT_USERNAME:
            client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
        if settings.MQTT_TLS:
            client.tls_set()  # memakai daftar sertifikat CA bawaan sistem
        # Bila koneksi putus, paho menyambung ulang sendiri, dengan jeda 1..30 detik.
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        # --- callback: dipanggil oleh thread jaringan paho -----------------------

        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                self.log(f"Broker refused the connection: {reason_code}", self.style.ERROR)
                return
            # Subscribe dilakukan di sini (bukan sekali di awal), supaya
            # langganan dipasang ulang setiap kali tersambung kembali.
            client.subscribe([(topic, 1) for topic in topics.subscriptions(prefix)])
            just_connected.set()
            self.log(f"Connected to {settings.MQTT_HOST}:{settings.MQTT_PORT}", self.style.SUCCESS)
            for topic in topics.subscriptions(prefix):
                self.log(f"Listening on {topic}")

        def on_disconnect(client, userdata, flags, reason_code, properties):
            self.log(f"Disconnected ({reason_code}); reconnecting…", self.style.WARNING)

        def on_message(client, userdata, message):
            # Proses yang berjalan lama: koneksi database bisa diputus server
            # MySQL setelah lama diam ("MySQL server has gone away").
            # close_old_connections() membuang koneksi yang sudah tidak layak,
            # sehingga query berikutnya membuka koneksi baru.
            close_old_connections()
            try:
                result = handle_message(message.topic, message.payload, prefix)
            except Exception as exc:  # satu pesan yang aneh tidak boleh mematikan bridge
                self.log(f"Error while handling {message.topic}: {exc!r}", self.style.ERROR)
                return
            if result is None:
                return
            who = result.device.name if result.device else "unknown device"
            if result.ok:
                self.log(f"{who}: {result.message}")
                return
            self.log(f"{who}: rejected — {result.message}", self.style.WARNING)
            if result.device is not None:
                # Beri tahu device. Tidak menunggu konfirmasi (jangan memblokir
                # thread jaringan dari dalam callback-nya sendiri).
                client.publish(
                    topics.errors(prefix, result.device.access_token),
                    encode({"error": result.message, "topic": message.topic}),
                    qos=0,
                )

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        # --- publish dari thread utama -----------------------------------------

        def publish(topic, payload, retain):
            if not client.is_connected():
                return False
            info = client.publish(topic, encode(payload), qos=1, retain=retain)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return False
            # QoS 1: tunggu sampai broker menjawab "sudah kuterima" (PUBACK).
            # Baru setelah itu status command diubah menjadi SENT.
            try:
                info.wait_for_publish(timeout=PUBLISH_TIMEOUT)
            except (RuntimeError, ValueError):
                return False
            if not info.is_published():
                return False
            self.log(f"→ {topic}  {encode(payload)}")
            return True

        # --- mulai ---------------------------------------------------------------

        self.log(f"Connecting to {settings.MQTT_HOST}:{settings.MQTT_PORT} (prefix '{prefix}')…")
        client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=settings.MQTT_KEEPALIVE)
        client.loop_start()  # menjalankan thread jaringan di latar belakang
        try:
            while True:
                if just_connected.is_set():
                    just_connected.clear()
                    outbox.reset()  # kirim ulang semua desired state setelah tersambung
                if client.is_connected():
                    close_old_connections()
                    try:
                        outbox.flush(publish)
                    except Exception as exc:
                        self.log(f"Error while sending: {exc!r}", self.style.ERROR)
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.log("Stopping…")
        finally:
            client.disconnect()
            client.loop_stop()
