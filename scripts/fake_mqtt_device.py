#!/usr/bin/env python3
"""
Device tiruan yang berkomunikasi lewat MQTT, dua arah:

- MENGIRIM telemetri (temperature, humidity) setiap --interval detik.
- MENERIMA desired state, "menjalankannya", lalu melaporkan keadaan barunya.
- MENERIMA command, lalu mengirim hasilnya.

Seperti fake_device.py, skrip ini tidak memakai Django: ia hanya tahu alamat
broker, prefix topik, dan access token, sama seperti ESP32. Satu-satunya
pustaka tambahan adalah paho-mqtt.

Pemakaian:
    python scripts/fake_mqtt_device.py <ACCESS_TOKEN>
    python scripts/fake_mqtt_device.py <ACCESS_TOKEN> --interval 10

Alamat broker dan prefix dibaca dari .env (MQTT_HOST, MQTT_PORT,
MQTT_TOPIC_PREFIX, ...) bila file itu ada, dan bisa ditimpa lewat argumen
--host, --port, --prefix.

Command yang dikenal: ping, reboot, blink {"times": 3}, dan ota (tahap 9).
Method lain dijawab gagal, supaya status Failed juga bisa dicoba.

Pembaruan firmware (OTA, tahap 9):
    python scripts/fake_mqtt_device.py <TOKEN> --firmware-version 1.0.0 --board sim
Device tiruan ini melaporkan versinya setiap kali tersambung, menerima command
"ota", MENGUNDUH file dari URL di command (HTTP sungguhan), memeriksa MD5,
melaporkan setiap langkah, lalu "restart" dengan versi baru. Untuk mencoba
kegagalan:
    --ota-fail download   unduhan dianggap putus di tengah jalan
    --ota-fail md5        checksum tidak cocok
    --ota-fail boot       firmware baru "tidak menyala": restart dengan versi lama (rollback)
"""
import argparse
import hashlib
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    raise SystemExit("paho-mqtt is not installed. Run: pip install paho-mqtt")

try:
    # Opsional: baca .env project supaya pengaturan broker sama dengan platform.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def now():
    return time.strftime("%H:%M:%S")


class FakeDevice:
    def __init__(self, args):
        self.args = args
        base = f"{args.prefix.strip('/')}/{args.token}"
        self.topic_telemetry = f"{base}/telemetry"
        self.topic_desired = f"{base}/desired"
        self.topic_commands = f"{base}/commands/"   # + <id>
        self.topic_errors = f"{base}/errors"
        self.topic_firmware = f"{base}/firmware"
        self.topic_status = f"{base}/status"
        # Firmware yang "sedang berjalan" di device tiruan ini.
        self.firmware_version = args.firmware_version
        self.seen_updates = set()   # command ota bisa terkirim ulang (QoS 1): jalankan sekali saja
        # Keadaan aktuator "fisik" device ini, mis. {"lamp": 1}.
        self.actuators = {}
        self.lock = threading.Lock()

        # clean_session=False + client_id tetap: broker MENGINGAT langganan
        # device ini. Command QoS 1 yang dikirim saat device mati disimpan
        # broker dan diterima begitu device tersambung lagi.
        client_id = "fake-device-" + hashlib.sha1(args.token.encode()).hexdigest()[:12]
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False
        )
        if args.username:
            self.client.username_pw_set(args.username, args.password)
        if args.tls:
            self.client.tls_set()
        # Last Will (tahap 16): bila koneksi putus tanpa pamit (proses dibunuh,
        # jaringan hilang), BROKER yang mengirim "offline" ke topik status atas
        # nama device ini, setelah ±1,5 × keepalive tanpa kabar.
        # --no-will: coba bedanya (platform baru tahu dari batas waktu tanpa data).
        if not args.no_will:
            self.client.will_set(self.topic_status, "offline", qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # --- koneksi ----------------------------------------------------------------

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"{now()}  broker refused the connection: {reason_code}")
            return
        print(f"{now()}  connected to {self.args.host}:{self.args.port}")
        client.subscribe([
            (self.topic_desired, 1),
            (self.topic_commands + "+", 1),  # + = satu level: hanya nomor command
            (self.topic_errors, 0),
        ])
        # Umumkan sesi (retained: bridge yang baru menyala pun langsung tahu).
        if not self.args.no_will:
            client.publish(self.topic_status, "online", qos=1, retain=True)
        # Setiap kali (re)start: beri tahu platform firmware apa yang berjalan.
        # Laporan inilah yang menyelesaikan pembaruan OTA (UPDATED).
        self.publish(self.topic_firmware, {"version": self.firmware_version, "board": self.args.board})

    # --- pesan masuk --------------------------------------------------------------

    def on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload)
        except ValueError:
            print(f"{now()}  ignored non-JSON message on {message.topic}")
            return
        if message.topic == self.topic_desired:
            self.apply_desired(data, retained=message.retain)
        elif message.topic.startswith(self.topic_commands):
            self.run_command(message.topic[len(self.topic_commands):], data)
        elif message.topic == self.topic_errors:
            print(f"{now()}  PLATFORM REJECTED A MESSAGE: {data.get('error')}")

    def apply_desired(self, desired, retained):
        """Pola 1: jalankan keadaan yang diminta, lalu LAPORKAN keadaan nyata."""
        source = "retained desired state" if retained else "desired state"
        print(f"{now()}  ← {source}: {desired}")
        with self.lock:
            self.actuators.update(desired)
            report = dict(self.actuators)
        # Laporan = telemetri biasa dengan key aktuator. Inilah "reported state".
        self.publish(self.topic_telemetry, report)

    def run_command(self, command_id, command):
        """Pola 2: jalankan aksi sekali jalan, lalu kirim hasilnya."""
        method = command.get("method")
        params = command.get("params") or {}
        print(f"{now()}  ← command {command_id}: {method} {params}")
        if method == "ping":
            result = {"success": True, "pong": int(time.time() * 1000)}
        elif method == "reboot":
            result = {"success": True, "message": "Rebooted (pretend)."}
        elif method == "blink":
            result = {"success": True, "blinked": int(params.get("times", 3))}
        elif method == "ota":
            update_id = params.get("update")
            if params.get("version") == self.firmware_version:
                result = {"success": False, "error": f"Already running {self.firmware_version}."}
            elif update_id in self.seen_updates:
                return  # kiriman ulang dari broker; pembaruan ini sudah ditangani
            else:
                self.seen_updates.add(update_id)
                result = {"success": True, "message": "Update accepted."}
                # Pembaruan berjalan di thread sendiri: callback MQTT tidak boleh diblokir lama.
                threading.Thread(target=self.run_ota, args=(params,), daemon=True).start()
        else:
            result = {"success": False, "error": f"Unknown method '{method}'."}
        self.publish(f"{self.topic_commands}{command_id}/response", result)

    # --- pembaruan firmware (OTA) ------------------------------------------------------

    def ota_report(self, update_id, state, **extra):
        self.publish(self.topic_firmware, {"update": update_id, "state": state, **extra})

    def run_ota(self, params):
        """Meniru yang dilakukan ESP32 dengan HTTPUpdate: unduh, cek MD5, tulis, restart."""
        update_id, fail = params["update"], self.args.ota_fail
        delay = self.args.ota_delay
        self.ota_report(update_id, "DOWNLOADING", progress=0)
        md5 = hashlib.md5()
        try:
            with urllib.request.urlopen(params["url"], timeout=30) as response:
                total = int(response.headers.get("Content-Length") or params.get("size") or 0)
                header_md5 = response.headers.get("x-MD5", "")
                received, last_step = 0, 0
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    md5.update(chunk)
                    received += len(chunk)
                    percent = int(received * 100 / total) if total else 0
                    if fail == "download" and percent >= 50:
                        raise OSError("connection lost at 50 %")
                    if percent - last_step >= 25 or percent == 100:
                        last_step = percent
                        self.ota_report(update_id, "DOWNLOADING", progress=percent)
                        time.sleep(delay)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            self.ota_report(update_id, "FAILED", error=f"Download failed: {reason}")
            return
        self.ota_report(update_id, "DOWNLOADED")
        time.sleep(delay)
        digest = md5.hexdigest() if fail != "md5" else "0" * 32
        if digest != params.get("md5") or (header_md5 and digest != header_md5):
            self.ota_report(update_id, "FAILED", error="MD5 mismatch")
            return
        self.ota_report(update_id, "VERIFIED")
        time.sleep(delay)
        self.ota_report(update_id, "UPDATING")
        time.sleep(delay)
        # "Restart": putuskan koneksi, ganti versi, sambung lagi. Setelah tersambung,
        # on_connect() melaporkan versi baru. --ota-fail boot meniru rollback.
        print(f"{now()}  restarting into firmware {params['version']} …")
        self.client.disconnect()
        time.sleep(2)
        if fail != "boot":
            self.firmware_version = params["version"]
        else:
            print(f"{now()}  new firmware did not start: rolled back to {self.firmware_version}")
        self.client.reconnect()

    # --- pesan keluar ---------------------------------------------------------------

    def publish(self, topic, payload):
        text = json.dumps(payload)
        self.client.publish(topic, text, qos=1)
        print(f"{now()}  → {topic}  {text}")

    def send_telemetry(self):
        phase = time.time() / 600
        with self.lock:
            payload = {
                "temperature": round(26 + 2 * math.sin(phase) + random.uniform(-0.3, 0.3), 2),
                "humidity": round(60 + 8 * math.cos(phase) + random.uniform(-1, 1), 1),
                **self.actuators,
            }
        self.publish(self.topic_telemetry, payload)

    def run(self):
        print(f"{now()}  connecting to {self.args.host}:{self.args.port} …")
        self.client.connect(self.args.host, self.args.port, keepalive=60)
        self.client.loop_start()
        try:
            while True:
                if self.client.is_connected():
                    self.send_telemetry()
                time.sleep(self.args.interval)
        except KeyboardInterrupt:
            print("Stopped.")
        finally:
            # Berhenti dengan pamit: Last Will TIDAK dikirim broker pada disconnect
            # yang rapi, jadi status "offline" dikirim sendiri.
            if not self.args.no_will and self.client.is_connected():
                self.client.publish(self.topic_status, "offline", qos=1, retain=True).wait_for_publish(2)
            self.client.disconnect()
            self.client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description="Fake device that talks MQTT to the IoT platform.")
    parser.add_argument("token", help="Device access token")
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", "broker.emqx.io"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--prefix", default=os.getenv("MQTT_TOPIC_PREFIX", "django-iot-demo"))
    parser.add_argument("--username", default=os.getenv("MQTT_USERNAME", ""))
    parser.add_argument("--password", default=os.getenv("MQTT_PASSWORD", ""))
    parser.add_argument("--tls", action="store_true", default=os.getenv("MQTT_TLS") == "True")
    parser.add_argument("--interval", type=float, default=10, help="Seconds between telemetry messages")
    parser.add_argument("--firmware-version", default="1.0.0", help="Firmware version this device starts with")
    parser.add_argument("--board", default="sim", help="Board reported to the platform (sim, esp32, ...)")
    parser.add_argument("--ota-fail", choices=["download", "md5", "boot"], help="Make the next update fail")
    parser.add_argument("--ota-delay", type=float, default=0.7, help="Seconds between update steps")
    parser.add_argument("--no-will", action="store_true",
                        help="Do not announce the MQTT session / Last Will on the status topic")
    FakeDevice(parser.parse_args()).run()


if __name__ == "__main__":
    main()
