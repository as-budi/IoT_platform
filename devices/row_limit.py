"""
Batas baris telemetri per variabel (docs/tahap-27-tambahan-batas-baris-variabel.md).

Variabel yang sudah menyimpan N baris tidak menerima data lagi, sampai pemiliknya
menghapus datanya (tombol "Delete data", yang lebih dulu mengunduh CSV-nya).

Kenapa penghitung, bukan COUNT(*)?
    COUNT(*) membaca SEMUA baris telemetri variabel itu, dan pesan datang terus-menerus.
    Di sini setiap variabel menyimpan jumlah barisnya sendiri (Variable.telemetry_rows):
      - diisi sekali oleh migrasi devices/0029 (dan oleh `manage.py recount_telemetry`
        setelah data dihapus manual lewat SQL);
      - naik di save_telemetry() lewat reserve();
      - turun di "Delete data" lewat release().
    Pemeriksaan batas hanya terjadi saat data masuk, memakai baris variabel yang memang
    sudah dibaca, ditambah satu UPDATE bersyarat per variabel:
        UPDATE devices_variable SET telemetry_rows = telemetry_rows + 2
        WHERE id = 11020 AND telemetry_rows + 2 <= 1000
    1 baris berubah = tempat masih ada; 0 = penuh (pesan lain lebih dulu mengisinya).
    UPDATE ini aman walaupun pesan untuk satu device tiba bersamaan di proses berbeda
    (web untuk HTTP, mqtt_bridge, coap_server).

Batasnya milik AKUN pemilik device: User.telemetry_max_rows (diatur Admin), kosong =
TELEMETRY_MAX_ROWS_PER_VARIABLE dari .env, 0 = tanpa batas.
"""
from collections import Counter

from django.db.models import Count, F
from django.db.models.functions import Greatest

from api.payload import PayloadError

from .models import Telemetry, Variable


class VariableFull(PayloadError):
    """Satu atau beberapa variabel sudah mencapai batas barisnya.

    Turunan PayloadError: MQTT mengirim pesannya ke topik errors seperti kesalahan
    payload; HTTP menjawab 409 dan CoAP 4.09 (api/views.py, coap_gateway/handlers.py).
    """

    def __init__(self, keys, limit):
        self.keys = list(keys)
        self.limit = limit
        names = ", ".join(self.keys)
        noun = "Variable" if len(self.keys) == 1 else "Variables"
        verb = "is" if len(self.keys) == 1 else "are"
        super().__init__(
            f"{noun} {names} {verb} full ({limit:,} rows, the limit of this account). "
            "The message was not saved. Delete the variable's data on IoT Platform to receive new data."
        )


# Mulai dari bagian ini variabel ditandai "hampir penuh" (kuning) di halaman variabel, halaman
# device, dan dashboard, sebelum datanya ditolak pada 100% ("Full", merah).
WARN_FRACTION = 0.9


def usage(rows, limit):
    """"full", "near" (>= 90%), atau None. Dari penghitung saja: tidak ada query."""
    if not limit:
        return None
    if rows >= limit:
        return "full"
    if rows >= limit * WARN_FRACTION:
        return "near"
    return None


def percent(rows, limit):
    """Persentase terpakai, dibulatkan KE BAWAH (999 dari 1000 = 99%, bukan 100%)."""
    return min(100, rows * 100 // limit) if limit else 0


def limit_for(device):
    """Batas baris per variabel untuk device ini (0 = tanpa batas)."""
    from accounts.models import User

    owner = User.objects.filter(pk=device.owner_id).only("telemetry_max_rows").first()
    return owner.telemetry_row_limit if owner else 0


def reserve(device, variables, rows):
    """Tambah penghitung variabel-variabel ini dengan jumlah baris baru di `rows`.

    `variables`: {key: Variable} (sudah dibaca pemanggil); `rows`: [(key, value, ts), ...].
    Dipanggil di DALAM transaksi penyimpanan: bila melempar VariableFull, transaksi
    dibatalkan dan tidak ada yang tersimpan (pesan ditolak utuh). Mengembalikan daftar
    variabel yang TEPAT menjadi penuh oleh pesan ini (untuk log aktivitas).
    """
    added = Counter(key for key, _, _ in rows)
    limit = limit_for(device)
    if limit:
        # Penyaringan awal tanpa query: nilai yang dibaca bersama variabelnya.
        full = [key for key, n in added.items() if variables[key].telemetry_rows + n > limit]
        if full:
            raise VariableFull(full, limit)
    became_full = []
    for key, n in added.items():
        variable = variables[key]
        updates = Variable.objects.filter(pk=variable.pk)
        if limit:
            updates = updates.filter(telemetry_rows__lte=limit - n)
        if not updates.update(telemetry_rows=F("telemetry_rows") + n):
            raise VariableFull([key], limit)     # pesan lain mengisinya lebih dulu
        variable.telemetry_rows += n             # perkiraan; nilai pastinya ada di database
        if limit and variable.telemetry_rows >= limit:
            became_full.append(variable)
    return became_full


def release(variable, deleted):
    """Kurangi penghitung setelah `deleted` baris telemetri dihapus (tidak pernah < 0)."""
    if deleted:
        Variable.objects.filter(pk=variable.pk).update(telemetry_rows=Greatest(F("telemetry_rows") - deleted, 0))


def recount(variables=None):
    """Hitung ulang penghitung dari tabel telemetri (satu COUNT ... GROUP BY).

    Untuk setelah data telemetri dihapus/diisi di luar aplikasi (SQL manual, seed_demo).
    `variables`: queryset Variable; bawaan semua. Mengembalikan jumlah variabel yang diubah.
    """
    variables = Variable.objects.all() if variables is None else variables
    counts = dict(
        Telemetry.objects.filter(variable__in=variables).order_by()
        .values_list("variable_id").annotate(n=Count("id"))
    )
    changed = 0
    for pk, current in variables.values_list("pk", "telemetry_rows"):
        actual = counts.get(pk, 0)
        if actual != current:
            Variable.objects.filter(pk=pk).update(telemetry_rows=actual)
            changed += 1
    return changed
