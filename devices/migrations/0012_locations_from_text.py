"""
Langkah 2 dari 3: migrasi data.

Setiap teks lokasi lama yang berbeda (mis. "Server room") dijadikan satu
baris Location, lalu device yang memakainya dihubungkan ke baris itu.

Teks lama tidak punya koordinat, jadi koordinatnya diisi 0, 0 dan diberi
catatan. Perbaiki lewat halaman Locations setelah migrasi.
"""
from django.db import migrations

PLACEHOLDER = "Created automatically from old device data. Set the real coordinates."


def forwards(apps, schema_editor):
    Device = apps.get_model("devices", "Device")
    Location = apps.get_model("devices", "Location")

    names = (
        Device.objects.exclude(location="")
        .order_by()                       # buang urutan default sebelum distinct()
        .values_list("location", flat=True)
        .distinct()
    )
    for name in names:
        location, _ = Location.objects.get_or_create(
            name=name.strip()[:100],
            defaults={"latitude": 0, "longitude": 0, "description": PLACEHOLDER},
        )
        Device.objects.filter(location=name).update(site=location)


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0011_location"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
