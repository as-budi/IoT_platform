"""
Ekspor dan impor aturan ECA sebagai JSON (tahap 13).

Bentuk JSON (versi 1):

    {
      "format": "iot-platform.rule", "version": 1, "model": "ECA",
      "name": "...", "description": "...", "enabled": true,
      "eca": {
        "event": "TELEMETRY",              # atau "INACTIVITY"
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

from .models import EcaCondition, EcaSpec, RuleAction, RuleModel

FORMAT = "iot-platform.rule"
VERSION = 1
# Batas ukuran file/teks yang diimpor. Aturan ECA biasa hanya beberapa KB.
MAX_IMPORT_BYTES = 100_000

# Field JSON per jenis aksi, selain "kind" dan "run_on".
ACTION_FIELDS = {
    RuleAction.Kind.SET_ACTUATOR: ("device", "variable", "value"),
    RuleAction.Kind.SEND_COMMAND: ("device", "method", "params", "ttl_seconds"),
    RuleAction.Kind.ALARM: ("severity", "message"),
    RuleAction.Kind.EMAIL: ("message", "recipients"),
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


def export_rule(rule):
    """Aturan ECA -> dict yang siap di-json.dumps()."""
    spec = rule.eca
    return {
        "format": FORMAT,
        "version": VERSION,
        "model": RuleModel.ECA,
        "exported_at": timezone.now().isoformat(timespec="seconds"),
        "name": rule.name,
        "description": rule.description,
        "enabled": rule.is_enabled,
        "eca": {
            "event": spec.event,
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
    if data.get("model") != RuleModel.ECA:
        errors.append('Only ECA rules can be imported ("model": "ECA").')
    if not isinstance(data.get("eca"), dict):
        errors.append('"eca" must be an object.')
    if not isinstance(data.get("actions"), list):
        errors.append('"actions" must be a list.')
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


def form_errors(forms, cross_errors=()):
    """Error dari form ECA -> daftar kalimat (untuk halaman impor)."""
    sections = {
        "rule_form": "Rule", "spec_form": "Event", "when_formset": "When",
        "clear_formset": "Clear", "action_formset": "Action",
    }
    messages = []
    for key, label in sections.items():
        form = forms[key]
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

