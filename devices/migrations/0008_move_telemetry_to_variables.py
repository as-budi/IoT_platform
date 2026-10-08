"""
Langkah 2 dari 3: migrasi data.

Setiap device lama (yang hanya punya satu variabel di kolom device.variable)
mendapat satu baris Variable dengan key dan satuan yang sama, lalu semua
telemetrinya dihubungkan ke variabel itu. Tidak ada data yang hilang.

Device yang dulu dipecah (mis. "Server Room Temperature" dan "Server Room
Humidity") TIDAK digabung otomatis: Django tidak bisa tahu mana yang
sebenarnya satu alat fisik. Penggabungan dilakukan manual bila diperlukan.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Device = apps.get_model("devices", "Device")
    Variable = apps.get_model("devices", "Variable")
    Telemetry = apps.get_model("devices", "Telemetry")

    for device in Device.objects.all():
        variable = Variable.objects.create(
            device=device,
            key=device.variable or "value",
            unit=device.unit,
        )
        # update() mengubah banyak baris sekaligus dengan satu query UPDATE.
        Telemetry.objects.filter(device=device).update(variable=variable)


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0007_variable"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
