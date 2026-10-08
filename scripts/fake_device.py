#!/usr/bin/env python3
"""
Device tiruan: mengirim telemetri ke platform lewat HTTP, persis seperti ESP32.

Skrip ini sengaja TIDAK memakai Django maupun pustaka tambahan (hanya pustaka
bawaan Python), supaya jelas bahwa ia adalah "klien luar" yang hanya tahu
alamat server dan access token — sama seperti device sungguhan.

Pemakaian:
    python scripts/fake_device.py <ACCESS_TOKEN>
    python scripts/fake_device.py <ACCESS_TOKEN> --interval 5 --count 10
    python scripts/fake_device.py <ACCESS_TOKEN> --server http://192.168.1.10:8000

Access token bisa disalin dari halaman device (bagian "Connect a device").
"""
import argparse
import json
import math
import random
import time
import urllib.error
import urllib.request


def send(server, token, payload):
    """Kirim satu payload JSON. Mengembalikan (kode status, isi balasan)."""
    url = f"{server.rstrip('/')}/api/v1/{token}/telemetry"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        # Kode 4xx/5xx: server menjawab, tetapi menolak. Isi balasannya
        # memuat alasan penolakan dalam JSON.
        return exc.code, exc.read().decode()


def main():
    parser = argparse.ArgumentParser(description="Send fake telemetry to the IoT platform.")
    parser.add_argument("token", help="Device access token")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="Platform address")
    parser.add_argument("--interval", type=float, default=5, help="Seconds between messages")
    parser.add_argument("--count", type=int, default=0, help="Number of messages (0 = until Ctrl+C)")
    args = parser.parse_args()

    sent = 0
    try:
        while args.count == 0 or sent < args.count:
            phase = time.time() / 600  # gelombang pelan, periode ~1 jam
            payload = {
                "temperature": round(26 + 2 * math.sin(phase) + random.uniform(-0.3, 0.3), 2),
                "humidity": round(60 + 8 * math.cos(phase) + random.uniform(-1, 1), 1),
            }
            status, body = send(args.server, args.token, payload)
            print(f"{time.strftime('%H:%M:%S')}  {status}  sent {payload}  ->  {body}")
            sent += 1
            if args.count == 0 or sent < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped.")
    except urllib.error.URLError as exc:
        print(f"Cannot reach the server at {args.server}: {exc.reason}")


if __name__ == "__main__":
    main()
