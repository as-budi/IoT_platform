"""Tes rule engine: mesin keadaan ECA, aksi, worker, dan halaman."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from api.services import save_telemetry
from devices.models import Command, Device, Variable

from . import queue
from .engines import ENGINES, implemented_engines
from .engines.eca import ACTIVATE, CLEAR, EcaEngine, step
from .models import (
    Alarm,
    EcaSpec,
    Rule,
    RuleAction,
    RuleExecution,
    RuleModel,
    RuleState,
)

User = get_user_model()
T0 = timezone.now().replace(microsecond=0)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class FakeSpec:
    def __init__(self, duration=0, cooldown=0):
        self.duration_seconds = duration
        self.cooldown_seconds = cooldown


class StepTests(TestCase):
    """Mesin keadaan IDLE -> PENDING -> ACTIVE -> IDLE, tanpa database."""

    def test_immediate_activation_and_clear(self):
        state, spec = RuleState(), FakeSpec()
        self.assertEqual(step(state, spec, True, False, at(0)), ACTIVATE)
        self.assertEqual(state.status, RuleState.Status.ACTIVE)
        self.assertIsNone(step(state, spec, True, False, at(1)), "no repeat while active")
        self.assertEqual(step(state, spec, False, True, at(2)), CLEAR)
        self.assertEqual(state.status, RuleState.Status.IDLE)

    def test_duration(self):
        state, spec = RuleState(), FakeSpec(duration=60)
        self.assertIsNone(step(state, spec, True, False, at(0)))
        self.assertEqual(state.status, RuleState.Status.PENDING)
        self.assertIsNone(step(state, spec, True, False, at(59)))
        self.assertEqual(step(state, spec, True, False, at(60)), ACTIVATE)

    def test_condition_dropping_while_pending_resets(self):
        state, spec = RuleState(), FakeSpec(duration=60)
        step(state, spec, True, False, at(0))
        step(state, spec, False, True, at(30))
        self.assertEqual(state.status, RuleState.Status.IDLE)
        step(state, spec, True, False, at(40))
        self.assertIsNone(step(state, spec, True, False, at(90)), "timer restarted at 40 s")
        self.assertEqual(step(state, spec, True, False, at(100)), ACTIVATE)

    def test_hysteresis_band_keeps_state(self):
        state, spec = RuleState(), FakeSpec()
        step(state, spec, True, False, at(0))
        # Nilai di antara ambang aktif dan ambang clear: tidak aktif, tidak clear.
        self.assertIsNone(step(state, spec, False, False, at(1)))
        self.assertEqual(state.status, RuleState.Status.ACTIVE)

    def test_cooldown(self):
        state, spec = RuleState(), FakeSpec(cooldown=300)
        step(state, spec, True, False, at(0))
        step(state, spec, False, True, at(10))
        self.assertIsNone(step(state, spec, True, False, at(20)))
        self.assertEqual(state.status, RuleState.Status.PENDING)
        self.assertEqual(step(state, spec, True, False, at(300)), ACTIVATE)


class RuleTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", email="siti@example.com", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.node = Device.objects.create(owner=cls.siti, name="Greenhouse Node")
        cls.soil = Variable.objects.create(device=cls.node, key="soil_moisture", label="Soil moisture", unit="%")
        cls.pump = Variable.objects.create(
            device=cls.node, key="pump", label="Water pump",
            kind=Variable.Kind.ACTUATOR, value_type=Variable.ValueType.BOOLEAN,
        )
        cls.foreign = Device.objects.create(owner=cls.budi, name="Budi's node")

    def make_rule(self, duration=0, clear=(">", 30), cooldown=0, actions=None, owner=None):
        rule = Rule.objects.create(owner=owner or self.siti, name="Irrigation")
        EcaSpec.objects.create(
            rule=rule, variable=self.soil, operator="<", threshold=20,
            duration_seconds=duration, clear_operator=clear[0] if clear else "",
            clear_threshold=clear[1] if clear else None, cooldown_seconds=cooldown,
        )
        for fields in actions if actions is not None else [
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 0, "run_on": RuleAction.RunOn.CLEAR},
        ]:
            RuleAction.objects.create(rule=rule, **fields)
        return rule

    def send(self, value, now, device=None, variable=None):
        variable = variable or self.soil
        message = queue.telemetry_message(device or self.node, [(variable, value, now)])
        return queue.process(message, now=now)


class EcaEngineTests(RuleTestData):
    def test_irrigation_cycle(self):
        rule = self.make_rule()
        self.assertEqual(self.send(25, at(0)), [])
        [execution] = self.send(18, at(10))
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)
        self.assertTrue(execution.ok)
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_value, 1)
        self.assertIsNone(self.pump.desired_by, "set by the rule, not a person")

        # Masih kering, lalu di pita histeresis: tidak ada aksi baru.
        self.assertEqual(self.send(15, at(20)), [])
        self.assertEqual(self.send(25, at(30)), [])
        [cleared] = self.send(31, at(40))
        self.assertEqual(cleared.transition, RuleExecution.Transition.CLEAR)
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_value, 0)
        self.assertEqual(rule.executions.count(), 2)

    def test_duration_is_completed_by_tick(self):
        self.make_rule(duration=60)
        self.assertEqual(self.send(18, at(0)), [])
        engine = ENGINES[RuleModel.ECA]
        self.assertEqual(engine.tick(at(30)), [])
        [execution] = engine.tick(at(61))
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)

    def test_set_actuator_is_idempotent(self):
        self.pump.set_desired(1, self.siti)
        rule = self.make_rule()
        [execution] = self.send(10, at(0))
        self.assertIn("already requested", execution.results[0]["detail"])
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_version, 1)

    def test_alarm_raised_and_cleared_automatically(self):
        rule = self.make_rule(actions=[
            {"kind": RuleAction.Kind.ALARM, "severity": Alarm.Severity.CRITICAL,
             "message": "{device}: soil {value} %"},
        ])
        self.send(12.5, at(0))
        alarm = Alarm.objects.get()
        self.assertEqual(alarm.message, "Greenhouse Node: soil 12.5 %")
        self.assertEqual((alarm.device, alarm.rule, alarm.severity), (self.node, rule, Alarm.Severity.CRITICAL))
        self.send(35, at(10))
        alarm.refresh_from_db()
        self.assertFalse(alarm.is_active)
        self.assertIsNone(alarm.cleared_by)

    def test_command_and_email(self):
        self.make_rule(actions=[
            {"kind": RuleAction.Kind.SEND_COMMAND, "target_device": self.node, "method": "blink", "params": {"times": 2}},
            {"kind": RuleAction.Kind.EMAIL, "message": "{device} is dry ({value} %)"},
        ])
        [execution] = self.send(10, at(0))
        self.assertTrue(execution.ok, execution.results)
        command = Command.objects.get()
        self.assertEqual((command.method, command.params), ("blink", {"times": 2}))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["siti@example.com"])
        self.assertIn("Greenhouse Node is dry (10 %)", mail.outbox[0].body)

    def test_failed_action_does_not_stop_others(self):
        # Aksi ke device milik orang lain gagal; aksi berikutnya tetap jalan.
        self.make_rule(actions=[
            {"kind": RuleAction.Kind.SEND_COMMAND, "target_device": self.foreign, "method": "reboot"},
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
        ])
        [execution] = self.send(10, at(0))
        self.assertFalse(execution.ok)
        self.assertEqual([r["ok"] for r in execution.results], [False, True])
        self.assertIn("no access", execution.results[0]["detail"])
        self.assertFalse(Command.objects.exists())

    def test_disabled_rule_is_ignored(self):
        rule = self.make_rule()
        rule.is_enabled = False
        rule.save()
        self.assertEqual(self.send(5, at(0)), [])

    def test_inactivity_rule(self):
        rule = Rule.objects.create(owner=self.siti, name="Node silent")
        EcaSpec.objects.create(rule=rule, event=EcaSpec.Event.INACTIVITY, device=self.node, silence_seconds=300)
        RuleAction.objects.create(rule=rule, kind=RuleAction.Kind.ALARM, severity=Alarm.Severity.MAJOR,
                                  message="{device} silent for {value} s")
        Device.objects.filter(pk=self.node.pk).update(last_seen_at=at(0))
        engine = ENGINES[RuleModel.ECA]
        self.assertEqual(engine.tick(at(200)), [])
        [execution] = engine.tick(at(301))
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)
        self.assertEqual(Alarm.objects.active().get().message, "Greenhouse Node silent for 301 s")
        # Data datang lagi -> pulih, alarm di-clear.
        Device.objects.filter(pk=self.node.pk).update(last_seen_at=at(400))
        [cleared] = self.send(22, at(400))
        self.assertEqual(cleared.transition, RuleExecution.Transition.CLEAR)
        self.assertFalse(Alarm.objects.active().exists())

    def test_save_telemetry_runs_rules_inline_after_commit(self):
        self.make_rule()
        with self.captureOnCommitCallbacks(execute=True):
            save_telemetry(self.node, [("soil_moisture", 11.0, timezone.now())], protocol="MQTT")
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_value, 1)

    @override_settings(RULE_ENGINE_MODE="off")
    def test_engine_off(self):
        self.make_rule()
        with self.captureOnCommitCallbacks(execute=True):
            save_telemetry(self.node, [("soil_moisture", 11.0, timezone.now())])
        self.assertFalse(RuleExecution.objects.exists())


class RegistryTests(TestCase):
    def test_all_six_models_are_registered_only_eca_implemented(self):
        self.assertEqual(list(ENGINES), list(RuleModel.values))
        self.assertEqual([e.key for e in implemented_engines()], [RuleModel.ECA])
        self.assertIsInstance(ENGINES[RuleModel.ECA], EcaEngine)
        for engine in ENGINES.values():
            self.assertTrue(engine.summary and engine.example, engine.key)


class WorkerTests(RuleTestData):
    def test_once_in_inline_mode_runs_timers(self):
        self.make_rule(duration=1)
        self.send(10, timezone.now() - timedelta(seconds=5))  # PENDING sejak 5 detik lalu
        out = StringIO()
        call_command("rule_engine", "--once", stdout=out)
        self.assertIn("Irrigation", out.getvalue())
        self.assertIn("activated", out.getvalue())
        self.assertIn("planned (not implemented yet)", out.getvalue())


def action_data(prefix="actions", forms=()):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(forms)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "1",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, fields in enumerate(forms):
        defaults = {"run_on": "ACTIVATE", "ttl_seconds": "300", "severity": "MAJOR"}
        for key, value in {**defaults, **fields}.items():
            data[f"{prefix}-{i}-{key}"] = value
    return data


class RuleViewTests(RuleTestData):
    def setUp(self):
        self.client.force_login(self.siti)

    def form_data(self, **eca):
        data = {
            "rule-name": "Irrigation",
            "rule-is_enabled": "on",
            "eca-event": "TELEMETRY",
            "eca-variable": str(self.soil.pk),
            "eca-operator": "<",
            "eca-threshold": "20",
            "eca-duration_seconds": "60",
            "eca-clear_operator": ">",
            "eca-clear_threshold": "30",
            "eca-cooldown_seconds": "0",
        }
        data.update({f"eca-{k}": v for k, v in eca.items()})
        data.update(action_data(forms=[
            {"kind": "SET_ACTUATOR", "target_variable": str(self.pump.pk), "value": "1"},
            {"kind": "SET_ACTUATOR", "target_variable": str(self.pump.pk), "value": "0", "run_on": "CLEAR"},
        ]))
        return data

    def test_model_picker_lists_six_models(self):
        response = self.client.get(reverse("rules:new"))
        for engine in ENGINES.values():
            self.assertContains(response, engine.label)
        self.assertContains(response, "Coming in a later stage", count=5)
        self.assertContains(response, reverse("rules:new_model", args=["eca"]))

    def test_planned_model_redirects_back(self):
        response = self.client.get(reverse("rules:new_model", args=["cep"]))
        self.assertRedirects(response, reverse("rules:new"))

    def test_create_eca_rule(self):
        response = self.client.get(reverse("rules:new_model", args=["eca"]))
        self.assertContains(response, "When — event")
        response = self.client.post(reverse("rules:new_model", args=["eca"]), self.form_data())
        rule = Rule.objects.get()
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        self.assertEqual((rule.owner, rule.model), (self.siti, RuleModel.ECA))
        self.assertEqual((rule.eca.threshold, rule.eca.duration_seconds), (20, 60))
        self.assertEqual(rule.actions.count(), 2)
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, "Soil moisture")
        self.assertContains(page, "value &lt; 20 for 60 s")

    def test_overlapping_hysteresis_is_rejected(self):
        response = self.client.post(
            reverse("rules:new_model", args=["eca"]), self.form_data(clear_threshold="15")
        )
        self.assertContains(response, "otherwise both conditions can hold at once")
        self.assertFalse(Rule.objects.exists())

    def test_cannot_pick_other_users_devices(self):
        other_var = Variable.objects.create(device=self.foreign, key="valve", kind=Variable.Kind.ACTUATOR)
        data = self.form_data()
        data["actions-0-target_variable"] = str(other_var.pk)
        response = self.client.post(reverse("rules:new_model", args=["eca"]), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Rule.objects.exists())

    def test_edit_and_other_users_cannot_see(self):
        rule = self.make_rule()
        response = self.client.post(reverse("rules:edit", args=[rule.pk]), {
            **self.form_data(threshold="15", clear_threshold="25"),
            "actions-INITIAL_FORMS": "0",
        })
        self.assertEqual(response.status_code, 302)
        rule.refresh_from_db()
        self.assertEqual(rule.eca.threshold, 15)
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(reverse("rules:detail", args=[rule.pk])).status_code, 404)

    def test_toggle_resets_state(self):
        rule = self.make_rule()
        self.send(10, at(0))
        self.client.post(reverse("rules:toggle", args=[rule.pk]))
        rule.refresh_from_db()
        self.assertFalse(rule.is_enabled)
        self.assertEqual(rule.state.status, RuleState.Status.IDLE)

    def test_alarm_pages_and_badge(self):
        alarm = Alarm.objects.create(device=self.node, message="Too dry", severity=Alarm.Severity.MAJOR)
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, "active alarm")
        response = self.client.get(reverse("devices:detail", args=[self.node.pk]))
        self.assertContains(response, "Too dry")
        self.client.post(reverse("rules:alarm_ack", args=[alarm.pk]))
        alarm.refresh_from_db()
        self.assertEqual(alarm.acknowledged_by, self.siti)
        # follow=True: pesan sukses ("... cleared.") ikut habis ditampilkan di sini.
        self.client.post(reverse("rules:alarm_clear", args=[alarm.pk]), follow=True)
        alarm.refresh_from_db()
        self.assertFalse(alarm.is_active)
        self.assertNotContains(self.client.get(reverse("rules:alarms")), "Too dry")
        self.assertContains(self.client.get(reverse("rules:alarms") + "?show=all"), "Too dry")
        # Pengguna lain tidak bisa menyentuh alarm ini.
        self.client.force_login(self.budi)
        self.assertEqual(self.client.post(reverse("rules:alarm_ack", args=[alarm.pk])).status_code, 404)
