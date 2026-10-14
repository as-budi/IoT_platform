"""Tes rule chain (model Flow, tahap 8): mesin penelusur graf, node, form, halaman, dan contoh."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from devices.models import Command, Device, Telemetry, Variable

from . import queue
from .engines import ENGINES
from .engines.flow import MAX_STEPS, NODE_TYPES
from .flow_diagram import layout
from .forms import check_graph
from .models import Alarm, FlowLink, FlowNode, FlowSpec, Rule, RuleExecution, RuleModel, RuleState

User = get_user_model()
T0 = timezone.now().replace(microsecond=0)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class FlowData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", email="siti@example.com", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.node = Device.objects.create(owner=cls.siti, name="Greenhouse Node")
        cls.station = Device.objects.create(owner=cls.siti, name="Weather Station")
        cls.temp = Variable.objects.create(device=cls.node, key="temperature", label="Temperature", unit="°C")
        cls.hum = Variable.objects.create(device=cls.node, key="humidity", label="Humidity", unit="%")
        cls.rain = Variable.objects.create(device=cls.station, key="rain", label="Rain", unit="mm/h")
        cls.fan = Variable.objects.create(
            device=cls.node, key="fan", label="Fan",
            kind=Variable.Kind.ACTUATOR, value_type=Variable.ValueType.BOOLEAN,
        )
        cls.foreign = Device.objects.create(owner=cls.budi, name="Budi's node")
        cls.foreign_temp = Variable.objects.create(device=cls.foreign, key="temperature")

    def chain(self, nodes, links, device="node", owner=None, name="Chain"):
        """nodes = [(nama, jenis, {field})], links = [(asal, label, tujuan)]; "Input" = awal."""
        rule = Rule.objects.create(owner=owner or self.siti, name=name, model=RuleModel.FLOW)
        FlowSpec.objects.create(rule=rule, device=getattr(self, device) if device else None)
        made = {}
        for order, (node_name, kind, fields) in enumerate(nodes):
            made[node_name] = FlowNode.objects.create(rule=rule, name=node_name, kind=kind, order=order, **fields)
        for order, (source, label, target) in enumerate(links):
            FlowLink.objects.create(
                rule=rule, source=None if source == "Input" else made[source],
                label=label, target=made[target], order=order,
            )
        return rule

    def send(self, now, device=None, **values):
        """Simpan telemetri (seperti save_telemetry), lalu serahkan pesannya ke rule engine."""
        device = device or self.node
        rows = []
        for key, value in values.items():
            variable = device.variables.get(key=key)
            Telemetry.objects.create(variable=variable, value=value, ts=now)
            rows.append((variable, value, now))
        return queue.process(queue.telemetry_message(device, rows), now=now)

    def trace(self, rule):
        return RuleState.objects.get(rule=rule).snapshot["trace"]

    def path(self, rule):
        """[(nama node, label)] dari jejak terakhir."""
        return [(row["name"], row["label"]) for row in self.trace(rule)]


class FlowEngineTests(FlowData):
    def fan_chain(self):
        return self.chain(
            [("too_hot", "CHECK", {"key": "temperature", "operator": ">", "threshold": 32}),
             ("fan_on", "SET_ACTUATOR", {"target_variable": self.fan, "value": 1}),
             ("fan_off", "SET_ACTUATOR", {"target_variable": self.fan, "value": 0})],
            [("Input", "SUCCESS", "too_hot"), ("too_hot", "TRUE", "fan_on"), ("too_hot", "FALSE", "fan_off")],
        )

    def test_true_branch_runs_action_and_logs(self):
        rule = self.fan_chain()
        [execution] = self.send(at(0), temperature=33.5)
        self.assertEqual(execution.transition, RuleExecution.Transition.RUN)
        self.assertEqual(self.path(rule), [("too_hot", "TRUE"), ("fan_on", "SUCCESS")])
        first = self.trace(rule)[0]
        self.assertEqual(first["from"], "Input")
        self.assertEqual(first["detail"], "temperature = 33.5 > 32 → yes")
        self.assertEqual(first["next"], ["fan_on"])
        self.assertEqual(self.trace(rule)[1]["next"], [], "no link after fan_on: end of branch")
        self.fan.refresh_from_db()
        self.assertEqual(self.fan.desired_value, 1)
        self.assertEqual(execution.results[0]["detail"], "Fan = ON")
        self.assertEqual(execution.trace, self.trace(rule))
        self.assertEqual(execution.snapshot, {"Greenhouse Node · temperature": 33.5})

    def test_repeat_message_is_not_logged_but_trace_updates(self):
        rule = self.fan_chain()
        self.send(at(0), temperature=33.5)
        self.assertEqual(self.send(at(10), temperature=34), [], "fan already ON: nothing changed")
        self.assertEqual(rule.executions.count(), 1)
        self.assertIn("already requested", self.trace(rule)[1]["detail"])
        self.assertEqual(self.trace(rule)[0]["data"], {"temperature": 34})
        [execution] = self.send(at(20), temperature=30)
        self.assertEqual(self.path(rule), [("too_hot", "FALSE"), ("fan_off", "SUCCESS")])
        self.assertEqual(execution.results[0]["detail"], "Fan = OFF")

    def test_missing_key_follows_failure_link(self):
        rule = self.chain(
            [("too_hot", "CHECK", {"key": "temperature", "operator": ">", "threshold": 32}),
             ("no_temp", "CREATE_ALARM", {"severity": "WARNING", "message": "{deviceName} sent no temperature"})],
            [("Input", "SUCCESS", "too_hot"), ("too_hot", "FAILURE", "no_temp")],
        )
        self.send(at(0), humidity=80)
        self.assertEqual(self.path(rule), [("too_hot", "FAILURE"), ("no_temp", "SUCCESS")])
        self.assertEqual(self.trace(rule)[0]["detail"], "the message has no 'temperature'")
        self.assertEqual(Alarm.objects.get().message, "Greenhouse Node sent no temperature")

    def test_no_matching_link_ends_branch(self):
        rule = self.fan_chain()
        FlowLink.objects.filter(label="FALSE").delete()
        self.assertEqual(self.send(at(0), temperature=20), [])
        self.assertEqual(self.path(rule), [("too_hot", "FALSE")])
        self.assertEqual(self.trace(rule)[0]["next"], [])

    def test_fan_out_copies_message_depth_first(self):
        rule = self.chain(
            [("double", "CALCULATE", {"key": "t2", "operand_a": "temperature", "math_op": "*", "operand_b": "2"}),
             ("check_double", "CHECK", {"key": "t2", "operator": ">", "threshold": 50}),
             ("check_plain", "CHECK", {"key": "t2", "operator": ">", "threshold": 50})],
            [("Input", "SUCCESS", "double"), ("Input", "SUCCESS", "check_plain"),
             ("double", "SUCCESS", "check_double")],
        )
        self.send(at(0), temperature=30)
        # Depth-first: cabang pertama (double -> check_double) habis dulu, baru cabang kedua.
        self.assertEqual(self.path(rule), [("double", "SUCCESS"), ("check_double", "TRUE"),
                                           ("check_plain", "FAILURE")])
        trace = self.trace(rule)
        self.assertEqual(trace[1]["data"], {"temperature": 30, "t2": 60.0})
        self.assertEqual(trace[2]["data"], {"temperature": 30}, "the second branch got its own copy")
        self.assertEqual([row["step"] for row in trace], [1, 2, 3])

    def test_fan_in_runs_shared_node_per_arrival(self):
        rule = self.chain(
            [("hot", "CHECK", {"key": "temperature", "operator": ">", "threshold": 30}),
             ("humid", "CHECK", {"key": "humidity", "operator": ">", "threshold": 80}),
             ("alarm", "CREATE_ALARM", {"severity": "MAJOR"})],
            [("Input", "SUCCESS", "hot"), ("Input", "SUCCESS", "humid"),
             ("hot", "TRUE", "alarm"), ("humid", "TRUE", "alarm")],
        )
        self.send(at(0), temperature=31, humidity=90)
        self.assertEqual([name for name, _ in self.path(rule)], ["hot", "alarm", "humid", "alarm"])
        self.assertEqual(Alarm.objects.count(), 1, "second arrival finds the alarm still active")
        self.assertIn("still active", self.trace(rule)[3]["detail"])

    def test_value_changed_memory_per_device(self):
        rule = self.chain(
            [("changed", "CHANGED", {"key": "temperature", "threshold": 0.5})],
            [("Input", "SUCCESS", "changed")], device=None,
        )
        Variable.objects.create(device=self.station, key="temperature")
        self.send(at(0), temperature=25)
        self.assertEqual(self.path(rule), [("changed", "TRUE")])
        self.assertEqual(self.trace(rule)[0]["detail"], "temperature = 25, first value")
        self.send(at(10), temperature=25.3)
        self.assertEqual(self.path(rule), [("changed", "FALSE")])
        self.send(at(20), temperature=25.6)
        self.assertEqual(self.path(rule), [("changed", "TRUE")], "0.6 away from the last value that passed")
        self.send(at(30), device=self.station, temperature=25.6)
        self.assertEqual(self.path(rule), [("changed", "TRUE")], "another device has its own memory")
        memory = FlowNode.objects.get(name="changed").memory
        self.assertEqual(set(memory), {str(self.node.pk), str(self.station.pk)})
        self.assertEqual(memory[str(self.node.pk)]["value"], 25.6)

    def test_rate_limit(self):
        rule = self.chain(
            [("limit", "THROTTLE", {"seconds": 60})], [("Input", "SUCCESS", "limit")],
        )
        self.send(at(0), temperature=25)
        self.assertEqual(self.path(rule), [("limit", "TRUE")])
        self.send(at(30), temperature=25)
        self.assertEqual(self.path(rule), [("limit", "FALSE")])
        self.assertEqual(self.trace(rule)[0]["detail"], "last passed 30 s ago, limit 60 s")
        self.send(at(60), temperature=25)
        self.assertEqual(self.path(rule), [("limit", "TRUE")])

    def test_latest_value_enrichment(self):
        rule = self.chain(
            [("rain_now", "LATEST", {"variable": self.rain, "key": "rain", "seconds": 600}),
             ("dry_air", "CHECK", {"key": "rain", "operator": "<", "threshold": 0.5})],
            [("Input", "SUCCESS", "rain_now"), ("rain_now", "SUCCESS", "dry_air")],
        )
        self.send(at(0), temperature=25)
        self.assertEqual(self.path(rule), [("rain_now", "FAILURE")])
        self.assertEqual(self.trace(rule)[0]["detail"], "Weather Station · Rain has no data")
        Telemetry.objects.create(variable=self.rain, value=0.2, ts=at(-100))
        self.send(at(0), temperature=25)
        self.assertEqual(self.path(rule), [("rain_now", "SUCCESS"), ("dry_air", "TRUE")])
        self.assertEqual(self.trace(rule)[0]["detail"], "rain = 0.2 from Weather Station · Rain (100 s old)")
        self.assertEqual(self.trace(rule)[1]["data"], {"temperature": 25, "rain": 0.2})
        self.send(at(700), temperature=25)
        self.assertEqual(self.path(rule), [("rain_now", "FAILURE")])
        self.assertIn("stale (800 s old, limit 600 s)", self.trace(rule)[0]["detail"])

    def test_latest_value_needs_access(self):
        Telemetry.objects.create(variable=self.foreign_temp, value=20, ts=at(0))
        rule = self.chain([("spy", "LATEST", {"variable": self.foreign_temp})], [("Input", "SUCCESS", "spy")])
        self.send(at(1), temperature=25)
        self.assertEqual(self.path(rule), [("spy", "FAILURE")])
        self.assertIn("no access", self.trace(rule)[0]["detail"])

    def test_calculate_and_division_by_zero(self):
        rule = self.chain(
            [("ratio", "CALCULATE", {"key": "ratio", "operand_a": "humidity", "math_op": "/",
                                     "operand_b": "temperature"}),
             ("highest", "CALCULATE", {"key": "top", "operand_a": "humidity", "math_op": "max",
                                       "operand_b": "temperature"})],
            [("Input", "SUCCESS", "ratio"), ("ratio", "SUCCESS", "highest")],
        )
        self.send(at(0), temperature=20, humidity=70)
        self.assertEqual(self.trace(rule)[0]["detail"], "ratio = humidity ÷ temperature = 3.5")
        self.assertEqual(self.trace(rule)[1]["detail"], "top = max(humidity, temperature) = 70")
        self.send(at(10), temperature=0, humidity=70)
        self.assertEqual(self.path(rule), [("ratio", "FAILURE")])
        self.assertEqual(self.trace(rule)[0]["detail"], "division by zero")

    def test_alarm_lifecycle_and_email(self):
        rule = self.chain(
            [("hot", "CHECK", {"key": "temperature", "operator": ">", "threshold": 32}),
             ("alarm", "CREATE_ALARM", {"severity": "CRITICAL", "key": "temperature",
                                        "message": "{deviceName}: {temperature} °C"}),
             ("mail", "EMAIL", {"message": "Hot: {values}"}),
             ("ok", "CLEAR_ALARM", {})],
            [("Input", "SUCCESS", "hot"), ("hot", "TRUE", "alarm"), ("alarm", "SUCCESS", "mail"),
             ("hot", "FALSE", "ok")],
        )
        [execution] = self.send(at(0), temperature=35)
        alarm = Alarm.objects.get()
        self.assertEqual((alarm.message, alarm.value, alarm.severity), ("Greenhouse Node: 35 °C", 35, "CRITICAL"))
        self.assertEqual(len(execution.results), 2)
        self.assertEqual(mail.outbox[0].subject, "[IoT] Chain triggered")
        self.assertEqual(mail.outbox[0].body, "Hot: temperature = 35")
        self.assertEqual(mail.outbox[0].to, ["siti@example.com"])
        [execution] = self.send(at(10), temperature=36)   # alarm masih aktif, tetapi email terkirim lagi
        self.assertEqual(len(mail.outbox), 2, "without a Rate limit node every message sends an email")
        [execution] = self.send(at(20), temperature=25)
        alarm.refresh_from_db()
        self.assertFalse(alarm.is_active)
        self.assertEqual(execution.results[0]["detail"], f"Cleared alarm #{alarm.pk}")
        self.assertEqual(self.send(at(30), temperature=24), [], "nothing left to clear")

    def test_failed_action_goes_to_failure_and_is_logged(self):
        foreign_fan = Variable.objects.create(
            device=self.foreign, key="fan", kind=Variable.Kind.ACTUATOR, value_type=Variable.ValueType.BOOLEAN,
        )
        rule = self.chain(
            [("fan_on", "SET_ACTUATOR", {"target_variable": foreign_fan, "value": 1}),
             ("report", "CREATE_ALARM", {"severity": "WARNING", "message": "Fan action failed"})],
            [("Input", "SUCCESS", "fan_on"), ("fan_on", "FAILURE", "report")],
        )
        [execution] = self.send(at(0), temperature=25)
        self.assertEqual(self.path(rule), [("fan_on", "FAILURE"), ("report", "SUCCESS")])
        self.assertFalse(execution.ok)
        self.assertIn("no access", execution.results[0]["detail"])

    def test_scope_disabled_and_foreign_devices(self):
        any_device = self.chain([("seen", "THROTTLE", {"seconds": 0})], [("Input", "SUCCESS", "seen")],
                                device=None, name="Any")
        station_only = self.chain([("seen", "THROTTLE", {"seconds": 0})], [("Input", "SUCCESS", "seen")],
                                  device="station", name="Station")
        off = self.chain([("seen", "THROTTLE", {"seconds": 0})], [("Input", "SUCCESS", "seen")], name="Off")
        Rule.objects.filter(pk=off.pk).update(is_enabled=False)
        self.send(at(0), temperature=25)
        self.assertTrue(RuleState.objects.filter(rule=any_device, last_evaluated_at=at(0)).exists())
        self.assertFalse(RuleState.objects.filter(rule=station_only).exists())
        self.assertFalse(RuleState.objects.filter(rule=off).exists())
        self.send(at(10), device=self.foreign, temperature=25)
        self.assertEqual(RuleState.objects.get(rule=any_device).last_evaluated_at, at(0),
                         "messages from Budi's device never reach Siti's chain")

    def test_loop_guard(self):
        rule = self.chain(
            [("a", "THROTTLE", {"seconds": 0}), ("b", "THROTTLE", {"seconds": 0})],
            [("Input", "SUCCESS", "a"), ("a", "TRUE", "b"), ("b", "TRUE", "a")],
        )
        with self.assertLogs("rules.engines.flow", "WARNING"):
            self.send(at(0), temperature=25)
        trace = self.trace(rule)
        self.assertEqual(len(trace), MAX_STEPS + 1)
        self.assertIn("Stopped after", trace[-1]["error"])

    def test_tick_does_nothing(self):
        self.fan_chain()
        self.assertEqual(ENGINES[RuleModel.FLOW].tick(at(0)), [])


class GraphCheckTests(TestCase):
    KINDS = {"hot": "CHECK", "fan": "SET_ACTUATOR", "calc": "CALCULATE"}

    def check(self, *links):
        return check_graph(dict(self.KINDS), [{"source": s, "label": l, "target": t} for s, l, t in links])

    def test_valid_graph(self):
        self.assertEqual(self.check(("Input", "SUCCESS", "hot"), ("hot", "TRUE", "fan"), ("hot", "FALSE", "calc")), [])

    def test_errors(self):
        self.assertIn("Connect Input", self.check(("hot", "TRUE", "fan"))[0])
        self.assertIn("no node called “nope”", self.check(("Input", "SUCCESS", "nope"))[0])
        self.assertIn("ends with True/False/Failure, not Success",
                      self.check(("Input", "SUCCESS", "hot"), ("hot", "SUCCESS", "fan"))[0])
        self.assertIn("ends with Success/Failure, not True",
                      self.check(("Input", "SUCCESS", "fan"), ("fan", "TRUE", "hot"))[0])
        self.assertIn("appears twice", self.check(("Input", "SUCCESS", "hot"), ("Input", "SUCCESS", "hot"))[0])
        self.assertIn("cannot link to itself", self.check(("Input", "SUCCESS", "calc"), ("calc", "SUCCESS", "calc"))[0])

    def test_loop_is_reported(self):
        errors = self.check(("Input", "SUCCESS", "hot"), ("hot", "TRUE", "calc"),
                            ("calc", "SUCCESS", "fan"), ("fan", "SUCCESS", "hot"))
        self.assertEqual(errors, ["The links form a loop: hot → calc → fan → hot. A rule chain must not loop back."])

    def test_input_label_is_forced_to_success(self):
        links = [{"source": "Input", "label": "TRUE", "target": "hot"}]
        self.assertEqual(check_graph(dict(self.KINDS), links), [])
        self.assertEqual(links[0]["label"], "SUCCESS")


def node_data(forms, initial=0):
    data = {"nodes-TOTAL_FORMS": str(len(forms)), "nodes-INITIAL_FORMS": str(initial),
            "nodes-MIN_NUM_FORMS": "1", "nodes-MAX_NUM_FORMS": "30"}
    for i, fields in enumerate(forms):
        for key, value in {"ttl_seconds": "300", "severity": "MAJOR", **fields}.items():
            data[f"nodes-{i}-{key}"] = value
    return data


def link_data(links):
    data = {"links-TOTAL_FORMS": str(len(links)), "links-INITIAL_FORMS": "0",
            "links-MIN_NUM_FORMS": "0", "links-MAX_NUM_FORMS": "60"}
    for i, (source, label, target) in enumerate(links):
        data.update({f"links-{i}-source": source, f"links-{i}-label": label, f"links-{i}-target": target})
    return data


class FlowViewTests(FlowData):
    def setUp(self):
        self.client.force_login(self.siti)

    def post_data(self, nodes=None, links=None, **extra):
        data = {"rule-name": "Fan chain", "rule-is_enabled": "on", "flow-device": str(self.node.pk)}
        data.update(node_data(nodes or [
            {"name": "too_hot", "kind": "CHECK", "key": "temperature", "operator": ">", "threshold": "32"},
            {"name": "fan_on", "kind": "SET_ACTUATOR", "target_variable": str(self.fan.pk), "value": "1"},
        ]))
        data.update(link_data(links or [("Input", "SUCCESS", "too_hot"), ("too_hot", "TRUE", "fan_on")]))
        data.update(extra)
        return data

    def test_create_page_and_rule(self):
        response = self.client.get(reverse("rules:new_model", args=["flow"]))
        self.assertContains(response, "New rule")
        self.assertContains(response, 'id="node-types"')
        response = self.client.post(reverse("rules:new_model", args=["flow"]), self.post_data())
        rule = Rule.objects.get(name="Fan chain")
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        self.assertEqual(rule.model, RuleModel.FLOW)
        self.assertEqual(rule.flow.device, self.node)
        check = rule.nodes.get(name="too_hot")
        self.assertEqual((check.key, check.operator, check.threshold), ("temperature", ">", 32))
        self.assertIsNone(check.target_variable, "fields of other node types are cleared")
        self.assertEqual([str(link) for link in rule.links.all()],
                         ["Input —Success→ too_hot", "too_hot —True→ fan_on"])
        # Langsung bekerja:
        self.send(at(0), temperature=35)
        self.fan.refresh_from_db()
        self.assertEqual(self.fan.desired_value, 1)

    def test_graph_errors_are_shown(self):
        response = self.client.post(reverse("rules:new_model", args=["flow"]), self.post_data(
            links=[("Input", "SUCCESS", "too_hot"), ("too_hot", "SUCCESS", "fan_on")]))
        self.assertContains(response, "“too_hot” ends with True/False/Failure, not Success.")
        self.assertFalse(Rule.objects.exists())

    def test_node_field_validation(self):
        response = self.client.post(reverse("rules:new_model", args=["flow"]), self.post_data(nodes=[
            {"name": "1bad", "kind": "CHECK", "key": "temperature"},
            {"name": "fan_on", "kind": "SET_ACTUATOR", "target_variable": str(self.fan.pk), "value": "3"},
        ]))
        self.assertContains(response, "Use letters, digits and _, starting with a letter.")
        self.assertContains(response, "Required for this node type.")
        self.assertContains(response, "On/off actuators accept 1 (ON) or 0 (OFF).")
        response = self.client.post(reverse("rules:new_model", args=["flow"]), self.post_data(nodes=[
            {"name": "a", "kind": "THROTTLE", "seconds": "5"},
            {"name": "A", "kind": "THROTTLE", "seconds": "5"},
        ], links=[("Input", "SUCCESS", "a")]))
        self.assertContains(response, "Another node already has this name.")

    def test_foreign_actuator_is_not_a_choice(self):
        other_fan = Variable.objects.create(device=self.foreign, key="fan", kind=Variable.Kind.ACTUATOR)
        response = self.client.post(reverse("rules:new_model", args=["flow"]), self.post_data(nodes=[
            {"name": "fan_on", "kind": "SET_ACTUATOR", "target_variable": str(other_fan.pk), "value": "1"},
        ], links=[("Input", "SUCCESS", "fan_on")]))
        self.assertContains(response, "Select a valid choice")

    def test_edit_rename_keeps_links_and_clears_memory(self):
        self.client.post(reverse("rules:new_model", args=["flow"]), self.post_data())
        rule = Rule.objects.get()
        FlowNode.objects.filter(rule=rule).update(memory={"1": {"at": "x"}})
        response = self.client.get(reverse("rules:edit", args=[rule.pk]))
        self.assertContains(response, 'value="too_hot" selected')
        nodes = list(rule.nodes.all())
        data = self.post_data(nodes=[
            {"id": str(nodes[0].pk), "name": "very_hot", "kind": "CHECK", "key": "temperature",
             "operator": ">", "threshold": "35"},
            {"id": str(nodes[1].pk), "name": "fan_on", "kind": "SET_ACTUATOR",
             "target_variable": str(self.fan.pk), "value": "1"},
        ], links=[("Input", "SUCCESS", "very_hot"), ("very_hot", "TRUE", "fan_on")])
        data["nodes-INITIAL_FORMS"] = "2"
        data.update({f"nodes-{i}-rule": str(rule.pk) for i in range(2)})
        response = self.client.post(reverse("rules:edit", args=[rule.pk]), data)
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        self.assertEqual(sorted(rule.nodes.values_list("pk", flat=True)), sorted(n.pk for n in nodes),
                         "existing nodes are updated, not recreated")
        self.assertEqual([str(link) for link in rule.links.all()],
                         ["Input —Success→ very_hot", "very_hot —True→ fan_on"])
        self.assertEqual(list(rule.nodes.values_list("memory", flat=True)), [{}, {}])

    def test_detail_list_and_live_box(self):
        self.client.post(reverse("rules:new_model", args=["flow"]), self.post_data())
        rule = Rule.objects.get()
        response = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(response, "No message has passed through the chain yet")
        self.assertContains(response, 'class="flow-diagram"')
        self.assertContains(response, f"/ws/devices/{self.node.pk}/")
        self.send(at(0), temperature=35)
        response = self.client.get(reverse("rules:evaluation", args=[rule.pk]))
        self.assertContains(response, "temperature = 35 &gt; 32 → yes")
        self.assertContains(response, "is-walked")
        response = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(response, "Path through the chain (2 steps)")
        self.assertContains(response, "fan_on (Set actuator): Fan = ON")
        response = self.client.get(reverse("rules:list"))
        self.assertContains(response, "Listening")
        self.assertContains(response, "rule chain of 2 nodes (1 action)")

    def test_other_users_cannot_open_or_edit(self):
        rule = self.chain([("seen", "THROTTLE", {"seconds": 0})], [("Input", "SUCCESS", "seen")],
                          device="foreign", owner=self.budi)
        self.assertEqual(self.client.get(reverse("rules:detail", args=[rule.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("rules:edit", args=[rule.pk])).status_code, 404)


class DiagramTests(FlowData):
    def test_columns_follow_longest_path_and_trace_is_marked(self):
        rule = self.chain(
            [("a", "CHECK", {"key": "temperature", "operator": ">", "threshold": 30}),
             ("b", "THROTTLE", {"seconds": 0}),
             ("c", "CLEAR_ALARM", {}),
             ("lonely", "CLEAR_ALARM", {})],
            [("Input", "SUCCESS", "a"), ("a", "TRUE", "b"), ("b", "TRUE", "c"), ("Input", "SUCCESS", "c")],
        )
        self.send(at(0), temperature=31)
        diagram = layout(rule, self.trace(rule))
        x = {node["name"]: node["x"] for node in diagram["nodes"]}
        self.assertLess(x["a"], x["b"])
        self.assertLess(x["b"], x["c"], "c sits after b even though Input also links to it")
        self.assertTrue(diagram["has_unreachable"])
        walked = [(link["label"], link["walked"]) for link in diagram["links"]]
        self.assertEqual(walked, [("", True), ("True", True), ("True", True), ("", True)])
        self.assertEqual({n["name"]: n["result"] for n in diagram["nodes"]}["a"], "True")


class SampleChainTests(TestCase):
    """Skenario di docs/flow-mekanisme.md: seed_demo + seed_rules, lalu pesan contoh."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_rules", stdout=StringIO())

    def send(self, device, seconds, **values):
        device = Device.objects.get(name=device)
        now = T0 + timedelta(days=1, seconds=seconds)
        rows = []
        for key, value in values.items():
            variable = device.variables.get(key=key)
            Telemetry.objects.create(variable=variable, value=value, ts=now)
            rows.append((variable, value, now))
        return queue.process(queue.telemetry_message(device, rows), now=now)

    def path(self, name):
        return [(row["name"], row["label"]) for row in Rule.objects.get(name=name).state.snapshot["trace"]]

    def test_seeded_chains_are_valid_graphs(self):
        for rule in Rule.objects.filter(model=RuleModel.FLOW):
            kinds = {node.name: node.kind for node in rule.nodes.all()}
            links = [{"source": l.source.name if l.source else "Input", "label": l.label, "target": l.target.name}
                     for l in rule.links.select_related("source", "target")]
            self.assertEqual(check_graph(kinds, links), [], rule.name)
            for node in rule.nodes.all():
                for link in node.outgoing.all():
                    self.assertIn(link.label, NODE_TYPES[node.kind].outputs)
        self.assertFalse(Rule.objects.get(name="Greenhouse watering chain").is_enabled)

    def test_lab_power_monitor(self):
        name = "Lab power monitor"
        executions = self.send("Lab Power Meter", 0, voltage=230, current=4, power=600)
        self.assertEqual(self.path(name), [
            ("over_voltage", "FALSE"), ("volt_ok", "SUCCESS"),
            ("apparent", "SUCCESS"), ("pf", "SUCCESS"), ("low_pf", "TRUE"),
            ("pf_limit", "TRUE"), ("pf_email", "SUCCESS"),
        ])
        trace = Rule.objects.get(name=name).state.snapshot["trace"]
        self.assertEqual(trace[2]["detail"], "apparent_power = voltage × current = 920")
        self.assertEqual(trace[3]["detail"], "power_factor = power ÷ apparent_power = 0.652174")
        self.assertEqual(mail.outbox[-1].body, "Low power factor on Lab Power Meter: 0.652174 (600 W of 920 VA).")
        self.assertEqual(len([e for e in executions if e.rule.name == name]), 1)
        # 2 menit kemudian: masih rendah, tetapi Rate limit menahan email kedua.
        self.send("Lab Power Meter", 120, voltage=238, current=4, power=600)
        self.assertEqual(self.path(name)[:2], [("over_voltage", "TRUE"), ("volt_alarm", "SUCCESS")])
        self.assertEqual(self.path(name)[-1], ("pf_limit", "FALSE"))
        self.assertEqual(len(mail.outbox), 1)
        alarm = Alarm.objects.get(rule__name=name)
        self.assertEqual(alarm.message, "Over-voltage on Lab Power Meter: 238 V")
        # Arus nol: pembagian dengan nol -> Failure, tanpa panah -> cabang selesai.
        self.send("Lab Power Meter", 240, voltage=230, current=0, power=0)
        self.assertEqual(self.path(name)[-1], ("pf", "FAILURE"))
        alarm.refresh_from_db()
        self.assertFalse(alarm.is_active)

    def test_pond_ph_watch(self):
        name = "Pond pH watch"
        self.send("Fish Pond Node", 0, ph=6.2)
        self.assertEqual(self.path(name), [("ph_changed", "TRUE"), ("too_acid", "TRUE"), ("ph_alarm", "SUCCESS")])
        self.assertEqual(Alarm.objects.get(rule__name=name).message, "Pond pH out of range: 6.2")
        self.send("Fish Pond Node", 60, ph=6.25)
        self.assertEqual(self.path(name), [("ph_changed", "FALSE")])
        self.send("Fish Pond Node", 120, ph=7.4)
        self.assertEqual(self.path(name), [("ph_changed", "TRUE"), ("too_acid", "FALSE"),
                                           ("too_alkaline", "FALSE"), ("ph_ok", "SUCCESS")])
        self.assertFalse(Alarm.objects.active().filter(rule__name=name).exists())
        self.send("Fish Pond Node", 180, ph=8.9)
        self.assertEqual(self.path(name)[-2:], [("too_alkaline", "TRUE"), ("ph_alarm", "SUCCESS")])
        self.send("Fish Pond Node", 240, dissolved_oxygen=6)
        self.assertEqual(self.path(name), [("ph_changed", "FAILURE")], "no ph in the message")

    def test_cold_room_door_check(self):
        name = "Cold room door check"
        self.send("Cold Storage Unit", 0, temperature=7, humidity=80)
        self.assertEqual(self.path(name), [("humid", "FALSE")])
        self.send("Cold Storage Unit", 30, temperature=7, humidity=90)
        self.assertEqual(self.path(name), [("humid", "TRUE"), ("warm", "TRUE"), ("buzz_limit", "TRUE"),
                                           ("buzzer", "SUCCESS")])
        command = Command.objects.get(device__name="Cold Storage Unit", params={"seconds": 5})
        self.assertEqual(command.method, "buzzer")
        self.send("Cold Storage Unit", 90, temperature=7, humidity=90)
        self.assertEqual(self.path(name)[-1], ("buzz_limit", "FALSE"))

    def test_greenhouse_watering_chain(self):
        Rule.objects.filter(name="Smart irrigation").update(is_enabled=False)
        Rule.objects.filter(name="Greenhouse watering chain").update(is_enabled=True)
        name = "Greenhouse watering chain"
        pump = Variable.objects.get(device__name="Greenhouse Node", key="pump")
        # Data hujan terakhir dari seed_demo sudah lebih dari 15 menit: basi.
        self.send("Greenhouse Node", 0, soil_moisture=20)
        self.assertEqual(self.path(name), [("dry", "TRUE"), ("rain_now", "FAILURE"), ("stale_rain", "SUCCESS")])
        self.send("Greenhouse Weather Station", 30, rain=0)
        [execution] = [e for e in self.send("Greenhouse Node", 60, soil_moisture=20) if e.rule.name == name]
        self.assertEqual(self.path(name), [
            ("dry", "TRUE"), ("rain_now", "SUCCESS"), ("no_rain", "TRUE"), ("pump_on", "SUCCESS"),
            ("mail_limit", "TRUE"), ("notify", "SUCCESS"), ("rain_back", "SUCCESS"),
        ])
        pump.refresh_from_db()
        self.assertEqual(pump.desired_value, 1)
        self.assertEqual(mail.outbox[-1].body, "Watering started: soil 20 %, rain 0 mm/h.")
        self.assertEqual([r["action"] for r in execution.results],
                         ["pump_on (Set actuator)", "notify (Send email)", "rain_back (Clear alarm)"])
        self.send("Greenhouse Node", 120, soil_moisture=35)
        self.assertEqual(self.path(name), [("dry", "FALSE"), ("wet", "FALSE")])
        self.send("Greenhouse Node", 180, soil_moisture=50)
        self.assertEqual(self.path(name), [("dry", "FALSE"), ("wet", "TRUE"), ("pump_off", "SUCCESS")])
        pump.refresh_from_db()
        self.assertEqual(pump.desired_value, 0)
