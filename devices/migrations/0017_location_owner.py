# Tahap 12, langkah 1/3: kolom pemilik lokasi (sementara boleh kosong), nama
# lokasi tidak lagi unik untuk seluruh platform, dan Device.location menjadi
# RESTRICT (lihat komentar di devices/models.py).
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0016_device_firmware"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="locations",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AlterField(
            model_name="location",
            name="name",
            field=models.CharField(max_length=100, verbose_name="name"),
        ),
        migrations.AlterField(
            model_name="device",
            name="location",
            field=models.ForeignKey(
                blank=True,
                help_text="Choose one of your locations. New locations are added on the Locations page.",
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="devices",
                to="devices.location",
                verbose_name="location",
            ),
        ),
    ]
