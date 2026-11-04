"""
Test vector untuk firmware (rencana tahap 26 bagian 8): skenario yang dijalankan
evaluator referensi, lalu hasilnya disimpan sebagai JSON di firmware/microrule/vectors/.
Firmware C++ (tahap 27-28) harus menghasilkan transisi yang sama untuk langkah yang sama.

Dibuat ulang dengan:  python manage.py export_microrule_vectors
Uji rules/tests_edge.py memastikan file di repo sama dengan hasil generate().

Format skenario evaluator / aktuator:
    {"name", "description", "fragment",
     "steps": [{"t": ms, "rx": {"mac", "frame"}}            frame hex dari udara
               {"t": ms, "local": {"key", "value"}}          sensor di device itu sendiri
               {"t": ms, "tick": true}                        penilaian berkala
               {"t": ms, "restart": true}                     (aktuator) device restart
              ... setiap langkah punya "expect": [{"tr", "actions"}] dan "status"/"applied"]}
"""
import copy
import json
from pathlib import Path

from .actuator import ActuatorRuntime
from .compile import compile_plan
from .evaluator import Runtime
from .samples import MACS, irrigation_ir, make_ir, set_action
from .wire import crc16, encode_data, encode_state, key_hash, kh_text

S, T, P = MACS[1], MACS[2], MACS[3]
VECTOR_DIR = Path(__file__).resolve().parents[2] / "firmware" / "microrule" / "vectors"


def _data(counter, *signals):
    return encode_data(counter, [(key_hash(key), value, age) for key, value, age in signals]).hex()


def _eval_fragment(ir, p=3, strategy="actuator"):
    return compile_plan(ir, strategy)["fragments"][p]


def _run_eval(name, description, fragment, steps):
    runtime = Runtime(fragment)
    out = []
    for step in steps:
        step = dict(step)
        t = step["t"]
        if "rx" in step:
            runtime.receive_frame(step["rx"]["mac"], bytes.fromhex(step["rx"]["frame"]), t)
        elif "local" in step:
            runtime.read_local(step["local"]["key"], step["local"]["value"], t)
        transition = runtime.tick(t) if step.get("tick") else runtime.step(t)
        step["expect"] = [{"tr": transition.kind, "actions": transition.actions}] if transition else []
        step["status"] = runtime.status
        out.append(step)
    return {"name": name, "description": description, "fragment": fragment, "steps": out}


def _run_act(name, description, fragment, steps):
    act = ActuatorRuntime(fragment)
    out = []
    for step in steps:
        step = dict(step)
        t = step["t"]
        events = []
        if "rx" in step:
            events = act.receive_state(step["rx"]["mac"], bytes.fromhex(step["rx"]["frame"]), t)
        elif step.get("restart"):
            act.restart()
        elif step.get("tick"):
            events = act.tick(t)
        step["expect"] = [{"tr": e.kind, "actions": e.actions} for e in events]
        step["applied"] = act.applied
        out.append(step)
    return {"name": name, "description": description, "fragment": fragment, "steps": out}


def eval_scenarios():
    base = irrigation_ir(duration=60)
    quick = irrigation_ir(duration=0)
    soil = lambda c, v, age=0: {"mac": S, "frame": _data(c, ("soil_moisture", v, age))}
    temp = lambda c, v, age=0: {"mac": T, "frame": _data(c, ("temperature", v, age))}
    yield _run_eval("activate_immediately", "Duration 0: the rule activates on the first true value.",
                    _eval_fragment(quick), [{"t": 0, "rx": soil(1, 5)}])
    yield _run_eval("duration_then_activate", "The condition must hold for 60 s before the rule activates.",
                    _eval_fragment(base), [{"t": 0, "rx": soil(1, 5)}, {"t": 30000, "tick": True},
                                           {"t": 59000, "tick": True}, {"t": 60000, "tick": True}])
    yield _run_eval("duration_interrupted", "A false value while pending returns the rule to IDLE.",
                    _eval_fragment(base), [{"t": 0, "rx": soil(1, 5)}, {"t": 30000, "rx": soil(2, 12)},
                                           {"t": 60000, "tick": True}])
    cooldown = make_ir([(1, "soil_moisture")], when=[[(1, "<", 10)]], actions=[
        set_action("activate", 3, "pump", 1), set_action("clear", 3, "pump", 0)], cooldown=120)
    yield _run_eval("cooldown", "Clear without hysteresis, then a second activation waits for the cooldown.",
                    _eval_fragment(cooldown), [{"t": 0, "rx": soil(1, 5)}, {"t": 10000, "rx": soil(2, 15)},
                                               {"t": 20000, "rx": soil(3, 5)}, {"t": 119000, "tick": True},
                                               {"t": 120000, "tick": True}])
    yield _run_eval("clear_with_hysteresis", "With a clear expression the rule clears only above 30.",
                    _eval_fragment(quick), [{"t": 0, "rx": soil(1, 5)}, {"t": 10000, "rx": soil(2, 25)},
                                            {"t": 20000, "rx": soil(3, 31)}])
    yield _run_eval("or_group", "The first group (soil < 20 AND temperature > 30) activates the rule.",
                    _eval_fragment(quick), [{"t": 0, "rx": soil(1, 15)}, {"t": 1000, "rx": temp(1, 35)}])
    short = irrigation_ir(duration=0, max_age=30)
    yield _run_eval("stale_stays_active", "Values older than max_age are unknown; unknown never clears.",
                    _eval_fragment(short), [{"t": 0, "rx": soil(1, 5)}, {"t": 40000, "tick": True},
                                            {"t": 41000, "rx": soil(2, 31)}])
    yield _run_eval("age_from_frame", "The sample age in the frame counts towards max_age.",
                    _eval_fragment(short), [{"t": 0, "rx": soil(1, 5, 31000)}, {"t": 1000, "rx": soil(2, 5, 0)}])
    equal = make_ir([(1, "level")], when=[[(1, "==", 0.1)]], actions=[set_action("activate", 3, "pump", 1)])
    yield _run_eval("float_equal", "\"=\" compares 32-bit values: 0.1 equals 0.1.",
                    _eval_fragment(equal), [{"t": 0, "rx": {"mac": S, "frame": _data(1, ("level", 0.1, 0))}}])
    yield _run_eval("ignored_frames", "Frames from an unknown MAC, with an unknown key, or with an old counter "
                                      "are ignored.",
                    _eval_fragment(quick), [
                        {"t": 0, "rx": {"mac": MACS[4], "frame": _data(1, ("soil_moisture", 5, 0))}},
                        {"t": 1000, "rx": {"mac": S, "frame": _data(1, ("other_key", 5, 0))}},
                        {"t": 2000, "rx": {"mac": S, "frame": _data(1, ("soil_moisture", 5, 0))}},
                        {"t": 3000, "rx": soil(5, 25)},
                        {"t": 4000, "rx": soil(4, 5)},
                    ])
    local = compile_plan(irrigation_ir(duration=0), "sensor")["fragments"][1]
    yield _run_eval("local_signal", "Sensor evaluates (SE): its own variable is read locally.",
                    local, [{"t": 0, "local": {"key": "soil_moisture", "value": 5}}])


def act_scenarios():
    plan = compile_plan(irrigation_ir(duration=0), "sensor")
    fragment = plan["fragments"][3]
    rev, rule = plan["rev"], plan["fragments"][3]["rule"]
    state = lambda c, active, seq, mac=S, r=rev: {"mac": mac, "frame": encode_state(c, rule, r, active, seq, 0).hex()}
    yield _run_act("apply_state", "Repeated and heartbeat frames run the action once.", fragment, [
        {"t": 0, "rx": state(1, False, 0)}, {"t": 1000, "rx": state(2, True, 1)},
        {"t": 1100, "rx": state(3, True, 1)}, {"t": 11000, "rx": state(4, True, 1)}])
    yield _run_act("missed_transitions", "A jump in the transition number still converges to the sent state.",
                   fragment, [{"t": 0, "rx": state(1, True, 1)}, {"t": 5000, "rx": state(2, True, 3)},
                              {"t": 6000, "rx": state(3, False, 4)}])
    yield _run_act("ignored_frames", "Frames from another MAC, another rule version, or replayed are ignored.",
                   fragment, [{"t": 0, "rx": state(1, True, 1, mac=T)}, {"t": 1000, "rx": state(2, True, 1, r="00000000")},
                              {"t": 2000, "rx": state(5, True, 1)}, {"t": 3000, "rx": state(4, False, 2)}])
    yield _run_act("timeout_hold", "When the evaluator goes quiet, \"hold\" keeps the last state.", fragment, [
        {"t": 0, "rx": state(1, True, 1)}, {"t": 30000, "tick": True}, {"t": 31000, "tick": True}])
    clearing = copy.deepcopy(fragment)
    clearing["on_timeout"] = "clear"
    yield _run_act("timeout_clear", "When the evaluator goes quiet, \"clear\" runs the clear actions.", clearing, [
        {"t": 0, "rx": state(1, True, 1)}, {"t": 31000, "tick": True}])
    yield _run_act("restart_recover", "After a restart the actuator applies the next state frame it hears.",
                   fragment, [{"t": 0, "rx": state(1, True, 1)}, {"t": 5000, "restart": True},
                              {"t": 10000, "rx": state(2, True, 1)}])


def wire_vectors():
    keys = ["soil_moisture", "temperature", "pump", "humidity"]
    data = encode_data(42, [(key_hash("soil_moisture"), 18.5, 1200), (key_hash("temperature"), 31.25, 0)])
    state = encode_state(7, 12, "9f3a1c07", True, 3, 500)
    return {
        "crc16": {"name": "crc16", "description": "CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF).",
                  "cases": [{"input": "123456789", "value": kh_text(crc16(b"123456789"))}]
                  + [{"input": key, "value": kh_text(key_hash(key))} for key in keys]},
        "data_two_signals": {"name": "data_two_signals", "description": "DATA frame, counter 42, two signals.",
                             "frame": data.hex(), "decoded": {
                                 "type": 1, "counter": 42, "signals": [
                                     {"kh": kh_text(key_hash("soil_moisture")), "value": 18.5, "age_ms": 1200},
                                     {"kh": kh_text(key_hash("temperature")), "value": 31.25, "age_ms": 0}]}},
        "state_active": {"name": "state_active", "description": "STATE frame: rule 12, active, transition 3.",
                         "frame": state.hex(), "decoded": {
                             "type": 4, "counter": 7, "rule": 12, "rev": "9f3a1c07", "active": True, "seq": 3,
                             "age_ms": 500}},
    }


def generate():
    """{jalur relatif terhadap VECTOR_DIR: isi JSON}."""
    files = {}
    for scenario in eval_scenarios():
        files[f"eval/{scenario['name']}.json"] = scenario
    for scenario in act_scenarios():
        files[f"act/{scenario['name']}.json"] = scenario
    for name, vector in wire_vectors().items():
        files[f"wire/{name}.json"] = vector
    return files


def dumps(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def write(directory=VECTOR_DIR):
    directory = Path(directory)
    written = []
    for relative, obj in generate().items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(obj), encoding="utf-8")
        written.append(relative)
    return written
