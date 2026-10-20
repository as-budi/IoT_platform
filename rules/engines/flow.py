"""
Model b — Flow-based rule chain (graf berarah), DIIMPLEMENTASIKAN di tahap 8.

Sebuah aturan adalah GRAF NODE, seperti ThingsBoard dan Node-RED:

    Input ──► [too_hot: temperature > 32] ──True──► [humid: add latest humidity] ──Success──► ...
                         │
                         └──False──► [all_clear: clear alarm]

Setiap pesan telemetri masuk lewat node Input, lalu berjalan dari node ke node:
1. Node mengerjakan tugasnya pada pesan (memeriksa, menambah data, menghitung,
   atau menjalankan aksi) dan menghasilkan satu LABEL: True/False (filter) atau
   Success/Failure (node lain).
2. Pesan diteruskan lewat SEMUA panah yang labelnya sama dengan hasil itu. Bila
   ada dua panah berlabel sama, pesan disalin: setiap cabang membawa salinannya
   sendiri, sehingga perubahan di satu cabang tidak terlihat di cabang lain.
3. Bila tidak ada panah dengan label itu, cabang tersebut selesai.

Penelusuran dilakukan DEPTH-FIRST: satu cabang diikuti sampai habis, baru
cabang berikutnya (urutan panah = urutan di form). Setiap node yang dilewati
dicatat sebagai satu baris JEJAK (trace) supaya jalannya pesan bisa dibaca di
halaman aturan. Penjelasan lengkap dengan contoh: docs/flow-mekanisme.md.

Input "On a schedule" (tahap 14, docs/tahap-14-penjadwalan.md): rule chain
tidak menunggu telemetri, tetapi menerima SATU pesan buatan pada setiap waktu
jadwal (RuleSchedule). Isi pesannya jam/menit/hari, sehingga node CHECK bisa
memilih cabang menurut waktu, dan node LATEST mengambil data sensor yang
dibutuhkan:

    Input (06:00) ──► [soil ← latest soil_moisture] ──► [soil < 40?] ──True──► [pump ON]

Bedanya dengan ECA (rules/engines/eca.py):
- ECA punya KEADAAN (IDLE/PENDING/ACTIVE) dan menjalankan aksi hanya saat
  keadaan berpindah. Rule chain TIDAK punya keadaan aturan: setiap pesan
  menelusuri graf dari awal, dan node aksi berjalan setiap kali pesan sampai.
- Pengulangan dicegah oleh NODE: "Value changed" dan "Rate limit" menyimpan
  ingatan kecil per device (FlowNode.memory), dan aksi dibuat idempoten
  (set aktuator dengan nilai sama dilewati, alarm aktif tidak dibuat dua kali).
"""
import copy
import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from devices.models import Device, Telemetry

from .. import schedules
from ..events import rule_changed, rule_evaluated
from ..models import (
    Alarm, FlowLink, FlowNode, FlowSpec, MathOp, Rule, RuleExecution, RuleModel, RuleState, compare,
)
from .base import RuleEngine

logger = logging.getLogger(__name__)

Kind = FlowNode.Kind
Label = FlowLink.Label
TRUE, FALSE, SUCCESS, FAILURE = Label.TRUE, Label.FALSE, Label.SUCCESS, Label.FAILURE

# Pengaman: satu pesan paling banyak melewati sekian node. Form menolak graf
# yang berputar (lihat rules/forms.py), tetapi mesin tetap menjaga diri.
MAX_STEPS = 50


@dataclass(frozen=True)
class NodeType:
    category: str           # Filter / Enrichment / Transformation / Action
    outputs: tuple          # label yang bisa dihasilkan node ini
    fields: tuple           # field FlowNode yang dipakai (untuk form dan validasi)
    help: str               # penjelasan singkat (bahasa Inggris, untuk UI)


NODE_TYPES = {
    Kind.CHECK: NodeType(
        "Filter", (TRUE, FALSE, FAILURE), ("key", "operator", "threshold"),
        "True when <key> <operator> <threshold>. Failure when the message has no such key.",
    ),
    Kind.CHANGED: NodeType(
        "Filter", (TRUE, FALSE, FAILURE), ("key", "threshold"),
        "True the first time, then only when <key> moved by at least <threshold> "
        "since the last value that passed (per device).",
    ),
    Kind.THROTTLE: NodeType(
        "Filter", (TRUE, FALSE), ("seconds",),
        "True at most once every <seconds> per device; False for messages in between.",
    ),
    Kind.LATEST: NodeType(
        "Enrichment", (SUCCESS, FAILURE), ("variable", "key", "seconds"),
        "Adds the latest stored value of a variable (any of your devices) to the message as <key>. "
        "Failure when there is no data or it is older than <seconds>.",
    ),
    Kind.CALCULATE: NodeType(
        "Transformation", (SUCCESS, FAILURE), ("key", "operand_a", "math_op", "operand_b"),
        "Sets <key> = <first operand> <operation> <second operand>. "
        "An operand is a message key or a number.",
    ),
    Kind.SET_ACTUATOR: NodeType(
        "Action", (SUCCESS, FAILURE), ("target_variable", "value"),
        "Requests a new state for an actuator (skipped when it is already requested).",
    ),
    Kind.SEND_COMMAND: NodeType(
        "Action", (SUCCESS, FAILURE), ("target_device", "method", "params", "ttl_seconds"),
        "Queues a command for a device.",
    ),
    Kind.CREATE_ALARM: NodeType(
        "Action", (SUCCESS, FAILURE), ("severity", "message", "key"),
        "Raises an alarm on the message's device, unless this rule's alarm there is still active.",
    ),
    Kind.CLEAR_ALARM: NodeType(
        "Action", (SUCCESS, FAILURE), (),
        "Clears this rule's active alarms on the message's device.",
    ),
    Kind.EMAIL: NodeType(
        "Action", (SUCCESS, FAILURE), ("recipients", "message"),
        "Sends an email. Put a Rate limit node in front of it to avoid an email for every message.",
    ),
    Kind.TELEGRAM: NodeType(
        "Action", (SUCCESS, FAILURE), ("recipients", "message"),
        "Sends a Telegram message (to the owner's connected chat, or to the chat ids given). "
        "Put a Rate limit node in front of it.",
    ),
}
assert set(NODE_TYPES) == set(Kind.values), "Every FlowNode kind needs a NODE_TYPES entry"

ACTION_KINDS = {kind for kind, info in NODE_TYPES.items() if info.category == "Action"}


class NodeFailure(Exception):
    """Node tidak bisa menyelesaikan tugasnya: pesan keluar lewat panah Failure."""


def fmt(number):
    return "—" if number is None else f"{number:g}"


# --- Satu kali penelusuran -----------------------------------------------------------

@dataclass
class Run:
    """Semua yang dibutuhkan node selama satu pesan berjalan di satu aturan."""

    rule: Rule
    device: Device          # device asal pesan (originator); None = pesan jadwal tanpa device
    now: object
    results: list = field(default_factory=list)    # hasil node aksi, untuk RuleExecution
    changed: bool = False   # ada aksi yang benar-benar mengubah sesuatu (atau gagal)?

    def memory_key(self):
        return str(self.device.pk) if self.device else "schedule"

    @property
    def device_name(self):
        return self.device.name if self.device else "Schedule"


def new_message(message, device):
    """Pesan antrean (rules/queue.py) -> pesan rule chain.

        {"data": {"temperature": 33.5, "humidity": 71},
         "metadata": {"deviceId": 3, "deviceName": "Poultry House Node",
                      "protocol": "MQTT", "ts": "2026-10-13T09:00:00+00:00"}}

    `data` berisi nilai TERBARU tiap key di pesan; node enrichment dan
    transformation menambah key baru ke dalamnya.
    """
    data, ts = {}, ""
    for point in sorted(message.get("data", []), key=lambda p: p.get("ts", "")):
        data[point["key"]] = point["value"]
        ts = point.get("ts", ts)
    return {
        "data": data,
        "metadata": {
            "deviceId": device.pk,
            "deviceName": device.name,
            "protocol": message.get("metadata", {}).get("protocol", ""),
            "ts": ts,
        },
    }


def schedule_message(scheduled, device):
    """Pesan buatan untuk rule chain berjadwal (tahap 14). Jam memakai zona waktu server.

        {"data": {"hour": 6, "minute": 0, "weekday": 0, "day": 19, "month": 10},
         "metadata": {"trigger": "schedule", "scheduledAt": "2026-10-19T06:00:00+07:00",
                      "deviceId": 3, "deviceName": "Greenhouse Node", "ts": "..."}}

    weekday: 0 = Senin ... 6 = Minggu (sama dengan pilihan hari di jadwal).
    """
    local = timezone.localtime(scheduled)
    return {
        "data": {"hour": local.hour, "minute": local.minute, "weekday": local.weekday(),
                 "day": local.day, "month": local.month},
        "metadata": {
            "trigger": "schedule",
            "scheduledAt": local.isoformat(),
            "deviceId": device.pk if device else "",
            "deviceName": device.name if device else "",
            "ts": scheduled.isoformat(),
        },
    }


# --- Node: filter ----------------------------------------------------------------------

def value_of(msg, key):
    if key not in msg["data"]:
        raise NodeFailure(f"the message has no '{key}'")
    return msg["data"][key]


def node_check(node, msg, run):
    value = value_of(msg, node.key)
    passed = compare(value, node.operator, node.threshold)
    text = f"{node.key} = {fmt(value)} {node.get_operator_display()} {fmt(node.threshold)}"
    return (TRUE if passed else FALSE), f"{text} → {'yes' if passed else 'no'}"


def remember(node, run, update):
    """Baca-ubah-simpan ingatan node untuk device pesan ini, dengan baris terkunci.

    `update(lama)` mengembalikan (ingatan_baru atau None, hasil). None = tidak diubah.
    Dikunci (select_for_update) karena dua proses bisa memproses pesan bersamaan
    dalam mode inline.
    """
    with transaction.atomic():
        fresh = FlowNode.objects.select_for_update().get(pk=node.pk)
        memory = fresh.memory or {}
        new, result = update(memory.get(run.memory_key()))
        if new is not None:
            memory[run.memory_key()] = new
            fresh.memory = memory
            fresh.save(update_fields=["memory"])
            node.memory = memory
    return result


def node_changed(node, msg, run):
    value = value_of(msg, node.key)
    minimum = node.threshold or 0

    def update(previous):
        if previous is None:
            return {"value": value, "at": run.now.isoformat()}, (TRUE, f"{node.key} = {fmt(value)}, first value")
        delta = abs(value - previous["value"])
        if delta > 0 and delta >= minimum:
            return ({"value": value, "at": run.now.isoformat()},
                    (TRUE, f"{node.key} {fmt(previous['value'])} → {fmt(value)} (changed by {fmt(round(delta, 6))})"))
        why = f"needs {fmt(minimum)}" if minimum else "same value"
        return None, (FALSE, f"{node.key} = {fmt(value)}, last passed {fmt(previous['value'])} ({why})")

    return remember(node, run, update)


def node_throttle(node, msg, run):
    period = node.seconds or 0

    def update(previous):
        if previous:
            last = parse_datetime(previous["at"])
            waited = (run.now - last).total_seconds()
            if waited < period:
                return None, (FALSE, f"last passed {waited:.0f} s ago, limit {period} s")
            note = f"last passed {waited:.0f} s ago"
        else:
            note = "first message"
        return {"at": run.now.isoformat()}, (TRUE, f"{note}, limit {period} s")

    return remember(node, run, update)


# --- Node: enrichment & transformation ---------------------------------------------------

def node_latest(node, msg, run):
    from devices.views import visible_devices

    variable = node.variable
    if variable is None:
        raise NodeFailure("no variable selected")
    if not visible_devices(run.rule.owner).filter(pk=variable.device_id).exists():
        raise NodeFailure(f"the rule owner has no access to {variable.device}")
    # SELECT value, ts FROM devices_telemetry WHERE variable_id = %s ORDER BY ts DESC LIMIT 1
    row = Telemetry.objects.filter(variable=variable).order_by("-ts").values("value", "ts").first()
    name = f"{variable.device.name} · {variable.display_name}"
    if row is None:
        raise NodeFailure(f"{name} has no data")
    age = (run.now - row["ts"]).total_seconds()
    if node.seconds and age > node.seconds:
        raise NodeFailure(f"{name} is stale ({age:.0f} s old, limit {node.seconds} s)")
    key = node.key or variable.key
    msg["data"][key] = row["value"]
    return SUCCESS, f"{key} = {fmt(row['value'])} from {name} ({max(age, 0):.0f} s old)"


def operand(msg, text):
    try:
        return float(text)
    except ValueError:
        return value_of(msg, text)


def node_calculate(node, msg, run):
    a, b = operand(msg, node.operand_a), operand(msg, node.operand_b)
    op = node.math_op
    if op == MathOp.ADD:
        result = a + b
    elif op == MathOp.SUB:
        result = a - b
    elif op == MathOp.MUL:
        result = a * b
    elif op == MathOp.DIV:
        if b == 0:
            raise NodeFailure("division by zero")
        result = a / b
    elif op == MathOp.MIN:
        result = min(a, b)
    elif op == MathOp.MAX:
        result = max(a, b)
    else:
        raise NodeFailure(f"unknown operation {op!r}")
    result = round(result, 6)
    msg["data"][node.key] = result
    expr = f"{node.operand_a} {node.get_math_op_display()} {node.operand_b}"
    if op in (MathOp.MIN, MathOp.MAX):
        expr = f"{op}({node.operand_a}, {node.operand_b})"
    return SUCCESS, f"{node.key} = {expr} = {fmt(result)}"


# --- Node: aksi --------------------------------------------------------------------------

def action_context(run, msg, node):
    """Placeholder pesan alarm/email: {rule} {device} {values} {value} dan setiap key pesan."""
    values = ", ".join(f"{key} = {fmt(value)}" for key, value in msg["data"].items())
    context = {key: fmt(value) for key, value in msg["data"].items()}
    value = msg["data"].get(node.key) if node.key else None
    context.update(
        rule=run.rule.name,
        device=run.device.name if run.device else "—",
        variable=node.key or "",
        value=value,
        threshold=None,
        values=values or "—",
        originator=run.device,
        **{name: str(v) for name, v in msg["metadata"].items()},
    )
    return context


def node_action(node, msg, run):
    """Node aksi memakai fungsi yang sama dengan aksi ECA (rules/actions.py).

    FlowNode punya nama field yang sama dengan RuleAction (target_variable,
    value, method, severity, message, ...), sehingga node bisa langsung
    diserahkan ke fungsi-fungsi itu.
    """
    from .. import actions

    context = action_context(run, msg, node)
    context["transition"] = "triggered"
    changed = True
    try:
        if node.kind == Kind.SET_ACTUATOR:
            detail = actions.set_actuator(node, run.rule, context)
            changed = not detail.endswith(actions.ALREADY_REQUESTED)
        elif node.kind == Kind.SEND_COMMAND:
            detail = actions.send_command(node, run.rule, context)
        elif node.kind in (Kind.CREATE_ALARM, Kind.CLEAR_ALARM) and run.device is None:
            # Alarm selalu menempel pada device (tahap 14: pesan jadwal tanpa device).
            raise actions.ActionError("Alarms need a device: choose one under Input → Device.")
        elif node.kind == Kind.CREATE_ALARM:
            changed = not Alarm.objects.active().filter(rule=run.rule, device=run.device).exists()
            detail = actions.raise_alarm(node, run.rule, context)
        elif node.kind == Kind.CLEAR_ALARM:
            cleared = []
            for alarm in Alarm.objects.active().filter(rule=run.rule, device=run.device):
                alarm.clear(user=None)
                cleared.append(f"#{alarm.pk}")
            changed = bool(cleared)
            detail = ("Cleared alarm " + ", ".join(cleared)) if cleared else "No active alarm"
        elif node.kind == Kind.TELEGRAM:
            detail = actions.send_telegram(node, run.rule, context)
        else:  # EMAIL
            detail = actions.send_email(node, run.rule, context)
    except Exception as exc:
        if not isinstance(exc, actions.ActionError):
            logger.exception("Node %s of rule %s failed", node.pk, run.rule.pk)
        run.results.append({"action": f"{node.name} ({node.get_kind_display()})", "ok": False, "detail": str(exc)})
        run.changed = True
        raise NodeFailure(str(exc)) from exc
    run.results.append({"action": f"{node.name} ({node.get_kind_display()})", "ok": True, "detail": detail})
    run.changed = run.changed or changed
    return SUCCESS, detail


HANDLERS = {
    Kind.CHECK: node_check,
    Kind.CHANGED: node_changed,
    Kind.THROTTLE: node_throttle,
    Kind.LATEST: node_latest,
    Kind.CALCULATE: node_calculate,
    **{kind: node_action for kind in ACTION_KINDS},
}


def execute(node, msg, run):
    """Jalankan satu node. Mengembalikan (label, keterangan). Tidak pernah melempar error."""
    try:
        return HANDLERS[node.kind](node, msg, run)
    except NodeFailure as exc:
        return FAILURE, str(exc)
    except Exception as exc:  # kesalahan tak terduga di satu node tidak menghentikan worker
        logger.exception("Node %s of rule %s crashed", node.pk, run.rule.pk)
        return FAILURE, f"error: {exc}"


# --- Mesin ------------------------------------------------------------------------------

class Graph:
    """Node dan panah satu aturan, disusun supaya mudah ditelusuri.

        graph.start              panah dari Input
        graph.links_from(node, label)   panah dari node dengan label tertentu
    """

    def __init__(self, rule):
        self.nodes = {node.pk: node for node in rule.nodes.all()}
        self.start = []
        self.outgoing = {}
        for link in rule.links.all():
            if link.source_id is None:
                self.start.append(link)
            else:
                self.outgoing.setdefault((link.source_id, link.label), []).append(link)

    def links_from(self, node, label):
        return self.outgoing.get((node.pk, label), [])


def walk(rule, msg, run):
    """Telusuri graf depth-first. Mengembalikan jejak: satu baris per node yang dilewati.

        [{"step": 1, "node": 12, "name": "too_hot", "kind": "CHECK", "category": "Filter",
          "from": "Input", "label": "TRUE", "detail": "temperature = 33.5 > 32 → yes",
          "data": {...isi pesan setelah node ini...}, "next": ["humid"]}, ...]
    """
    graph = Graph(rule)
    trace = []
    # Tumpukan (stack): (node, pesan untuk node itu, nama node sebelumnya).
    # Dimasukkan terbalik supaya panah PERTAMA diambil lebih dulu (pop dari belakang).
    stack = [(graph.nodes[link.target_id], copy.deepcopy(msg), "Input") for link in reversed(graph.start)]
    while stack:
        if len(trace) >= MAX_STEPS:
            trace.append({"step": len(trace) + 1, "error": f"Stopped after {MAX_STEPS} steps (is there a loop?)"})
            logger.warning("Rule %s stopped after %s steps", rule.pk, MAX_STEPS)
            break
        node, branch_msg, came_from = stack.pop()
        label, detail = execute(node, branch_msg, run)
        links = graph.links_from(node, label)
        trace.append({
            "step": len(trace) + 1,
            "node": node.pk,
            "name": node.name,
            "kind": node.kind,
            "kind_label": node.get_kind_display(),
            "category": NODE_TYPES[node.kind].category,
            "from": came_from,
            "label": label,
            "detail": detail,
            "data": dict(branch_msg["data"]),
            "next": [graph.nodes[link.target_id].name for link in links],
        })
        # Setiap panah keluar membawa SALINAN pesan: cabang tidak saling memengaruhi.
        for link in reversed(links):
            stack.append((graph.nodes[link.target_id], copy.deepcopy(branch_msg), node.name))
    return trace


class FlowEngine(RuleEngine):
    key = RuleModel.FLOW
    implemented = True
    summary = "Each message travels through a graph of nodes (filter → enrich → transform → act)."
    example = "Input → [temperature > 32?] →True→ [add latest humidity] → [set fan ON] → [rate limit] → [email]"
    best_for = "Processing pipelines with branches, enrichment and calculations."
    state = "None per rule; optional per node (Value changed, Rate limit)"

    def rules(self):
        return (
            Rule.objects.filter(model=RuleModel.FLOW, is_enabled=True)
            .select_related("owner", "flow", "flow__device")
            .prefetch_related(
                "nodes__variable__device", "nodes__target_variable__device", "nodes__target_device", "links"
            )
        )

    def on_message(self, message, now):
        """Masukkan pesan ke setiap rule chain yang mendengarkan device asalnya.
        Rule chain berjadwal (Input "On a schedule") tidak menerima telemetri."""
        if message.get("type") != "TELEMETRY" or not message.get("data"):
            return []
        from devices.views import visible_devices
        from sharing import access

        device_id = message["originator"]["device"]
        device = Device.objects.filter(pk=device_id).first()
        if device is None:
            return []
        executions = []
        candidates = self.rules().filter(flow__trigger=FlowSpec.Trigger.TELEMETRY).filter(
            Q(flow__device_id=device_id) | Q(flow__device__isnull=True)
        )
        for rule in candidates:
            # Device yang dipilih: pemilik aturan harus (masih) boleh melihatnya, termasuk
            # lewat share (tahap 19). "Semua device": hanya device MILIK pemilik aturan.
            if rule.flow.device_id is None:
                if not access.devices_for(rule.owner, shared=False).filter(pk=device_id).exists():
                    continue
            elif not visible_devices(rule.owner).filter(pk=device_id).exists():
                continue
            execution = self.run(rule, message, device, now)
            if execution:
                executions.append(execution)
        return executions

    def tick(self, now):
        """Rule chain telemetri hanya digerakkan pesan. Tick hanya menjalankan
        rule chain berjadwal yang jatuh tempo (tahap 14)."""
        executions = []
        for schedule in schedules.due(RuleModel.FLOW, now):
            claimed = schedules.claim(schedule, now)
            if claimed is None:
                continue
            outcome, scheduled, late = claimed
            rule = self.rules().filter(pk=schedule.rule_id, flow__trigger=FlowSpec.Trigger.SCHEDULE).first()
            if rule is None:
                continue
            if outcome == schedules.MISSED:
                executions.append(schedules.log_skipped(rule, now, schedules.missed_detail(scheduled, late)))
                continue
            execution = self.run_schedule(rule, scheduled, now)
            if execution:
                executions.append(execution)
        return executions

    def run_schedule(self, rule, scheduled, now):
        """Satu waktu jadwal: kirim satu pesan jadwal ke Input rule chain."""
        device = rule.flow.device
        return self.run(rule, None, device, now, msg=schedule_message(scheduled, device))

    def run(self, rule, message, device, now, msg=None):
        """Satu pesan melewati satu rule chain. Mengembalikan RuleExecution atau None.
        `msg` sudah jadi (pesan jadwal) atau dibuat dari `message` antrean."""
        msg = msg or new_message(message, device)
        run = Run(rule=rule, device=device, now=now)
        trace = walk(rule, msg, run)

        state, _ = RuleState.objects.get_or_create(rule=rule)
        state.last_evaluated_at = now
        state.snapshot = {
            "at": now.isoformat(),
            "device": device.pk if device else None,
            "device_name": run.device_name,
            "trigger": msg["metadata"].get("trigger", "telemetry"),
            "input": msg["data"],
            "trace": trace,
        }
        if run.results:
            state.last_activated_at = now
        state.save()
        rule.state = state

        # Log hanya untuk pesan yang benar-benar mengubah sesuatu (atau aksinya
        # gagal). Pesan yang hanya menemukan "pompa sudah ON" tidak dicatat,
        # supaya log tidak bertambah setiap 10 detik. Jejak terakhirnya tetap
        # terlihat di kotak "Last run" (RuleState.snapshot).
        device_id = device.pk if device else None
        if not run.changed:
            rule_evaluated(rule, device_id=device_id)
            return None
        execution = RuleExecution.objects.create(
            rule=rule,
            transition=RuleExecution.Transition.RUN,
            value=None,
            results=run.results,
            ok=all(r["ok"] for r in run.results),
            snapshot={f"{run.device_name} · {key}": value for key, value in msg["data"].items()},
            trace=trace,
            created_at=now,
        )
        logger.info("Rule chain %s ran %s action(s)", rule.pk, len(run.results))
        rule_changed(rule, state, transition=RuleExecution.Transition.RUN, device_id=device_id)
        return execution

    def describe(self, rule):
        spec = getattr(rule, "flow", None)
        nodes = list(rule.nodes.all())
        actions = sum(1 for node in nodes if node.kind in ACTION_KINDS)
        if spec and spec.trigger == FlowSpec.Trigger.SCHEDULE:
            schedule = getattr(rule, "schedule", None) if hasattr(rule, "schedule") else None
            when = "schedule: " + (schedule.describe() if schedule else "not set")
        else:
            source = spec.device.name if spec and spec.device_id else "any of the owner's devices"
            when = f"new data from {source}"
        return {
            "when": when,
            "if": f"rule chain of {len(nodes)} node{'s' if len(nodes) != 1 else ''} "
                  f"({actions} action{'s' if actions != 1 else ''})",
            "clear": "—",
        }


def node_summary(node):
    """Isi node dalam satu baris pendek, untuk diagram dan tabel node."""
    if node.kind == Kind.CHECK:
        return f"{node.key} {node.get_operator_display()} {fmt(node.threshold)}"
    if node.kind == Kind.CHANGED:
        return f"{node.key} changed" + (f" by ≥ {fmt(node.threshold)}" if node.threshold else "")
    if node.kind == Kind.THROTTLE:
        return f"once per {node.seconds} s"
    if node.kind == Kind.LATEST:
        variable = node.variable
        return f"{node.key or (variable.key if variable else '?')} ← {variable.display_name if variable else '?'}"
    if node.kind == Kind.CALCULATE:
        if node.math_op in (MathOp.MIN, MathOp.MAX):
            return f"{node.key} = {node.math_op}({node.operand_a}, {node.operand_b})"
        return f"{node.key} = {node.operand_a} {node.get_math_op_display()} {node.operand_b}"
    if node.kind == Kind.SET_ACTUATOR:
        variable = node.target_variable
        if variable is None:
            return "?"
        shown = ("ON" if node.value >= 0.5 else "OFF") if variable.is_boolean else fmt(node.value)
        return f"{variable.display_name} = {shown}"
    if node.kind == Kind.SEND_COMMAND:
        return f"{node.method} → {node.target_device.name if node.target_device else '?'}"
    if node.kind == Kind.CREATE_ALARM:
        return f"{(node.severity or 'major').lower()} alarm"
    if node.kind == Kind.CLEAR_ALARM:
        return "clear this rule's alarm"
    if node.kind == Kind.EMAIL:
        return f"email {node.recipients or 'the owner'}"
    if node.kind == Kind.TELEGRAM:
        return f"Telegram {node.recipients or 'the owner'}"
    return ""
