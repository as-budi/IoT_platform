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
            from paho.mqtt.packettypes import PacketTypes
            from paho.mqtt.properties import Properties
        except ImportError:
            raise CommandError("paho-mqtt is not installed. Run: pip install paho-mqtt")

        prefix = settings.MQTT_TOPIC_PREFIX
        outbox = Outbox(prefix)
        if "InMemory" in settings.CHANNEL_LAYERS["default"]["BACKEND"]:
            # Kabar WebSocket dari proses ini tidak bisa sampai ke proses runserver
            # tanpa Redis (lihat docs/tahap-04-websocket.md).
            self.log(
                "REDIS_URL is not set: open dashboards will show MQTT data only "
                "through their 30-second fallback check.",
                self.style.WARNING,
            )
        # Penanda dari thread jaringan ke thread utama: "baru saja tersambung".
        just_connected = threading.Event()

        # CallbackAPIVersion.VERSION2 = bentuk callback paho-mqtt 2.x.
        # MQTT 5 (bawaan, .env MQTT_PROTOCOL=5): command dikirim dengan Message Expiry Interval,
        # sehingga broker membuang command yang tertahan di antrean setelah kedaluwarsa.
        # Device boleh tetap MQTT 3.1.1; batas umur pesan dijalankan oleh broker.
        use_v5 = settings.MQTT_PROTOCOL == "5"
        if use_v5:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=settings.MQTT_CLIENT_ID,  # kosong = dibuat acak
                protocol=mqtt.MQTTv5,
            )
        else:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=settings.MQTT_CLIENT_ID,
                clean_session=True,
                protocol=mqtt.MQTTv311,
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
                result = handle_message(message.topic, message.payload, prefix, retained=bool(message.retain))
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

        def publish(topic, payload, retain, expiry=None):
            if not client.is_connected():
                return False
            # payload None = pesan kosong (menghapus pesan retained di topik itu).
            # str = teks JSON yang sudah jadi (JSON aturan): dikirim apa adanya, urutan kunci tetap.
            if payload is None:
                data = b""
            elif isinstance(payload, str):
                data = payload.encode("utf-8")
            else:
                data = encode(payload)
            properties = None
            if use_v5 and expiry:
                properties = Properties(PacketTypes.PUBLISH)
                properties.MessageExpiryInterval = int(expiry)
            info = client.publish(topic, data, qos=1, retain=retain, properties=properties)
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
            shown = "(empty: retained message removed)" if payload is None else encode(payload)
            self.log(f"→ {topic}  {shown[:300]}" + (f"  (expires in {int(expiry)} s)" if expiry else ""))
            return True

        # --- mulai ---------------------------------------------------------------

        self.log(f"Connecting to {settings.MQTT_HOST}:{settings.MQTT_PORT} with MQTT {'5' if use_v5 else '3.1.1'} "
                 f"(prefix '{prefix}')…")
        if use_v5:
            client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=settings.MQTT_KEEPALIVE,
                                 clean_start=True)
        else:
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
