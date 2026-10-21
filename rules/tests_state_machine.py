"""Tes tahap 20: model aturan state machine (mesin, form, halaman, JSON, akses)."""
import json
from datetime import timedelta

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from activity.models import ActivityEvent
from devices.models import Device, Variable
from sharing.models import DeviceShare

from .engines import ENGINES
from .engines.state_machine import ConditionSyntaxError, format_condition, parse_condition
from .models import (
    Alarm, MachineCondition, MachineInstance, MachineSpec, MachineState, MachineTransition, Rule, RuleAction,
    RuleModel,
)
from .portable import export_rule
from .tests import RuleTestData, at

Trigger = MachineTransition.Trigger
ENTER, EXIT = RuleAction.RunOn.ENTER, RuleAction.RunOn.EXIT


class ConditionTextTests(SimpleTestCase):
    def test_and_binds_before_or(self):
        groups = parse_condition("temperature > 35 and humidity < 40 or temperature >= 45")
        self.assertEqual(groups, [[("temperature", ">", 35.0), ("humidity", "<", 40.0)],
                                  [("temperature", ">=", 45.0)]])
        self.assertEqual(format_condition(groups), "temperature > 35 and humidity < 40 or temperature >= 45")

    def test_words_and_aliases(self):
        self.assertEqual(parse_condition("pump = ON AND door != off"), [[("pump", "==", 1.0), ("door", "!=", 0.0)]])
        self.assertEqual(parse_condition("t ≤ -2.5"), [[("t", "<=", -2.5)]])

    def test_errors(self):
        for text, message in [
            ("", "Enter a condition"),
            ("temperature", "is not a comparison"),
            ("temperature > hot", "is not a number"),
            ("temperature > 3 and", "is not a comparison"),
            ("_x > 1", "is not a comparison"),
        ]:
            with self.assertRaisesMessage(ConditionSyntaxError, message):
                parse_condition(text)


class MachineData(RuleTestData):
    """siti: Greenhouse Node (soil_moisture, pump) + temperature."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.temp = Variable.objects.create(device=cls.node, key="temperature", unit="°C")
        Device.objects.filter(pk=cls.node.pk).update(is_online=True)

    def machine(self, device="node", owner=None, states=None, transitions=None, actions=None, name="Heat stages"):
        """Aturan contoh. states = [(nama, tone)], keadaan pertama = initial.
        transitions = [(asal|"*", tujuan, pemicu, kondisi, detik, label)]; actions = [(keadaan, run_on, jenis, field)]."""
        rule = Rule.objects.create(owner=owner or self.siti, name=name, model=RuleModel.STATE_MACHINE)
        MachineSpec.objects.create(rule=rule, device=self.node if device == "node" else device)
        states = states or [("NORMAL", "OK"), ("HOT", "WARNING"), ("CRITICAL", "DANGER"), ("OFFLINE", "NEUTRAL"),
                            ("MAINTENANCE", "INFO")]
        by_name = {}
        for order, (state_name, tone) in enumerate(states):
            by_name[state_name] = MachineState.objects.create(rule=rule, name=state_name, tone=tone, order=order,
                                                              is_initial=order == 0)
        transitions = transitions if transitions is not None else [
            ("NORMAL", "HOT", Trigger.CONDITION, "temperature > 30", 0, ""),
            ("HOT", "CRITICAL", Trigger.CONDITION, "temperature > 40", 60, ""),
            ("HOT", "NORMAL", Trigger.CONDITION, "temperature <= 28", 0, ""),
            ("CRITICAL", "NORMAL", Trigger.CONDITION, "temperature <= 28", 0, ""),
            ("CRITICAL", "CRITICAL", Trigger.TIMEOUT, "", 600, ""),
            ("*", "OFFLINE", Trigger.OFFLINE, "", None, ""),
            ("OFFLINE", "NORMAL", Trigger.CONDITION, "temperature <= 30", 0, ""),
            ("NORMAL", "MAINTENANCE", Trigger.MANUAL, "", None, "Start maintenance"),
            ("MAINTENANCE", "NORMAL", Trigger.TIMEOUT, "", 1800, ""),
        ]
        for order, (source, target, trigger, condition, seconds, label) in enumerate(transitions):
            transition = MachineTransition.objects.create(
                rule=rule, order=order, trigger=trigger, seconds=seconds, label=label,
                source=None if source == "*" else by_name[source], target=by_name[target],
            )
            for number, group in enumerate(parse_condition(condition) if condition else [], start=1):
                for position, (key, op, value) in enumerate(group):
                    MachineCondition.objects.create(transition=transition, group=number, order=position, key=key,
                                                    operator=op, threshold=value)
        actions = actions if actions is not None else [
            ("HOT", ENTER, RuleAction.Kind.SET_ACTUATOR, {"target_variable": self.pump, "value": 1}),
            ("HOT", EXIT, RuleAction.Kind.SET_ACTUATOR, {"target_variable": self.pump, "value": 0}),
            ("CRITICAL", ENTER, RuleAction.Kind.ALARM, {"severity": "CRITICAL", "message": "{device}: {values}"}),
            ("CRITICAL", EXIT, RuleAction.Kind.CLEAR_ALARM, {}),
        ]
        for order, (state_name, run_on, kind, fields) in enumerate(actions):
            RuleAction.objects.create(rule=rule, state=by_name[state_name], run_on=run_on, kind=kind, order=order,
                                      **fields)
        return rule

    def temperature(self, value, seconds, device=None):
        variable = self.temp if device is None else device.variables.get(key="temperature")
        return self.send(value, at(seconds), device=device, variable=variable)

    def tick(self, seconds):
        return ENGINES[RuleModel.STATE_MACHINE].tick(at(seconds))

    def state_of(self, rule, device=None):
        return MachineInstance.objects.get(rule=rule, device=device or self.node).state.name


class EngineTests(MachineData):
    def test_full_cycle(self):
        rule = self.machine()
        self.assertEqual(self.temperature(25, 0), [])
        self.assertEqual(self.state_of(rule), "NORMAL", "first data: initial state, no actions")

        [move] = self.temperature(31, 10)
        self.assertEqual((move.transition, move.from_state, move.to_state, move.device),
                         ("MOVE", "NORMAL", "HOT", self.node))
        self.assertEqual(move.results[0]["action"], "Enter HOT · Set actuator")
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_value, 1)

        # > 40 harus bertahan 60 s: menunggu, lalu tick memindahkannya.
        self.assertEqual(self.temperature(45, 20), [])
        instance = MachineInstance.objects.get(rule=rule)
        self.assertEqual(len(instance.pending), 1)
        self.assertEqual(self.tick(50), [])
        [move] = self.tick(81)
        self.assertEqual(move.to_state, "CRITICAL")
        self.assertEqual([r["action"] for r in move.results],
                         ["Exit HOT · Set actuator", "Enter CRITICAL · Raise alarm"])
        self.assertEqual(Alarm.objects.active().get().message, "Greenhouse Node: temperature = 45")
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_value, 0)

        # Ke dirinya sendiri setiap 600 s: keluar (clear alarm) lalu masuk lagi (alarm baru).
        self.assertEqual(self.tick(600), [])
        [again] = self.tick(681)
        self.assertEqual((again.from_state, again.to_state), ("CRITICAL", "CRITICAL"))
        self.assertEqual(Alarm.objects.count(), 2)
        self.assertEqual(Alarm.objects.active().count(), 1)

        [back] = self.temperature(25, 700)
        self.assertEqual(back.to_state, "NORMAL")
        self.assertFalse(Alarm.objects.active().exists())
        self.assertEqual(rule.executions.count(), 4)

    def test_waiting_condition_resets_when_false(self):
        rule = self.machine()
        self.temperature(31, 0)
        self.temperature(45, 10)
        self.temperature(35, 40)       # > 40 tidak lagi benar: penantian batal
        self.assertEqual(MachineInstance.objects.get(rule=rule).pending, {})
        self.temperature(45, 50)
        self.assertEqual(self.tick(100), [], "timer restarted at 50 s")
        self.assertEqual(self.tick(110)[0].to_state, "CRITICAL")

    def test_first_matching_transition_wins_and_one_step_per_message(self):
        rule = self.machine(transitions=[
            ("NORMAL", "HOT", Trigger.CONDITION, "temperature > 30", 0, ""),
            ("NORMAL", "CRITICAL", Trigger.CONDITION, "temperature > 40", 0, ""),
            ("HOT", "CRITICAL", Trigger.CONDITION, "temperature > 40", 0, ""),
        ], actions=[])
        [move] = self.temperature(50, 0)
        self.assertEqual(move.to_state, "HOT", "order decides; HOT → CRITICAL waits for the next message")
        self.assertEqual(self.temperature(50, 1)[0].to_state, "CRITICAL")
        self.assertEqual(self.state_of(rule), "CRITICAL")

    def test_stale_or_missing_values_are_unknown(self):
        rule = self.machine(transitions=[
            ("NORMAL", "HOT", Trigger.CONDITION, "temperature > 30 and humidity > 50", 0, ""),
        ], actions=[])
        self.assertEqual(self.temperature(35, 0), [])
        snapshot = MachineInstance.objects.get(rule=rule).snapshot
        self.assertIsNone(snapshot["transitions"][0]["result"], "humidity has no data: unknown, no move")

    def test_offline_from_any_state_and_back(self):
        rule = self.machine()
        self.temperature(31, 0)
        Device.objects.filter(pk=self.node.pk).update(is_online=False)
        [move] = self.tick(10)
        self.assertEqual((move.from_state, move.to_state), ("HOT", "OFFLINE"))
        self.assertEqual(self.tick(20), [], "“* → OFFLINE” does not fire again while OFFLINE")
        Device.objects.filter(pk=self.node.pk).update(is_online=True)
        self.assertEqual(self.temperature(25, 30)[0].to_state, "NORMAL")
        self.assertEqual(rule.executions.count(), 3)

    def test_silent_device(self):
        rule = self.machine(transitions=[("*", "OFFLINE", Trigger.SILENT, "", 300, "")], actions=[])
        self.temperature(25, 0)
        Device.objects.filter(pk=self.node.pk).update(last_seen_at=at(0))
        self.assertEqual(self.tick(200), [])
        self.assertEqual(self.tick(301)[0].to_state, "OFFLINE")
        self.assertEqual(self.state_of(rule), "OFFLINE")

    def test_specific_device_starts_without_data(self):
        rule = self.machine(transitions=[("NORMAL", "HOT", Trigger.TIMEOUT, "", 60, "")], actions=[])
        self.assertEqual(self.tick(0), [])
        self.assertEqual(self.state_of(rule), "NORMAL")
        self.assertEqual(self.tick(61)[0].to_state, "HOT")

    def test_every_owned_device_has_its_own_state(self):
        rule = self.machine(device=None)
        other = Device.objects.create(owner=self.siti, name="Second Node", is_online=True)
        Variable.objects.create(device=other, key="temperature")
        Variable.objects.create(device=self.foreign, key="temperature")
        DeviceShare.objects.create(device=self.foreign, user=self.siti, role="EDITOR")
        self.temperature(35, 0)
        self.temperature(20, 0, device=other)
        self.temperature(35, 0, device=self.foreign)
        self.assertEqual(self.state_of(rule), "HOT")
        self.assertEqual(self.state_of(rule, other), "NORMAL")
        self.assertFalse(MachineInstance.objects.filter(device=self.foreign).exists(),
                         "“every device I own” leaves out devices shared with me")

    def test_disabled_rule_and_reset(self):
        rule = self.machine()
        self.temperature(35, 0)
        self.client.force_login(self.siti)
        self.client.post(reverse("rules:toggle", args=[rule.pk]))
        self.assertFalse(MachineInstance.objects.filter(rule=rule).exists(), "disabling resets every device")
        self.assertEqual(self.temperature(35, 10), [])
        self.assertFalse(MachineInstance.objects.filter(rule=rule).exists())

    def test_move_is_logged_in_activity(self):
        self.machine()
        self.temperature(31, 0)
        event = ActivityEvent.objects.get(verb="rule.move")
        self.assertEqual((event.owner, event.device, event.actor_name), (self.siti, self.node, "rule engine"))
        self.assertIn("Greenhouse Node NORMAL → HOT", event.message)


class AccessTests(MachineData):
    def test_shared_viewer_reads_but_cannot_control(self):
        DeviceShare.objects.create(device=self.node, user=self.budi, role="VIEWER")
        rule = self.machine(owner=self.budi)
        [move] = self.temperature(31, 0)
        self.assertEqual(move.to_state, "HOT")
        self.assertFalse(move.ok)
        self.assertIn("no access", move.results[0]["detail"])
        DeviceShare.objects.all().delete()
        self.assertEqual(self.temperature(20, 10), [], "share removed: the device is no longer watched")
        self.assertEqual(self.state_of(rule), "HOT")

    def test_button(self):
        rule = self.machine()
        self.temperature(25, 0)
        button = rule.machine_transitions.get(trigger=Trigger.MANUAL)
        url = reverse("rules:machine_press", args=[rule.pk, self.node.pk, button.pk])
        self.client.force_login(self.siti)
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, "Start maintenance")
        response = self.client.post(url, follow=True)
        self.assertContains(response, "Greenhouse Node: NORMAL → MAINTENANCE.")
        execution = rule.executions.get()
        self.assertIn("pressed by siti", execution.results[0]["detail"])
        self.assertEqual(ActivityEvent.objects.get(verb="rule.move").actor, self.siti)
        # Tidak berlaku di MAINTENANCE.
        self.assertContains(self.client.post(url, follow=True), "is in MAINTENANCE, not in NORMAL")
        # Tombol memakai jam sungguhan (bukan at()): 1801 s dari SEKARANG.
        later = timezone.now() + timedelta(seconds=1801)
        self.assertEqual(ENGINES[RuleModel.STATE_MACHINE].tick(later)[0].to_state, "NORMAL")

    def test_button_needs_operator_on_the_device(self):
        DeviceShare.objects.create(device=self.node, user=self.budi, role="VIEWER")
        rule = self.machine(owner=self.budi)
        button = rule.machine_transitions.get(trigger=Trigger.MANUAL)
        self.client.force_login(self.budi)
        response = self.client.post(reverse("rules:machine_press", args=[rule.pk, self.node.pk, button.pk]))
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.siti)   # bukan pemilik aturan: aturannya "tidak ada"
        response = self.client.post(reverse("rules:machine_press", args=[rule.pk, self.node.pk, button.pk]))
        self.assertEqual(response.status_code, 404)


def form_post(**overrides):
    data = {
        "rule-name": "Cold room", "rule-description": "", "rule-is_enabled": "on",
        "machine-device": "", "machine-max_age_seconds": "300",
        "states-TOTAL_FORMS": "3", "states-INITIAL_FORMS": "0",
        "states-MIN_NUM_FORMS": "2", "states-MAX_NUM_FORMS": "20",
        "states-0-name": "NORMAL", "states-0-tone": "OK", "states-0-is_initial": "on",
        "states-1-name": "WARM", "states-1-tone": "WARNING",
        "states-2-name": "ALARM", "states-2-tone": "DANGER",
        "transitions-TOTAL_FORMS": "3", "transitions-INITIAL_FORMS": "0",
        "transitions-0-source": "NORMAL", "transitions-0-target": "WARM", "transitions-0-trigger": "CONDITION",
        "transitions-0-condition": "temperature > 8", "transitions-0-seconds": "120",
        "transitions-1-source": "WARM", "transitions-1-target": "ALARM", "transitions-1-trigger": "TIMEOUT",
        "transitions-1-seconds": "600",
        "transitions-2-source": "*", "transitions-2-target": "NORMAL", "transitions-2-trigger": "MANUAL",
        "transitions-2-label": "",
        "actions-TOTAL_FORMS": "1", "actions-INITIAL_FORMS": "0",
        "actions-0-state_name": "ALARM", "actions-0-run_on": "ENTER", "actions-0-kind": "ALARM",
        "actions-0-severity": "CRITICAL", "actions-0-message": "{device} too warm", "actions-0-ttl_seconds": "300",
    }
    data.update(overrides)
    return data


class FormTests(MachineData):
    def setUp(self):
        self.client.force_login(self.siti)
        self.url = reverse("rules:new_model", args=["state_machine"])

    def test_create_edit_and_detail(self):
        self.assertContains(self.client.get(self.url), "Transitions")
        response = self.client.post(self.url, form_post(**{"machine-device": str(self.node.pk)}))
        rule = Rule.objects.get(name="Cold room")
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        self.assertEqual(rule.model, RuleModel.STATE_MACHINE)
        self.assertEqual([s.name for s in rule.machine_states.all()], ["NORMAL", "WARM", "ALARM"])
        normal_to_warm, warm_to_alarm, reset = rule.machine_transitions.all()
        self.assertEqual(format_condition(normal_to_warm.condition_groups()), "temperature > 8")
        self.assertEqual((normal_to_warm.seconds, warm_to_alarm.seconds), (120, 600))
        self.assertEqual((reset.source, reset.label), (None, "Go to NORMAL"))
        action = rule.actions.get()
        self.assertEqual((action.state.name, action.run_on, action.kind), ("ALARM", "ENTER", "ALARM"))

        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, "machine-diagram")
        self.assertContains(page, "temperature &gt; 8 for 120 s")
        self.assertContains(page, "not started")   # device tertentu yang belum mengirim data

        # Edit: ganti nama keadaan; aksinya ikut keadaan yang sama (lewat nama).
        edit = self.client.get(reverse("rules:edit", args=[rule.pk]))
        self.assertContains(edit, 'value="temperature &gt; 8"')
        states = list(rule.machine_states.all())
        data = form_post(**{
            "states-INITIAL_FORMS": "3", "states-0-id": str(states[0].pk), "states-1-id": str(states[1].pk),
            "states-2-id": str(states[2].pk), "states-2-name": "ALERT", "transitions-1-target": "ALERT",
            "actions-INITIAL_FORMS": "1", "actions-0-id": str(action.pk), "actions-0-state_name": "ALERT",
        })
        self.assertEqual(self.client.post(reverse("rules:edit", args=[rule.pk]), data).status_code, 302)
        action.refresh_from_db()
        self.assertEqual(action.state.name, "ALERT")
        self.assertEqual(action.state.pk, states[2].pk, "state kept its id")

    def test_validation(self):
        cases = [
            ({"transitions-0-target": "NOPE"}, "there is no state called “NOPE”"),
            ({"transitions-0-condition": "temperature >"}, "is not a comparison"),
            ({"transitions-0-target": "NORMAL"}, "only allowed with"),
            ({"transitions-1-seconds": ""}, "Enter the number of seconds"),
            ({"states-1-is_initial": "on"}, "Only one state can be the initial state"),
            ({"states-1-name": "normal"}, "Another state already has this name"),
            ({"actions-0-state_name": "GONE"}, "Action 1: there is no state called “GONE”"),
            ({"transitions-TOTAL_FORMS": "0"}, "Add at least one transition"),
        ]
        for overrides, message in cases:
            response = self.client.post(self.url, form_post(**overrides))
            self.assertContains(response, message, msg_prefix=str(overrides))
        self.assertFalse(Rule.objects.filter(name="Cold room").exists())

    def test_viewer_device_is_not_an_action_target(self):
        DeviceShare.objects.create(device=self.foreign, user=self.siti, role="VIEWER")
        foreign_lamp = Variable.objects.create(device=self.foreign, key="lamp", kind=Variable.Kind.ACTUATOR,
                                               value_type=Variable.ValueType.BOOLEAN)
        response = self.client.post(self.url, form_post(**{
            "actions-0-kind": "SET_ACTUATOR", "actions-0-target_variable": str(foreign_lamp.pk), "actions-0-value": "1",
        }))
        self.assertContains(response, "Select a valid choice")


class JsonTests(MachineData):
    def test_round_trip(self):
        rule = self.machine()
        data = export_rule(rule)
        self.assertEqual(data["model"], "STATE_MACHINE")
        machine = data["machine"]
        self.assertEqual(machine["device"], "Greenhouse Node")
        self.assertEqual(machine["states"][0], {"name": "NORMAL", "description": "", "tone": "OK", "initial": True})
        self.assertIn({"from": "*", "to": "OFFLINE", "trigger": "OFFLINE", "condition": "", "seconds": None,
                       "label": ""}, machine["transitions"])
        self.assertIn({"state": "CRITICAL", "kind": "CLEAR_ALARM", "run_on": "EXIT"}, machine["actions"])

        self.client.force_login(self.siti)
        copy = dict(data, name="Copy")
        response = self.client.post(reverse("rules:import"), {"text": json.dumps(copy)})
        imported = Rule.objects.get(name="Copy")
        self.assertRedirects(response, reverse("rules:detail", args=[imported.pk]))
        again = export_rule(imported)
        for part in ("states", "transitions", "actions", "device", "max_age_seconds"):
            self.assertEqual(again["machine"][part], machine[part], part)

        # Impor ke aturan yang ada mengganti isinya; error dilaporkan tanpa menyimpan apa pun.
        wrong = [{"from": "NORMAL", "to": "NOWHERE", "trigger": "CONDITION", "condition": "x > 1"}]
        broken = dict(data, machine=dict(machine, transitions=wrong))
        response = self.client.post(reverse("rules:import_into", args=[imported.pk]), {"text": json.dumps(broken)})
        self.assertContains(response, "there is no state called “NOWHERE”")
        self.assertEqual(imported.machine_transitions.count(), len(machine["transitions"]))


class PageTests(MachineData):
    def test_list_detail_evaluation_and_device_page(self):
        rule = self.machine()
        self.temperature(31, 0)
        self.temperature(45, 5)
        self.client.force_login(self.siti)
        listing = self.client.get(reverse("rules:list"))
        self.assertContains(listing, "Heat stages")
        self.assertContains(listing, "Running")
        self.assertContains(self.client.get(reverse("rules:list") + "?q=MAINTENANCE"), "Heat stages")
        detail = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(detail, "1 device")
        self.assertContains(detail, "NORMAL → <strong>HOT</strong>", html=False)
        box = self.client.get(reverse("rules:evaluation", args=[rule.pk]))
        self.assertContains(box, "CRITICAL in 60 s")
        device_page = self.client.get(reverse("devices:detail", args=[self.node.pk]))
        self.assertContains(device_page, "State machines")
        self.assertContains(device_page, "HOT")
        picker = self.client.get(reverse("rules:new"))
        self.assertContains(picker, reverse("rules:new_model", args=["state_machine"]))

    def test_rule_of_another_user_is_hidden_on_the_device_page(self):
        DeviceShare.objects.create(device=self.node, user=self.budi, role="VIEWER")
        self.machine(owner=self.budi)
        self.temperature(31, 0)
        self.client.force_login(self.siti)
        self.assertNotContains(self.client.get(reverse("devices:detail", args=[self.node.pk])), "State machines")
