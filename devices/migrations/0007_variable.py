"""
Kembali ke model "satu device = banyak variabel" — langkah 1 dari 3.

Membuat tabel Variable dan menambah kolom telemetry.variable. Kolom itu
sementara boleh kosong (null=True), karena baris telemetri lama baru akan
diisi pada migrasi berikutnya.
"""
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0006_device_location"),
    ]

    operations = [
        migrations.CreateModel(
            name="Variable",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(
                    help_text="Name used in the data the device sends. Example: temperature.",
                    max_length=50,
                    validators=[django.core.validators.RegexValidator(
                        "^[A-Za-z][A-Za-z0-9_]*$",
                        "Use letters, digits and underscores only, starting with a letter.",
                    )],
                    verbose_name="key",
                )),
                ("label", models.CharField(blank=True, help_text="Display name. Leave blank to show the key.", max_length=100, verbose_name="label")),
                ("unit", models.CharField(blank=True, help_text="Example: °C, %, V. May be left blank.", max_length=20, verbose_name="unit")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created")),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="variables", to="devices.device", verbose_name="device")),
            ],
            options={
                "verbose_name": "variable",
                "verbose_name_plural": "variables",
                "ordering": ["key"],
                "constraints": [
                    models.UniqueConstraint(fields=("device", "key"), name="unique_variable_key_per_device"),
                ],
            },
        ),
        migrations.AddField(
            model_name="telemetry",
            name="variable",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="telemetry",
                to="devices.variable",
                verbose_name="variable",
            ),
        ),
    ]
