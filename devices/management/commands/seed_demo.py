"""
Perintah kustom:  python manage.py seed_demo

Mengisi database dengan pengguna, device, variabel, dan telemetri contoh, karena di
tahap ini belum ada device sungguhan yang mengirim data.

File di <app>/management/commands/ otomatis menjadi sub-perintah manage.py.
Perintah ini aman dijalankan berulang (tidak membuat data ganda).
"""
import math
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from devices.models import Device, Telemetry, Variable

User = get_user_model()

DEMO_PASSWORD = "demo12345"

# username -> (role, nama depan)
DEMO_USERS = {
    "admin": (User.Role.ADMIN, "Admin"),
    "budi": (User.Role.USER, "Budi"),
    "siti": (User.Role.USER, "Siti"),
}

# Satu device = satu alat fisik, dengan beberapa variabel.
# (pemilik, nama device, tipe, lokasi, [(key, label, satuan, nilai tengah, simpangan), ...])
DEMO_DEVICES = [
    ("budi", "Server Room Node", "ESP32 + DHT22", "Server room", [
        ("temperature", "Temperature", "°C", 24, 3),
        ("humidity", "Humidity", "%", 55, 10),
    ]),
    ("budi", "Lab Power Meter", "power meter", "Electronics lab", [
        ("voltage", "Voltage", "V", 220, 5),
        ("current", "Current", "A", 3, 1.5),
        ("power", "Power", "W", 650, 300),
    ]),
    ("siti", "Greenhouse Node", "ESP32 + soil sensor", "Greenhouse A", [
        ("temperature", "Air temperature", "°C", 29, 5),
        ("humidity", "Air humidity", "%", 75, 12),
        ("soil_moisture", "Soil moisture", "%", 40, 15),
    ]),
]

INTERVAL_MINUTES = 10
HOURS = 24


class Command(BaseCommand):
    help = "Seed demo data (users, devices, variables, last 24 hours of telemetry)."

    def handle(self, *args, **options):
        users = {}
        for username, (role, first_name) in DEMO_USERS.items():
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"role": role, "first_name": first_name},
            )
            if created:
                # set_password menyimpan password sebagai hash, bukan teks asli.
                user.set_password(DEMO_PASSWORD)
                if role == User.Role.ADMIN:
                    # is_staff/is_superuser = boleh masuk ke situs /admin/.
                    user.is_staff = True
                    user.is_superuser = True
                user.save()
                self.stdout.write(f"User created: {username} / {DEMO_PASSWORD}")
            users[username] = user

        now = timezone.now()
        steps = HOURS * 60 // INTERVAL_MINUTES

        for owner_name, name, device_type, location, variables in DEMO_DEVICES:
            device, _ = Device.objects.get_or_create(
                owner=users[owner_name],
                name=name,
                defaults={"device_type": device_type, "location": location},
            )
            for key, label, unit, center, spread in variables:
                variable, _ = Variable.objects.get_or_create(
                    device=device,
                    key=key,
                    defaults={"label": label, "unit": unit},
                )
                if variable.telemetry.exists():
                    self.stdout.write(f"Skipping {name} · {key}: telemetry already exists.")
                    continue

                rows = []
                for step in range(steps):
                    # Semua variabel satu device memakai waktu yang sama,
                    # seperti satu kiriman data berisi beberapa nilai.
                    ts = now - timedelta(minutes=INTERVAL_MINUTES * step)
                    # Gelombang sinus harian + sedikit derau acak, supaya
                    # datanya tampak seperti bacaan sensor.
                    wave = math.sin(2 * math.pi * step / steps)
                    value = center + spread * 0.6 * wave + random.uniform(-0.2, 0.2) * spread
                    rows.append(Telemetry(variable=variable, value=round(value, 2), ts=ts))
                # bulk_create menyimpan ratusan baris dalam sedikit query.
                Telemetry.objects.bulk_create(rows, batch_size=500)
                self.stdout.write(f"{name} · {key}: {len(rows)} telemetry rows created.")

        self.stdout.write(self.style.SUCCESS("Done."))
