"""
python manage.py recount_telemetry [--device ID]

Hitung ulang jumlah baris telemetri setiap variabel (Variable.telemetry_rows, dasar batas
baris per variabel, devices/row_limit.py) dari tabel telemetri.

Aplikasi menjaga angka ini sendiri; perintah ini hanya perlu dijalankan sekali setelah data
telemetri diubah DI LUAR aplikasi, mis. dihapus manual lewat SQL:
    TRUNCATE TABLE devices_telemetry;   ->   python manage.py recount_telemetry
"""
from django.core.management.base import BaseCommand

from devices.models import Variable
from devices.row_limit import recount


class Command(BaseCommand):
    help = "Recount the telemetry rows of each variable (basis of the per-variable row limit)."

    def add_arguments(self, parser):
        parser.add_argument("--device", type=int, help="Only the variables of this device id.")

    def handle(self, *args, **options):
        variables = Variable.objects.all()
        if options["device"]:
            variables = variables.filter(device_id=options["device"])
        changed = recount(variables)
        self.stdout.write(self.style.SUCCESS(f"Checked {variables.count()} variable(s); {changed} corrected."))
