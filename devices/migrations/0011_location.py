"""
Lokasi sebagai tabel tersendiri — langkah 1 dari 3.

Membuat tabel Location dan kolom sementara device.site (foreign key ke
Location). Kolom teks lama device.location belum disentuh, karena isinya
masih dibutuhkan migrasi berikutnya.
"""
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0010_variable_desired_at_variable_desired_by_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Location",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True, verbose_name="name")),
                ("description", models.TextField(blank=True, verbose_name="description")),
                ("latitude", models.DecimalField(
                    decimal_places=6,
                    help_text="Between -90 and 90. Example: -7.952600",
                    max_digits=9,
                    validators=[
                        django.core.validators.MinValueValidator(Decimal("-90")),
                        django.core.validators.MaxValueValidator(Decimal("90")),
                    ],
                    verbose_name="latitude",
                )),
                ("longitude", models.DecimalField(
                    decimal_places=6,
                    help_text="Between -180 and 180. Example: 112.614500",
                    max_digits=9,
                    validators=[
                        django.core.validators.MinValueValidator(Decimal("-180")),
                        django.core.validators.MaxValueValidator(Decimal("180")),
                    ],
                    verbose_name="longitude",
                )),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created")),
            ],
            options={
                "verbose_name": "location",
                "verbose_name_plural": "locations",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="device",
            name="site",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="devices.location",
            ),
        ),
    ]
