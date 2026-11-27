# Batas baris telemetri per variabel per akun (docs/tahap-27-tambahan-batas-baris-variabel.md).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_user_ui_style'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='telemetry_max_rows',
            field=models.PositiveIntegerField(blank=True, help_text="Empty = server default. 0 = no limit. Applies to every variable of this account's devices.", null=True, verbose_name='telemetry rows per variable'),
        ),
    ]
