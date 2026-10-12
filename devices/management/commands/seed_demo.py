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

from devices.models import DesiredStateLog, Device, Location, Telemetry, Variable

User = get_user_model()

DEMO_PASSWORD = "demo12345"

# username -> (role, nama depan). Email contoh: <username>@example.com.
DEMO_USERS = {
    "admin": (User.Role.ADMIN, "Admin"),
    "budi": (User.Role.USER, "Budi"),
    "siti": (User.Role.USER, "Siti"),
    "andi": (User.Role.USER, "Andi"),   # peternakan ayam & kolam ikan
    "rina": (User.Role.USER, "Rina"),   # gudang pendingin
}

# Lokasi contoh: nama -> (latitude, longitude, deskripsi).
# Koordinatnya hanya contoh; ubah lewat halaman Locations bila perlu.
DEMO_LOCATIONS = {
    "Server room": (-7.952900, 112.614500, "Main server room, ground floor."),
    "Electronics lab": (-7.953300, 112.614100, "Teaching laboratory."),
    "Greenhouse A": (-7.946500, 112.609800, "Research greenhouse."),
    "Poultry house": (-7.938200, 112.602400, "Broiler house, 2,000 birds."),
    "Fish pond": (-7.938900, 112.603100, "Tilapia pond, 200 m²."),
    "Cold storage": (-7.955100, 112.616900, "Vaccine and food cold room, 2–8 °C."),
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
    # Device kedua di greenhouse yang sama: dipakai aturan lintas device.
    ("siti", "Greenhouse Weather Station", "rain gauge + lux sensor", "Greenhouse A", [
        ("rain", "Rainfall", "mm/h", 0.3, 0.3),
        ("light", "Light", "lux", 20000, 15000),
    ]),
    ("andi", "Poultry House Node", "ESP32 + SHT31 + MQ-137", "Poultry house", [
        ("temperature", "Temperature", "°C", 28, 3),
        ("humidity", "Humidity", "%", 65, 8),
        ("ammonia", "Ammonia", "ppm", 10, 5),
    ]),
    ("andi", "Fish Pond Node", "ESP32 + DO + pH probe", "Fish pond", [
        ("water_temp", "Water temperature", "°C", 28, 2),
        ("dissolved_oxygen", "Dissolved oxygen", "mg/L", 6, 1.5),
        ("ph", "pH", "", 7.2, 0.4),
    ]),
    ("rina", "Cold Storage Unit", "ESP32 + DS18B20 + DHT22", "Cold storage", [
        ("temperature", "Temperature", "°C", 5, 1.5),
        ("humidity", "Humidity", "%", 70, 8),
    ]),
]

# Aktuator: (pemilik, nama device, tipe, lokasi,
#            [(key, label, satuan, tipe nilai, min, maks, pilihan keadaan), ...])
# Device yang sama boleh muncul di DEMO_DEVICES dan di sini: Greenhouse Node
# punya sensor sekaligus aktuator (pompa).
BOOLEAN, NUMBER = Variable.ValueType.BOOLEAN, Variable.ValueType.NUMBER
DEMO_ACTUATORS = [
    ("budi", "Lab Relay Board", "ESP32 + 2-channel relay", "Electronics lab", [
        ("lamp", "Lamp", "", BOOLEAN, None, None, [0, 1]),
        ("fan_speed", "Fan speed", "%", NUMBER, 0, 100, [0, 25, 50, 75, 100]),
    ]),
    ("siti", "Greenhouse Node", "ESP32 + soil sensor", "Greenhouse A", [
        ("pump", "Water pump", "", BOOLEAN, None, None, [0, 1]),
    ]),
    ("andi", "Poultry House Node", "ESP32 + SHT31 + MQ-137", "Poultry house", [
        ("exhaust_fan", "Exhaust fan", "", BOOLEAN, None, None, [0, 1]),
    ]),
    ("andi", "Fish Pond Node", "ESP32 + DO + pH probe", "Fish pond", [
        ("aerator", "Aerator", "", BOOLEAN, None, None, [0, 1]),
    ]),
    ("rina", "Cold Storage Unit", "ESP32 + DS18B20 + DHT22", "Cold storage", [
        ("compressor_boost", "Compressor boost", "", BOOLEAN, None, None, [0, 1]),
    ]),
]

INTERVAL_MINUTES = 10
HOURS = 24
# Keadaan aktuator contoh berganti setiap 3 jam.
CHANGE_EVERY_STEPS = 3 * 60 // INTERVAL_MINUTES


class Command(BaseCommand):
    help = "Seed demo data (users, devices, variables, last 24 hours of telemetry)."

    def handle(self, *args, **options):
        users = {}
        for username, (role, first_name) in DEMO_USERS.items():
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"role": role, "first_name": first_name, "email": f"{username}@example.com"},
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

        locations = {}
        for name, (lat, lng, description) in DEMO_LOCATIONS.items():
            locations[name], _ = Location.objects.get_or_create(
                name=name,
                defaults={"latitude": lat, "longitude": lng, "description": description},
            )

        now = timezone.now()
        steps = HOURS * 60 // INTERVAL_MINUTES

        for owner_name, name, device_type, location, variables in DEMO_DEVICES:
            device, _ = Device.objects.get_or_create(
                owner=users[owner_name],
                name=name,
                defaults={"device_type": device_type, "location": locations[location]},
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
                    value = max(value, 0)  # curah hujan, cahaya, dll. tidak pernah negatif
                    rows.append(Telemetry(variable=variable, value=round(value, 2), ts=ts))
                # bulk_create menyimpan ratusan baris dalam sedikit query.
                Telemetry.objects.bulk_create(rows, batch_size=500)
                self.stdout.write(f"{name} · {key}: {len(rows)} telemetry rows created.")

        for owner_name, name, device_type, location, variables in DEMO_ACTUATORS:
            owner = users[owner_name]
            device, _ = Device.objects.get_or_create(
                owner=owner,
                name=name,
                defaults={"device_type": device_type, "location": locations[location]},
            )
            for key, label, unit, value_type, low, high, states in variables:
                variable, _ = Variable.objects.get_or_create(
                    device=device,
                    key=key,
                    defaults={
                        "label": label,
                        "unit": unit,
                        "kind": Variable.Kind.ACTUATOR,
                        "value_type": value_type,
                        "min_value": low,
                        "max_value": high,
                    },
                )
                if variable.telemetry.exists():
                    self.stdout.write(f"Skipping {name} · {key}: telemetry already exists.")
                    continue
                self.seed_actuator(variable, owner, states, now, steps)

        self.stdout.write(self.style.SUCCESS("Done."))
        self.stdout.write("Sample rules: python manage.py seed_rules")

    def seed_actuator(self, variable, owner, states, now, steps):
        """Riwayat 24 jam: device melapor keadaannya tiap 10 menit, keadaan
        berganti tiap 3 jam. Desired diset ke keadaan terakhir, sehingga
        statusnya "In sync"."""
        rows = []
        last_change_ts = None
        state = states[0]
        # Dari yang paling lama ke yang terbaru.
        for step in range(steps - 1, -1, -1):
            ts = now - timedelta(minutes=INTERVAL_MINUTES * step)
            if step % CHANGE_EVERY_STEPS == 0:
                state = random.choice([s for s in states if s != state])
                last_change_ts = ts
            rows.append(Telemetry(variable=variable, value=state, ts=ts))
        Telemetry.objects.bulk_create(rows, batch_size=500)

        # Permintaan terakhir terjadi tepat sebelum perubahan terakhir.
        requested_at = last_change_ts - timedelta(seconds=5)
        variable.desired_value = state
        variable.desired_at = requested_at
        variable.desired_by = owner
        variable.desired_version = 1
        variable.save()
        log = DesiredStateLog.objects.create(
            variable=variable, value=state, version=1, changed_by=owner
        )
        # created_at memakai auto_now_add (selalu "sekarang"); untuk data contoh
        # waktunya diubah lewat update(), yang tidak memicu auto_now_add.
        DesiredStateLog.objects.filter(pk=log.pk).update(created_at=requested_at)
        self.stdout.write(f"{variable.device.name} · {variable.key}: {len(rows)} state reports created.")
