"""
Perintah kustom:  python manage.py housekeeping [--once]

Worker untuk pekerjaan berkala yang tidak dipicu pesan (tahap 16). Jalankan
terus-menerus seperti mqtt_bridge dan rule_engine (runall: "housekeeping",
systemd: iot-housekeeping.service). Cukup SATU proses.

    setiap STATUS_EVERY detik   devices/presence.check_timeouts()
                                device tanpa data > DEVICE_OFFLINE_SECONDS -> offline
    setiap ROLLUP_EVERY detik   devices/rollups.update()
                                tabel ringkasan telemetri per menit dan per jam

Mengapa tidak di dalam rule_engine? Rule engine bisa dimatikan
(RULE_ENGINE_MODE=off), sedangkan status device dan chart tetap dibutuhkan.

Proses pertama kali di server dengan data lama bisa memakan waktu (semua data
diringkas); lihat juga `manage.py rollup_telemetry`.
"""
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.utils import timezone

from devices import presence, rollups

STATUS_EVERY = 10
ROLLUP_EVERY = 60


class Command(BaseCommand):
    help = "Mark silent devices offline and keep the telemetry summary tables up to date."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run both jobs once, then exit.")

    def log(self, message, style=None):
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.stdout.write(style(line) if style else line)

    def run_status(self):
        count = presence.check_timeouts(timezone.now())
        if count:
            self.log(f"{count} device(s) went offline (no data)", self.style.WARNING)

    def run_rollups(self):
        started = time.monotonic()
        result = rollups.update(timezone.now())
        if result and (result["minutes"] or result["hours"]):
            took = time.monotonic() - started
            self.log(f"Summaries: {result['minutes']} minute row(s), {result['hours']} hour row(s) "
                     f"from {timezone.localtime(result['from']):%Y-%m-%d %H:%M} ({took:.1f} s)")

    def handle(self, *args, **options):
        jobs = [(self.run_status, STATUS_EVERY), (self.run_rollups, ROLLUP_EVERY)]
        if options["once"]:
            for job, _ in jobs:
                job()
            return
        self.log(f"Device status every {STATUS_EVERY} s, telemetry summaries every {ROLLUP_EVERY} s. "
                 "Press Ctrl+C to stop.", self.style.SUCCESS)
        due = {job: 0.0 for job, _ in jobs}
        try:
            while True:
                if not connection.in_atomic_block:
                    close_old_connections()
                for job, every in jobs:
                    if time.monotonic() >= due[job]:
                        try:
                            job()
                        except Exception as exc:      # satu kegagalan tidak menghentikan worker
                            self.log(f"{job.__name__} failed: {exc}", self.style.ERROR)
                        due[job] = time.monotonic() + every
                time.sleep(1)
        except KeyboardInterrupt:
            self.log("Stopped.")
