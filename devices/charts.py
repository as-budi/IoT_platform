"""
Data untuk chart history di halaman variabel.

Dua urusan ada di sini:
1. Daftar pilihan periode waktu (5 menit ... 1 tahun, custom).
2. Mengambil titik chart untuk sebuah rentang waktu, dan meringkasnya bila
   jumlahnya terlalu banyak untuk digambar browser.

Mengapa perlu diringkas? Device yang mengirim tiap 10 detik menghasilkan
~260.000 baris per bulan dan ~3 juta baris per tahun. Mengirim semuanya ke
browser membuat halaman berat, padahal layar hanya selebar ~1000 piksel.
Karena itu rentang waktu dibagi menjadi paling banyak CHART_MAX_POINTS
"ember" (bucket) sama lebar, lalu database menghitung RATA-RATA tiap ember.
Cara ini juga dipakai platform IoT lain (ThingsBoard menyebutnya
"aggregation").

Sejak tahap 16, periode panjang tidak lagi meringkas data asli setiap kali
halaman dibuka, tetapi membaca TABEL RINGKASAN (devices/rollups.py):

    periode <= 1 hari    data asli (Telemetry)
    periode <= 7 hari    ringkasan per menit  + data asli untuk bagian terbaru
    periode  > 7 hari    ringkasan per jam    + data asli untuk bagian terbaru

"Bagian terbaru" = waktu setelah RollupProgress.done_until, yang belum diringkas
worker. Bila ringkasan belum pernah dibuat (worker housekeeping belum berjalan),
chart memakai data asli seperti sebelumnya.
"""
import math
from datetime import timedelta

from django.db.models import Avg, Count, DateTimeField, F, Func, IntegerField, Max, Sum, Value

# Batas atas jumlah titik yang dikirim ke browser.
CHART_MAX_POINTS = 1000

# key -> (label tombol, panjang periode). Urutan di sini = urutan tombol.
# "custom" tidak punya panjang tetap: awal dan akhirnya diisi pengguna.
PERIODS = {
    "5m": ("5 min", timedelta(minutes=5)),
    "10m": ("10 min", timedelta(minutes=10)),
    "30m": ("30 min", timedelta(minutes=30)),
    "1h": ("1 hour", timedelta(hours=1)),
    "1d": ("1 day", timedelta(days=1)),
    "1w": ("1 week", timedelta(weeks=1)),
    "1mo": ("1 month", timedelta(days=30)),
    "1y": ("1 year", timedelta(days=365)),
    "custom": ("Custom", None),
}
DEFAULT_PERIOD = "1d"
CUSTOM = "custom"

# Sampai panjang ini chart memakai data asli; di atasnya tabel ringkasan (tahap 16).
RAW_MAX_SPAN = timedelta(days=1)
MINUTE_MAX_SPAN = timedelta(days=7)


class BucketIndex(Func):
    """Nomor ember waktu untuk setiap baris: (detik sejak `start`) DIV lebar ember.

    SQL yang dihasilkan (khusus MySQL/MariaDB):
        TIMESTAMPDIFF(SECOND, '2026-10-07 03:00:00', ts) DIV 60
    Baris pukul 03:00:00–03:00:59 mendapat nomor 0, 03:01:00–03:01:59 nomor 1,
    dan seterusnya. Baris dengan nomor yang sama lalu dikelompokkan (GROUP BY).

    TIMESTAMPDIFF menghitung selisih dua waktu yang sama-sama tersimpan dalam
    UTC, sehingga tidak bergantung pada tabel zona waktu MySQL.
    """

    template = "(TIMESTAMPDIFF(SECOND, %(expressions)s) DIV %(width)s)"
    output_field = IntegerField()

    def __init__(self, start, width, field="ts"):
        # `width` adalah int buatan kita sendiri (bukan input pengguna), jadi
        # aman ditulis langsung ke SQL. `field`: kolom waktu (Telemetry.ts, atau
        # TelemetryRollup.bucket untuk tabel ringkasan, tahap 16).
        super().__init__(Value(start, output_field=DateTimeField()), F(field), width=int(width))

    def as_sqlite(self, compiler, connection, **extra_context):
        # SQLite (dipakai bila tes dijalankan tanpa MySQL) tidak punya
        # TIMESTAMPDIFF; selisih detik dihitung lewat julianday().
        # Hasilnya: julianday(start) - julianday(ts), lalu dibalik tandanya.
        return self.as_sql(
            compiler,
            connection,
            template="(CAST(ROUND((julianday(%(expressions)s)) * -86400) AS INTEGER) / %(width)s)",
            arg_joiner=") - julianday(",
            **extra_context,
        )


def to_ms(dt):
    """datetime -> milidetik sejak 1970 (format waktu yang dipakai Chart.js)."""
    return int(dt.timestamp() * 1000)


def describe_seconds(seconds):
    """3600 -> '1 h', 90 -> '1 min 30 s'. Untuk keterangan di bawah judul chart."""
    parts = []
    for size, name in ((86400, "d"), (3600, "h"), (60, "min"), (1, "s")):
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount} {name}")
    return " ".join(parts[:2]) or "0 s"


def chart_series(variable, start, end):
    """Titik chart `variable` antara `start` dan `end`.

    Mengembalikan dict:
        points          [{"x": milidetik, "y": nilai}, ...], urut lama -> baru
        rows            jumlah baris telemetri asli dalam rentang ini
        bucket_seconds  0 bila titik = data asli; selain itu lebar ember (detik)
        source          0 = data asli; 60 / 3600 = tabel ringkasan per menit / per jam
    """
    span = end - start
    if span > RAW_MAX_SPAN:
        from .rollups import HOUR, MINUTE, progress

        resolution = MINUTE if span <= MINUTE_MAX_SPAN else HOUR
        done = progress(resolution)
        if done is not None and done > start:
            return rollup_series(variable, start, end, resolution, done)
    return raw_series(variable, start, end)


def raw_series(variable, start, end):
    """Chart dari data asli: apa adanya bila sedikit, diringkas di database bila banyak."""
    rows = variable.telemetry.filter(ts__gte=start, ts__lte=end)
    total = rows.count()

    # Cukup sedikit: kirim data asli apa adanya.
    if total <= CHART_MAX_POINTS:
        points = [
            {"x": to_ms(ts), "y": value}
            for ts, value in rows.order_by("ts").values_list("ts", "value")
        ]
        return {"points": points, "rows": total, "bucket_seconds": 0, "source": 0}

    # Terlalu banyak: bagi rentang menjadi <= CHART_MAX_POINTS ember.
    span = (end - start).total_seconds()
    width = max(1, math.ceil(span / CHART_MAX_POINTS))
    # Angka diringkas dengan rata-rata. Untuk ON/OFF rata-rata (mis. 0,4) tidak
    # bermakna, jadi dipakai nilai maksimum: "pernah ON dalam ember ini".
    summary = Max("value") if variable.is_boolean else Avg("value")
    buckets = (
        rows.annotate(bucket=BucketIndex(start, width))
        .values("bucket")              # GROUP BY bucket
        .annotate(y=summary)
        .order_by("bucket")
    )
    start_ms = to_ms(start)
    points = [
        # Titik diletakkan di tengah embernya.
        {"x": start_ms + int((int(b["bucket"]) + 0.5) * width * 1000), "y": b["y"]}
        for b in buckets
    ]
    return {"points": points, "rows": total, "bucket_seconds": width, "source": 0}


def rollup_series(variable, start, end, resolution, done):
    """Chart dari tabel ringkasan (sebelum `done`) + data asli (sesudahnya).

    Lebar ember chart = kelipatan `resolution`, dan ember dihitung dari titik nol
    1970, sehingga satu baris ringkasan selalu masuk ke tepat SATU ember chart.
    Dari kedua sumber diambil jumlah (n), total (s), dan maksimum (hi) per ember,
    lalu digabung: rata-rata = total gabungan / n gabungan.
    """
    from .rollups import floor

    seconds = (end - start).total_seconds()
    width = max(resolution, math.ceil(seconds / CHART_MAX_POINTS / resolution) * resolution)
    origin = floor(start, width)
    summary = (
        variable.rollups.filter(resolution=resolution, bucket__gte=floor(start, resolution), bucket__lt=done)
        .annotate(idx=BucketIndex(origin, width, field="bucket"))
        .values("idx")
        .annotate(n=Sum("count"), s=Sum("total"), hi=Max("max_value"))
        .order_by()
    )
    recent = (
        variable.telemetry.filter(ts__gte=max(start, done), ts__lte=end)
        .annotate(idx=BucketIndex(origin, width))
        .values("idx")
        .annotate(n=Count("id"), s=Sum("value"), hi=Max("value"))
        .order_by()
    )
    buckets = {}
    for row in list(summary) + list(recent):
        n, total, high = buckets.get(int(row["idx"]), (0, 0.0, None))
        hi = row["hi"] if high is None else max(high, row["hi"])
        buckets[int(row["idx"])] = (n + row["n"], total + row["s"], hi)

    origin_ms = to_ms(origin)
    points = [
        {
            "x": origin_ms + int((idx + 0.5) * width * 1000),
            # ON/OFF: "pernah ON dalam ember ini" (maksimum), angka: rata-rata.
            "y": high if variable.is_boolean else round(total / n, 6),
        }
        for idx, (n, total, high) in sorted(buckets.items())
    ]
    rows = sum(n for n, _, _ in buckets.values())
    return {"points": points, "rows": rows, "bucket_seconds": width, "source": resolution}
