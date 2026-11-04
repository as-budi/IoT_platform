"""Tes micro rule tahap 26: IR, analisis, compiler, frame, evaluator, vector, halaman."""
import io
import json
import random
import zipfile
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from devices.models import Device, Variable
from sharing.models import DeviceShare

from . import conditions
from .edge import analyze, compile as compiler, ir as ir_module, placement, vectors, wire
from .edge.evaluator import Runtime, at
from .edge.limits import limits
from .edge.samples import MACS, irrigation_ir, make_ir, set_action
from .edge.services import compile_and_store, device_context, rule_context
from .engines.eca import step
from .models import EcaCondition, EcaSpec, EdgeFragment, EdgePlan, Rule, RuleAction, RuleModel
from .tests import RuleViewTests

User = get_user_model()


def lim(**overrides):
    return {**limits(), **overrides}


class EdgeData(TestCase):
    """Aturan irigasi dengan tiga device: S (soil), T (temperature), P (pump)."""

    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.s = Device.objects.create(owner=cls.siti, name="Soil node", mac_address=MACS[1])
        cls.t = Device.objects.create(owner=cls.siti, name="Weather node", mac_address=MACS[2])
        cls.p = Device.objects.create(owner=cls.siti, name="Pump node", mac_address=MACS[3])
        cls.soil = Variable.objects.create(device=cls.s, key="soil_moisture")
        cls.temp = Variable.objects.create(device=cls.t, key="temperature")
        cls.pump = Variable.objects.create(device=cls.p, key="pump", kind=Variable.Kind.ACTUATOR)

    def make_rule(self, edge=True, event=EcaSpec.Event.TELEMETRY, alarm=True, owner=None, model=RuleModel.ECA):
        rule = Rule.objects.create(owner=owner or self.siti, name="Irrigation", edge_enabled=edge, model=model)
        if model != RuleModel.ECA:
            return rule
        spec = EcaSpec.objects.create(rule=rule, event=event, duration_seconds=60, max_age_seconds=300)
        rows = [
            ("WHEN", 1, self.soil, "<", 20), ("WHEN", 1, self.temp, ">", 30), ("WHEN", 2, self.soil, "<", 10),
            ("CLEAR", 1, self.soil, ">", 30),
        ]
        for order, (role, group, variable, op, threshold) in enumerate(rows):
            EcaCondition.objects.create(spec=spec, role=role, group=group, order=order, variable=variable,
                                        operator=op, threshold=threshold)
        RuleAction.objects.create(rule=rule, kind="SET_ACTUATOR", target_variable=self.pump, value=1)
        RuleAction.objects.create(rule=rule, kind="SET_ACTUATOR", target_variable=self.pump, value=0, run_on="CLEAR")
        if alarm:
            RuleAction.objects.create(rule=rule, kind="ALARM", severity="MAJOR", message="Dry")
        return rule


# --- frame ----------------------------------------------------------------------------

class WireTests(SimpleTestCase):
    def test_crc16_check_value(self):
        self.assertEqual(wire.crc16(b"123456789"), 0x29B1)

    def test_data_round_trip_and_size(self):
        frame = wire.encode_data(9, [(wire.key_hash("a"), 1.5, 250), (wire.key_hash("b"), -2.0, 0)])
        self.assertEqual(len(frame), wire.data_frame_bytes(2))
        self.assertEqual(wire.data_frame_bytes(29), 249)
        decoded = wire.decode_data(frame)
        self.assertEqual(decoded["counter"], 9)
        self.assertEqual(decoded["signals"], [(wire.key_hash("a"), 1.5, 300), (wire.key_hash("b"), -2.0, 0)])

    def test_state_round_trip(self):
        frame = wire.encode_state(3, 12, "9f3a1c07", True, 2, 1500)
        self.assertEqual(len(frame), wire.STATE_FRAME_BYTES)
        self.assertEqual(wire.decode_state(frame)["rev"], "9f3a1c07")
        self.assertEqual(wire.frame_type(frame), wire.STATE)

    def test_bad_frames(self):
        frame = wire.encode_data(1, [(1, 1.0, 0)])
        for bad in (b"XX" + frame[2:], frame[:-1], frame[:5]):
            with self.assertRaises(wire.WireError):
                wire.decode_data(bad)
        with self.assertRaises(wire.WireError):
            wire.decode_state(frame)


# --- IR -----------------------------------------------------------------------------

class IrTests(EdgeData):
    def test_irrigation_rule(self):
        rule = self.make_rule()
        ir = ir_module.from_rule(rule)
        self.assertEqual([d["mac"] for d in ir["devices"]], [MACS[1], MACS[2], MACS[3]])
        self.assertEqual([(s["s"], s["p"], s["key"]) for s in ir["signals"]],
                         [(1, 1, "soil_moisture"), (2, 2, "temperature")])
        self.assertEqual(ir["when"], [[[1, "<", 20.0], [2, ">", 30.0]], [[1, "<", 10.0]]])
        self.assertEqual(ir["clear"], [[[1, ">", 30.0]]])
        self.assertEqual(ir["actions"], [set_action("activate", 3, "pump", 1), set_action("clear", 3, "pump", 0)])
        self.assertEqual(ir["cloud"], [{"on": "activate", "kind": "ALARM"}])
        self.assertEqual(ir["primary"], 1)

    def test_hash_is_stable_and_tracks_content(self):
        rule = self.make_rule()
        first = ir_module.digest(ir_module.from_rule(rule))
        self.assertEqual(first, ir_module.digest(ir_module.from_rule(Rule.objects.get(pk=rule.pk))))
        Device.objects.filter(pk=self.s.pk).update(name="Renamed")
        self.assertEqual(first, ir_module.digest(ir_module.from_rule(Rule.objects.get(pk=rule.pk))))
        EcaCondition.objects.filter(threshold=10).update(threshold=12)
        second = ir_module.digest(ir_module.from_rule(Rule.objects.get(pk=rule.pk)))
        self.assertNotEqual(first, second)
        Device.objects.filter(pk=self.p.pk).update(mac_address="AA:BB:CC:00:00:09")
        self.assertNotEqual(second, ir_module.digest(ir_module.from_rule(Rule.objects.get(pk=rule.pk))))

    def test_thresholds_are_float32(self):
        rule = self.make_rule()
        EcaCondition.objects.filter(threshold=10).update(threshold=0.1)
        ir = ir_module.from_rule(Rule.objects.get(pk=rule.pk))
        self.assertEqual(ir["when"][1][0][2], wire.f32(0.1))
        self.assertNotEqual(wire.f32(0.1), 0.1)


# --- analisis -------------------------------------------------------------------------

def codes(report):
    return {i["code"] for i in report.items}


class AnalyzeRuleTests(EdgeData):
    def test_irrigation_is_partial(self):
        report = analyze.analyze_rule(self.make_rule())
        self.assertEqual(report.verdict, analyze.PARTIAL)
        self.assertIn("CLOUD_ACTION", codes(report))
        self.assertIsNotNone(report.plan)

    def test_without_cloud_actions_is_full(self):
        self.assertEqual(analyze.analyze_rule(self.make_rule(alarm=False)).verdict, analyze.FULL)

    def test_other_models_and_events(self):
        self.assertEqual(codes(analyze.analyze_rule(self.make_rule(model=RuleModel.FLOW))), {"MODEL"})
        report = analyze.analyze_rule(self.make_rule(event=EcaSpec.Event.SCHEDULE))
        self.assertEqual(report.verdict, analyze.NONE)
        self.assertIn("EVENT_SCHEDULE", codes(report))

    def test_missing_mac_blocks(self):
        Device.objects.filter(pk=self.t.pk).update(mac_address="")
        report = analyze.analyze_rule(self.make_rule())
        self.assertIn("NO_MAC", codes(report))
        self.assertIn("Weather node", " ".join(i["text"] for i in report.items))
        self.assertIsNone(report.plan)

    def test_actuator_needs_operator_access(self):
        foreign = Device.objects.create(owner=self.budi, name="Budi pump", mac_address=MACS[4])
        valve = Variable.objects.create(device=foreign, key="valve", kind=Variable.Kind.ACTUATOR)
        DeviceShare.objects.create(device=foreign, user=self.siti, role="VIEWER", created_by=self.budi)
        rule = self.make_rule()
        RuleAction.objects.create(rule=rule, kind="SET_ACTUATOR", target_variable=valve, value=1)
        report = analyze.analyze_rule(rule)
        self.assertIn("ACCESS", codes(report))
        DeviceShare.objects.filter(device=foreign).update(role="OPERATOR")
        self.assertNotIn("ACCESS", codes(analyze.analyze_rule(rule)))


class AnalyzeIrTests(SimpleTestCase):
    def report(self, ir, **overrides):
        return analyze.analyze_ir_full(ir, lim=lim(**overrides))

    def test_limits(self):
        many = make_ir([(1, f"k{i}") for i in range(9)], when=[[(i, ">", 0) for i in range(1, 10)]],
                       actions=[set_action("activate", 2, "pump", 1)])
        self.assertIn("LIMIT_SIGNALS", codes(self.report(many)))
        groups = make_ir([(1, "a")], when=[[(1, ">", i)] for i in range(5)], actions=[set_action("activate", 2, "x", 1)])
        self.assertIn("LIMIT_GROUPS", codes(self.report(groups)))
        self.assertIn("LIMIT_CONDITIONS", codes(self.report(groups, MAX_CONDITIONS=4, MAX_GROUPS=9)))
        actions = make_ir([(1, "a")], when=[[(1, ">", 0)]],
                          actions=[set_action("activate", 2, f"x{i}", 1) for i in range(5)])
        self.assertIn("LIMIT_ACTIONS", codes(self.report(actions)))

    def test_sizes(self):
        ir = irrigation_ir()
        self.assertIn("LIMIT_FRAGMENT", codes(self.report(ir, MAX_FRAGMENT_BYTES=200)))
        self.assertIn("LIMIT_FRAME", codes(self.report(ir, MAX_FRAME_BYTES=20)))
        self.assertIn("LIMIT_SOURCES", codes(self.report(ir, MAX_SOURCES=1)))
        self.assertEqual(self.report(ir).verdict, analyze.PARTIAL)

    def test_max_age_and_float_equal(self):
        self.assertIn("MAX_AGE_ZERO", codes(self.report(irrigation_ir(max_age=0))))
        self.assertIn("MAX_AGE_RANGE", codes(self.report(irrigation_ir(max_age=7000))))
        equal = make_ir([(1, "a")], when=[[(1, "==", 0.5)]], actions=[set_action("activate", 2, "x", 1)])
        self.assertIn("FLOAT_EQ", codes(self.report(equal)))
        whole = make_ir([(1, "a")], when=[[(1, "==", 1)]], actions=[set_action("activate", 2, "x", 1)])
        self.assertNotIn("FLOAT_EQ", codes(self.report(whole)))

    def test_no_control_and_commands(self):
        ir = make_ir([(1, "a")], when=[[(1, ">", 0)]], cloud=[{"on": "activate", "kind": "EMAIL"}])
        self.assertIn("NO_CONTROL", codes(self.report(ir)))
        command = make_ir([(1, "a")], when=[[(1, ">", 0)]],
                          actions=[{"on": "activate", "do": "command", "p": 2, "method": "beep", "params": {}}])
        self.assertIn("COMMAND_HANDLER", codes(self.report(command)))

    def test_key_hash_collision(self):
        seen, pair = {}, None
        for i in range(200000):
            key = f"k{i}"
            value = wire.key_hash(key)
            if value in seen:
                pair = (seen[value], key)
                break
            seen[value] = key
        ir = make_ir([(1, pair[0]), (1, pair[1])], when=[[(1, ">", 0), (2, ">", 0)]],
                     actions=[set_action("activate", 2, "x", 1)])
        self.assertIn("KEY_HASH", codes(self.report(ir)))


# --- penempatan dan fragmen -------------------------------------------------------------

class CompileTests(SimpleTestCase):
    def test_actuator_strategy_irrigation(self):
        plan = compiler.compile_plan(irrigation_ir(), placement.ACTUATOR)
        self.assertEqual(plan["evaluators"], [3])
        self.assertEqual({p: f["role"] for p, f in plan["fragments"].items()}, {1: "src", 2: "src", 3: "eval"})
        self.assertEqual(plan["fragments"][1]["publish"][0]["key"], "soil_moisture")
        self.assertEqual(plan["fragments"][1]["publish"][0]["heartbeat"], 100)
        self.assertNotIn("rule", json.dumps(plan["fragments"][1]["publish"]))
        evaluator = plan["fragments"][3]
        self.assertEqual([s["mac"] for s in evaluator["sources"]], [MACS[1], MACS[2]])
        self.assertEqual(evaluator["on_activate"], [{"do": "set", "key": "pump", "value": 1.0}])
        self.assertNotIn("token", json.dumps(plan))
        self.assertEqual(plan["data_frames"], {1: 25, 2: 25})

    def test_same_device_has_no_radio(self):
        ir = make_ir([(1, "level")], when=[[(1, ">", 3)]], actions=[set_action("activate", 1, "pump", 1)])
        plan = compiler.compile_plan(ir)
        self.assertEqual(list(plan["fragments"]), [1])
        self.assertEqual(plan["fragments"][1]["role"], "eval")
        self.assertEqual(plan["fragments"][1]["sources"], [])
        self.assertEqual(plan["fragments"][1]["signals"][0]["from"], 0)

    def test_two_actuators_evaluate_independently(self):
        plan = compiler.compile_plan(irrigation_ir(two_actuators=True))
        self.assertEqual(plan["evaluators"], [3, 4])
        self.assertEqual(plan["fragments"][4]["on_activate"], [{"do": "set", "key": "valve", "value": 1.0}])
        self.assertEqual(len(plan["fragments"][1]["publish"]), 1)   # satu broadcast untuk dua evaluator

    def test_deterministic_and_strategy_specific_rev(self):
        a = compiler.compile_plan(irrigation_ir())
        self.assertEqual(a, compiler.compile_plan(irrigation_ir()))
        self.assertNotEqual(a["rev"], compiler.compile_plan(irrigation_ir(), placement.SENSOR)["rev"])

    def test_publish_table_merges_rules(self):
        one = compiler.compile_plan(irrigation_ir())["fragments"][1]
        other = compiler.compile_plan(irrigation_ir(max_age=30))["fragments"][1]
        table = compiler.publish_table(MACS[1], [one, other])
        self.assertEqual([e["key"] for e in table["publish"]], ["soil_moisture"])
        self.assertEqual(table["publish"][0]["heartbeat"], 10)


# --- evaluator --------------------------------------------------------------------------

class FakeVariable(SimpleNamespace):
    pass


class CloudCondition:
    """Kondisi yang dapat dinilai rules.conditions.evaluate (jalur cloud)."""

    def __init__(self, s, operator, threshold):
        from .models import compare

        self.variable_id, self.operator, self.threshold = s, operator, threshold
        self.variable = FakeVariable(display_name=f"s{s}", device=FakeVariable(name="d"), unit="", is_boolean=False)
        self._compare = compare

    def get_operator_display(self):
        return self.operator

    def test(self, value):
        return self._compare(value, self.operator, self.threshold)


class CloudPath:
    """Mesin ECA cloud tanpa database: conditions.evaluate + decide + step, dengan aturan
    kapan menilai yang sama dengan EcaEngine (pesan: selalu; tick: hanya bila PENDING)."""

    def __init__(self, fragment):
        self.when = [[CloudCondition(*c) for c in g] for g in fragment["when"]]
        self.clear = [[CloudCondition(*c) for c in g] for g in fragment["clear"]]
        timing = fragment["timing"]
        self.spec = SimpleNamespace(duration_seconds=timing["duration"], cooldown_seconds=timing["cooldown"])
        self.max_age = timing["max_age"]
        self.state = SimpleNamespace(status="IDLE", pending_since=None, last_activated_at=None, last_cleared_at=None)
        self.readings = {}

    def evaluate(self, now_ms):
        now = at(now_ms)
        when, _ = conditions.evaluate(self.when, self.readings, now, self.max_age)
        clear = conditions.evaluate(self.clear, self.readings, now, self.max_age)[0] if self.clear else (
            None if when is None else not when)
        condition, clear_ok = conditions.decide(when, clear, bool(self.clear))
        transition = step(self.state, self.spec, condition, clear_ok, now)
        return getattr(transition, "value", transition)

    def message(self, s, value, now_ms):
        self.readings[s] = conditions.Reading(value, at(now_ms), "message")
        return self.evaluate(now_ms)

    def tick(self, now_ms):
        return self.evaluate(now_ms) if self.state.status == "PENDING" else None


def random_fragment(rng):
    signals = [(1, "a"), (2, "b"), (3, "c")][: rng.randint(1, 3)]
    n = len(signals)
    group = lambda: [(rng.randint(1, n), rng.choice(["<", "<=", ">", ">=", "==", "!="]), rng.randint(0, 20))
                     for _ in range(rng.randint(1, 3))]
    when = [group() for _ in range(rng.randint(1, 3))]
    clear = [group() for _ in range(rng.randint(1, 2))] if rng.random() < 0.5 else []
    ir = make_ir(signals, when=when, clear=clear, actions=[set_action("activate", 4, "x", 1),
                                                          set_action("clear", 4, "x", 0)],
                 duration=rng.choice([0, 0, 5, 10]), cooldown=rng.choice([0, 0, 8]),
                 max_age=rng.choice([0, 15, 30, 300]))
    return compiler.compile_plan(ir)["fragments"][4], n


class ParityTests(SimpleTestCase):
    """Evaluator referensi == mesin cloud, untuk 1000 aturan dan urutan data acak."""

    def test_random_rules(self):
        rng = random.Random(2026)
        transitions = 0
        for case in range(1000):
            fragment, n = random_fragment(rng)
            cloud, edge = CloudPath(fragment), Runtime(fragment)
            now = 0
            for _ in range(40):
                now += rng.choice([500, 1000, 2000, 4000, 8000, 16000])
                if rng.random() < 0.6:
                    s, value = rng.randint(1, n), float(rng.randint(0, 20))
                    expected = cloud.message(s, value, now)
                    edge.receive(s, value, 0, now)
                    got = edge.step(now)
                else:
                    expected = cloud.tick(now)
                    got = edge.tick(now)
                self.assertEqual(got.kind if got else None, expected, f"case {case} at {now} ms")
                self.assertEqual(edge.status, str(getattr(cloud.state.status, "value", cloud.state.status)))
                transitions += expected is not None
        self.assertGreater(transitions, 500)

    def test_frames_feed_two_rules(self):
        one = compiler.compile_plan(irrigation_ir(duration=0))["fragments"][3]
        two = compiler.compile_plan(make_ir([(1, "soil_moisture")], when=[[(1, "<", 10)]],
                                            actions=[set_action("activate", 3, "fan", 1)]))["fragments"][3]
        frame = wire.encode_data(1, [(wire.key_hash("soil_moisture"), 5.0, 0)])
        runtimes = [Runtime(one), Runtime(two)]
        for runtime in runtimes:
            self.assertEqual(runtime.receive_frame(MACS[1].lower(), frame, 0), 1)
        self.assertEqual([r.step(0).kind for r in runtimes], ["ACTIVATE", "ACTIVATE"])


# --- test vector ------------------------------------------------------------------------

def check_schema(value, schema, path="$"):
    """Validator JSON Schema sederhana (type, required, properties, items, enum, pattern)."""
    import re

    types = schema.get("type")
    names = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    if types:
        allowed = tuple(names[t] for t in ([types] if isinstance(types, str) else types))
        assert isinstance(value, allowed) and not (isinstance(value, bool) and int in allowed), f"{path}: type"
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: enum"
    if "pattern" in schema:
        assert re.match(schema["pattern"], value), f"{path}: pattern"
    for key in schema.get("required", []):
        assert key in value, f"{path}: missing {key}"
    for key, sub in schema.get("properties", {}).items():
        if isinstance(value, dict) and key in value:
            check_schema(value[key], sub, f"{path}.{key}")
    if "items" in schema and isinstance(value, list):
        for i, entry in enumerate(value):
            check_schema(entry, schema["items"], f"{path}[{i}]")


class VectorTests(SimpleTestCase):
    def test_files_match_generator(self):
        files = vectors.generate()
        for relative, obj in files.items():
            path = vectors.VECTOR_DIR / relative
            self.assertTrue(path.exists(), f"{relative} missing: run manage.py export_microrule_vectors")
            self.assertEqual(path.read_text(encoding="utf-8"), vectors.dumps(obj), f"{relative} is stale")
        on_disk = {p.relative_to(vectors.VECTOR_DIR).as_posix() for p in vectors.VECTOR_DIR.rglob("*.json")}
        self.assertEqual(on_disk, set(files))

    def test_fragments_follow_schema(self):
        schema = json.loads((vectors.VECTOR_DIR.parent / "schema" / "fragment-v1.schema.json").read_text())
        for strategy in placement.STRATEGIES:
            for fragment in compiler.compile_plan(irrigation_ir(two_actuators=True), strategy)["fragments"].values():
                check_schema(fragment, schema)
        for relative, obj in vectors.generate().items():
            if "fragment" in obj:
                check_schema(obj["fragment"], schema, relative)

    def test_documented_expectations(self):
        files = vectors.generate()
        statuses = lambda name: [s["status"] for s in files[f"eval/{name}.json"]["steps"]]
        self.assertEqual(statuses("duration_then_activate"), ["PENDING", "PENDING", "PENDING", "ACTIVE"])
        self.assertEqual(statuses("duration_interrupted"), ["PENDING", "IDLE", "IDLE"])
        self.assertEqual(statuses("stale_stays_active"), ["ACTIVE", "ACTIVE", "IDLE"])
        self.assertEqual(statuses("ignored_frames"), ["IDLE"] * 5)
        applied = [s["applied"] for s in files["act/restart_recover.json"]["steps"]]
        self.assertEqual(applied, [True, None, True])


# --- penyimpanan, halaman, dan unduhan ---------------------------------------------------

class ServiceTests(EdgeData):
    def test_store_revisions(self):
        rule = self.make_rule()
        plans = compile_and_store(rule, self.siti)
        self.assertEqual(set(plans), set(placement.STRATEGIES))
        self.assertEqual(plans["actuator"].revision, 1)
        self.assertEqual(EdgeFragment.objects.filter(plan=plans["actuator"]).count(), 3)
        compile_and_store(rule, self.siti)
        self.assertEqual(EdgePlan.objects.filter(rule=rule).count(), 2)        # tidak berubah
        EcaCondition.objects.filter(threshold=10).update(threshold=12)
        again = compile_and_store(Rule.objects.get(pk=rule.pk), self.siti)
        self.assertEqual(again["actuator"].revision, 2)
        self.assertEqual(EdgePlan.objects.filter(rule=rule, is_current=True).count(), 2)

    def test_not_stored_when_off(self):
        self.assertEqual(compile_and_store(self.make_rule(edge=False)), {})
        with override_settings(MICRO_RULES={"VISIBILITY": "off"}):
            self.assertEqual(compile_and_store(self.make_rule()), {})
        self.assertFalse(EdgePlan.objects.exists())

    def test_stale_after_mac_change(self):
        rule = self.make_rule()
        compile_and_store(rule)
        self.assertFalse(any(tab["stale"] for tab in rule_context(rule)["tabs"]))
        Device.objects.filter(pk=self.t.pk).update(mac_address="AA:BB:CC:00:00:22")
        self.assertTrue(all(tab["stale"] for tab in rule_context(Rule.objects.get(pk=rule.pk))["tabs"]))

    def test_table_shows_state_broadcast(self):
        rule = self.make_rule()
        tab = {t["strategy"]: t for t in rule_context(rule)["tabs"]}["sensor"]
        rows = {row["name"]: row for row in tab["rows"]}
        self.assertEqual(rows["Soil node"]["state_frame"], 29)               # evaluator memancarkan keputusan
        self.assertEqual(rows["Pump node"]["sources"], ["Soil node"])        # aktuator mendengar evaluator
        self.assertTrue(rows["Pump node"]["listens_state"])
        self.assertEqual(rows["Weather node"]["publish"], ["temperature"])
        self.assertEqual(tab["diagram"].count('id="edge-arrow-'), 1)
        self.assertIn(tab["rev"], tab["diagram"])                             # id panah unik per diagram

    def test_device_context_and_budget(self):
        rule = self.make_rule()
        compile_and_store(rule)
        context = device_context(self.s)
        self.assertEqual([row["role"] for row in context["rows"]], ["src"])
        self.assertEqual([e["key"] for e in context["table"]["publish"]], ["soil_moisture"])
        with override_settings(MICRO_RULES={"VISIBILITY": "all", "MAX_RULES_PER_DEVICE": 1}):
            from .edge.services import analyze as service_analyze

            report = service_analyze(self.make_rule(), "actuator")
            self.assertIn("DEVICE_BUDGET", codes(report))


class EdgeViewTests(RuleViewTests):
    """Form, halaman aturan, unduhan, dan halaman device (memakai data RuleViewTests)."""

    def setUp(self):
        super().setUp()
        Device.objects.filter(pk=self.node.pk).update(mac_address=MACS[1])

    def create(self, edge=True):
        data = self.form_data()
        if edge:
            data["rule-edge_enabled"] = "on"
        self.client.post(reverse("rules:new_model", args=["eca"]), data)
        return Rule.objects.get()

    def test_form_switch_creates_plans(self):
        page = self.client.get(reverse("rules:new_model", args=["eca"]))
        self.assertContains(page, "Also run on my devices")
        rule = self.create()
        self.assertTrue(rule.edge_enabled)
        self.assertEqual(EdgePlan.objects.filter(rule=rule, is_current=True).count(), 2)
        detail = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(detail, "Run on devices")
        self.assertContains(detail, "Runs on devices")          # satu device, tanpa aksi cloud
        self.assertContains(detail, "<svg", html=False)
        device = self.client.get(reverse("devices:detail", args=[self.node.pk]))
        self.assertContains(device, "Rules that can run on this device")

    def test_hidden_when_disabled(self):
        rule = self.create(edge=False)
        with override_settings(MICRO_RULES={"VISIBILITY": "off"}):
            self.assertNotContains(self.client.get(reverse("rules:detail", args=[rule.pk])), "Run on devices")
            self.assertNotContains(self.client.get(reverse("rules:new_model", args=["eca"])), "Also run on my devices")
            self.assertEqual(self.client.get(reverse("rules:edge_download", args=[rule.pk, "actuator"])).status_code, 404)

    def test_admin_only(self):
        rule = self.create()
        detail = reverse("rules:detail", args=[rule.pk])
        download = reverse("rules:edge_download", args=[rule.pk, "actuator"])
        docs = reverse("userdocs:page", args=["edge-rules"])
        admin = User.objects.create_user("root", password="rahasia-12345", role=User.Role.ADMIN)
        with override_settings(MICRO_RULES={"VISIBILITY": "admin"}):
            # pengguna biasa (pemilik aturan): tidak melihat apa pun
            self.assertNotContains(self.client.get(detail), "Run on devices")
            self.assertNotContains(self.client.get(reverse("rules:edit", args=[rule.pk])), "Also run on my devices")
            self.assertEqual(self.client.get(download).status_code, 404)
            self.assertEqual(self.client.get(docs).status_code, 404)
            self.assertNotContains(self.client.get(reverse("userdocs:index")), "Running rules on devices")
            self.assertNotContains(self.client.get(reverse("rules:list")), "title=\"Also prepared to run on the devices\"")
            # menyimpan form tanpa field itu tidak mematikan edge_enabled yang sudah ada
            self.client.post(reverse("rules:edit", args=[rule.pk]), self.form_data())
            self.assertTrue(Rule.objects.get(pk=rule.pk).edge_enabled)
            # Admin melihat semuanya
            self.client.force_login(admin)
            self.assertContains(self.client.get(detail), "Run on devices")
            self.assertContains(self.client.get(reverse("rules:edit", args=[rule.pk])), "Also run on my devices")
            self.assertEqual(self.client.get(download).status_code, 200)
            self.assertEqual(self.client.get(docs).status_code, 200)
        with override_settings(MICRO_RULES={"VISIBILITY": "off"}):
            self.assertNotContains(self.client.get(detail), "Run on devices")      # juga untuk Admin
            self.assertEqual(self.client.get(docs).status_code, 404)

    def test_downloads(self):
        rule = self.create()
        url = reverse("rules:edge_download", args=[rule.pk, "actuator"])
        body = json.loads(self.client.get(url).content)
        self.assertEqual(body["verdict"], "FULL")
        self.assertIn("1", body["fragments"])
        part = self.client.get(url + "?device=1")
        self.assertEqual(json.loads(part.content)["role"], "eval")
        self.assertIn("attachment", part["Content-Disposition"])
        archive = zipfile.ZipFile(io.BytesIO(self.client.get(url + "?format=zip").content))
        self.assertIn("plan.json", archive.namelist())
        self.assertEqual(self.client.get(url + "?device=9").status_code, 404)
        self.assertEqual(self.client.get(reverse("rules:edge_download", args=[rule.pk, "nope"])).status_code, 404)

    def test_other_users_cannot_see(self):
        rule = self.create()
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(reverse("rules:edge_download", args=[rule.pk, "actuator"])).status_code, 404)
        self.assertEqual(self.client.post(reverse("rules:edge_compile", args=[rule.pk])).status_code, 404)

    def test_compile_button(self):
        rule = self.create()
        Device.objects.filter(pk=self.node.pk).update(mac_address="AA:BB:CC:00:00:77")
        response = self.client.post(reverse("rules:edge_compile", args=[rule.pk]))
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]) + "#edge", fetch_redirect_response=False)
        self.assertEqual(EdgePlan.objects.get(rule=rule, strategy="actuator", is_current=True).revision, 2)

    def test_export_import_keeps_flag(self):
        from .portable import export_rule

        rule = self.create()
        self.assertTrue(export_rule(rule)["run_on_devices"])
        rule.edge_enabled = False
        rule.save()
        self.assertNotIn("run_on_devices", export_rule(rule))

    def test_user_docs_page(self):
        response = self.client.get(reverse("userdocs:page", args=["edge-rules"]))
        self.assertContains(response, "Running rules on devices")
        self.assertContains(response, str(limits()["MAX_SIGNALS"]))
