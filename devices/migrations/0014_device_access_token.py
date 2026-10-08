"""
Access token per device — tiga langkah dalam satu migrasi.

Kolom unik dengan nilai default acak tidak bisa ditambahkan sekaligus: saat
kolom dibuat, Django mengisi SEMUA baris lama dengan SATU nilai default yang
sama, sehingga aturan unik langsung dilanggar. Jalan keluarnya:

1. tambah kolom yang boleh kosong (null),
2. isi setiap device lama dengan token acaknya sendiri (RunPython),
3. baru jadikan kolom itu wajib dan unik.
"""
import secrets

import devices.models
from django.db import migrations, models


def fill_tokens(apps, schema_editor):
    Device = apps.get_model("devices", "Device")
    for device in Device.objects.filter(access_token__isnull=True):
        device.access_token = secrets.token_urlsafe(15)
        device.save(update_fields=["access_token"])


class Migration(migrations.Migration):

    # MySQL tidak mendukung transaksi untuk perubahan struktur tabel, jadi
    # migrasi campuran ini dijalankan tanpa membungkusnya dalam satu transaksi.
    atomic = False

    dependencies = [
        ("devices", "0013_device_location_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="access_token",
            field=models.CharField(editable=False, max_length=40, null=True, verbose_name="access token"),
        ),
        migrations.RunPython(fill_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="device",
            name="access_token",
            field=models.CharField(
                default=devices.models.generate_access_token,
                editable=False,
                max_length=40,
                unique=True,
                verbose_name="access token",
            ),
        ),
    ]
