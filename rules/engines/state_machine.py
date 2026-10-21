"""
Model f — Mesin keadaan (state machine), DIIMPLEMENTASIKAN di tahap 20.

    NORMAL ──temperature > 8 selama 120 s──► WARM ──600 s di WARM──► ALARM
       ▲                                      │                       │
       └──────────── temperature <= 7 ────────┴───────────────────────┘
    * (keadaan mana pun) ──device offline──► OFFLINE

Setiap DEVICE yang diamati berada di SATU keadaan (MachineInstance). Satu aturan
bisa mengamati satu device tertentu, atau semua device milik pemiliknya; masing-masing
device punya keadaannya sendiri (Node A di WARM, Node B di NORMAL).

Dari keadaan saat ini, mesin memeriksa transisi KELUAR (asal = keadaan ini, atau
asal "*" = keadaan mana pun) BERURUTAN seperti di form. Transisi pertama yang
pemicunya terpenuhi dijalankan:

    CONDITION  kondisi key (OR dari AND, logika tiga nilai seperti ECA) bernilai BENAR,
               dan, bila ada "selama N detik", sudah bertahan selama itu
    TIMEOUT    device sudah N detik di keadaan asal
    SILENT     device tidak mengirim data N detik
    OFFLINE    device berstatus offline (tahap 16)
    MANUAL     pengguna menekan tombol di halaman aturan

Menjalankan transisi = aksi "on exit" keadaan lama, lalu aksi "on enter" keadaan
baru, lalu keadaan disimpan. Dalam SATU penilaian paling banyak satu transisi
berjalan: keadaan baru baru dinilai pada pesan atau tick berikutnya. Ini mencegah
putaran tanpa akhir bila dua transisi saling bolak-balik.

Penjelasan lengkap dengan contoh: docs/tahap-20-state-machine.md.
"""
import logging
import re

from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from devices.models import Device, Telemetry, Variable
from realtime import events

from .. import conditions
from ..events import rule_evaluated
from ..models import MachineInstance, MachineTransition, Operator, Rule, RuleAction, RuleExecution, RuleModel
from .base import RuleEngine

logger = logging.getLogger(__name__)

Trigger = MachineTransition.Trigger
# Pemicu yang bergantung pada WAKTU: diperiksa oleh tick, bukan oleh pesan.
TIMED = {Trigger.TIMEOUT, Trigger.SILENT, Trigger.OFFLINE}
ANY = "*"


# --- Kondisi sebagai teks -----------------------------------------------------------------
#
# Di form, kondisi sebuah transisi diketik sebagai satu baris teks:
#     temperature > 35 and humidity < 40 or temperature > 45
# "and" lebih kuat daripada "or" (seperti di matematika: kali sebelum tambah), jadi
# baris di atas berarti (temperature > 35 AND humidity < 40) OR (temperature > 45).
# Itulah bentuk "OR dari AND" yang sama dengan kondisi ECA (tahap 7).

OPERATOR_ALIASES = {
    ">": Operator.GT, ">=": Operator.GTE, "≥": Operator.GTE, "<": Operator.LT, "<=": Operator.LTE,
    "≤": Operator.LTE, "==": Operator.EQ, "=": Operator.EQ, "!=": Operator.NE, "≠": Operator.NE,
}
COMPARISON = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9_]*)\s*(?P<op>>=|<=|==|!=|>|<|=|≥|≤|≠)\s*(?P<value>[^\s]+)\s*$"
)
WORDS = {"on": 1.0, "off": 0.0, "true": 1.0, "false": 0.0}
MAX_COMPARISONS = 12


class ConditionSyntaxError(ValueError):
    pass


def parse_condition(text):
    """Teks -> [[(key, operator, ambang), ...], ...]. Melempar ConditionSyntaxError.

    >>> parse_condition("temperature > 35 and humidity < 40 or temperature > 45")
    [[('temperature', '>', 35.0), ('humidity', '<', 40.0)], [('temperature', '>', 45.0)]]
    >>> parse_condition("pump == ON")
    [[('pump', '==', 1.0)]]
    """
    text = (text or "").strip()
    if not text:
        raise ConditionSyntaxError("Enter a condition, e.g. temperature > 35.")
    groups = []
    count = 0
    for part in re.split(r"\s+or\s+", text, flags=re.IGNORECASE):
        group = []
        for piece in re.split(r"\s+and\s+", part, flags=re.IGNORECASE):
            match = COMPARISON.match(piece)
            if not match:
                raise ConditionSyntaxError(
                    f"“{piece.strip()}” is not a comparison. Write key, operator, number, "
                    "e.g. temperature > 35 (operators: > >= < <= == !=)."
                )
            raw = match["value"]
            if raw.lower() in WORDS:
                value = WORDS[raw.lower()]
            else:
                try:
                    value = float(raw)
                except ValueError:
                    raise ConditionSyntaxError(f"“{raw}” is not a number (ON/OFF are allowed too).")
            group.append((match["key"], OPERATOR_ALIASES[match["op"]], value))
            count += 1
        groups.append(group)
    if count > MAX_COMPARISONS:
        raise ConditionSyntaxError(f"At most {MAX_COMPARISONS} comparisons in one condition.")
    return groups


def fmt(number):
    return "—" if number is None else f"{number:g}"


def format_condition(groups):
    """Kebalikan parse_condition: [[MachineCondition | tuple, ...], ...] -> teks."""
    parts = []
    for group in groups:
        items = []
        for item in group:
            key, op, value = (item.key, item.operator, item.threshold) if hasattr(item, "key") else item
            items.append(f"{key} {op} {fmt(value)}")
        parts.append(" and ".join(items))
    return " or ".join(parts)


def describe_transition(transition):
    """Pemicu dalam kata-kata pendek, untuk diagram dan tabel."""
    if transition.trigger == Trigger.CONDITION:
        text = format_condition(transition.condition_groups()) or "?"
        if transition.seconds:
            text += f" for {transition.seconds} s"
        return text
    if transition.trigger == Trigger.TIMEOUT:
        return f"after {transition.seconds or 0} s"
    if transition.trigger == Trigger.SILENT:
        return f"no data for {transition.seconds or 0} s"
    if transition.trigger == Trigger.OFFLINE:
        return "device offline"
    return f"button “{transition.label or 'Go'}”"


# --- Membaca nilai -----------------------------------------------------------------------

def latest_by_key(device, keys):
    """{key: Reading} nilai terakhir setiap key di device ini, dengan satu query."""
    keys = sorted(set(keys))
    if not keys:
        return {}
    newest = Telemetry.objects.filter(variable=OuterRef("pk")).order_by("-ts")
    rows = (
        Variable.objects.filter(device=device, key__in=keys)
        .annotate(last_value=Subquery(newest.values("value")[:1]), last_ts=Subquery(newest.values("ts")[:1]))
        .order_by()
        .values_list("key", "last_value", "last_ts")
    )
    return {key: conditions.Reading(value, ts, "database") for key, value, ts in rows if ts is not None}


def evaluate_groups(groups, readings, now, max_age):
    """Nilai kondisi (OR dari AND). Mengembalikan (hasil tiga nilai, rincian per perbandingan)."""
    rows, results = [], []
    for conditions_in_group in groups:
        group_rows = []
        for condition in conditions_in_group:
            reading = readings.get(condition.key)
            result, reason = conditions.check(condition, reading, now, max_age)
            group_rows.append({
                "key": condition.key, "operator": condition.operator, "threshold": condition.threshold,
                "value": reading.value if reading else None,
                "age": round((now - reading.ts).total_seconds()) if reading else None,
                "result": result, "reason": reason,
            })
        results.append(conditions.all_of(r["result"] for r in group_rows))
        rows.append(group_rows)
    return conditions.any_of(results), rows


# --- Mesin -------------------------------------------------------------------------------

class StateMachineEngine(RuleEngine):
    key = RuleModel.STATE_MACHINE
    implemented = True
    summary = ("Each device is in one named state; conditions, timers or a button move it between "
               "states and run entry/exit actions.")
    example = "NORMAL →(temp > 35)→ WARNING →(temp > 45)→ CRITICAL →(temp < 30)→ NORMAL"
    best_for = "Alarms with stages, device modes and life cycles."
    state = "Yes (current state per device)"

    def rules(self):
        return (
            Rule.objects.filter(model=RuleModel.STATE_MACHINE, is_enabled=True)
            .select_related("owner", "machine", "machine__device")
            .prefetch_related("machine_states", "machine_transitions__conditions")
        )

    # --- siapa yang diamati ---------------------------------------------------------

    def watches(self, rule, device):
        """Apakah aturan ini (masih) boleh mengamati device ini?

        Satu device tertentu: harus terlihat oleh pemilik aturan (miliknya, atau dibagikan
        kepadanya, tahap 19). "Semua device": hanya device MILIK pemilik aturan.
        """
        from devices.views import visible_devices
        from sharing import access

        spec = getattr(rule, "machine", None)
        if spec is None or device is None:
            return False
        if spec.device_id is None:
            return access.devices_for(rule.owner, shared=False).filter(pk=device.pk).exists()
        return spec.device_id == device.pk and visible_devices(rule.owner).filter(pk=device.pk).exists()

    # --- pesan masuk -----------------------------------------------------------------

    def on_message(self, message, now):
        if message.get("type") != "TELEMETRY" or not message.get("data"):
            return []
        device_id = message["originator"]["device"]
        device = Device.objects.filter(pk=device_id).first()
        if device is None:
            return []
        # Nilai TERBARU setiap key di pesan ini (pesan bisa memuat beberapa titik waktu).
        points = {}
        for point in sorted(message["data"], key=lambda p: p.get("ts", "")):
            ts = parse_datetime(point["ts"]) if isinstance(point.get("ts"), str) else point.get("ts")
            points[point["key"]] = conditions.Reading(point["value"], ts or now, "message")
        executions = []
        candidates = self.rules().filter(Q(machine__device_id=device_id) | Q(machine__device__isnull=True))
        for rule in candidates:
            if not self.watches(rule, device):
                continue
            execution = self.step(rule, device, now, points=points)
            if execution:
                executions.append(execution)
        return executions

    # --- pemeriksaan berkala ----------------------------------------------------------

    def tick(self, now):
        """Pemicu waktu (TIMEOUT, SILENT, OFFLINE) dan kondisi "selama N detik" yang sedang ditunggu."""
        executions = []
        for rule in self.rules():
            transitions = list(rule.machine_transitions.all())
            timed_sources = {t.source_id for t in transitions if t.trigger in TIMED}
            if not timed_sources and not any(t.trigger == Trigger.CONDITION and t.seconds for t in transitions):
                continue
            spec = getattr(rule, "machine", None)
            if spec is None:
                continue
            # Aturan untuk satu device tertentu mulai bekerja walau device belum pernah
            # mengirim data, supaya "device diam" dan "offline" tetap terdeteksi.
            if spec.device_id and not rule.machine_instances.exists() and self.watches(rule, spec.device):
                self.start(rule, spec.device, now)
            instances = rule.machine_instances.select_related("device", "state")
            for instance in instances:
                if not (instance.pending or instance.state_id in timed_sources or None in timed_sources):
                    continue
                if not self.watches(rule, instance.device):
                    continue
                execution = self.step(rule, instance.device, now, points=None)
                if execution:
                    executions.append(execution)
        return executions

    # --- inti ------------------------------------------------------------------------

    def initial_state(self, rule):
        states = list(rule.machine_states.all())
        return next((s for s in states if s.is_initial), states[0] if states else None)

    def start(self, rule, device, now):
        """Keadaan awal untuk device ini (tanpa aksi). None bila aturan belum punya keadaan."""
        initial = self.initial_state(rule)
        if initial is None:
            return None
        instance, _ = MachineInstance.objects.get_or_create(
            rule=rule, device=device, defaults={"state": initial, "entered_at": now},
        )
        return instance

    def step(self, rule, device, now, points=None):
        """Nilai transisi keluar dari keadaan device saat ini; jalankan yang pertama terpenuhi.

        points=None berarti dipanggil tick: hanya pemicu waktu dan kondisi yang sedang
        ditunggu yang diperiksa, dengan nilai dari database.
        """
        if self.start(rule, device, now) is None:
            return None
        spec = rule.machine
        with transaction.atomic():
            # Kunci baris keadaan: dua proses tidak boleh memindahkan device yang sama bersamaan.
            instance = (MachineInstance.objects.select_for_update()
                        .select_related("state").get(rule=rule, device=device))
            states = {s.pk: s for s in rule.machine_states.all()}
            if instance.state_id not in states:          # data lama: kembali ke awal
                instance.state = self.initial_state(rule)
                instance.entered_at = now
            current = instance.state
            device = Device.objects.get(pk=device.pk)    # last_seen_at / is_online terbaru

            readings = None
            pending = dict(instance.pending or {})
            report = []
            fire = None
            for transition in rule.machine_transitions.all():
                if transition.source_id not in (None, current.pk) or transition.trigger == Trigger.MANUAL:
                    continue
                if transition.source_id is None and transition.target_id == current.pk:
                    continue   # "* → X" tidak berlaku saat device sudah di X
                key = str(transition.pk)
                row = {"id": transition.pk, "to": states[transition.target_id].name,
                       "text": describe_transition(transition), "result": None}
                if transition.trigger == Trigger.CONDITION:
                    if points is None and key not in pending:
                        continue          # tick: kondisi tanpa penantian dinilai saat ada data
                    if readings is None:
                        keys = {c.key for t in rule.machine_transitions.all() for c in t.conditions.all()}
                        readings = latest_by_key(device, keys)
                        readings.update(points or {})
                    result, row["groups"] = evaluate_groups(
                        transition.condition_groups(), readings, now, spec.max_age_seconds)
                    row["result"] = result
                    if result is True:
                        hold = transition.seconds or 0
                        since = parse_datetime(pending[key]) if key in pending else now
                        waited = (now - since).total_seconds()
                        if waited >= hold:
                            fire = transition
                        else:
                            pending[key] = since.isoformat()
                            row["waiting"] = {"since": since.isoformat(), "left": round(hold - waited)}
                    else:
                        pending.pop(key, None)
                elif points is not None:
                    continue              # pemicu waktu tidak dinilai pada pesan data
                elif transition.trigger == Trigger.TIMEOUT:
                    elapsed = (now - instance.entered_at).total_seconds()
                    row["result"] = elapsed >= (transition.seconds or 0)
                    fire = transition if row["result"] else None
                elif transition.trigger == Trigger.SILENT:
                    last = device.last_seen_at or device.created_at
                    row["result"] = (now - last).total_seconds() >= (transition.seconds or 0)
                    fire = transition if row["result"] else None
                elif transition.trigger == Trigger.OFFLINE:
                    row["result"] = not device.is_online
                    fire = transition if row["result"] else None
                report.append(row)
                if fire:
                    break

            values = {key: reading.value for key, reading in (readings or {}).items()}
            if fire is not None:
                return self.move(rule, instance, fire, now, values=values,
                                 trigger_value=self.trigger_value(fire, values))
            changed = pending != (instance.pending or {})
            instance.pending = pending
            if points is not None:
                instance.last_evaluated_at = now
                instance.snapshot = {"at": now.isoformat(), "state": current.name, "values": values,
                                     "transitions": report}
            if points is not None or changed:
                instance.save(update_fields=["state", "entered_at", "pending", "last_evaluated_at", "snapshot"])
        if points is not None:
            rule_evaluated(rule, device_id=device.pk)
        return None

    @staticmethod
    def trigger_value(transition, values):
        """Nilai pertama di kondisi transisi, untuk placeholder {value} dan log."""
        for group in transition.condition_groups():
            for condition in group:
                if condition.key in values:
                    return condition.key, values[condition.key]
        return None, None

    def move(self, rule, instance, transition, now, values=None, trigger_value=(None, None), actor=None):
        """Jalankan transisi: aksi keluar, aksi masuk, simpan keadaan baru, catat log."""
        from ..actions import run_state_actions

        old = instance.state
        new = transition.target
        device = instance.device
        values = values or {}
        key, value = trigger_value
        context = {
            "rule": rule.name,
            "device": device.name,
            "originator": device,
            "state": new.name,
            "from_state": old.name,
            "to_state": new.name,
            "transition": f"moved from {old.name} to {new.name}",
            "variable": key or "",
            "value": value,
            "values": ", ".join(f"{k} = {fmt(v)}" for k, v in values.items()) or "—",
            "time": timezone.localtime(now).strftime("%H:%M"),
        }
        results = []
        if actor is not None:
            results.append({"action": "Button", "ok": True,
                            "detail": f"“{transition.label or new.name}” pressed by {actor.username}"})
        results += run_state_actions(rule, old, RuleAction.RunOn.EXIT, context)
        results += run_state_actions(rule, new, RuleAction.RunOn.ENTER, context)

        instance.state = new
        instance.entered_at = now
        instance.pending = {}
        instance.last_evaluated_at = now
        instance.snapshot = {"at": now.isoformat(), "state": new.name, "values": values, "transitions": [],
                             "moved": {"from": old.name, "to": new.name, "by": describe_transition(transition)}}
        instance.save()
        execution = RuleExecution.objects.create(
            rule=rule,
            transition=RuleExecution.Transition.MOVE,
            value=value,
            results=results,
            ok=all(r["ok"] for r in results),
            snapshot={f"{device.name} · {k}": v for k, v in values.items()},
            device=device,
            from_state=old.name,
            to_state=new.name,
            created_at=now,
        )
        logger.info("State machine %s: %s %s -> %s", rule.pk, device.pk, old.name, new.name)
        # Kabar WebSocket ke device itu: halaman aturan dan device memuat ulang.
        events.emit(device.pk, {"type": "rule", "rule": rule.pk, "status": new.name,
                                "transition": RuleExecution.Transition.MOVE, "value": value})
        return execution

    def press(self, rule, device, transition, user, now=None):
        """Tombol manual: jalankan transisi MANUAL bila device sedang di keadaan asalnya.

        Mengembalikan RuleExecution, atau melempar ValueError dengan alasan yang bisa dibaca.
        """
        now = now or timezone.now()
        if transition.trigger != Trigger.MANUAL or transition.rule_id != rule.pk:
            raise ValueError("This is not a button of this rule.")
        if self.start(rule, device, now) is None:
            raise ValueError("The rule has no states.")
        with transaction.atomic():
            instance = (MachineInstance.objects.select_for_update()
                        .select_related("state", "device").get(rule=rule, device=device))
            if transition.source_id not in (None, instance.state_id):
                raise ValueError(f"{device.name} is in {instance.state.name}, "
                                 f"not in {transition.source.name}; the button does not apply.")
            if transition.source_id is None and transition.target_id == instance.state_id:
                raise ValueError(f"{device.name} is already in {instance.state.name}.")
            return self.move(rule, instance, transition, now, actor=user)

    def describe(self, rule):
        spec = getattr(rule, "machine", None)
        states = list(rule.machine_states.all())
        transitions = list(rule.machine_transitions.all())
        source = spec.device.name if spec and spec.device_id else "each of the owner's devices"
        initial = self.initial_state(rule)
        return {
            "when": f"data and timers of {source}",
            "if": f"{len(states)} state{'s' if len(states) != 1 else ''}"
                  f"{f', starts in {initial.name}' if initial else ''}, "
                  f"{len(transitions)} transition{'s' if len(transitions) != 1 else ''}",
            "clear": "—",
        }


def reset_instances(rule):
    """Semua device mulai lagi dari keadaan awal (dipanggil saat aturan disimpan / di-toggle)."""
    MachineInstance.objects.filter(rule=rule).delete()
