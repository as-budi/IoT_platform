"""
Ekspor dan impor aturan ECA dan rule chain (FLOW) sebagai JSON (tahap 13).

Bentuk JSON (versi 1):

    {
      "format": "iot-platform.rule", "version": 1, "model": "ECA",
      "name": "...", "description": "...", "enabled": true,
      "eca": {
        "event": "TELEMETRY",              # atau "INACTIVITY" / "SCHEDULE"
        "device": null,                    # nama device, hanya untuk INACTIVITY
        "silence_seconds": null, "duration_seconds": 0,
        "cooldown_seconds": 0, "max_age_seconds": 300,
        "when":  [[kondisi, kondisi], [kondisi]],   # grup OR, isi grup AND
        "clear": []
      },
      "actions": [aksi, ...]
    }
    kondisi = {"device": "Greenhouse Node", "variable": "soil_moisture", "operator": "<", "threshold": 30}
    aksi    = {"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "...", "variable": "pump", "value": 1}

Rule chain memakai "model": "FLOW" dan bagian "flow" (tanpa "eca"/"actions"):

      "flow": {
        "device": null,                    # pesan dari device ini saja; null = semua device
        "nodes": [{"name": "too_hot", "kind": "CHECK", "key": "temperature", "operator": ">", "threshold": 30}, ...],
        "links": [{"source": "Input", "label": "SUCCESS", "target": "too_hot"}, ...]
      }

Jadwal (tahap 14): ECA dengan "event": "SCHEDULE" dan rule chain dengan
"trigger": "SCHEDULE" punya bagian "schedule":

      "schedule": {"kind": "DAILY", "times": ["06:00", "18:00"],
                   "weekdays": ["mon", "tue", "wed", "thu", "fri"],
                   "interval_minutes": null, "run_at": null,
                   "active_for_seconds": 600}           # hanya ECA

"run_at" (kind ONCE) ditulis sebagai jam lokal server, "2026-10-20T06:00".
next_run_at/last_run_at tidak ikut: dihitung ulang saat aturan disimpan.

Field sebuah node = field milik jenis node itu (NODE_TYPES[kind].fields di
rules/engines/flow.py). Rujukan ke device/variabel ditulis seperti di ECA:
LATEST dan SET_ACTUATOR memakai "device" + "variable" (key variabel),
SEND_COMMAND memakai "device". Panah menyebut node lewat NAMA, "Input" = awal.
Ingatan node (memory: nilai terakhir CHANGED, waktu terakhir THROTTLE) tidak ikut.

State machine (tahap 20) memakai "model": "STATE_MACHINE" dan bagian "machine":

      "machine": {
        "device": "Cold Storage Unit",     # null = setiap device milik pemilik aturan
        "max_age_seconds": 300,
        "states": [{"name": "NORMAL", "description": "...", "tone": "OK", "initial": true}, ...],
        "transitions": [{"from": "NORMAL", "to": "WARM", "trigger": "CONDITION",
                         "condition": "temperature > 8", "seconds": 120, "label": ""},
                        {"from": "*", "to": "OFFLINE", "trigger": "OFFLINE", ...}, ...],
        "actions": [{"state": "ALARM", "run_on": "ENTER", "kind": "ALARM", "severity": "CRITICAL", ...}, ...]
      }

Kondisi transisi ditulis sebagai TEKS yang sama dengan isian form
("temperature > 35 and humidity < 40 or ..."); "from": "*" = dari keadaan mana pun.
Keadaan device saat ini (MachineInstance) tidak ikut.

Device dan variabel disebut lewat NAMA device dan KEY variabel, bukan id
database. Id hanya bermakna di satu database; nama bisa dibaca manusia dan
tetap cocok di server lain atau untuk pengguna lain yang punya device bernama sama.
Keadaan aturan (IDLE/ACTIVE), log, dan alarm tidak ikut: file ini hanya definisi.

Impor TIDAK menulis ke database sendiri. JSON diterjemahkan menjadi data
"seolah-olah form ECA dikirim" (eca_form_data), lalu divalidasi dan disimpan
oleh kode yang sama dengan form biasa (rules/views.py). Jadi semua aturan form
ikut berlaku: device harus milik pemilik aturan, aktuator harus aktuator, nilai
harus di dalam min/max, histeresis tidak boleh tumpang tindih, dan seterusnya.
"""
import json

from django.utils import timezone

from devices.views import visible_devices

from .engines.flow import NODE_TYPES
from .models import EcaCondition, EcaSpec, FlowNode, FlowSpec, RuleAction, RuleModel, RuleSchedule
from .schedules import DAY_NAMES

FORMAT = "iot-platform.rule"
VERSION = 1
# Model yang bisa diekspor/diimpor.
PORTABLE_MODELS = (RuleModel.ECA, RuleModel.FLOW, RuleModel.STATE_MACHINE)
# Batas ukuran file/teks yang diimpor. Aturan biasa hanya beberapa KB.
MAX_IMPORT_BYTES = 100_000

# Field JSON per jenis aksi, selain "kind" dan "run_on".
ACTION_FIELDS = {
    RuleAction.Kind.SET_ACTUATOR: ("device", "variable", "value"),
    RuleAction.Kind.SEND_COMMAND: ("device", "method", "params", "ttl_seconds"),
    RuleAction.Kind.ALARM: ("severity", "message"),
    RuleAction.Kind.EMAIL: ("message", "recipients"),
    RuleAction.Kind.TELEGRAM: ("message", "recipients"),
    RuleAction.Kind.CLEAR_ALARM: (),
}


class RuleImportError(Exception):
    """JSON tidak bisa diimpor. `errors` = daftar pesan untuk pengguna."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


# --- Ekspor ---------------------------------------------------------------------------

def _groups(spec, role):
    groups = {}
    for condition in spec.conditions.filter(role=role).select_related("variable__device").order_by("group", "order"):
        groups.setdefault(condition.group, []).append({
            "device": condition.variable.device.name,
            "variable": condition.variable.key,
            "operator": condition.operator,
            "threshold": condition.threshold,
        })
    return [groups[number] for number in sorted(groups)]


def _action(action):
    data = {"kind": action.kind, "run_on": action.run_on}
    for name in ACTION_FIELDS.get(action.kind, ()):
        if name == "device":
            device = action.target_variable.device if action.target_variable_id else action.target_device
            data["device"] = device.name if device else None
        elif name == "variable":
            data["variable"] = action.target_variable.key if action.target_variable_id else None
        else:
            data[name] = getattr(action, name)
    return data


def _header(rule):
    return {
        "format": FORMAT,
        "version": VERSION,
        "model": rule.model,
        "exported_at": timezone.now().isoformat(timespec="seconds"),
        "name": rule.name,
        "description": rule.description,
        "enabled": rule.is_enabled,
    }


def _node(node):
    """Satu FlowNode -> dict. Hanya field milik jenis node itu."""
    data = {"name": node.name, "kind": node.kind}
    for name in NODE_TYPES[node.kind].fields:
        if name in ("variable", "target_variable"):
            variable = getattr(node, name)
            data["device"] = variable.device.name if variable else None
            data["variable"] = variable.key if variable else None
        elif name == "target_device":
            data["device"] = node.target_device.name if node.target_device_id else None
        else:
            data[name] = getattr(node, name)
    return data


def _schedule(rule, with_active_for):
    """RuleSchedule -> dict, atau None bila aturan belum punya jadwal."""
    schedule = RuleSchedule.objects.filter(rule=rule).first()
    if schedule is None:
        return None
    try:
        times = [t.strftime("%H:%M") for t in schedule.time_list()]
    except ValueError:
        times = []
    data = {
        "kind": schedule.kind,
        "times": times,
        "weekdays": [DAY_NAMES[d] for d in schedule.weekday_list()],
        "interval_minutes": schedule.interval_minutes,
        "run_at": timezone.localtime(schedule.run_at).strftime("%Y-%m-%dT%H:%M") if schedule.run_at else None,
    }
    if with_active_for:
        data["active_for_seconds"] = schedule.active_for_seconds
    return data


def export_flow(rule):
    """Rule chain -> dict yang siap di-json.dumps()."""
    nodes = list(rule.nodes.select_related("variable__device", "target_variable__device", "target_device"))
    names = {node.pk: node.name for node in nodes}
    spec = getattr(rule, "flow", None)
    trigger = spec.trigger if spec else FlowSpec.Trigger.TELEMETRY
    head = {"trigger": trigger}
    if trigger == FlowSpec.Trigger.SCHEDULE:
        head["schedule"] = _schedule(rule, with_active_for=False)
    return {
        **_header(rule),
        "flow": {
            **head,
            "device": spec.device.name if spec and spec.device_id else None,
            "nodes": [_node(node) for node in nodes],
            "links": [
                {"source": names.get(link.source_id, "Input"), "label": link.label, "target": names[link.target_id]}
                for link in rule.links.order_by("order", "pk")
            ],
        },
    }


def export_machine(rule):
    """State machine -> dict yang siap di-json.dumps()."""
    from .engines.state_machine import format_condition

    spec = getattr(rule, "machine", None)
    states = list(rule.machine_states.order_by("order", "pk"))
    names = {state.pk: state.name for state in states}
    actions = rule.actions.select_related("state", "target_variable__device", "target_device").order_by(
        "state__order", "order", "pk")
    return {
        **_header(rule),
        "machine": {
            "device": spec.device.name if spec and spec.device_id else None,
            "max_age_seconds": spec.max_age_seconds if spec else 300,
            "states": [
                {"name": s.name, "description": s.description, "tone": s.tone, "initial": s.is_initial}
                for s in states
            ],
            "transitions": [
                {"from": names.get(t.source_id, "*"), "to": names.get(t.target_id), "trigger": t.trigger,
                 "condition": format_condition(t.condition_groups()), "seconds": t.seconds, "label": t.label}
                for t in rule.machine_transitions.prefetch_related("conditions").order_by("order", "pk")
            ],
            "actions": [{"state": a.state.name if a.state_id else None, **_action(a)} for a in actions],
        },
    }


def export_rule(rule):
    """Aturan ECA, FLOW, atau STATE_MACHINE -> dict yang siap di-json.dumps()."""
    if rule.model == RuleModel.FLOW:
        return export_flow(rule)
    if rule.model == RuleModel.STATE_MACHINE:
        return export_machine(rule)
    spec = rule.eca
    extra = {}
    if spec.event == EcaSpec.Event.SCHEDULE:
        extra["schedule"] = _schedule(rule, with_active_for=True)
    return {
        **_header(rule),
        "eca": {
            "event": spec.event,
            **extra,
            "device": spec.device.name if spec.device_id else None,
            "silence_seconds": spec.silence_seconds,
            "duration_seconds": spec.duration_seconds,
            "cooldown_seconds": spec.cooldown_seconds,
            "max_age_seconds": spec.max_age_seconds,
            "when": _groups(spec, EcaCondition.Role.WHEN),
            "clear": _groups(spec, EcaCondition.Role.CLEAR),
        },
        "actions": [
            _action(action)
            for action in rule.actions.select_related("target_variable__device", "target_device").order_by("order", "pk")
        ],
    }


def export_json(rule):
    return json.dumps(export_rule(rule), indent=2, ensure_ascii=False)


# --- Impor ----------------------------------------------------------------------------

def parse(text):
    """Teks JSON -> dict, dengan pemeriksaan bentuk dasar. Melempar RuleImportError."""
    if len(text.encode()) > MAX_IMPORT_BYTES:
        raise RuleImportError([f"The file is too large (limit {MAX_IMPORT_BYTES // 1000} KB)."])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuleImportError([f"Not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})."])
    if not isinstance(data, dict):
        raise RuleImportError(["The JSON must be an object { ... }."])
    errors = []
    if data.get("format") != FORMAT:
        errors.append(f'"format" must be "{FORMAT}".')
    if data.get("version") != VERSION:
        errors.append(f'"version" must be {VERSION}.')
    model = data.get("model")
    if model == RuleModel.ECA:
        if not isinstance(data.get("eca"), dict):
            errors.append('"eca" must be an object.')
        if not isinstance(data.get("actions"), list):
            errors.append('"actions" must be a list.')
    elif model == RuleModel.FLOW:
        flow = data.get("flow")
        if not isinstance(flow, dict):
            errors.append('"flow" must be an object.')
        else:
            if not isinstance(flow.get("nodes"), list):
                errors.append('"flow.nodes" must be a list.')
            if not isinstance(flow.get("links"), list):
                errors.append('"flow.links" must be a list.')
    elif model == RuleModel.STATE_MACHINE:
        machine = data.get("machine")
        if not isinstance(machine, dict):
            errors.append('"machine" must be an object.')
        else:
            for part in ("states", "transitions", "actions"):
                if not isinstance(machine.get(part, []), list):
                    errors.append(f'"machine.{part}" must be a list.')
    else:
        errors.append('"model" must be "ECA", "FLOW" or "STATE_MACHINE".')
    if errors:
        raise RuleImportError(errors)
    return data


class _Resolver:
    """Menerjemahkan nama device / key variabel menjadi id.

    Dicari di visible_devices(owner), sama persis dengan pilihan di form ECA:
    pengguna biasa hanya device miliknya; aturan milik Admin boleh memakai semua device.
    """

    def __init__(self, owner):
        self.devices = {}
        for device in visible_devices(owner).prefetch_related("variables"):
            self.devices.setdefault(device.name, []).append(device)
        self.errors = []

    def device(self, name, where):
        if name in (None, ""):
            return None
        matches = self.devices.get(name, [])
        if not matches:
            self.errors.append(f'{where}: no device called "{name}" is available to the rule owner.')
            return None
        if len(matches) > 1:
            self.errors.append(f'{where}: {len(matches)} devices are called "{name}"; rename one first.')
            return None
        return matches[0]

    def variable(self, device_name, key, where):
        device = self.device(device_name, where)
        if device is None:
            return None
        for variable in device.variables.all():
            if variable.key == key:
                return variable
        self.errors.append(f'{where}: device "{device_name}" has no variable "{key}".')
        return None


def _text(value):
    """Nilai JSON -> teks isian form ("" untuk null)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else ""
    return str(value)


def _management(post, prefix, total):
    post[f"{prefix}-TOTAL_FORMS"] = str(total)
    post[f"{prefix}-INITIAL_FORMS"] = "0"
    post[f"{prefix}-MIN_NUM_FORMS"] = "0"
    post[f"{prefix}-MAX_NUM_FORMS"] = "1000"


def _conditions(post, prefix, groups, resolve):
    if groups is None:
        groups = []
    if not isinstance(groups, list) or not all(isinstance(g, list) for g in groups):
        resolve.errors.append(f'"{prefix}" must be a list of groups, e.g. [[{{...}}, {{...}}], [{{...}}]].')
        groups = []
    index = 0
    for number, group in enumerate(groups, start=1):
        for condition in group:
            where = f"{prefix.title()} group {number}"
            if not isinstance(condition, dict):
                resolve.errors.append(f"{where}: each condition must be an object.")
                continue
            variable = resolve.variable(condition.get("device"), condition.get("variable"), where)
            post[f"{prefix}-{index}-group"] = str(number)
            post[f"{prefix}-{index}-variable"] = _text(variable.pk if variable else "")
            post[f"{prefix}-{index}-operator"] = _text(condition.get("operator"))
            post[f"{prefix}-{index}-threshold"] = _text(condition.get("threshold"))
            index += 1
    _management(post, prefix, index)


def _schedule_post(post, schedule, resolve, where):
    """Bagian "schedule" JSON -> isian form jadwal (prefix "schedule")."""
    if not isinstance(schedule, dict):
        resolve.errors.append(f'{where}: "schedule" must be an object, e.g. {{"kind": "DAILY", "times": ["06:00"]}}.')
        return
    post["schedule-kind"] = _text(schedule.get("kind", RuleSchedule.Kind.DAILY))
    times = schedule.get("times") or []
    if isinstance(times, str):
        times = [times]
    post["schedule-times"] = ", ".join(str(t) for t in times)
    days = schedule.get("weekdays")
    if days is None:
        days = DAY_NAMES
    numbers = []
    for day in days if isinstance(days, list) else []:
        text = str(day).strip().lower()[:3]
        if text in DAY_NAMES:
            numbers.append(str(DAY_NAMES.index(text)))
        elif text.isdigit() and int(text) < 7:
            numbers.append(text)
        else:
            resolve.errors.append(f'{where}: unknown weekday "{day}"; use mon, tue, wed, thu, fri, sat, sun.')
    post["schedule-weekdays"] = numbers
    post["schedule-interval_minutes"] = _text(schedule.get("interval_minutes"))
    post["schedule-run_at"] = _text(schedule.get("run_at"))
    post["schedule-active_for_seconds"] = _text(schedule.get("active_for_seconds", 0))


def eca_form_data(data, owner):
    """dict hasil parse() -> data POST untuk form ECA (lihat EcaRuleFormView.build_forms).

    Nama device/variabel dicari di antara device milik `owner` (pemilik aturan).
    Melempar RuleImportError bila ada nama yang tidak ditemukan.
    """
    resolve = _Resolver(owner)
    eca = data["eca"]
    post = {
        "rule-name": _text(data.get("name")),
        "rule-description": _text(data.get("description")),
        "rule-is_enabled": _text(bool(data.get("enabled", True))),
        "eca-event": _text(eca.get("event", EcaSpec.Event.TELEMETRY)),
        "eca-silence_seconds": _text(eca.get("silence_seconds")),
        "eca-duration_seconds": _text(eca.get("duration_seconds", 0)),
        "eca-cooldown_seconds": _text(eca.get("cooldown_seconds", 0)),
        "eca-max_age_seconds": _text(eca.get("max_age_seconds", 300)),
    }
    device = resolve.device(eca.get("device"), "Event") if eca.get("event") == EcaSpec.Event.INACTIVITY else None
    post["eca-device"] = _text(device.pk if device else "")
    if eca.get("event") == EcaSpec.Event.SCHEDULE:
        _schedule_post(post, eca.get("schedule"), resolve, "Schedule")
    _conditions(post, "when", eca.get("when"), resolve)
    _conditions(post, "clear", eca.get("clear"), resolve)

    actions = data["actions"]
    for index, action in enumerate(actions):
        where = f"Action {index + 1}"
        if not isinstance(action, dict):
            resolve.errors.append(f"{where}: each action must be an object.")
            continue
        prefix = f"actions-{index}"
        kind = action.get("kind")
        post[f"{prefix}-kind"] = _text(kind)
        post[f"{prefix}-run_on"] = _text(action.get("run_on", RuleAction.RunOn.ACTIVATE))
        if kind == RuleAction.Kind.SET_ACTUATOR:
            variable = resolve.variable(action.get("device"), action.get("variable"), where)
            post[f"{prefix}-target_variable"] = _text(variable.pk if variable else "")
        elif kind == RuleAction.Kind.SEND_COMMAND:
            target = resolve.device(action.get("device"), where)
            post[f"{prefix}-target_device"] = _text(target.pk if target else "")
        for name in ("value", "method", "ttl_seconds", "severity", "message", "recipients"):
            if name in action:
                post[f"{prefix}-{name}"] = _text(action[name])
        params = action.get("params")
        post[f"{prefix}-params"] = json.dumps(params) if params else ""
        post.setdefault(f"{prefix}-ttl_seconds", "300")
    _management(post, "actions", len(actions))
    post["actions-MIN_NUM_FORMS"] = "1"

    if resolve.errors:
        raise RuleImportError(resolve.errors)
    return post


def flow_form_data(data, owner):
    """dict hasil parse() (model FLOW) -> data POST untuk form rule chain.

    Panah tetap memakai nama node, persis seperti form rule chain (LinkFormSet),
    jadi validasi graf (check_graph: nama harus ada, label harus keluaran node
    asal, tanpa siklus) berjalan tanpa terjemahan tambahan.
    """
    resolve = _Resolver(owner)
    flow = data["flow"]
    post = {
        "rule-name": _text(data.get("name")),
        "rule-description": _text(data.get("description")),
        "rule-is_enabled": _text(bool(data.get("enabled", True))),
    }
    device = resolve.device(flow.get("device"), "Messages from")
    post["flow-device"] = _text(device.pk if device else "")
    post["flow-trigger"] = _text(flow.get("trigger", FlowSpec.Trigger.TELEMETRY))
    if flow.get("trigger") == FlowSpec.Trigger.SCHEDULE:
        _schedule_post(post, flow.get("schedule"), resolve, "Schedule")

    nodes = flow["nodes"]
    for index, node in enumerate(nodes):
        where = f"Node {index + 1}"
        if not isinstance(node, dict):
            resolve.errors.append(f"{where}: each node must be an object.")
            continue
        if node.get("name"):
            where = f'Node "{node["name"]}"'
        prefix = f"nodes-{index}"
        kind = node.get("kind")
        post[f"{prefix}-name"] = _text(node.get("name"))
        post[f"{prefix}-kind"] = _text(kind)
        fields = NODE_TYPES[kind].fields if kind in NODE_TYPES else ()
        for name in fields:
            if name in ("variable", "target_variable"):
                variable = resolve.variable(node.get("device"), node.get("variable"), where)
                post[f"{prefix}-{name}"] = _text(variable.pk if variable else "")
            elif name == "target_device":
                target = resolve.device(node.get("device"), where)
                post[f"{prefix}-{name}"] = _text(target.pk if target else "")
            elif name == "params":
                params = node.get("params")
                post[f"{prefix}-params"] = json.dumps(params) if params else ""
            elif name in node:
                post[f"{prefix}-{name}"] = _text(node[name])
        # Field wajib di model walaupun tidak dipakai jenis node ini.
        post.setdefault(f"{prefix}-ttl_seconds", "300")
    _management(post, "nodes", len(nodes))
    post["nodes-MIN_NUM_FORMS"] = "1"

    links = flow["links"]
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            resolve.errors.append(f"Link {index + 1}: each link must be an object.")
            continue
        for name in ("source", "label", "target"):
            post[f"links-{index}-{name}"] = _text(link.get(name))
    _management(post, "links", len(links))

    unknown = [n.get("kind") for n in nodes if isinstance(n, dict) and n.get("kind") not in NODE_TYPES]
    if unknown:
        kinds = ", ".join(FlowNode.Kind.values)
        resolve.errors.append(f"Unknown node kind {', '.join(map(str, unknown))}. Use one of: {kinds}.")
    if resolve.errors:
        raise RuleImportError(resolve.errors)
    return post


def _action_post(post, prefix, action, resolve, where):
    """Satu aksi JSON -> isian form aksi (dipakai ECA dan state machine)."""
    kind = action.get("kind")
    post[f"{prefix}-kind"] = _text(kind)
    if kind == RuleAction.Kind.SET_ACTUATOR:
        variable = resolve.variable(action.get("device"), action.get("variable"), where)
        post[f"{prefix}-target_variable"] = _text(variable.pk if variable else "")
    elif kind == RuleAction.Kind.SEND_COMMAND:
        target = resolve.device(action.get("device"), where)
        post[f"{prefix}-target_device"] = _text(target.pk if target else "")
    for name in ("value", "method", "ttl_seconds", "severity", "message", "recipients"):
        if name in action:
            post[f"{prefix}-{name}"] = _text(action[name])
    params = action.get("params")
    post[f"{prefix}-params"] = json.dumps(params) if params else ""
    post.setdefault(f"{prefix}-ttl_seconds", "300")


def machine_form_data(data, owner):
    """dict hasil parse() (model STATE_MACHINE) -> data POST untuk form state machine.

    Transisi dan aksi tetap menyebut keadaan lewat NAMA, persis seperti form, jadi
    pemeriksaan check_machine() berjalan tanpa terjemahan tambahan.
    """
    resolve = _Resolver(owner)
    machine = data["machine"]
    post = {
        "rule-name": _text(data.get("name")),
        "rule-description": _text(data.get("description")),
        "rule-is_enabled": _text(bool(data.get("enabled", True))),
        "machine-max_age_seconds": _text(machine.get("max_age_seconds", 300)),
    }
    device = resolve.device(machine.get("device"), "Device")
    post["machine-device"] = _text(device.pk if device else "")

    states = machine.get("states") or []
    for index, state in enumerate(states):
        if not isinstance(state, dict):
            resolve.errors.append(f"State {index + 1}: each state must be an object.")
            continue
        prefix = f"states-{index}"
        post[f"{prefix}-name"] = _text(state.get("name"))
        post[f"{prefix}-description"] = _text(state.get("description"))
        post[f"{prefix}-tone"] = _text(state.get("tone", "NEUTRAL"))
        post[f"{prefix}-is_initial"] = _text(bool(state.get("initial")))
    _management(post, "states", len(states))

    transitions = machine.get("transitions") or []
    for index, row in enumerate(transitions):
        if not isinstance(row, dict):
            resolve.errors.append(f"Transition {index + 1}: each transition must be an object.")
            continue
        prefix = f"transitions-{index}"
        post[f"{prefix}-source"] = _text(row.get("from", "*"))
        post[f"{prefix}-target"] = _text(row.get("to"))
        for name in ("trigger", "condition", "seconds", "label"):
            post[f"{prefix}-{name}"] = _text(row.get(name))
    _management(post, "transitions", len(transitions))

    actions = machine.get("actions") or []
    for index, action in enumerate(actions):
        where = f"Action {index + 1}"
        if not isinstance(action, dict):
            resolve.errors.append(f"{where}: each action must be an object.")
            continue
        prefix = f"actions-{index}"
        post[f"{prefix}-state_name"] = _text(action.get("state"))
        post[f"{prefix}-run_on"] = _text(action.get("run_on", RuleAction.RunOn.ENTER))
        _action_post(post, prefix, action, resolve, where)
    _management(post, "actions", len(actions))

    if resolve.errors:
        raise RuleImportError(resolve.errors)
    return post


def form_data(data, owner):
    """Pilih penerjemah sesuai model."""
    if data["model"] == RuleModel.FLOW:
        return flow_form_data(data, owner)
    if data["model"] == RuleModel.STATE_MACHINE:
        return machine_form_data(data, owner)
    return eca_form_data(data, owner)


def form_errors(forms, cross_errors=()):
    """Error dari form ECA atau rule chain -> daftar kalimat (untuk halaman impor)."""
    sections = {
        "rule_form": "Rule", "spec_form": "Settings", "when_formset": "When",
        "clear_formset": "Clear", "action_formset": "Action",
        "node_formset": "Node", "link_formset": "Link", "schedule_form": "Schedule",
        "state_formset": "State", "transition_formset": "Transition",
    }
    messages = []
    for key, label in sections.items():
        if key not in forms:
            continue
        form = forms[key]
        if getattr(form, "in_use", True) is False:   # form jadwal aturan yang tidak berjadwal
            continue
        if hasattr(form, "forms"):   # formset
            messages += [f"{label}: {error}" for error in form.non_form_errors()]
            for number, sub in enumerate(form.forms, start=1):
                for field, errors in sub.errors.items():
                    name = "" if field == "__all__" else f" {field}"
                    messages += [f"{label} {number}{name}: {error}" for error in errors]
        else:
            for field, errors in form.errors.items():
                name = "" if field == "__all__" else f" {field}"
                messages += [f"{label}{name}: {error}" for error in errors]
    return messages + list(cross_errors)
