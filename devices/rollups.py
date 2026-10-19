"""
Tabel ringkasan telemetri (tahap 16, docs/tahap-16-ringkasan-dan-status-device.md).

Chart "1 minggu" atau "1 tahun" harus membaca ratusan ribu sampai jutaan baris
Telemetry lalu meringkasnya setiap kali halaman dibuka. Tabel ringkasan
menyiapkan hasil itu SEKALI, di latar belakang:

    Telemetry (data asli)                       ~8.640 baris/hari per variabel (tiap 10 s)
      └─► TelemetryRollup resolution=60         1.440 baris/hari  (per menit)
            └─► TelemetryRollup resolution=3600    24 baris/hari  (per jam, dari baris menit)

Setiap baris ringkasan menyimpan count, total, min, max. Rata-rata = total / count.

update(now) dijalankan worker `manage.py housekeeping` setiap menit:

    1. Menit: hitung ulang ember menit pada [done_until - LATE, menit sekarang)
       langsung dari Telemetry. Mundur LATE detik (TELEMETRY_ROLLUP_LATE_SECONDS)
       karena device boleh mengirim data dengan waktu lampau (mis. data yang
       disimpan selama Wi-Fi putus). Ember menit yang sedang berjalan belum dihitung.
    2. Jam: hitung ulang jam-jam yang tersentuh langkah 1, dari baris menit
       (60 baris per jam, bukan ribuan baris asli).
    3. Catat batasnya di RollupProgress.done_until.

"Hitung ulang" = hapus ember lama dalam rentang itu, lalu tulis yang baru, dalam
satu transaksi. Cara ini sama untuk MySQL dan SQLite, dan menjalankannya dua kali
hasilnya tetap sama (idempoten).

Data yang datang LEBIH terlambat dari LATE tidak masuk ringkasan sampai
`manage.py rollup_telemetry --rebuild --since <tanggal>` dijalankan.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Min, Sum
from django.utils import timezone

from .charts import BucketIndex
from .models import RollupProgress, Telemetry, TelemetryRollup

EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
MINUTE = TelemetryRollup.Resolution.MINUTE
HOUR = TelemetryRollup.Resolution.HOUR
# Ukuran potongan kerja: rentang panjang (mis. rebuild setahun) diproses sedikit
# demi sedikit supaya memori dan lama satu transaksi tetap kecil.
MINUTE_CHUNK = timedelta(hours=6)
HOUR_CHUNK = timedelta(days=7)


def floor(moment, seconds):
    """Bulatkan ke bawah ke kelipatan `seconds` sejak 1970 (UTC). 10:07:31 -> 10:07:00."""
    stamp = int(moment.timestamp())
    return EPOCH + timedelta(seconds=stamp - stamp % seconds)


def chunks(start, end, size):
    while start < end:
        yield start, min(start + size, end)
        start += size


def progress(resolution):
    """done_until untuk satu resolusi, atau None bila belum pernah dihitung."""
    row = RollupProgress.objects.filter(resolution=resolution).first()
    return row.done_until if row else None


def _save_progress(resolution, until):
    RollupProgress.objects.update_or_create(resolution=resolution, defaults={"done_until": until})


def _rows(queryset, resolution, count, total, low, high):
    """Hasil GROUP BY (variable, idx) -> objek TelemetryRollup (belum disimpan)."""
    return [
        TelemetryRollup(
            variable_id=row["variable_id"],
            resolution=resolution,
            bucket=EPOCH + timedelta(seconds=int(row["idx"]) * resolution),
            count=row[count], total=row[total], min_value=row[low], max_value=row[high],
        )
        for row in queryset
    ]


def minute_rows(start, end):
    """Ember menit untuk SEMUA variabel pada [start, end), langsung dari Telemetry.

        SELECT variable_id, TIMESTAMPDIFF(SECOND, '1970-01-01', ts) DIV 60 AS idx,
               COUNT(*), SUM(value), MIN(value), MAX(value)
        FROM devices_telemetry WHERE ts >= %s AND ts < %s
        GROUP BY variable_id, idx;
    """
    query = (
        Telemetry.objects.filter(ts__gte=start, ts__lt=end)
        .annotate(idx=BucketIndex(EPOCH, MINUTE))
        .values("variable_id", "idx")
        .annotate(n=Count("id"), s=Sum("value"), lo=Min("value"), hi=Max("value"))
        .order_by()
    )
    return _rows(query, MINUTE, "n", "s", "lo", "hi")


def hour_rows(start, end):
    """Ember jam pada [start, end), dari baris MENIT (bukan dari data asli)."""
    query = (
        TelemetryRollup.objects.filter(resolution=MINUTE, bucket__gte=start, bucket__lt=end)
        .annotate(idx=BucketIndex(EPOCH, HOUR, field="bucket"))
        .values("variable_id", "idx")
        .annotate(n=Sum("count"), s=Sum("total"), lo=Min("min_value"), hi=Max("max_value"))
        .order_by()
    )
    return _rows(query, HOUR, "n", "s", "lo", "hi")


def _replace(resolution, start, end, rows):
    """Ganti semua ember `resolution` pada [start, end) dengan `rows`."""
    with transaction.atomic():
        TelemetryRollup.objects.filter(resolution=resolution, bucket__gte=start, bucket__lt=end).delete()
        TelemetryRollup.objects.bulk_create(rows, batch_size=1000)
    return len(rows)


def update(now=None, since=None):
    """Perbarui ringkasan menit dan jam. `since` = hitung ulang mulai waktu itu (rebuild).

    Mengembalikan {"minutes": baris menit ditulis, "hours": baris jam ditulis,
                   "from": awal rentang, "until": batas menit} atau None bila tidak ada data.
    """
    now = now or timezone.now()
    closed = floor(now, MINUTE)             # ember menit yang sudah selesai: < closed
    done = progress(MINUTE)
    if since is not None:
        start = floor(since, MINUTE)
    elif done is not None:
        start = floor(done - timedelta(seconds=settings.TELEMETRY_ROLLUP_LATE_SECONDS), MINUTE)
    else:
        first = Telemetry.objects.order_by("ts").values_list("ts", flat=True).first()
        if first is None:
            return None
        start = floor(first, MINUTE)
    if start >= closed:
        return {"minutes": 0, "hours": 0, "from": start, "until": closed}

    minutes = 0
    for chunk_start, chunk_end in chunks(start, closed, MINUTE_CHUNK):
        minutes += _replace(MINUTE, chunk_start, chunk_end, minute_rows(chunk_start, chunk_end))
    _save_progress(MINUTE, closed)

    # Jam yang tersentuh: dari awal jam `start` sampai jam terakhir yang sudah lengkap.
    hour_start, hour_end = floor(start, HOUR), floor(closed, HOUR)
    hours = 0
    for chunk_start, chunk_end in chunks(hour_start, hour_end, HOUR_CHUNK):
        hours += _replace(HOUR, chunk_start, chunk_end, hour_rows(chunk_start, chunk_end))
    if hour_end > hour_start or progress(HOUR) is None:
        _save_progress(HOUR, hour_end)
    return {"minutes": minutes, "hours": hours, "from": start, "until": closed}
