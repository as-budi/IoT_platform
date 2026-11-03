# Tahap 24: MAC address yang dilaporkan device bersama versi firmware.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0020_rollups_and_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="mac_address",
            field=models.CharField(blank=True, editable=False, max_length=17, verbose_name="MAC address"),
        ),
        migrations.AddField(
            model_name="device",
            name="mac_reported_at",
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name="MAC reported"),
        ),
    ]
