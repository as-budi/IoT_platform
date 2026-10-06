"""
Langkah 1 dari 3 perubahan "satu device = satu variabel".

Menambah kolom `variable` dan `unit` pada Device. Kolom `key` pada Telemetry
belum dihapus di sini, karena isinya masih dibutuhkan migrasi berikutnya.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="variable",
            field=models.CharField(
                default="",
                help_text="Besaran yang diukur. Contoh: temperature.",
                max_length=50,
                verbose_name="variabel",
            ),
            # default="" hanya untuk mengisi baris yang sudah ada saat kolom
            # ditambahkan; setelah itu kolom ini wajib diisi.
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="device",
            name="unit",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Contoh: °C, %, V. Boleh dikosongkan.",
                max_length=20,
                verbose_name="satuan",
            ),
            preserve_default=False,
        ),
    ]
