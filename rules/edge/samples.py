"""
IR contoh tanpa database, untuk uji, test vector, dan simulasi.

irrigation_ir() adalah aturan dari docs/eca-mekanisme.md dengan tiga device:
    S (p1) soil_moisture, T (p2) temperature, P (p3) pump.
"""
from .ir import FORMAT
from .wire import f32

MACS = {1: "AA:BB:CC:00:00:01", 2: "AA:BB:CC:00:00:02", 3: "AA:BB:CC:00:00:03", 4: "AA:BB:CC:00:00:04"}


def make_ir(signals, when, clear=(), actions=(), cloud=(), duration=0, cooldown=0, max_age=300,
            rule=1, event="TELEMETRY", devices=None, primary=None):
    """signals: [(p, key), ...] berurutan -> s = 1, 2, ...; when/clear: [[(s, op, th), ...], ...]."""
    used = {p for p, _ in signals} | {a["p"] for a in actions}
    devices = devices or [{"p": p, "device": 100 + p, "mac": MACS.get(p, f"AA:BB:CC:00:00:{p:02X}")}
                          for p in sorted(used)]
    return {
        "fmt": FORMAT, "rule": rule, "event": event, "devices": devices,
        "signals": [{"s": i, "p": p, "key": key, "variable": 1000 + i} for i, (p, key) in enumerate(signals, 1)],
        "primary": primary or (when[0][0][0] if when else None),
        "when": [[[s, op, f32(th)] for s, op, th in group] for group in when],
        "clear": [[[s, op, f32(th)] for s, op, th in group] for group in clear],
        "timing": {"duration": duration, "cooldown": cooldown, "max_age": max_age},
        "actions": [dict(a) for a in actions],
        "cloud": [dict(c) for c in cloud],
    }


def set_action(on, p, key, value):
    return {"on": on, "do": "set", "p": p, "key": key, "value": f32(value)}


def irrigation_ir(duration=60, max_age=300, two_actuators=False):
    actions = [set_action("activate", 3, "pump", 1), set_action("clear", 3, "pump", 0)]
    if two_actuators:
        actions += [set_action("activate", 4, "valve", 1), set_action("clear", 4, "valve", 0)]
    return make_ir(
        signals=[(1, "soil_moisture"), (2, "temperature")],
        when=[[(1, "<", 20), (2, ">", 30)], [(1, "<", 10)]],
        clear=[[(1, ">", 30)]],
        actions=actions,
        cloud=[{"on": "activate", "kind": "ALARM"}, {"on": "activate", "kind": "EMAIL"}],
        duration=duration, max_age=max_age, rule=12,
    )
