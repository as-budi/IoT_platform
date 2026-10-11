"""
Model a — Event–Condition–Action (ECA), DIIMPLEMENTASIKAN di tahap 7.

    WHEN  telemetri baru untuk soil_moisture        (event)
    IF    soil_moisture < 20 selama 60 detik         (condition + durasi)
    THEN  pump = ON, alarm, email                    (aksi "on activate")
    CLEAR bila soil_moisture > 30                    (histeresis)
    THEN  pump = OFF                                 (aksi "on clear")

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
    summary = "When an event arrives, check one condition; if it holds, run the actions."
    example = "WHEN soil_moisture arrives IF < 20 for 60 s THEN pump ON; CLEAR when > 30 THEN pump OFF"
    best_for = "Simple automations and threshold alarms built from a form."
    state = "Small (Idle / Pending / Active per rule)"

    def rules(self):
        return (
            Rule.objects.filter(model=RuleModel.ECA, is_enabled=True)
            .select_related("owner", "eca", "eca__variable__device", "eca__device")
        )

    # --- pesan masuk -------------------------------------------------------------

    def on_message(self, message, now):
        """Nilai aturan yang dipicu pesan ini. Mengembalikan daftar RuleExecution."""
        if message.get("type") != "TELEMETRY":
            return []
        executions = []
        device_id = message["originator"]["device"]
        points = sorted(message.get("data", []), key=lambda p: p.get("ts", ""))
        variable_ids = {p["variable"] for p in points}

        for rule in self.rules().filter(eca__event=EcaSpec.Event.TELEMETRY, eca__variable_id__in=variable_ids):
            for point in points:
                if point["variable"] == rule.eca.variable_id:
                    executions += self.evaluate(rule, point["value"], now)

        # Device mengirim data -> aturan "device diam" untuk device ini pulih.
        for rule in self.rules().filter(eca__event=EcaSpec.Event.INACTIVITY, eca__device_id=device_id):
            executions += self.evaluate(rule, 0, now, condition=False, clear=True)
        return executions

    # --- pemeriksaan berkala --------------------------------------------------------

    def tick(self, now):
        """Aturan yang bergantung pada waktu, walaupun tidak ada pesan baru."""
        executions = []
        # 1) "< 20 selama 60 detik": sudah 60 detik sejak PENDING?
        pending = self.rules().filter(eca__event=EcaSpec.Event.TELEMETRY, state__status=Status.PENDING)
        for rule in pending:
            executions += self.evaluate(rule, rule.state.last_value, now)
        # 2) "device tidak mengirim data selama N detik".
        for rule in self.rules().filter(eca__event=EcaSpec.Event.INACTIVITY):
            device = rule.eca.device
            last = device.last_seen_at or device.created_at
            silent_for = (now - last).total_seconds()
            silent = silent_for >= (rule.eca.silence_seconds or 0)
            executions += self.evaluate(rule, round(silent_for), now, condition=silent, clear=not silent)
        return executions

    # --- inti ---------------------------------------------------------------------

    def evaluate(self, rule, value, now, condition=None, clear=None):
        """Nilai satu aturan untuk satu nilai. Bila status berpindah, jalankan aksinya."""
        spec = rule.eca
        if condition is None:
            condition = spec.condition_met(value)
            clear = spec.clear_met(value)

        RuleState.objects.get_or_create(rule=rule)
        with transaction.atomic():
            # Kunci baris state: dua proses (mis. web dan worker) tidak boleh
            # menilai aturan yang sama bersamaan lalu sama-sama menjalankan aksi.
            state = RuleState.objects.select_for_update().get(rule=rule)
            transition = step(state, spec, condition, clear, now)
            if value is not None:
                state.last_value = value
            state.last_evaluated_at = now
            state.save()
        rule.state = state

        if transition is None:
            return []
        # Aksi dijalankan SETELAH keadaan tersimpan: bila sebuah aksi gagal,
        # aturan tetap tercatat ACTIVE dan tidak mengulang aksi yang lain.
        from ..actions import run_actions

        results = run_actions(rule, transition, value, context=self.context(rule, value))
        execution = RuleExecution.objects.create(
            rule=rule,
            transition=transition,
            value=value,
            results=results,
            ok=all(r["ok"] for r in results),
            created_at=now,
        )
        logger.info("Rule %s %s (value %s)", rule.pk, transition, value)
        return [execution]

    def context(self, rule, value):
        """Nilai untuk placeholder pesan alarm/email dan perangkat asal (originator)."""
        spec = rule.eca
        if spec.event == EcaSpec.Event.TELEMETRY:
            device = spec.variable.device
            variable = spec.variable.display_name
            threshold = spec.threshold
        else:
            device = spec.device
            variable = "no data (s)"
            threshold = spec.silence_seconds
        return {
            "rule": rule.name,
            "device": device.name,
            "variable": variable,
            "value": value,
            "threshold": threshold,
            "originator": device,
        }

    def describe(self, rule):
        return describe_eca(rule.eca)


def fmt(number):
    return "—" if number is None else f"{number:g}"


def describe_eca(spec):
    """{"when": ..., "if": ..., "clear": ...} dalam kalimat pendek (bahasa Inggris, untuk UI)."""
    if spec.event == EcaSpec.Event.INACTIVITY:
        device = spec.device.name if spec.device_id else "?"
        when = f"{device} sends no data"
        condition = f"for {fmt(spec.silence_seconds)} s"
        clear = "the device sends data again"
    else:
        variable = spec.variable
        name = f"{variable.device.name} · {variable.display_name}" if variable else "?"
        when = f"new value of {name}"
        condition = f"value {spec.get_operator_display()} {fmt(spec.threshold)}"
        if spec.duration_seconds:
            condition += f" for {spec.duration_seconds} s"
        if spec.has_hysteresis:
            clear = f"value {spec.get_clear_operator_display()} {fmt(spec.clear_threshold)}"
        else:
            clear = "the condition no longer holds"
    if spec.cooldown_seconds:
        condition += f" (cooldown {spec.cooldown_seconds} s)"
    return {"when": when, "if": condition, "clear": clear}
