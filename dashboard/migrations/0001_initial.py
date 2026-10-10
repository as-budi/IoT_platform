# Ditulis tangan; setara dengan hasil `python manage.py makemigrations dashboard`.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("devices", "0014_device_access_token"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hide_unlocated", models.BooleanField(default=False, verbose_name="hide devices without a location")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated")),
                ("hidden_devices", models.ManyToManyField(blank=True, related_name="+", to="devices.device", verbose_name="hidden devices")),
                ("hidden_locations", models.ManyToManyField(blank=True, related_name="+", to="devices.location", verbose_name="hidden locations")),
                ("hidden_variables", models.ManyToManyField(blank=True, related_name="+", to="devices.variable", verbose_name="hidden variables")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="dashboard_preference", to=settings.AUTH_USER_MODEL, verbose_name="user")),
            ],
            options={
                "verbose_name": "dashboard preference",
                "verbose_name_plural": "dashboard preferences",
            },
        ),
    ]
