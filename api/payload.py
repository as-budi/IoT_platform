"""
Membaca dan memvalidasi isi (payload) telemetri yang dikirim device.

Fungsi di sini sengaja tidak menyentuh database maupun request HTTP: masukannya
data Python hasil json.loads(), keluarannya daftar baris yang siap disimpan.
Dengan begitu aturannya mudah dibaca dan mudah dites.

Format yang diterima (sama dengan ThingsBoard):

1. Nilai saja — waktunya = saat diterima server
       {"temperature": 25.1, "humidity": 60}

2. Nilai dengan waktu sendiri (milidetik sejak 1 Jan 1970, UTC)
       {"ts": 1791436800000, "values": {"temperature": 25.1}}

3. Daftar dari format 1 atau 2, untuk mengirim beberapa bacaan sekaligus
       [{"ts": 1791436800000, "values": {...}}, {"ts": 1791436860000, "values": {...}}]

Nilai harus angka. true/false diterima dan disimpan sebagai 1/0.
"""
import math
import re
from datetime import datetime, timedelta, timezone as dt_timezone

# Sama dengan aturan Variable.key (devices/models.py).
KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
KEY_MAX_LENGTH = 50

# Batas jumlah nilai dalam satu request, supaya satu kiriman tidak membebani server.
MAX_VALUES_PER_REQUEST = 1000

# Waktu (ts) di bawah angka ini hampir pasti dikirim dalam DETIK, bukan
# milidetik (1e11 ms = Maret 1973). Kesalahan ini sangat umum di firmware.
MIN_TS_MS = 100_000_000_000
# Waktu dari masa depan masih ditoleransi sedikit (jam device bisa melenceng).
MAX_FUTURE = timedelta(minutes=5)


class PayloadError(ValueError):
    """Payload tidak valid. Pesannya dikirim balik ke device apa adanya."""


def parse_telemetry(data, now):
    """Ubah payload menjadi list (key, value, ts).

    `data` : hasil json.loads() dari body request.
    `now`  : waktu server (datetime aware), dipakai bila payload tidak membawa ts.

    Melempar PayloadError bila ada SATU saja bagian yang salah. Tidak ada
    penyimpanan sebagian: payload diterima utuh atau ditolak utuh.
    """
    is_list = isinstance(data, list)
    entries = data if is_list else [data]
    if not entries:
        raise PayloadError("Payload is empty.")

    rows = []
    for index, entry in enumerate(entries):
        where = f"Item {index}: " if is_list else ""
        if not isinstance(entry, dict):
            raise PayloadError(f"{where}expected a JSON object.")

        if "values" in entry:
            extra = set(entry) - {"ts", "values"}
            if extra:
                raise PayloadError(f"{where}unexpected field(s) next to 'values': {', '.join(sorted(extra))}.")
            values = entry["values"]
            if not isinstance(values, dict):
                raise PayloadError(f"{where}'values' must be a JSON object.")
            ts = parse_ts(entry.get("ts"), now, where) if "ts" in entry else now
        else:
            if "ts" in entry:
                raise PayloadError(
                    f'{where}to send a timestamp, use {{"ts": ..., "values": {{...}}}}.'
                )
            values = entry
            ts = now

        if not values:
            raise PayloadError(f"{where}no values to save.")
        for key, raw in values.items():
            check_key(key, where)
            rows.append((key, to_number(raw, key, where), ts))

    if len(rows) > MAX_VALUES_PER_REQUEST:
        raise PayloadError(f"Too many values in one request (maximum {MAX_VALUES_PER_REQUEST}).")
    return rows


def check_key(key, where=""):
    if len(key) > KEY_MAX_LENGTH or not KEY_PATTERN.match(key):
        raise PayloadError(
            f"{where}invalid key '{key[:60]}': use letters, digits and underscores, "
            f"starting with a letter (max {KEY_MAX_LENGTH} characters)."
        )


def to_number(raw, key, where=""):
    # bool harus dicek lebih dulu: di Python, True juga termasuk int.
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        value = float(raw)
        if not math.isfinite(value):
            raise PayloadError(f"{where}value of '{key}' must be a finite number.")
        return value
    raise PayloadError(f"{where}value of '{key}' must be a number or true/false, not {type(raw).__name__}.")


def parse_ts(raw, now, where=""):
    """Milidetik sejak epoch (UTC) -> datetime aware."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PayloadError(f"{where}'ts' must be an integer number of milliseconds since 1970-01-01 UTC.")
    if raw < MIN_TS_MS:
        raise PayloadError(f"{where}'ts' looks like seconds; send milliseconds (seconds × 1000).")
    ts = datetime.fromtimestamp(raw / 1000, tz=dt_timezone.utc)
    if ts > now + MAX_FUTURE:
        raise PayloadError(f"{where}'ts' is in the future. Check the device clock.")
    return ts
