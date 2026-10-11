"""
Perintah kustom:  python manage.py coap_server

Server CoAP (UDP, port 5683) untuk device. Jalankan di terminal TERPISAH dari
`runserver`, seperti `mqtt_bridge`: server web Daphne melayani HTTP dan
WebSocket (TCP), sedangkan CoAP berjalan di atas UDP.

    ESP32 ──CoAP/UDP──► coap_server ──► database ──► dashboard (WebSocket)

Pengaturan di .env (lihat config/settings.py, bagian CoAP):
    COAP_BIND, COAP_PORT, COAP_PUBLIC_HOST, COAP_OBSERVE_INTERVAL
"""
import asyncio
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the CoAP server for devices (telemetry, desired state, commands)."

    def log(self, message, style=None):
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.stdout.write(style(line) if style else line)

    def log_request(self, method, path, reply, remote):
        # Token disamarkan di log: cukup 4 karakter awal untuk mengenali device.
        shown = list(path)
        if shown:
            shown[0] = shown[0][:4] + "…"
        who = reply.device.name if reply.device else "unknown device"
        text = f"{method} /api/v1/{'/'.join(shown)} -> {reply.code} ({who})"
        if reply.ok:
            self.log(text)
        else:
            self.log(f"{text}: {reply.body.get('error', '')}", self.style.WARNING)

    def handle(self, *args, **options):
        # Diimpor di sini supaya perintah lain dan tes tetap jalan tanpa aiocoap.
        try:
            import aiocoap  # noqa: F401
        except ImportError:
            raise CommandError("aiocoap is not installed. Run: pip install aiocoap")
        try:
            asyncio.run(self.serve())
        except KeyboardInterrupt:
            self.log("Stopped.")

    async def serve(self):
        from coap_gateway.resources import build_site, start_servers

        site, api = build_site(on_request=self.log_request)
        port = settings.COAP_PORT
        contexts, hosts = await start_servers(site, port, settings.COAP_BIND, settings.COAP_PUBLIC_HOST)
        if not contexts:
            raise CommandError(f"Cannot open UDP port {port}. Is another coap_server already running?")
        for host in hosts:
            shown = f"[{host}]" if ":" in host else host
            self.log(f"CoAP server listening on udp://{shown}:{port}", self.style.SUCCESS)
        self.log(f"Devices use coap://{settings.COAP_PUBLIC_HOST or '<this computer>'}:{port}/api/v1/<token>/...")
        try:
            await api.watch(settings.COAP_OBSERVE_INTERVAL)
        finally:
            for context in contexts:
                await context.shutdown()
