"""
Perintah kustom:  python manage.py simulate_devices [--loop] [--interval 5]

Berpura-pura menjadi device, karena jalur komunikasi dengan device sungguhan
belum dibuat. Dalam satu putaran, simulator melakukan apa yang nanti akan
dilakukan firmware:

1. Pola 1 (desired/reported state): untuk setiap aktuator yang statusnya
   "Waiting for device", "menjalankan" permintaan lalu MELAPORKAN keadaan
   barunya sebagai telemetri. Status di dashboard pun berubah ke "In sync".
2. Pola 2 (command): mengambil command yang menunggu (QUEUED -> SENT), lalu
   melaporkan hasilnya (SUCCEEDED / FAILED).
3. Menandai EXPIRED command yang sudah lewat batas waktu, dan tidak
   menjalankannya.

Contoh pemakaian:
    python manage.py simulate_devices                 # satu putaran
    python manage.py simulate_devices --loop          # terus-menerus, tiap 5 detik
    python manage.py simulate_devices --fail-rate 0.3 # 30% gagal, untuk mencoba
"""
import random
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

# Diberi alias karena class wajib di file ini juga bernama `Command`
# (aturan Django untuk management command).
from devices.models import Command as DeviceCommand
from devices.models import CommandStatus, Telemetry, Variable


class Command(BaseCommand):
    help = "Pretend to be the devices: apply desired states and answer queued commands."

    def add_arguments(self, parser):
        parser.add_argument("--device", type=int, help="Only simulate the device with this id.")
        parser.add_argument("--loop", action="store_true", help="Keep running until Ctrl+C.")
        parser.add_argument("--interval", type=float, default=5, help="Seconds between rounds (with --loop).")
        parser.add_argument(
            "--fail-rate", type=float, default=0.0,
            help="Probability (0–1) that a command fails or an actuator gets stuck.",
        )

    def handle(self, *args, **options):
        if not options["loop"]:
            self.run_once(options)
            return
        self.stdout.write("Simulating devices. Press Ctrl+C to stop.")
        try:
            while True:
                self.run_once(options)
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")

    def run_once(self, options):
        fail_rate = options["fail_rate"]
        device_id = options["device"]

        # --- 3) command kedaluwarsa tidak boleh dijalankan ---
        commands = DeviceCommand.objects.all()
        if device_id:
            commands = commands.filter(device_id=device_id)
        expired = commands.expire_due()
        if expired:
            self.stdout.write(f"{expired} command(s) expired.")

        # --- 1) pola desired / reported state ---
        actuators = Variable.objects.filter(
            kind=Variable.Kind.ACTUATOR, desired_value__isnull=False
        ).select_related("device")
        if device_id:
            actuators = actuators.filter(device_id=device_id)
        for variable in actuators:
            if variable.sync_status != Variable.SyncStatus.PENDING:
                continue
            reported = variable.desired_value
            if random.random() < fail_rate:
                # Aktuator "macet": melapor keadaan lama -> status Mismatch.
                previous = variable.latest
                reported = previous.value if previous else (0 if variable.is_boolean else reported)
                if reported == variable.desired_value:
                    reported = 1 - reported if variable.is_boolean else reported + 1
            Telemetry.objects.create(variable=variable, value=reported, ts=timezone.now())
            self.stdout.write(f"{variable}: reported {reported:g} (requested {variable.desired_value:g})")

        # --- 2) pola command ---
        for command in commands.filter(status=CommandStatus.QUEUED):
            command.mark_sent()
            if random.random() < fail_rate:
                command.complete(False, {"error": "simulated failure"})
            else:
                command.complete(True, {"ok": True, "simulated": True, "params": command.params})
            self.stdout.write(f"{command.device} · {command.method}: {command.status}")
