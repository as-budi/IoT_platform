# Tahap 12, langkah 3/3: setelah semua lokasi punya pemilik, kolom owner wajib
# diisi dan nama lokasi unik per pemilik.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0018_location_owner_data"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="location",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="locations",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="location",
            constraint=models.UniqueConstraint(fields=("owner", "name"), name="location_unique_name_per_owner"),
        ),
    ]
