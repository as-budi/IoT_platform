# Penghitung baris telemetri per variabel (docs/tahap-27-tambahan-batas-baris-variabel.md).
# Diisi SEKALI di sini dari tabel telemetri (satu COUNT ... GROUP BY); setelah itu dijaga
# oleh devices/row_limit.py tanpa menghitung ulang.

from django.db import migrations, models
from django.db.models import Count


def fill_counts(apps, schema_editor):
    Telemetry = apps.get_model("devices", "Telemetry")
    Variable = apps.get_model("devices", "Variable")
    for row in Telemetry.objects.order_by().values("variable_id").annotate(n=Count("id")):
        Variable.objects.filter(pk=row["variable_id"]).update(telemetry_rows=row["n"])


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0028_device_last_telemetry_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='variable',
            name='telemetry_rows',
            field=models.PositiveIntegerField(default=0, editable=False, verbose_name='telemetry rows'),
        ),
        migrations.RunPython(fill_counts, migrations.RunPython.noop),
    ]
