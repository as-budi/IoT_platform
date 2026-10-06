"""
Langkah 2 dari 3: "migrasi data" (mengubah ISI tabel, bukan strukturnya).

Device lama bisa punya beberapa key telemetri. Supaya tidak ada data yang
hilang, device seperti itu dipecah: satu device baru untuk tiap key, dan
baris telemetrinya dipindahkan ke device yang sesuai.

Contoh: "Sensor Ruang Server" (temperature, humidity) menjadi
"Sensor Ruang Server – humidity" dan "Sensor Ruang Server – temperature".
"""
from django.db import migrations

# Satuan untuk nama variabel yang umum (dipakai data contoh).
KNOWN_UNITS = {
    "temperature": "°C",
    "humidity": "%",
    "soil_moisture": "%",
    "voltage": "V",
    "current": "A",
    "power": "W",
}


def split_devices(apps, schema_editor):
    # Di dalam migrasi, model diambil lewat apps.get_model() — versi model
    # "pada titik sejarah ini" (masih punya kolom key) — bukan di-import.
    Device = apps.get_model("devices", "Device")
    Telemetry = apps.get_model("devices", "Telemetry")

    for device in Device.objects.all():
        keys = sorted(
            Telemetry.objects.filter(device=device)
            .order_by()
            .values_list("key", flat=True)
            .distinct()
        )

        if not keys:
            # Belum punya data: beri nama variabel sementara.
            device.variable = "value"
            device.save(update_fields=["variable"])
            continue

        original_name = device.name
        for index, key in enumerate(keys):
            if index == 0:
                target = device  # key pertama tetap memakai device lama
            else:
                target = Device(
                    owner_id=device.owner_id,
                    device_type=device.device_type,
                    description=device.description,
                    is_active=device.is_active,
                )
            target.variable = key
            target.unit = KNOWN_UNITS.get(key, "")
            if len(keys) > 1:
                target.name = f"{original_name} – {key}"[:100]
            else:
                target.name = original_name
            target.save()
            if target.pk != device.pk:
                Telemetry.objects.filter(device=device, key=key).update(device=target)


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0002_device_variable_unit"),
    ]

    operations = [
        # Argumen kedua = fungsi untuk membatalkan (rollback). noop berarti
        # rollback diizinkan tetapi tidak menggabungkan device kembali.
        migrations.RunPython(split_devices, migrations.RunPython.noop),
    ]
