"""
Perintah kustom:  python manage.py rule_engine

Worker rule engine. Jalankan di terminal TERPISAH, seperti mqtt_bridge dan
coap_server. Setiap putaran:

    1. ambil pesan dari antrean Redis (paling lama RULE_ENGINE_TICK detik menunggu)
       -> nilai aturan yang dipicu pesan itu
    2. setiap RULE_ENGINE_TICK detik: tick() -> aturan berbasis waktu
       ("< 20 selama 60 detik", "device diam 5 menit")

Dalam mode inline (tanpa Redis) langkah 1 dilakukan oleh proses yang
menyimpan data; worker ini hanya menjalankan langkah 2.

    python manage.py rule_engine           # terus berjalan
    python manage.py rule_engine --once    # satu putaran (untuk mencoba)
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection
from django.utils import timezone

from rules import queue
from rules.engines import ENGINES, implemented_engines


class Command(BaseCommand):
    help = "Run the rule engine worker (evaluates rules and runs their actions)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process waiting messages and one tick, then exit.")

    def log(self, message, style=None):
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.stdout.write(style(line) if style else line)

    def report(self, executions):
        for execution in executions:
            failed = [r for r in execution.results if not r["ok"]]
            value = "—" if execution.value is None else f"{execution.value:g}"
            text = f"Rule \"{execution.rule.name}\" {execution.get_transition_display().lower()} (value {value})"
            details = "; ".join(f"{r['action']}: {r['detail']}" for r in execution.results) or "no actions"
            style = self.style.WARNING if failed else self.style.SUCCESS
            self.log(f"{text} -> {details}", style)

    def handle(self, *args, **options):
        mode = settings.RULE_ENGINE_MODE
        if mode == "off":
            raise CommandError("The rule engine is off (RULE_ENGINE_MODE=off in .env).")
        tick_every = settings.RULE_ENGINE_TICK

        for engine in ENGINES.values():
            status = "ready" if engine.implemented else "planned (not implemented yet)"
            self.log(f"Rule model {engine.label}: {status}")

        client = None
        if mode == "worker":
            client = queue.redis_client()
            try:
                client.ping()
            except Exception as exc:
                raise CommandError(f"Cannot reach Redis at REDIS_URL: {exc}")
            self.log(f"Reading messages from Redis list \"{queue.QUEUE_KEY}\"", self.style.SUCCESS)
        else:
            self.log("Inline mode: rules are evaluated where data is saved; this worker runs timers only.",
                     self.style.SUCCESS)
        self.log(f"Timers checked every {tick_every:g} s. Press Ctrl+C to stop.")

        try:
            self.loop(client, tick_every, once=options["once"])
        except KeyboardInterrupt:
            self.log("Stopped.")

    def loop(self, client, tick_every, once=False):
        next_tick = time.monotonic()
        message = None
        while True:
            # Koneksi database yang sudah lama tidak dipakai dibuang (proses berjalan lama).
            # Tidak boleh di dalam transaksi yang sedang berjalan (mis. saat tes:
            # setiap tes dibungkus transaksi). close_old_connections() menganggap
            # koneksi dengan autocommit mati sebagai "rusak" lalu menutupnya, dan
            # MySQL membalas "(2006) server has gone away" pada query berikutnya.
            if not connection.in_atomic_block:
                close_old_connections()
            if client is not None:
                wait = max(1, round(next_tick - time.monotonic()))
                try:
                    message = queue.pop(client, timeout=0.1 if once else wait)
                except Exception as exc:
                    self.log(f"Redis error: {exc}; retrying in 5 s", self.style.ERROR)
                    time.sleep(5)
                    continue
                if message is not None:
                    self.report(queue.process(message))
            elif not once:
                time.sleep(max(0.0, next_tick - time.monotonic()))

            if time.monotonic() >= next_tick or once:
                now = timezone.now()
                for engine in implemented_engines():
                    self.report(engine.tick(now) or [])
                next_tick = time.monotonic() + tick_every
            if once and (client is None or message is None):
                return
