"""Menyimpan telemetri yang sudah divalidasi ke database."""
from django.db import transaction

from devices.models import Telemetry, Variable


def save_telemetry(device, rows):
    """Simpan baris (key, value, ts) milik `device`.

    Key yang belum dikenal otomatis dibuat sebagai variabel SENSOR baru
    (mirip ThingsBoard), sehingga device baru bisa langsung mengirim data
    tanpa harus mendaftarkan variabelnya satu per satu.

    Untuk variabel AKTUATOR, telemetri ini adalah "reported state": laporan
    keadaan sebenarnya dari device. Karena itu status desired/reported di
    dashboard otomatis ikut diperbarui.

    Mengembalikan daftar key yang baru dibuat.
    """
    created_keys = []
    with transaction.atomic():
        variables = {}
        # dict.fromkeys: daftar key unik dengan urutan tetap.
        for key in dict.fromkeys(key for key, _, _ in rows):
            variable, created = Variable.objects.get_or_create(device=device, key=key)
            variables[key] = variable
            if created:
                created_keys.append(key)
        Telemetry.objects.bulk_create(
            [Telemetry(variable=variables[key], value=value, ts=ts) for key, value, ts in rows]
        )
    return created_keys
