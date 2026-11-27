# Batas interval pengiriman telemetri (docs/tahap-27-tambahan-interval-telemetri.md).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0027_firmware_title'),
    ]

    operations = [
        migrations.AddField(
            model_name='device',
            name='last_telemetry_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='last accepted telemetry'),
        ),
    ]
