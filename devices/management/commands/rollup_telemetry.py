"""
Perintah kustom:  python manage.py rollup_telemetry [--rebuild] [--since YYYY-MM-DD]

Memperbarui tabel ringkasan telemetri SEKALI (tahap 16). Biasanya tidak perlu:
worker `housekeeping` sudah melakukannya setiap menit. Berguna untuk:

    rollup_telemetry                         sekali jalan (mis. untuk mencoba)
    rollup_telemetry --rebuild               hitung ulang SEMUA ringkasan dari data asli
    rollup_telemetry --rebuild --since 2026-10-01
                                             hitung ulang mulai tanggal itu, mis. setelah
                                             mengimpor data lama atau data yang datang
                                             jauh terlambat
"""
from datetime import datetime, time as dt_time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from devices import rollups
from devices.models import Telemetry


class Command(BaseCommand):
    help = "Update (or rebuild) the per-minute and per-hour telemetry summary tables."

    def add_arguments(self, parser):
        parser.add_argument("--rebuild", action="store_true", help="Recompute from the oldest reading (or --since).")
        parser.add_argument("--since", help="With --rebuild: start date YYYY-MM-DD (server time zone).")

    def handle(self, *args, **options):
        since = None
        if options["since"]:
            if not options["rebuild"]:
                raise CommandError("--since needs --rebuild.")
            try:
                day = datetime.strptime(options["since"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--since must look like 2026-10-01.")
            since = timezone.make_aware(datetime.combine(day, dt_time.min))
        elif options["rebuild"]:
            since = Telemetry.objects.order_by("ts").values_list("ts", flat=True).first()
            if since is None:
                self.stdout.write("No telemetry yet.")
                return
        result = rollups.update(timezone.now(), since=since)
        if result is None:
            self.stdout.write("No telemetry yet.")
            return
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {result['minutes']} minute row(s) and {result['hours']} hour row(s) "
            f"for {timezone.localtime(result['from']):%Y-%m-%d %H:%M} – "
            f"{timezone.localtime(result['until']):%Y-%m-%d %H:%M}."
        ))
