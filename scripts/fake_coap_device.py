#!/usr/bin/env python3
"""
Device tiruan yang berkomunikasi lewat CoAP, dua arah:

- MENGIRIM telemetri (temperature, humidity + keadaan aktuator) setiap --interval detik.
  Kiriman berkala ini sekaligus menjadi "keepalive": menjaga jalur UDP di router/NAT
  tetap terbuka supaya kabar Observe dari server bisa masuk.
- MENGAMATI (Observe) desired state: begitu tombol ON/OFF ditekan di dashboard,
  server mengirim keadaan baru; device "menjalankannya" lalu melapor balik.
- MENGAMATI daftar command: menjalankan command baru, lalu mengirim hasilnya.

Seperti fake_device.py dan fake_mqtt_device.py, skrip ini tidak memakai Django:
ia hanya tahu alamat server dan access token. Pustaka tambahan: aiocoap.

Pemakaian:
    python scripts/fake_coap_device.py <ACCESS_TOKEN>
    python scripts/fake_coap_device.py <ACCESS_TOKEN> --host 192.168.1.10 --interval 5

Alamat server dibaca dari .env (COAP_PUBLIC_HOST, COAP_PORT) bila ada.
Command yang dikenal: ping, reboot, blink {"times": 3}. Method lain dijawab gagal.
"""
import argparse
import asyncio
import json
import math
import os
import random
import time
from pathlib import Path

try:
    import aiocoap
except ImportError:
    raise SystemExit("aiocoap is not installed. Run: pip install aiocoap")

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def now():
    return time.strftime("%H:%M:%S")


class FakeCoapDevice:
    def __init__(self, args):
        self.args = args
        self.base = f"coap://{args.host}:{args.port}/api/v1/{args.token}"
        self.actuators = {}        # keadaan aktuator "fisik", mis. {"pump": 1}
        self.done_commands = set() # id command yang sudah dijalankan (cegah dobel)
        self.context = None

    async def request(self, method, path, payload=None, observe=False):
        message = aiocoap.Message(code=method, uri=f"{self.base}/{path}")
        if payload is not None:
            message.payload = json.dumps(payload).encode()
            message.opt.content_format = 50  # application/json
        if observe:
            message.opt.observe = 0
        return self.context.request(message)

    async def post(self, path, payload):
        response = await (await self.request(aiocoap.POST, path, payload)).response
        print(f"{now()}  → POST {path} {json.dumps(payload)}  ←  {response.code} {response.payload.decode()}")
        return response

    # --- telemetri (juga berfungsi sebagai keepalive) ---------------------------

    async def telemetry_loop(self):
        while True:
            phase = time.time() / 600
            payload = {
                "temperature": round(26 + 2 * math.sin(phase) + random.uniform(-0.3, 0.3), 2),
                "humidity": round(60 + 8 * math.cos(phase) + random.uniform(-1, 1), 1),
                **self.actuators,
            }
            try:
                await self.post("telemetry", payload)
            except Exception as exc:
                print(f"{now()}  cannot reach the server: {exc}")
            await asyncio.sleep(self.args.interval)

    # --- Observe: daftar ulang otomatis bila terputus ------------------------------

    async def observe_loop(self, path, handler):
        while True:
            try:
                pending = await self.request(aiocoap.GET, path, observe=True)
                first = await pending.response
                if not first.code.is_successful():
                    print(f"{now()}  observe {path} refused: {first.code} {first.payload.decode()}")
                    await asyncio.sleep(10)
                    continue
                print(f"{now()}  observing {path}")
                await handler(json.loads(first.payload))
                async for notification in pending.observation:
                    await handler(json.loads(notification.payload))
                print(f"{now()}  observation of {path} ended; registering again")
            except Exception as exc:
                print(f"{now()}  observe {path} failed ({exc}); retrying in 5 s")
                await asyncio.sleep(5)

    async def on_desired(self, desired):
        """Pola 1: jalankan keadaan yang diminta, lalu LAPORKAN keadaan nyata."""
        if not desired or desired == {k: self.actuators.get(k) for k in desired}:
            return
        print(f"{now()}  ← desired state {desired}")
        self.actuators.update(desired)
        await self.post("telemetry", dict(self.actuators))

    async def on_commands(self, data):
        """Pola 2: jalankan command baru, lalu kirim hasilnya."""
        for command in data.get("commands", []):
            if command["id"] in self.done_commands:
                continue  # sudah dijalankan; server masih mengirimnya sampai hasil diterima
            self.done_commands.add(command["id"])
            method, params = command.get("method"), command.get("params") or {}
            print(f"{now()}  ← command {command['id']}: {method} {params}")
            if method == "ping":
                result = {"success": True, "pong": int(time.time() * 1000)}
            elif method == "reboot":
                result = {"success": True, "message": "Rebooted (pretend)."}
            elif method == "blink":
                result = {"success": True, "blinked": int(params.get("times", 3))}
            else:
                result = {"success": False, "error": f"Unknown method '{method}'."}
            await self.post(f"commands/{command['id']}/response", result)

    async def run(self):
        self.context = await aiocoap.Context.create_client_context()
        print(f"{now()}  talking to {self.base.rsplit('/', 1)[0]}/…")
        await asyncio.gather(
            self.telemetry_loop(),
            self.observe_loop("desired", self.on_desired),
            self.observe_loop("commands", self.on_commands),
        )


def main():
    parser = argparse.ArgumentParser(description="Fake device that talks CoAP to the IoT platform.")
    parser.add_argument("token", help="Device access token")
    parser.add_argument("--host", default=os.getenv("COAP_PUBLIC_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("COAP_PORT", "5683")))
    parser.add_argument("--interval", type=float, default=10, help="Seconds between telemetry messages")
    try:
        asyncio.run(FakeCoapDevice(parser.parse_args()).run())
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
