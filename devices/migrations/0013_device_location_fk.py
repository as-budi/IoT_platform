"""
Langkah 3 dari 3: kolom teks lama dibuang, kolom sementara `site` diganti
nama menjadi `location`, lalu diberi pengaturan akhirnya.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0012_locations_from_text"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="device",
            name="location",
        ),
        migrations.RenameField(
            model_name="device",
            old_name="site",
            new_name="location",
        ),
        migrations.AlterField(
            model_name="device",
            name="location",
            field=models.ForeignKey(
                blank=True,
                help_text="Choose from existing locations. New locations are added on the Locations page.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="devices",
                to="devices.location",
                verbose_name="location",
            ),
        ),
    ]
