"""
Apakah sebuah aturan bisa dijalankan di device? (rencana tahap 26 bagian 3, 26b bagian 5)

Hasil: Report(verdict, items, ir, plan)

    FULL     semua aksi jalan di device
    PARTIAL  aksi kendali jalan di device; alarm, email, Telegram tetap di platform
    NONE     tidak bisa dijalankan di device (lihat item ber-level "block")

Setiap item: {"code", "level" (block / warn / info), "text"}. Teksnya untuk pengguna
(bahasa Inggris, seperti antarmuka web).
"""
from dataclasses import dataclass, field

from .compile import compile_plan
from .ir import from_rule
from .limits import limits
from .placement import ACTUATOR, SENSOR
from .wire import key_hash

BLOCK, WARN, INFO = "block", "warn", "info"
FULL, PARTIAL, NONE = "FULL", "PARTIAL", "NONE"
VERDICT_LABELS = {FULL: "Runs on devices", PARTIAL: "Partly on devices", NONE: "Platform only"}
LEVEL_ORDER = {BLOCK: 0, WARN: 1, INFO: 2}


def item(code, level, text):
    return {"code": code, "level": level, "text": text}


@dataclass
class Report:
    verdict: str
    items: list = field(default_factory=list)
    ir: dict = None
    plan: dict = None

    @property
    def blocked(self):
        return any(i["level"] == BLOCK for i in self.items)

    @property
    def label(self):
        return VERDICT_LABELS[self.verdict]

    def sorted_items(self):
        return sorted(self.items, key=lambda i: LEVEL_ORDER[i["level"]])


def _name(names, p):
    return (names or {}).get(p, f"device {p}")


def analyze_ir(ir, lim=None, names=None):
    """Pemeriksaan yang hanya butuh IR (tanpa database)."""
    lim = lim or limits()
    items = []
    if ir["event"] == "SCHEDULE":
        items.append(item("EVENT_SCHEDULE", BLOCK,
                          "Scheduled rules need an accurate clock and stay on the platform."))
    elif ir["event"] == "INACTIVITY":
        items.append(item("EVENT_INACTIVITY", BLOCK,
                          "\"Device sends no data\" rules stay on the platform: a silent device cannot be "
                          "told apart from a lost radio link."))
    if not ir["actions"]:
        items.append(item("NO_CONTROL", BLOCK,
                          "The rule has no Set actuator or Send command action, so there is nothing for "
                          "devices to run."))
    missing = [d["p"] for d in ir["devices"] if not d["mac"]]
    if missing:
        items.append(item("NO_MAC", BLOCK,
                          "These devices have not reported their MAC address yet: "
                          + ", ".join(_name(names, p) for p in missing)
                          + ". Devices find each other by MAC address."))

    conditions = [c for groups in (ir["when"], ir["clear"]) for group in groups for c in group]
    if len(ir["signals"]) > lim["MAX_SIGNALS"]:
        items.append(item("LIMIT_SIGNALS", BLOCK,
                          f"The rule uses {len(ir['signals'])} variables; devices support at most "
                          f"{lim['MAX_SIGNALS']}."))
    for part, groups in (("activate", ir["when"]), ("clear", ir["clear"])):
        if len(groups) > lim["MAX_GROUPS"]:
            items.append(item("LIMIT_GROUPS", BLOCK,
                              f"The {part} expression has {len(groups)} OR groups; devices support at most "
                              f"{lim['MAX_GROUPS']}."))
    if len(conditions) > lim["MAX_CONDITIONS"]:
        items.append(item("LIMIT_CONDITIONS", BLOCK,
                          f"The rule has {len(conditions)} conditions; devices support at most "
                          f"{lim['MAX_CONDITIONS']}."))
    for p in sorted({a["p"] for a in ir["actions"]}):
        for on in ("activate", "clear"):
            count = sum(1 for a in ir["actions"] if a["p"] == p and a["on"] == on)
            if count > lim["MAX_ACTIONS"]:
                items.append(item("LIMIT_ACTIONS", BLOCK,
                                  f"{_name(names, p)} has {count} actions on {on}; at most "
                                  f"{lim['MAX_ACTIONS']} are supported."))

    max_age = ir["timing"]["max_age"]
    if max_age == 0:
        items.append(item("MAX_AGE_ZERO", WARN,
                          "\"Ignore values older than\" is 0, so a device would trust the last value "
                          "forever, even after the radio link is lost. Set a limit."))
    elif max_age > lim["MAX_AGE_LIMIT"]:
        items.append(item("MAX_AGE_RANGE", BLOCK,
                          f"\"Ignore values older than\" must be at most {lim['MAX_AGE_LIMIT']} s to run "
                          "on devices."))
    if any(op in ("==", "!=") and float(th) != int(th) for _, op, th in conditions):
        items.append(item("FLOAT_EQ", WARN,
                          "\"=\" or \"≠\" with a fractional threshold may give a different result on a "
                          "device, which uses 32-bit numbers. Prefer < or >."))

    for p in sorted({s["p"] for s in ir["signals"]}):
        keys = [s["key"] for s in ir["signals"] if s["p"] == p]
        hashes = [key_hash(k) for k in keys]
        if len(set(hashes)) != len(hashes):
            items.append(item("KEY_HASH", BLOCK,
                              f"Two variable keys of {_name(names, p)} share the same short code. "
                              "Rename one of them."))
    if ir["cloud"]:
        kinds = sorted({c["kind"] for c in ir["cloud"]})
        text = ", ".join(k.replace("_", " ").lower() for k in kinds)
        items.append(item("CLOUD_ACTION", INFO,
                          f"These actions stay on the platform and run when it is reachable: {text}."))
    if any(a["do"] == "command" for a in ir["actions"]):
        items.append(item("COMMAND_HANDLER", INFO,
                          "Send command actions need a handler for the method in the device firmware."))
    return items


def plan_items(ir, plan, lim=None, names=None):
    """Pemeriksaan ukuran dan catatan khusus strategi, setelah kompilasi."""
    lim = lim or limits()
    items = []
    for p, size in plan["sizes"].items():
        if size > lim["MAX_FRAGMENT_BYTES"]:
            items.append(item("LIMIT_FRAGMENT", BLOCK,
                              f"The part for {_name(names, p)} is {size} bytes; at most "
                              f"{lim['MAX_FRAGMENT_BYTES']} bytes fit on a device."))
    for p, size in plan["data_frames"].items():
        if size > lim["MAX_FRAME_BYTES"]:
            items.append(item("LIMIT_FRAME", BLOCK,
                              f"Data from {_name(names, p)} needs {size} bytes per radio message; at most "
                              f"{lim['MAX_FRAME_BYTES']} fit."))
        count = len(plan["fragments"][p].get("publish", []))
        if count > lim["MAX_PUBLISHED_SIGNALS"]:
            items.append(item("LIMIT_PUBLISHED", BLOCK,
                              f"{_name(names, p)} would broadcast {count} variables; at most "
                              f"{lim['MAX_PUBLISHED_SIGNALS']} are supported."))
    for e in plan["evaluators"]:
        count = len(plan["fragments"][e].get("sources", []))
        if count > lim["MAX_SOURCES"]:
            items.append(item("LIMIT_SOURCES", BLOCK,
                              f"{_name(names, e)} would listen to {count} devices; at most "
                              f"{lim['MAX_SOURCES']} are supported."))

    if plan["strategy"] == SENSOR and plan["evaluators"]:
        e = plan["evaluators"][0]
        remote_sources = {s["p"] for s in ir["signals"] if s["p"] != e}
        remote_acts = [a for a in plan["actuators"] if a != e]
        if remote_sources and remote_acts:
            items.append(item("SE_RELAY", INFO,
                              f"Data from {', '.join(_name(names, p) for p in sorted(remote_sources))} reaches "
                              "the actuator in two radio hops (through the evaluating sensor)."))
        if e in plan["actuators"]:
            # Sumber sinyal yang juga aktuator selalu dipilih (tanpa hop tambahan). Dengan satu
            # aktuator, SE menjadi sama dengan AE; yang berbeda hanya versi (hash strategi).
            if len(plan["actuators"]) == 1:
                items.append(item("SE_SAME_AS_AE", INFO,
                                  f"{_name(names, e)} measures a variable of this rule and also controls the "
                                  "actuator, so it evaluates the rule itself. This arrangement is the same as "
                                  "\"Actuator evaluates\"; only the version differs."))
            else:
                items.append(item("SE_SAME_AS_AE", INFO,
                                  f"{_name(names, e)} measures a variable of this rule and also controls an "
                                  "actuator, so it evaluates the rule and sends the decision to the other "
                                  "actuators."))
        if e in plan["actuators"]:
            if len(plan["actuators"]) == 1:
                text = (f"{_name(names, e)} both measures and controls, so it evaluates the rule itself. For this "
                        "rule the two arrangements are the same; only the version differs.")
            else:
                text = (f"{_name(names, e)} both measures and controls, so it evaluates the rule and sends its "
                        "decision to the other actuators.")
            items.append(item("SE_AT_ACTUATOR", INFO, text))
        if e not in plan["actuators"]:
            items.append(item("SE_EXTRA_CRITICAL", INFO,
                              f"{_name(names, e)} decides for the actuators: if it stops, the actions stop "
                              "even when the actuators and other sensors are working."))
            items.append(item("SE_ALWAYS_ON", WARN,
                              f"{_name(names, e)} must stay awake to time the rule and send its state; this "
                              "does not suit battery sensors that deep sleep."))
        if len(remote_acts) > 1:
            items.append(item("SE_MULTI_ACT", INFO,
                              "All actuators follow one decision, so they cannot disagree."))
    elif plan["strategy"] == ACTUATOR and len(plan["evaluators"]) > 1:
        items.append(item("AE_REPLICATED", INFO,
                          "Each actuator evaluates the rule itself. A lost radio message can make them "
                          "briefly disagree."))
    return items


def access_items(rule, ir, names=None):
    """Hak akses pemilik aturan: sama dengan mesin cloud (tahap 19)."""
    from devices.models import Device
    from sharing import access

    from ..engines.eca import can_watch

    items = []
    devices = {d.pk: d for d in Device.objects.filter(pk__in=[d["device"] for d in ir["devices"]])}
    p_to_pk = {d["p"]: d["device"] for d in ir["devices"]}
    for p in sorted({s["p"] for s in ir["signals"]}):
        if not can_watch(rule, devices.get(p_to_pk[p])):
            items.append(item("ACCESS", BLOCK, f"The rule owner can no longer read {_name(names, p)}."))
    for p in sorted({a["p"] for a in ir["actions"]}):
        device = devices.get(p_to_pk[p])
        if device is None or not access.can(rule.owner, device, access.OPERATOR):
            items.append(item("ACCESS", BLOCK,
                              f"The rule owner needs Operator access to {_name(names, p)} to control it."))
    return items


def verdict_of(items, ir):
    if any(i["level"] == BLOCK for i in items):
        return NONE
    return PARTIAL if ir and ir["cloud"] else FULL


def analyze_ir_full(ir, strategy=ACTUATOR, lim=None, names=None, extra_items=(), policy=None, rates=None):
    """Analisis + kompilasi dari IR saja (dipakai simulasi dan uji)."""
    lim = lim or limits()
    items = analyze_ir(ir, lim, names) + list(extra_items)
    plan = None
    if not any(i["level"] == BLOCK for i in items):
        plan = compile_plan(ir, strategy, policy=policy, rates=rates, lim=lim)
        items += plan_items(ir, plan, lim, names)
    return Report(verdict_of(items, ir), items, ir, plan)


def analyze_rule(rule, strategy=ACTUATOR, lim=None, extra_items=()):
    """Analisis lengkap sebuah Rule dari database."""
    from ..models import RuleModel

    spec = getattr(rule, "eca", None) if rule.model == RuleModel.ECA else None
    if spec is None:
        return Report(NONE, [item("MODEL", BLOCK, "Only ECA rules can run on devices for now.")])
    ir = from_rule(rule)
    from .ir import device_names

    names = device_names(ir)
    return analyze_ir_full(ir, strategy, lim, names, extra_items=access_items(rule, ir, names) + list(extra_items))
