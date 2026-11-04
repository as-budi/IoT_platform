"""
Representasi antara (IR) micro rule: aturan ECA sebagai dict JSON yang tidak
bergantung pada Django. IR inilah yang dianalisis, dikompilasi, dan di-hash.

    {"fmt": "microrule-ir/1", "rule": 12, "event": "TELEMETRY",
     "devices": [{"p": 1, "device": 41, "mac": "AA:BB:CC:00:00:01"}, ...],
     "signals": [{"s": 1, "p": 1, "key": "soil_moisture", "variable": 7}, ...],
     "primary": 1,
     "when":  [[[1, "<", 20.0], [2, ">", 30.0]], [[1, "<", 10.0]]],
     "clear": [[[1, ">", 30.0]]],
     "timing": {"duration": 60, "cooldown": 0, "max_age": 300},
     "actions": [{"on": "activate", "do": "set", "p": 3, "key": "pump", "value": 1.0}, ...],
     "cloud": [{"on": "activate", "kind": "ALARM"}]}

- p (nomor device) dan s (nomor sinyal) diurutkan menurut pk device lalu key variabel,
  supaya aturan yang sama selalu menghasilkan IR yang sama.
- Ambang dan nilai aksi dibulatkan ke float32 seperti di ESP32.
- Nama device tidak masuk IR: mengganti nama tidak membuat rencana usang. MAC masuk:
  mengganti board (MAC berubah) membuat rencana usang.
"""
import hashlib
import json

from .wire import f32

FORMAT = "microrule-ir/1"
EDGE_KINDS = ("SET_ACTUATOR", "SEND_COMMAND")
CLOUD_KINDS = ("ALARM", "EMAIL", "TELEGRAM", "CLEAR_ALARM")
RUN_ON = {"ACTIVATE": "activate", "CLEAR": "clear"}


def canonical(obj):
    """JSON kanonik: kunci terurut, tanpa spasi. Dasar hash dan ukuran fragmen."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj):
    """8 hex pertama SHA-256 dari JSON kanonik."""
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()[:8]


def size_of(obj):
    return len(canonical(obj).encode("utf-8"))


def from_rule(rule):
    """Rule ECA (dengan EcaSpec) -> IR."""
    spec = rule.eca
    conditions = spec.condition_list()
    actions = list(
        rule.actions.select_related("target_variable__device", "target_device").order_by("order", "pk")
    )

    devices = {}
    for condition in conditions:
        devices[condition.variable.device_id] = condition.variable.device
    for action in actions:
        if action.kind == "SET_ACTUATOR" and action.target_variable_id:
            devices[action.target_variable.device_id] = action.target_variable.device
        elif action.kind == "SEND_COMMAND" and action.target_device_id:
            devices[action.target_device_id] = action.target_device
    p_of = {pk: number for number, pk in enumerate(sorted(devices), start=1)}

    variables = {}
    for condition in conditions:
        variables[condition.variable_id] = condition.variable
    ordered = sorted(variables.values(), key=lambda v: (v.device_id, v.key))
    s_of = {variable.pk: number for number, variable in enumerate(ordered, start=1)}

    def groups(grouped):
        return [[[s_of[c.variable_id], c.operator, f32(c.threshold)] for c in group] for group in grouped]

    edge_actions, cloud = [], []
    for action in actions:
        on = RUN_ON.get(action.run_on)
        if on is None:
            continue
        if action.kind == "SET_ACTUATOR" and action.target_variable_id:
            variable = action.target_variable
            edge_actions.append({"on": on, "do": "set", "p": p_of[variable.device_id], "key": variable.key,
                                 "value": f32(action.value or 0)})
        elif action.kind == "SEND_COMMAND" and action.target_device_id:
            edge_actions.append({"on": on, "do": "command", "p": p_of[action.target_device_id],
                                 "method": action.method, "params": action.params or {}})
        elif action.kind in CLOUD_KINDS:
            cloud.append({"on": on, "kind": action.kind})

    primary = spec.primary_condition()
    return {
        "fmt": FORMAT,
        "rule": rule.pk,
        "event": spec.event,
        "devices": [{"p": p_of[pk], "device": pk, "mac": devices[pk].mac_address or ""} for pk in sorted(devices)],
        "signals": [{"s": s_of[v.pk], "p": p_of[v.device_id], "key": v.key, "variable": v.pk} for v in ordered],
        "primary": s_of[primary.variable_id] if primary else None,
        "when": groups(spec.when_groups),
        "clear": groups(spec.clear_groups),
        "timing": {
            "duration": spec.duration_seconds,
            "cooldown": spec.cooldown_seconds,
            "max_age": spec.max_age_seconds,
        },
        "actions": edge_actions,
        "cloud": cloud,
    }


def device_names(ir):
    """{p: nama device} untuk teks dan diagram (nama tidak masuk IR)."""
    from devices.models import Device

    names = dict(Device.objects.filter(pk__in=[d["device"] for d in ir["devices"]]).values_list("pk", "name"))
    return {d["p"]: names.get(d["device"], f"device {d['p']}") for d in ir["devices"]}
