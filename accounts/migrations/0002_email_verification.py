# Tahap 11: verifikasi email saat Sign up dan konfirmasi perubahan email.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="activation_pending",
            field=models.BooleanField(default=False, verbose_name="awaiting email confirmation"),
        ),
        migrations.AddField(
            model_name="user",
            name="pending_email",
            field=models.EmailField(blank=True, max_length=254, verbose_name="new email (unconfirmed)"),
        ),
    ]
