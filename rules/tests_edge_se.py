"""Tes strategi pembanding SE (evaluator di sensor, tahap 26b), metrik, dan simulator."""
import csv
import io
import random
from pathlib import Path
import tempfile

from django.core.management import call_command
from django.test import SimpleTestCase

from .edge import compile as compiler, metrics, placement, sim, wire
from .edge.actuator import ActuatorRuntime
from .edge.emitter import StateEmitter
from .edge.evaluator import Runtime
from .edge.samples import MACS, irrigation_ir, make_ir, set_action
from .tests_edge import EdgeData

SENSOR, ACTUATOR = placement.SENSOR, placement.ACTUATOR


def either_sensor_ir():
    """Pompa P (p3) menyala bila soil[S] < 5 ATAU temperature[T] > 35, mati bila temperature < 30.
    Tidak ada sensor yang wajib ada; tanpa S, AE tetap bisa aktif dan pulih lewat T. Pada SE,
    S (kondisi pertama) menjadi evaluator, sehingga matinya S menghentikan semuanya."""
    return make_ir([(1, "soil_moisture"), (2, "temperature")], when=[[(1, "<", 5)], [(2, ">", 35)]],
                   clear=[[(2, "<", 30)]],
                   actions=[set_action("activate", 3, "pump", 1), set_action("clear", 3, "pump", 0)],
                   max_age=60, rule=7)


class PlacementTests(SimpleTestCase):
    def test_policies(self):
        ir = make_ir([(1, "a"), (2, "b"), (2, "c")], when=[[(1, ">", 0), (2, ">", 0), (3, ">", 0)]],
                     actions=[set_action("activate", 4, "x", 1)])
        self.assertEqual(placement.evaluators(ir, SENSOR, "primary"), [1])
        self.assertEqual(placement.evaluators(ir, SENSOR, "most_signals"), [2])
        self.assertEqual(placement.evaluators(ir, SENSOR, "min_traffic", rates={1: 5, 2: 0.1, 3: 0.1}), [1])
        self.assertEqual(placement.evaluators(ir, ACTUATOR), [4])

    def test_source_that_is_actuator_evaluates(self):
        ir = make_ir([(1, "a"), (3, "level")], when=[[(1, ">", 0), (2, ">", 0)]],
                     actions=[set_action("activate", 3, "pump", 1)])
        self.assertEqual(placement.evaluators(ir, SENSOR), [3])
        plan = compiler.compile_plan(ir, SENSOR)
        self.assertNotIn("emit", plan["fragments"][3])      # tidak ada aktuator lain
        self.assertIsNone(plan["state_frame"])
        # SE identik dengan AE untuk aturan ini; pengguna diberi tahu, hanya rev dan strategy berbeda.
        from .edge.analyze import analyze_ir_full

        report = analyze_ir_full(ir, SENSOR)
        self.assertIn("SE_AT_ACTUATOR", {i["code"] for i in report.items})
        ae = compiler.compile_plan(ir)["fragments"]
        strip = lambda f: {k: v for k, v in f.items() if k not in ("rev", "strategy")}
        self.assertEqual({p: strip(f) for p, f in plan["fragments"].items()}, {p: strip(f) for p, f in ae.items()})
        from .edge.analyze import analyze_ir_full

        report = analyze_ir_full(ir, SENSOR)
        self.assertIn("SE_SAME_AS_AE", {i["code"] for i in report.items})
        same = compiler.compile_plan(ir, ACTUATOR)
        strip = lambda f: {k: v for k, v in f.items() if k not in ("rev", "strategy")}
        self.assertEqual({p: strip(f) for p, f in plan["fragments"].items()},
                         {p: strip(f) for p, f in same["fragments"].items()})

    def test_sensor_strategy_irrigation(self):
        plan = compiler.compile_plan(irrigation_ir(), SENSOR)
        self.assertEqual({p: f["role"] for p, f in plan["fragments"].items()}, {1: "eval", 2: "src", 3: "act"})
        self.assertEqual(plan["fragments"][1]["on_activate"], [])
        self.assertEqual(plan["fragments"][1]["emit"]["repeat"], 3)
        act = plan["fragments"][3]
        self.assertEqual(act["evaluator"]["mac"], MACS[1])
        self.assertEqual(act["state_timeout"], 30)
        self.assertEqual(plan["state_frame"], 29)


class MetricsTests(SimpleTestCase):
    def test_irrigation(self):
        ae = metrics.plan_metrics(irrigation_ir(), compiler.compile_plan(irrigation_ir()))
        se = metrics.plan_metrics(irrigation_ir(), compiler.compile_plan(irrigation_ir(), SENSOR))
        self.assertEqual((ae["max_hops"], se["max_hops"]), (1, 2))
        self.assertEqual((ae["always_on_sensor"], se["always_on_sensor"]), (False, True))
        self.assertGreater(se["frames_per_minute"], ae["frames_per_minute"])

    def test_extra_critical_device(self):
        ir = either_sensor_ir()
        ae = metrics.plan_metrics(ir, compiler.compile_plan(ir))
        se = metrics.plan_metrics(ir, compiler.compile_plan(ir, SENSOR))
        self.assertEqual((ae["critical"], se["critical"]), ([3], [1, 3]))

    def test_divergence_only_with_replicated_actuators(self):
        ir = irrigation_ir(two_actuators=True)
        self.assertTrue(metrics.plan_metrics(ir, compiler.compile_plan(ir))["divergence"])
        self.assertFalse(metrics.plan_metrics(ir, compiler.compile_plan(ir, SENSOR))["divergence"])


class EmitterActuatorTests(SimpleTestCase):
    def setUp(self):
        plan = compiler.compile_plan(irrigation_ir(duration=0), SENSOR)
        self.evaluator = plan["fragments"][1]
        self.act = ActuatorRuntime(plan["fragments"][3])

    def test_repeats_then_heartbeat(self):
        emitter = StateEmitter.from_fragment(self.evaluator)
        emitter.start(0)
        self.assertEqual(len(emitter.due(0)), 1)
        emitter.on_transition("ACTIVATE", 1000)
        sent = [len(emitter.due(t)) for t in (1000, 1100, 1200, 1300, 10999, 11000)]
        self.assertEqual(sent, [1, 1, 1, 0, 0, 1])
        state = wire.decode_state(emitter.frame(12000))
        self.assertEqual((state["active"], state["seq"], state["age_ms"]), (True, 1, 11000))

    def test_lost_frames_converge(self):
        emitter = StateEmitter.from_fragment(self.evaluator)
        emitter.on_transition("ACTIVATE", 0)
        emitter.due(0)                                  # semua salinan hilang
        emitter.on_transition("CLEAR", 1000)
        emitter.due(1000)
        emitter.on_transition("ACTIVATE", 2000)
        frames = emitter.due(2000)
        events = self.act.receive_state(MACS[1], frames[0], 2010)
        self.assertEqual([e.kind for e in events], ["ACTIVATE"])
        heartbeat = emitter.frame(12000)
        self.assertEqual(self.act.receive_state(MACS[1], heartbeat, 12010), [])
        self.assertEqual(self.act.applied, True)

    def test_missed_count(self):
        emitter = StateEmitter.from_fragment(self.evaluator)
        emitter.on_transition("ACTIVATE", 0)
        self.act.receive_state(MACS[1], emitter.frame(0), 0)
        emitter.on_transition("CLEAR", 1000)
        emitter.on_transition("ACTIVATE", 2000)
        self.act.receive_state(MACS[1], emitter.frame(2000), 2000)
        self.assertEqual(self.act.missed, 1)


class RuntimeEquivalenceTests(SimpleTestCase):
    """Tanpa kehilangan frame dan tanpa kegagalan, AE dan SE sama dengan "kebenaran"."""

    def test_lossless_three_way(self):
        rng = random.Random(7)
        for case in range(25):
            ir = irrigation_ir(duration=rng.choice([0, 10, 30]), max_age=rng.choice([30, 300]))
            traces = sim.synthetic_traces(ir, 1800, sample_s=rng.choice([2, 5, 10]), seed=case)
            for strategy in placement.STRATEGIES:
                plan = compiler.compile_plan(ir, strategy)
                result = sim.simulate(ir, plan, traces, sim.SimConfig(duration_s=1800, seed=case))
                for values in result["actuators"].values():
                    self.assertEqual((values["missed"], values["spurious"]), (0, 0), f"{strategy} case {case}")
                    self.assertLess(values["wrong_ratio"], 0.01)

    def test_evaluator_failure_hurts_only_sensor_strategy(self):
        ir = either_sensor_ir()
        traces = sim.synthetic_traces(ir, 3600, sample_s=5, seed=0)
        config = sim.SimConfig(duration_s=3600, failures=[(1, 600, None)], seed=1)
        wrong, missed = {}, {}
        for strategy in placement.STRATEGIES:
            result = sim.simulate(ir, compiler.compile_plan(ir, strategy), traces, config)
            wrong[strategy] = result["actuators"][3]["wrong_ms"]
            missed[strategy] = result["actuators"][3]["missed"]
        # AE hanya kehilangan aktivasi karena tanah kering (sinyal S); SE kehilangan semuanya.
        self.assertGreater(wrong[SENSOR], wrong[ACTUATOR] * 3)
        self.assertGreater(missed[SENSOR], missed[ACTUATOR])

    def test_loss_is_counted(self):
        ir = irrigation_ir(duration=0)
        traces = sim.synthetic_traces(ir, 600, seed=2)
        result = sim.simulate(ir, compiler.compile_plan(ir), traces, sim.SimConfig(duration_s=600, loss=0.3, seed=2))
        self.assertGreater(result["lost"], 0)
        self.assertEqual(result["data_sent"] * 1, result["lost"] + result["delivered"])


class CommandTests(EdgeData):
    def test_edge_report_and_simulate(self):
        rule = self.make_rule()
        out = io.StringIO()
        call_command("edge_report", stdout=out)
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual([(r["strategy"], r["verdict"]) for r in rows], [("actuator", "PARTIAL"), ("sensor", "PARTIAL")])
        self.assertEqual(rows[1]["max_hops"], "2")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "sim.csv"
            call_command("edge_simulate", rule=rule.pk, loss="0,0.2", kill=["Soil node@300-600"], duration=900,
                         out=str(target), stdout=io.StringIO())
            rows = list(csv.DictReader(target.open()))
        self.assertEqual(len(rows), 2 * 2 * 2)                 # strategi x loss x (tanpa / dengan kegagalan)
        self.assertEqual({r["actuator"] for r in rows}, {"Pump node"})

    def test_vectors_command(self):
        with tempfile.TemporaryDirectory() as folder:
            call_command("export_microrule_vectors", out=folder, stdout=io.StringIO())
            self.assertTrue((Path(folder) / "eval" / "duration_then_activate.json").exists())


class SeedEdgeRulesTests(EdgeData):
    """Setiap contoh di seed_edge_rules menghasilkan verdict dan kode yang dijelaskannya."""

    def test_examples(self):
        from django.contrib.auth import get_user_model

        from .edge.services import analyze
        from .management.commands.seed_edge_rules import RULES
        from .models import Rule

        get_user_model().objects.create_user("andi", password="rahasia-12345")
        out = io.StringIO()
        call_command("seed_edge_rules", owner="andi", neighbour="budi", stdout=out)
        self.assertIn(f"{len(RULES)} example rule(s)", out.getvalue())
        for spec in RULES:
            rule = Rule.objects.get(owner__username="andi", name=spec["name"])
            verdict, ae_codes, se_codes = spec["expect"]
            ae, se = analyze(rule, ACTUATOR), analyze(rule, SENSOR)
            self.assertEqual(ae.verdict, verdict, spec["name"])
            for code in ae_codes:
                self.assertIn(code, {i["code"] for i in ae.items}, spec["name"])
            for code in se_codes:
                self.assertIn(code, {i["code"] for i in se.items}, spec["name"])
        policy = Rule.objects.get(owner__username="andi", name__startswith="Edge 08")
        from .edge.ir import device_names, from_rule

        ir = from_rule(policy)
        names = device_names(ir)
        self.assertEqual(names[placement.evaluators(ir, SENSOR, "primary")[0]], "Edge Light Sensor")
        self.assertEqual(names[placement.evaluators(ir, SENSOR, "most_signals")[0]], "Edge Weather Station")
        # Semua halaman aturan dan halaman Weather Station tampil; humidity dipancarkan sekali, heartbeat 10 s.
        from django.urls import reverse

        andi = get_user_model().objects.get(username="andi")
        self.client.force_login(andi)
        for rule in Rule.objects.filter(owner=andi):
            self.assertContains(self.client.get(reverse("rules:detail", args=[rule.pk])), "Run on devices")
        from devices.models import Device

        station = Device.objects.get(owner=andi, name="Edge Weather Station")
        page = self.client.get(reverse("devices:detail", args=[station.pk]))
        self.assertContains(page, "<code>humidity</code> every 10 s")
        again = io.StringIO()
        call_command("seed_edge_rules", owner="andi", neighbour="budi", stdout=again)
        self.assertIn("0 example rule(s)", again.getvalue())


class BatchTests(EdgeData):
    """edge_batch: aturan acak deterministik, kolom CSV, sumber database."""

    def run_batch(self, folder, name, **options):
        path = Path(folder) / name
        call_command("edge_batch", out=str(path), stdout=io.StringIO(), stderr=io.StringIO(), **options)
        return path.read_text(), list(csv.DictReader(path.open()))

    def test_random_rules(self):
        from .edge import batch

        self.assertEqual(batch.random_rule(1, 5), batch.random_rule(1, 5))
        self.assertNotEqual(batch.random_rule(1, 5), batch.random_rule(1, 6))
        with tempfile.TemporaryDirectory() as folder:
            text, rows = self.run_batch(folder, "a.csv", rules=6, duration=600, loss="0,0.2")
            again, _ = self.run_batch(folder, "b.csv", rules=6, duration=600, loss="0,0.2")
        self.assertEqual(text, again)
        self.assertEqual(list(rows[0]), batch.COLUMNS)
        self.assertEqual({r["strategy"] for r in rows}, {"actuator", "sensor"})
        self.assertEqual({r["loss"] for r in rows}, {"0.0", "0.2"})
        # AE dan SE dibandingkan pada kombinasi yang sama
        key = lambda r: (r["rule"], r["loss"], r["failure"], r["run"], r["actuator"])
        self.assertEqual({key(r) for r in rows if r["strategy"] == "actuator"},
                         {key(r) for r in rows if r["strategy"] == "sensor"})

    def test_database_rules(self):
        self.make_rule()
        with tempfile.TemporaryDirectory() as folder:
            _, rows = self.run_batch(folder, "db.csv", source="db", duration=600, loss="0", failures="none")
        self.assertEqual(len(rows), 2)                       # 1 aturan x 2 strategi x 1 aktuator
