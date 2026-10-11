# Dua kolom baru di Device (jalur & waktu data terakhir), lalu kolom waktu
# diisi dari telemetri yang sudah ada supaya device lama tidak tampil "No data".
from django.db import migrations, models
from django.db.models import Max


def fill_last_seen(apps, schema_editor):
    Device = apps.get_model("devices", "Device")
    Telemetry = apps.get_model("devices", "Telemetry")
    latest = Telemetry.objects.values("variable__device").annotate(last=Max("ts"))
    for row in latest:
        Device.objects.filter(pk=row["variable__device"]).update(last_seen_at=row["last"])


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0014_device_access_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="last_protocol",
            field=models.CharField(
                blank=True,
                choices=[("HTTP", "HTTP"), ("MQTT", "MQTT"), ("COAP", "CoAP")],
                editable=False,
                max_length=4,
                verbose_name="last protocol",
            ),
        ),
        migrations.AddField(
            model_name="device",
            name="last_seen_at",
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name="last data"),
        ),
        migrations.RunPython(fill_last_seen, migrations.RunPython.noop),
    ]
