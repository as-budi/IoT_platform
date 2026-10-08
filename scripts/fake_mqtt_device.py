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

Command yang dikenal: ping, reboot, blink {"times": 3}.
Method lain dijawab gagal, supaya status Failed juga bisa dicoba.
"""
import argparse
import hashlib
import json
import math
import os
import random
import threading
import time
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
        else:
            result = {"success": False, "error": f"Unknown method '{method}'."}
        self.publish(f"{self.topic_commands}{command_id}/response", result)

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
    FakeDevice(parser.parse_args()).run()


if __name__ == "__main__":
    main()
