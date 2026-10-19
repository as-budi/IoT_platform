"""
Penjadwalan aturan (tahap 14): menghitung waktu jadwal dan mengambil jadwal yang jatuh tempo.

Dipakai bersama oleh ECA (event "Schedule", rules/engines/eca.py) dan rule chain
(Input "On a schedule", rules/engines/flow.py). Penjelasan lengkap:
docs/tahap-14-penjadwalan.md.

Alur di worker (`manage.py rule_engine`, setiap RULE_ENGINE_TICK detik):

    due(model, now)            jadwal aktif dengan next_run_at <= now
      └─ claim(schedule, now)  majukan next_run_at SEBELUM menjalankan aturan;
                               hanya satu proses yang berhasil "mengklaim"
           ├─ "fire"           jadwal dijalankan oleh mesin aturan
           └─ "missed"         terlambat lebih dari RULE_SCHEDULE_GRACE_SECONDS
                               (worker mati, server restart): dicatat, tidak dijalankan

Semua jam memakai zona waktu server (settings.TIME_ZONE). Perhitungan dilakukan
di jam lokal (tanggal + jam dinding), lalu diubah ke waktu aware.
"""
import re
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import RuleExecution, RuleSchedule

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TIME_TEXT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
# Cari waktu berikutnya paling jauh 8 hari ke depan (cukup untuk jadwal mingguan).
SEARCH_DAYS = 8


# --- Bentuk teks ---------------------------------------------------------------------

def parse_times(text):
    """"6:00, 18:30" -> [time(6, 0), time(18, 30)] (urut, tanpa duplikat).
    Melempar ValueError bila ada bagian yang bukan HH:MM."""
    result = set()
    for part in re.split(r"[,\s;]+", text or ""):
        if not part:
            continue
        match = TIME_TEXT.match(part)
        if not match:
            raise ValueError(f"“{part}” is not a time like 06:00")
        result.add(time(int(match[1]), int(match[2])))
    return sorted(result)


def format_times(times):
    return ",".join(t.strftime("%H:%M") for t in times)


def days_text(weekdays):
    """[0,1,2,3,4] -> "Mon–Fri"; semua hari -> "every day"."""
    days = sorted(weekdays)
    if days == list(range(7)):
        return "every day"
    if days == list(range(5)):
        return "Mon–Fri"
    if days == [5, 6]:
        return "Sat–Sun"
    return ", ".join(DAY_LABELS[d] for d in days) or "no day"


def describe(schedule):
    """Ringkasan satu kalimat (bahasa Inggris, untuk UI), mis. "Mon–Fri at 06:00, 18:00"."""
    kind = schedule.kind
    if kind == RuleSchedule.Kind.ONCE:
        if not schedule.run_at:
            return "once (no date set)"
        return "once on " + timezone.localtime(schedule.run_at).strftime("%d %b %Y %H:%M")
    days = days_text(schedule.weekday_list())
    if kind == RuleSchedule.Kind.INTERVAL:
        return f"every {schedule.interval_minutes or '?'} min, {days}"
    try:
        times = ", ".join(t.strftime("%H:%M") for t in schedule.time_list())
    except ValueError:
        times = schedule.times
    return f"{days} at {times or '?'}"


# --- Menghitung waktu berikutnya ---------------------------------------------------------

def _local(day, clock):
    """Tanggal + jam dinding di zona waktu server -> datetime aware."""
    return timezone.make_aware(datetime.combine(day, clock), timezone.get_current_timezone())


def next_run(schedule, after):
    """Waktu jadwal pertama yang SESUDAH `after` (aware), atau None bila tidak ada lagi."""
    kind = schedule.kind
    if kind == RuleSchedule.Kind.ONCE:
        return schedule.run_at if schedule.run_at and schedule.run_at > after else None

    days = set(schedule.weekday_list())
    if not days:
        return None
    start = timezone.localtime(after).date()
    if kind == RuleSchedule.Kind.DAILY:
        try:
            times = schedule.time_list()
        except ValueError:
            return None
        for offset in range(SEARCH_DAYS):
            day = start + timedelta(days=offset)
            if day.weekday() not in days:
                continue
            for clock in times:
                candidate = _local(day, clock)
                if candidate > after:
                    return candidate
        return None

    # INTERVAL: titik-titik 00:00 + k × N menit setiap hari yang berlaku.
    step = timedelta(minutes=schedule.interval_minutes or 0)
    if step <= timedelta(0):
        return None
    for offset in range(SEARCH_DAYS):
        day = start + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        midnight = _local(day, time(0, 0))
        end = _local(day + timedelta(days=1), time(0, 0))
        if after < midnight:
            return midnight
        k = (after - midnight) // step + 1
        candidate = midnight + k * step
        if candidate < end:
            return candidate
    return None


def upcoming(schedule, count, now):
    """`count` waktu jadwal berikutnya, untuk halaman detail."""
    result, moment = [], now
    for _ in range(count):
        moment = next_run(schedule, moment)
        if moment is None:
            break
        result.append(moment)
    return result


# --- Worker ------------------------------------------------------------------------------

FIRE, MISSED = "fire", "missed"


def due(model, now):
    """Jadwal aturan aktif dengan model `model` yang sudah jatuh tempo."""
    return (
        RuleSchedule.objects.filter(rule__model=model, rule__is_enabled=True, next_run_at__lte=now)
        .order_by("next_run_at")
    )


def claim(schedule, now):
    """Klaim satu jadwal yang jatuh tempo. Mengembalikan (FIRE|MISSED, waktu_jadwal, terlambat_detik)
    atau None bila proses lain sudah mengklaimnya.

    next_run_at dimajukan DULU dengan UPDATE bersyarat ("... WHERE next_run_at = waktu
    lama"). Hanya satu proses yang barisnya ikut ter-update, sehingga jadwal tidak
    dijalankan dua kali walaupun ada dua worker. Bila aturan gagal di tengah jalan,
    jadwal tetap maju (tidak diulang terus-menerus setiap tick).
    """
    scheduled = schedule.next_run_at
    following = next_run(schedule, max(now, scheduled))
    updated = RuleSchedule.objects.filter(pk=schedule.pk, next_run_at=scheduled).update(
        next_run_at=following, last_run_at=scheduled
    )
    if not updated:
        return None
    schedule.next_run_at, schedule.last_run_at = following, scheduled
    late = (now - scheduled).total_seconds()
    outcome = MISSED if late > settings.RULE_SCHEDULE_GRACE_SECONDS else FIRE
    return outcome, scheduled, late


def local_text(moment):
    return timezone.localtime(moment).strftime("%H:%M") if moment else "—"


def log_skipped(rule, now, detail):
    """Catat jadwal yang tidak dijalankan sebagai baris log "Skipped"."""
    from .events import rule_changed

    execution = RuleExecution.objects.create(
        rule=rule,
        transition=RuleExecution.Transition.SKIPPED,
        results=[{"action": "Schedule", "ok": True, "detail": detail}],
        ok=True,
        created_at=now,
    )
    state = getattr(rule, "state", None)
    if state is not None:
        rule_changed(rule, state, transition=RuleExecution.Transition.SKIPPED)
    return execution


def missed_detail(scheduled, late):
    grace = settings.RULE_SCHEDULE_GRACE_SECONDS
    return (f"Missed the {local_text(scheduled)} run: the rule engine was not running "
            f"({late:.0f} s late, limit {grace} s)")


def reschedule(rule, now=None):
    """Hitung ulang next_run_at aturan ini bila punya jadwal (setelah disimpan / diaktifkan)."""
    schedule = RuleSchedule.objects.filter(rule=rule).first()
    if schedule is not None:
        schedule.reschedule(now)
    return schedule
