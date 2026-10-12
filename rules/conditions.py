"""
Menilai ekspresi kondisi ECA: OR dari kelompok-kelompok AND, dengan logika TIGA NILAI.

Mengapa tiga nilai (benar / salah / tidak diketahui)?
    Aturan "soil_moisture < 20 AND temperature > 30" dinilai saat soil_moisture
    mengirim data. Nilai temperature diambil dari data TERAKHIR di database. Bila
    sensor suhu sudah 2 jam diam, nilainya tidak bisa dipercaya: bukan "benar",
    bukan juga "salah", melainkan TIDAK DIKETAHUI (None).

Aturan gabungannya (logika Kleene):

    AND: salah bila ada yang salah; selain itu tidak diketahui bila ada yang
         tidak diketahui; selain itu benar.
    OR : benar bila ada yang benar; selain itu tidak diketahui bila ada yang
         tidak diketahui; selain itu salah.

    A      B      A AND B   A OR B
    True   None   None      True
    False  None   False     None
    None   None   None      None

Mesin aturan hanya AKTIF bila hasilnya benar, dan hanya PULIH (clear) bila
kondisi clear benar, atau (tanpa kondisi clear) bila kondisi aktif SALAH.
"Tidak diketahui" tidak pernah memicu apa pun: aturan bertahan di keadaannya.

File ini tidak menyentuh database supaya mudah diuji dan dibaca; nilai-nilai
variabel disiapkan oleh rules/engines/eca.py (fungsi latest_values).
"""
from dataclasses import dataclass

UNKNOWN = None


def all_of(results):
    """AND tiga nilai."""
    results = list(results)
    if any(r is False for r in results):
        return False
    if any(r is None for r in results):
        return UNKNOWN
    return True


def any_of(results):
    """OR tiga nilai. Daftar kosong = salah (tidak ada grup yang terpenuhi)."""
    results = list(results)
    if any(r is True for r in results):
        return True
    if any(r is None for r in results):
        return UNKNOWN
    return False


@dataclass
class Reading:
    """Nilai terakhir sebuah variabel yang dipakai saat menilai kondisi."""

    value: float
    ts: object          # datetime (aware)
    source: str         # "message" = dari pesan yang sedang diproses, "database" = data terakhir tersimpan


def check(condition, reading, now, max_age):
    """Nilai SATU kondisi. Mengembalikan (hasil, alasan)."""
    if reading is None:
        return UNKNOWN, "no data"
    age = (now - reading.ts).total_seconds()
    if max_age and age > max_age:
        return UNKNOWN, f"stale ({age:.0f} s old)"
    return condition.test(reading.value), ""


def evaluate(groups, readings, now, max_age):
    """Nilai ekspresi (grup1) OR (grup2) OR ..., tiap grup = kondisi AND kondisi ...

    `groups`   : [[EcaCondition, ...], ...]
    `readings` : {variable_id: Reading}
    Mengembalikan (hasil, rincian). Rincian dipakai untuk log dan halaman detail:
        [{"group": 1, "result": true,
          "conditions": [{"variable": 7, "label": "Soil moisture", "operator": "<",
                          "threshold": 20, "value": 18.0, "age": 3, "source": "message",
                          "result": true, "reason": ""}, ...]}, ...]
    """
    details = []
    group_results = []
    for number, conditions in enumerate(groups, start=1):
        rows = []
        for condition in conditions:
            reading = readings.get(condition.variable_id)
            result, reason = check(condition, reading, now, max_age)
            rows.append({
                "variable": condition.variable_id,
                "label": condition.variable.display_name,
                "device": condition.variable.device.name,
                "unit": condition.variable.unit,
                "boolean": condition.variable.is_boolean,
                "operator": condition.operator,
                "operator_label": condition.get_operator_display(),
                "threshold": condition.threshold,
                "value": reading.value if reading else None,
                "age": round((now - reading.ts).total_seconds()) if reading else None,
                "source": reading.source if reading else "",
                "result": result,
                "reason": reason,
            })
        group_result = all_of(row["result"] for row in rows)
        group_results.append(group_result)
        details.append({"group": number, "result": group_result, "conditions": rows})
    return any_of(group_results), details
