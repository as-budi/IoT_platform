"""
Langkah 3 dari 3: membuang struktur lama.

- telemetry.device dihapus (telemetri kini terhubung lewat variabel),
  beserta index lamanya.
- telemetry.variable menjadi wajib (tidak boleh null lagi).
- device.variable dan device.unit dihapus (pindah ke model Variable).
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0008_move_telemetry_to_variables"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="telemetry",
            name="telemetry_dev_ts",
        ),
        migrations.RemoveField(
            model_name="telemetry",
            name="device",
        ),
        migrations.AlterField(
            model_name="telemetry",
            name="variable",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="telemetry",
                to="devices.variable",
                verbose_name="variable",
            ),
        ),
        migrations.AddIndex(
            model_name="telemetry",
            index=models.Index(fields=["variable", "ts"], name="telemetry_var_ts"),
        ),
        migrations.RemoveField(
            model_name="device",
            name="variable",
        ),
        migrations.RemoveField(
            model_name="device",
            name="unit",
        ),
    ]
