"""
Perintah kustom:  python manage.py seed_demo

Mengisi database dengan pengguna, device, dan telemetri contoh, karena di
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

from devices.models import Device, Telemetry

User = get_user_model()

DEMO_PASSWORD = "demo12345"

# username -> (role, nama depan)
DEMO_USERS = {
    "admin": (User.Role.ADMIN, "Admin"),
    "budi": (User.Role.USER, "Budi"),
    "siti": (User.Role.USER, "Siti"),
}

# Satu device = satu variabel.
# (pemilik, nama device, tipe, variabel, satuan, nilai tengah, simpangan)
DEMO_DEVICES = [
    ("budi", "Suhu Ruang Server", "sensor suhu", "temperature", "°C", 24, 3),
    ("budi", "Kelembapan Ruang Server", "sensor kelembapan", "humidity", "%", 55, 10),
    ("budi", "Tegangan Panel Lab", "power meter", "voltage", "V", 220, 5),
    ("siti", "Suhu Greenhouse", "sensor suhu", "temperature", "°C", 29, 5),
    ("siti", "Kelembapan Tanah Greenhouse", "sensor tanah", "soil_moisture", "%", 40, 15),
]

INTERVAL_MINUTES = 10
HOURS = 24


class Command(BaseCommand):
    help = "Mengisi data contoh (pengguna, device, telemetri 24 jam terakhir)."

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
                self.stdout.write(f"Pengguna dibuat: {username} / {DEMO_PASSWORD}")
            users[username] = user

        now = timezone.now()
        steps = HOURS * 60 // INTERVAL_MINUTES

        for owner_name, name, device_type, variable, unit, center, spread in DEMO_DEVICES:
            device, _ = Device.objects.get_or_create(
                owner=users[owner_name],
                name=name,
                defaults={
                    "device_type": device_type,
                    "variable": variable,
                    "unit": unit,
                },
            )
            if device.telemetry.exists():
                self.stdout.write(f"Lewati {name}: telemetri sudah ada.")
                continue

            rows = []
            for step in range(steps):
                ts = now - timedelta(minutes=INTERVAL_MINUTES * step)
                # Gelombang sinus harian + sedikit derau acak, supaya datanya
                # tampak seperti bacaan sensor.
                wave = math.sin(2 * math.pi * step / steps)
                value = center + spread * 0.6 * wave + random.uniform(-0.2, 0.2) * spread
                rows.append(Telemetry(device=device, value=round(value, 2), ts=ts))
            # bulk_create menyimpan ratusan baris dalam sedikit query.
            Telemetry.objects.bulk_create(rows, batch_size=500)
            self.stdout.write(f"{name}: {len(rows)} baris telemetri dibuat.")

        self.stdout.write(self.style.SUCCESS("Selesai."))
