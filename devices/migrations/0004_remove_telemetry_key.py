"""
Langkah 3 dari 3: setelah datanya dipindahkan, kolom `key` tidak dipakai lagi.

Index yang memuat kolom itu harus dihapus lebih dulu, baru kolomnya.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0003_split_devices_per_variable"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="telemetry",
            name="telemetry_dev_key_ts",
        ),
        migrations.RemoveField(
            model_name="telemetry",
            name="key",
        ),
    ]
