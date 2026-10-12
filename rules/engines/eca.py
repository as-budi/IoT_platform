"""
Model a — Event–Condition–Action (ECA), DIIMPLEMENTASIKAN di tahap 7.

    WHEN  data baru untuk soil_moisture atau temperature             (event)
    IF    (soil_moisture < 20 AND temperature > 30) OR soil_moisture < 10
          selama 60 detik                                             (condition + durasi)
    THEN  pump = ON, alarm, email                                     (aksi "on activate")
    CLEAR bila soil_moisture > 30                                     (histeresis)
    THEN  pump = OFF                                                  (aksi "on clear")

Penilaian ekspresinya ada di rules/conditions.py (logika tiga nilai); nilai
variabel diambil di latest_values(). Penjelasan lengkap dengan contoh:
docs/eca-mekanisme.md.

Setiap aturan punya keadaan (RuleState) dengan tiga status:

            kondisi terpenuhi                durasi & cooldown terlewati
    IDLE ───────────────────────► PENDING ─────────────────────────────► ACTIVE
      ▲                              │                                       │
      └──── kondisi tidak terpenuhi ─┘                                       │
      └───────────────────────── kondisi clear terpenuhi ◄───────────────────┘

Aksi hanya dijalankan saat BERPINDAH ke ACTIVE (activate) atau dari ACTIVE ke
IDLE (clear), bukan pada setiap pesan. Inilah yang mencegah:
- pompa dinyalakan berulang-ulang setiap 10 detik selama tanah kering,
- email banjir,
- loop: aksi set pump -> device melapor pump=1 -> aturan dinilai lagi -> tetap
  ACTIVE, tidak ada aksi baru.
"""
import logging

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils.dateparse import parse_datetime

from devices.models import Telemetry, Variable

from .. import conditions
from ..events import rule_changed, rule_evaluated
from ..models import EcaSpec, Rule, RuleExecution, RuleModel, RuleState
from .base import RuleEngine

logger = logging.getLogger(__name__)

ACTIVATE = RuleExecution.Transition.ACTIVATE
CLEAR = RuleExecution.Transition.CLEAR
Status = RuleState.Status


def step(state, spec, condition, clear, now):
    """Satu langkah mesin keadaan. Mengubah `state` (belum disimpan) dan
    mengembalikan ACTIVATE, CLEAR, atau None.

    Fungsi ini sengaja tidak menyentuh database supaya mudah diuji dan dibaca.
    """
    if state.status == Status.ACTIVE:
        if clear:
            state.status = Status.IDLE
            state.pending_since = None
            state.last_cleared_at = now
            return CLEAR
        return None

    # IDLE atau PENDING
    if not condition:
        state.status = Status.IDLE
        state.pending_since = None
        return None
    if state.status == Status.IDLE:
        state.status = Status.PENDING
        state.pending_since = now

    # Kapan boleh aktif: setelah durasi terpenuhi DAN cooldown sejak aktivasi terakhir lewat.
    due = state.pending_since.timestamp() + spec.duration_seconds
    if spec.cooldown_seconds and state.last_activated_at:
        due = max(due, state.last_activated_at.timestamp() + spec.cooldown_seconds)
    if now.timestamp() >= due:
        state.status = Status.ACTIVE
        state.pending_since = None
        state.last_activated_at = now
        return ACTIVATE
    return None


class EcaEngine(RuleEngine):
    key = RuleModel.ECA
    implemented = True
    summary = "When an event arrives, check a condition (AND/OR of variable comparisons); if it holds, run the actions."
    example = "WHEN soil_moisture or temperature arrives IF (soil < 20 AND temp > 30) OR soil < 10 THEN pump ON"
    best_for = "Automations and threshold alarms built from a form."
    state = "Small (Idle / Pending / Active per rule)"

    def rules(self):
        return (
            Rule.objects.filter(model=RuleModel.ECA, is_enabled=True)
            .select_related("owner", "eca", "eca__device")
            .prefetch_related("eca__conditions__variable__device")
        )

    # --- pesan masuk -------------------------------------------------------------

    def on_message(self, message, now):
        """Nilai aturan yang dipicu pesan ini. Mengembalikan daftar RuleExecution."""
        if message.get("type") != "TELEMETRY":
            return []
        executions = []
        device_id = message["originator"]["device"]
        # Nilai TERBARU tiap variabel di pesan ini (sebuah pesan bisa memuat
        # beberapa titik waktu untuk variabel yang sama).
        points = {}
        for point in sorted(message.get("data", []), key=lambda p: p.get("ts", "")):
            points[point["variable"]] = point

        # 1) Aturan yang kondisinya (aktif ATAU clear) menyebut salah satu variabel di pesan.
        candidates = self.rules().filter(
            eca__event=EcaSpec.Event.TELEMETRY, eca__conditions__variable_id__in=list(points)
        ).distinct()
        for rule in candidates:
            executions += self.evaluate(rule, now, points=points)

        # 2) Device mengirim data -> aturan "device diam" untuk device ini pulih.
        for rule in self.rules().filter(eca__event=EcaSpec.Event.INACTIVITY, eca__device_id=device_id):
            executions += self.evaluate(rule, now, value=0, condition=False, clear=True)
        return executions

    # --- pemeriksaan berkala --------------------------------------------------------

    def tick(self, now):
        """Aturan yang bergantung pada waktu, walaupun tidak ada pesan baru."""
        executions = []
        # 1) "... selama 60 detik": sudah waktunya? Kondisi dinilai ULANG dengan
        #    data terakhir di database (nilai lain bisa sudah basi sejak PENDING).
        pending = self.rules().filter(eca__event=EcaSpec.Event.TELEMETRY, state__status=Status.PENDING)
        for rule in pending:
            executions += self.evaluate(rule, now)
        # 2) "device tidak mengirim data selama N detik".
        for rule in self.rules().filter(eca__event=EcaSpec.Event.INACTIVITY):
            device = rule.eca.device
            last = device.last_seen_at or device.created_at
            silent_for = (now - last).total_seconds()
            silent = silent_for >= (rule.eca.silence_seconds or 0)
            executions += self.evaluate(rule, now, value=round(silent_for), condition=silent, clear=not silent)
        return executions

    # --- inti ---------------------------------------------------------------------

    def judge(self, rule, now, points):
        """Langkah 2-3 dari docs/eca-mekanisme.md: ambil nilai, nilai ekspresi.

        Mengembalikan (condition, clear, trigger, snapshot):
          condition  boleh aktif?   (ekspresi aktif BENAR, dan ekspresi clear tidak benar)
          clear      boleh pulih?   (ekspresi clear BENAR; tanpa clear: ekspresi aktif SALAH)
          trigger    (variabel, nilai) dari pesan yang memicu penilaian, atau None
        """
        spec = rule.eca
        readings = latest_values(spec.variable_ids(), points, now)
        when_result, when_details = conditions.evaluate(spec.when_groups, readings, now, spec.max_age_seconds)
        if spec.has_hysteresis:
            clear_result, clear_details = conditions.evaluate(
                spec.clear_groups, readings, now, spec.max_age_seconds
            )
            clear = clear_result is True
            # Bila kedua ekspresi benar bersamaan, aturan tidak diaktifkan
            # (mencegah aktif lalu langsung pulih, berulang-ulang).
            condition = when_result is True and not clear
        else:
            clear_result, clear_details = (None if when_result is None else not when_result), []
            clear = when_result is False      # "tidak diketahui" TIDAK memulihkan aturan
            condition = when_result is True

        trigger = None
        for condition_row in spec.condition_list():
            if points and condition_row.variable_id in points:
                trigger = (condition_row.variable, points[condition_row.variable_id]["value"])
                break
        snapshot = {
            "at": now.isoformat(),
            "when": {"result": when_result, "groups": when_details},
            "clear": {"result": clear_result, "groups": clear_details},
            "values": values_by_label(spec, readings),
        }
        return condition, clear, trigger, snapshot

    def evaluate(self, rule, now, points=None, value=None, condition=None, clear=None):
        """Nilai satu aturan. Bila status berpindah, jalankan aksinya.

        `points`: nilai dari pesan yang sedang diproses {variable_id: point}.
        `condition`/`clear` diisi langsung untuk event "device diam".
        """
        spec = rule.eca
        snapshot, trigger = {}, None
        if condition is None:
            condition, clear, trigger, snapshot = self.judge(rule, now, points or {})
            value = trigger[1] if trigger else None

        RuleState.objects.get_or_create(rule=rule)
        with transaction.atomic():
            # Kunci baris state: dua proses (mis. web dan worker) tidak boleh
            # menilai aturan yang sama bersamaan lalu sama-sama menjalankan aksi.
            state = RuleState.objects.select_for_update().get(rule=rule)
            previous_status = state.status
            transition = step(state, spec, condition, clear, now)
            if value is not None:
                state.last_value = value
            state.last_evaluated_at = now
            state.snapshot = snapshot
            state.save()
        rule.state = state

        if transition is None:
            if state.status != previous_status:  # mis. IDLE -> PENDING
                rule_changed(rule, state)
            elif points:  # dinilai karena data baru; status sama
                rule_evaluated(rule)
            return []
        # Aksi dijalankan SETELAH keadaan tersimpan: bila sebuah aksi gagal,
        # aturan tetap tercatat ACTIVE dan tidak mengulang aksi yang lain.
        from ..actions import run_actions

        context = self.context(rule, value, trigger, snapshot)
        results = run_actions(rule, transition, value, context=context)
        execution = RuleExecution.objects.create(
            rule=rule,
            transition=transition,
            value=value,
            results=results,
            ok=all(r["ok"] for r in results),
            snapshot=snapshot.get("values", {}),
            created_at=now,
        )
        logger.info("Rule %s %s (value %s)", rule.pk, transition, value)
        # Kabar dikirim SETELAH log tersimpan, supaya halaman yang memuat ulang
        # langsung menampilkan baris log dan alarm yang baru.
        rule_changed(rule, state, transition=transition, value=value)
        return [execution]

    def context(self, rule, value, trigger, snapshot):
        """Nilai untuk placeholder pesan alarm/email dan device asal (originator)."""
        spec = rule.eca
        if spec.event == EcaSpec.Event.TELEMETRY:
            primary = spec.primary_condition()
            device = primary.variable.device
            variable = trigger[0].display_name if trigger else primary.variable.display_name
            threshold = primary.threshold
        else:
            device = spec.device
            variable = "no data (s)"
            threshold = spec.silence_seconds
        values = ", ".join(f"{label} = {fmt(v)}" for label, v in snapshot.get("values", {}).items())
        return {
            "rule": rule.name,
            "device": device.name,
            "variable": variable,
            "value": value,
            "threshold": threshold,
            "values": values or "—",
            "originator": device,
        }

    def describe(self, rule):
        return describe_eca(rule.eca)


def latest_values(variable_ids, points, now):
    """Nilai terakhir setiap variabel kondisi: {variable_id: Reading}.

    - Variabel yang ADA di pesan yang sedang diproses: nilai dari pesan itu.
    - Variabel lain: baris telemetri TERBARU di database, dengan SATU query:

        SELECT v.id,
               (SELECT t.value FROM devices_telemetry t WHERE t.variable_id = v.id
                ORDER BY t.ts DESC LIMIT 1) AS last_value,
               (SELECT t.ts    FROM devices_telemetry t WHERE t.variable_id = v.id
                ORDER BY t.ts DESC LIMIT 1) AS last_ts
        FROM devices_variable v WHERE v.id IN (7, 9);

      Subquery itu cepat karena tabel telemetri punya index (variable_id, ts).
    """
    readings = {}
    missing = [pk for pk in variable_ids if pk not in points]
    if missing:
        newest = Telemetry.objects.filter(variable=OuterRef("pk")).order_by("-ts")
        rows = (
            Variable.objects.filter(pk__in=missing)
            .annotate(last_value=Subquery(newest.values("value")[:1]),
                      last_ts=Subquery(newest.values("ts")[:1]))
            .order_by()  # tanpa ORDER BY bawaan model: tidak diperlukan di sini
            .values_list("pk", "last_value", "last_ts")
        )
        for pk, last_value, last_ts in rows:
            if last_ts is not None:
                readings[pk] = conditions.Reading(last_value, last_ts, "database")
    for pk in variable_ids:
        if pk in points:
            point = points[pk]
            ts = parse_datetime(point["ts"]) if isinstance(point.get("ts"), str) else (point.get("ts") or now)
            readings[pk] = conditions.Reading(point["value"], ts or now, "message")
    return readings


def values_by_label(spec, readings):
    """{"Greenhouse Node · Soil moisture": 18.0, ...} untuk log dan pesan."""
    seen = {}
    for condition in spec.condition_list():
        label = f"{condition.variable.device.name} · {condition.variable.display_name}"
        reading = readings.get(condition.variable_id)
        seen.setdefault(label, reading.value if reading else None)
    return seen


def fmt(number):
    return "—" if number is None else f"{number:g}"


def expression_text(groups):
    """[[a, b], [c]] -> "(Soil moisture < 20 AND Temperature > 30) OR Soil moisture < 10"."""
    parts = []
    for conditions_in_group in groups:
        text = " AND ".join(
            f"{c.variable.display_name} {c.get_operator_display()} {fmt(c.threshold)}" for c in conditions_in_group
        )
        parts.append(f"({text})" if len(conditions_in_group) > 1 and len(groups) > 1 else text)
    return " OR ".join(parts) or "—"


def describe_eca(spec):
    """{"when": ..., "if": ..., "clear": ...} dalam kalimat pendek (bahasa Inggris, untuk UI)."""
    if spec.event == EcaSpec.Event.INACTIVITY:
        device = spec.device.name if spec.device_id else "?"
        when = f"{device} sends no data"
        condition = f"for {fmt(spec.silence_seconds)} s"
        clear = "the device sends data again"
    else:
        names = []
        for c in spec.condition_list():
            name = f"{c.variable.device.name} · {c.variable.display_name}"
            if name not in names:
                names.append(name)
        when = "new data for " + (", ".join(names) or "?")
        condition = expression_text(spec.when_groups)
        if spec.duration_seconds:
            condition += f" for {spec.duration_seconds} s"
        clear = expression_text(spec.clear_groups) if spec.has_hysteresis else "the condition is false"
    if spec.cooldown_seconds:
        condition += f" (cooldown {spec.cooldown_seconds} s)"
    return {"when": when, "if": condition, "clear": clear}
