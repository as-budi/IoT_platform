"""Tes rule engine: mesin keadaan ECA, aksi, worker, dan halaman."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from api.services import save_telemetry
from devices.models import Command, Device, Telemetry, Variable

from . import queue
from .engines import ENGINES, implemented_engines
from .engines.eca import ACTIVATE, CLEAR, EcaEngine, step
from .models import (
    Alarm,
    EcaCondition,
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

    def make_rule(self, duration=0, clear=(">", 30), cooldown=0, actions=None, owner=None,
                  when=None, clear_groups=None, max_age=300):
        """Aturan contoh. Bawaan: soil_moisture < 20, clear bila soil_moisture > 30.

        `when` / `clear_groups`: [[(variabel, op, ambang), ...], ...] = OR dari grup AND.
        """
        rule = Rule.objects.create(owner=owner or self.siti, name="Irrigation")
        spec = EcaSpec.objects.create(
            rule=rule, duration_seconds=duration, cooldown_seconds=cooldown, max_age_seconds=max_age,
        )
        if when is None:
            when = [[(self.soil, "<", 20)]]
        if clear_groups is None:
            clear_groups = [[(self.soil, clear[0], clear[1])]] if clear else []
        for role, groups in ((EcaCondition.Role.WHEN, when), (EcaCondition.Role.CLEAR, clear_groups)):
            for number, group in enumerate(groups, start=1):
                for order, (variable, op, threshold) in enumerate(group):
                    EcaCondition.objects.create(
                        spec=spec, role=role, group=number, order=order,
                        variable=variable, operator=op, threshold=threshold,
                    )
        for fields in actions if actions is not None else [
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 0, "run_on": RuleAction.RunOn.CLEAR},
        ]:
            RuleAction.objects.create(rule=rule, **fields)
        return rule

    def send(self, value, now, device=None, variable=None):
        """Seperti save_telemetry(): simpan barisnya dulu, lalu serahkan pesannya ke rule engine."""
        variable = variable or self.soil
        Telemetry.objects.create(variable=variable, value=value, ts=now)
        message = queue.telemetry_message(device or variable.device, [(variable, value, now)])
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


def condition_data(prefix, rows, ids=()):
    """Data POST formset kondisi. rows = [(grup, variabel, op, ambang), ...]."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(len(ids)),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "20",
    }
    for i, (group, variable, op, threshold) in enumerate(rows):
        data.update({
            f"{prefix}-{i}-group": str(group),
            f"{prefix}-{i}-variable": str(variable.pk),
            f"{prefix}-{i}-operator": op,
            f"{prefix}-{i}-threshold": str(threshold),
        })
        if i < len(ids):
            data[f"{prefix}-{i}-id"] = str(ids[i])
    return data


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

    def form_data(self, threshold="20", clear_threshold="30", **eca):
        data = {
            "rule-name": "Irrigation",
            "rule-is_enabled": "on",
            "eca-event": "TELEMETRY",
            "eca-duration_seconds": "60",
            "eca-cooldown_seconds": "0",
            "eca-max_age_seconds": "300",
        }
        data.update({f"eca-{k}": v for k, v in eca.items()})
        data.update(condition_data("when", [(1, self.soil, "<", threshold)]))
        data.update(condition_data("clear", [(1, self.soil, ">", clear_threshold)] if clear_threshold else []))
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
        [when] = rule.eca.when_groups
        self.assertEqual([(c.variable, c.operator, c.threshold) for c in when], [(self.soil, "<", 20)])
        self.assertEqual(rule.eca.duration_seconds, 60)
        self.assertEqual(rule.actions.count(), 2)
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        soil_url = reverse("devices:variable_detail", args=[self.node.pk, self.soil.pk])
        self.assertContains(page, f'href="{soil_url}"')
        self.assertContains(page, "&lt; 20")
        self.assertContains(page, "for 60 s")

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
        when_ids = list(rule.eca.conditions.filter(role="WHEN").values_list("pk", flat=True))
        clear_ids = list(rule.eca.conditions.filter(role="CLEAR").values_list("pk", flat=True))
        data = self.form_data()
        data.update(condition_data("when", [(1, self.soil, "<", 15)], ids=when_ids))
        data.update(condition_data("clear", [(1, self.soil, ">", 25)], ids=clear_ids))
        response = self.client.post(reverse("rules:edit", args=[rule.pk]), data)
        self.assertEqual(response.status_code, 302)
        rule = Rule.objects.get(pk=rule.pk)
        self.assertEqual([c.threshold for c in rule.eca.condition_list()], [25, 15])  # CLEAR, WHEN
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


class LiveUpdateTests(RuleTestData):
    """Halaman aturan/alarm diperbarui tanpa refresh: kabar WebSocket + versi data."""

    def setUp(self):
        self.client.force_login(self.siti)

    def test_rule_events_are_sent_to_the_originator_device(self):
        self.make_rule(duration=60)
        with mock.patch("realtime.events.emit") as emit:
            self.send(10, at(0))                      # IDLE -> PENDING
            ENGINES[RuleModel.ECA].tick(at(61))      # PENDING -> ACTIVE (+ log)
        rule_events = [c.args for c in emit.call_args_list if c.args[1]["type"] == "rule"]
        self.assertEqual([e[0] for e in rule_events], [self.node.pk, self.node.pk])
        self.assertEqual([e[1]["status"] for e in rule_events], ["PENDING", "ACTIVE"])
        self.assertEqual(rule_events[1][1]["transition"], "ACTIVATE")

    def test_no_event_when_nothing_changes(self):
        self.make_rule()
        self.send(25, at(0))
        with mock.patch("realtime.events.emit") as emit:
            self.send(26, at(1))
        self.assertFalse([c for c in emit.call_args_list if c.args[1]["type"] == "rule"])

    def test_alarm_event_names_its_rule(self):
        rule = self.make_rule(actions=[{"kind": RuleAction.Kind.ALARM, "severity": "MAJOR"}])
        with mock.patch("realtime.events.emit") as emit:
            self.send(10, at(0))
        alarm_events = [c.args[1] for c in emit.call_args_list if c.args[1]["type"] == "alarm"]
        self.assertEqual(alarm_events[0]["rule"], rule.pk)

    def test_pages_subscribe_and_versions_change(self):
        rule = self.make_rule(actions=[{"kind": RuleAction.Kind.ALARM, "severity": "MAJOR"}])
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, f'data-ws-path="/ws/devices/{self.node.pk}/"')
        self.assertContains(page, reverse("rules:detail_updates", args=[rule.pk]))
        self.assertContains(self.client.get(reverse("rules:list")), 'data-ws-path="/ws/dashboard/"')
        self.assertContains(self.client.get(reverse("rules:alarms")), reverse("rules:alarms_updates"))

        urls = [reverse("rules:detail_updates", args=[rule.pk]), reverse("rules:updates"),
                reverse("rules:alarms_updates")]
        before = [self.client.get(url).json()["version"] for url in urls]
        self.send(10, at(0))
        after = [self.client.get(url).json()["version"] for url in urls]
        for url, old, new in zip(urls, before, after):
            self.assertNotEqual(old, new, url)

    def test_updates_need_login_and_ownership(self):
        rule = self.make_rule()
        self.client.logout()
        self.assertEqual(self.client.get(reverse("rules:updates")).status_code, 401)
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(reverse("rules:detail_updates", args=[rule.pk])).status_code, 404)


class KleeneLogicTests(SimpleTestCase):
    """Tabel kebenaran tiga nilai di rules/conditions.py."""

    def test_and_or(self):
        from .conditions import all_of, any_of

        T, F, U = True, False, None
        self.assertIs(all_of([T, T]), T)
        self.assertIs(all_of([T, U]), U)
        self.assertIs(all_of([F, U]), F)
        self.assertIs(any_of([F, F]), F)
        self.assertIs(any_of([F, U]), U)
        self.assertIs(any_of([T, U]), T)
        self.assertIs(any_of([]), F)


class MultiVariableData(RuleTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.temp = Variable.objects.create(device=cls.node, key="temperature", label="Temperature", unit="°C")
        cls.weather = Device.objects.create(owner=cls.siti, name="Weather station")
        cls.rain = Variable.objects.create(device=cls.weather, key="rain", label="Rain", unit="mm")


class MultiConditionTests(MultiVariableData):
    """(soil < 20 AND temperature > 30) OR soil < 10."""

    def irrigation(self, **kwargs):
        return self.make_rule(
            when=[[(self.soil, "<", 20), (self.temp, ">", 30)], [(self.soil, "<", 10)]],
            clear=None, **kwargs,
        )

    def test_and_uses_latest_stored_value_of_the_other_variable(self):
        self.irrigation()
        self.send(25, at(0), variable=self.temp)           # suhu 25: belum panas
        self.assertEqual(self.send(15, at(10)), [])         # 15 < 20 tetapi suhu tidak > 30
        [execution] = self.send(32, at(20), variable=self.temp)  # suhu naik: kini kedua kondisi benar
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)
        self.assertEqual(execution.snapshot, {
            "Greenhouse Node · Soil moisture": 15.0, "Greenhouse Node · Temperature": 32.0,
        })
        snapshot = execution.rule.state.snapshot
        self.assertEqual(snapshot["when"]["groups"][0]["conditions"][0]["source"], "database")
        self.assertEqual(snapshot["when"]["groups"][0]["conditions"][1]["source"], "message")
        self.assertIs(snapshot["when"]["groups"][1]["result"], False)

    def test_second_group_alone_is_enough(self):
        self.irrigation()
        self.send(25, at(0), variable=self.temp)
        [execution] = self.send(8, at(10))                   # grup 2: soil < 10
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)

    def test_clear_when_expression_becomes_false(self):
        self.irrigation()
        self.send(35, at(0), variable=self.temp)
        self.send(15, at(10))
        [cleared] = self.send(25, at(20), variable=self.temp)
        self.assertEqual(cleared.transition, RuleExecution.Transition.CLEAR)

    def test_stale_value_is_unknown_and_changes_nothing(self):
        self.irrigation(max_age=60)
        self.send(35, at(0), variable=self.temp)
        self.assertEqual(self.send(15, at(120)), [])         # suhu sudah 120 s: tidak diketahui
        state = RuleState.objects.get()
        self.assertEqual(state.status, RuleState.Status.IDLE)
        row = state.snapshot["when"]["groups"][0]["conditions"][1]
        self.assertIsNone(row["result"])
        self.assertIn("stale", row["reason"])

    def test_unknown_does_not_clear_an_active_rule(self):
        self.irrigation(max_age=60)
        self.send(35, at(0), variable=self.temp)
        self.send(15, at(10))                                # ACTIVE
        self.assertEqual(self.send(15, at(200)), [])         # suhu basi: tidak diketahui, tetap ACTIVE
        self.assertEqual(RuleState.objects.get().status, RuleState.Status.ACTIVE)

    def test_no_data_at_all_is_unknown(self):
        self.irrigation()
        self.assertEqual(self.send(15, at(0)), [])           # belum pernah ada data suhu
        self.assertEqual(RuleState.objects.get().snapshot["when"]["groups"][0]["conditions"][1]["reason"], "no data")

    def test_conditions_across_devices(self):
        self.make_rule(when=[[(self.soil, "<", 20), (self.rain, "<", 1)]], clear=None)
        self.send(0, at(0), variable=self.rain)              # data dari device lain
        [execution] = self.send(15, at(10))
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)
        # Data dari Weather station juga memicu penilaian ulang aturan ini.
        [cleared] = self.send(5, at(20), variable=self.rain)
        self.assertEqual(cleared.transition, RuleExecution.Transition.CLEAR)

    def test_activation_blocked_while_clear_expression_is_true(self):
        # Aktif: soil < 20; clear: temperature < 25. Keduanya benar -> jangan aktif.
        self.make_rule(clear_groups=[[(self.temp, "<", 25)]])
        self.send(20, at(0), variable=self.temp)
        self.assertEqual(self.send(15, at(10)), [])
        [execution] = self.send(26, at(20), variable=self.temp)
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)

    def test_describe_and_values_placeholder(self):
        rule = self.irrigation(actions=[{"kind": RuleAction.Kind.ALARM, "severity": "MAJOR", "message": "{values}"}])
        self.assertEqual(
            ENGINES[RuleModel.ECA].describe(rule)["if"],
            "(Soil moisture < 20 AND Temperature > 30) OR Soil moisture < 10",
        )
        self.send(31, at(0), variable=self.temp)
        self.send(12, at(5))
        self.assertEqual(Alarm.objects.get().message,
                         "Greenhouse Node · Soil moisture = 12, Greenhouse Node · Temperature = 31")

    def test_rule_eval_event_without_status_change(self):
        rule = self.irrigation()
        with mock.patch("realtime.events.emit") as emit:
            self.send(25, at(0), variable=self.temp)
        self.assertIn({"type": "rule_eval", "rule": rule.pk}, [c.args[1] for c in emit.call_args_list])


class ConditionFormTests(RuleTestData):
    def setUp(self):
        self.client.force_login(self.siti)
        self.temp = Variable.objects.create(device=self.node, key="temperature", label="Temperature")

    def post(self, when, clear=(), extra=None):
        data = {
            "rule-name": "Combo", "rule-is_enabled": "on", "eca-event": "TELEMETRY",
            "eca-duration_seconds": "0", "eca-cooldown_seconds": "0", "eca-max_age_seconds": "300",
            **condition_data("when", when), **condition_data("clear", list(clear)),
            **action_data(forms=[{"kind": "SET_ACTUATOR", "target_variable": str(self.pump.pk), "value": "1"}]),
            **(extra or {}),
        }
        return self.client.post(reverse("rules:new_model", args=["eca"]), data)

    def test_groups_are_saved_and_renumbered(self):
        response = self.post([(3, self.soil, "<", 20), (3, self.temp, ">", 30), (7, self.soil, "<", 10)])
        rule = Rule.objects.get()
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        groups = [[(c.variable.key, c.operator, c.threshold) for c in g] for g in rule.eca.when_groups]
        self.assertEqual(groups, [[("soil_moisture", "<", 20), ("temperature", ">", 30)], [("soil_moisture", "<", 10)]])
        self.assertEqual([c.group for c in rule.eca.when_groups[1]], [2])

    def test_at_least_one_condition(self):
        response = self.post([])
        self.assertContains(response, "Add at least one condition.")
        self.assertFalse(Rule.objects.exists())

    def test_empty_new_row_is_ignored(self):
        data = condition_data("when", [(1, self.soil, "<", 20)])
        data.update({"when-TOTAL_FORMS": "2", "when-1-group": "2", "when-1-operator": ">"})
        response = self.post([], extra=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EcaCondition.objects.count(), 1)

    def test_evaluation_box_endpoint(self):
        rule = self.make_rule()
        self.send(18, at(0))
        response = self.client.get(reverse("rules:evaluation", args=[rule.pk]))
        self.assertContains(response, "Last evaluation")
        self.assertContains(response, "✓ true")
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(reverse("rules:evaluation", args=[rule.pk])).status_code, 404)


class DocumentedTimelineTests(MultiVariableData):
    """Linimasa di docs/eca-mekanisme.md bagian 8, dijalankan sungguhan."""

    def test_timeline(self):
        rule = self.make_rule(
            when=[[(self.soil, "<", 20), (self.temp, ">", 30)], [(self.soil, "<", 10)]],
            duration=60,
            actions=[
                {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
                {"kind": RuleAction.Kind.ALARM, "severity": "MAJOR", "message": "{device}: {values}"},
                {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 0,
                 "run_on": RuleAction.RunOn.CLEAR},
            ],
        )
        engine = ENGINES[RuleModel.ECA]

        def status():
            return RuleState.objects.get(rule=rule).status

        def when_result():
            return RuleState.objects.get(rule=rule).snapshot["when"]["result"]

        self.send(28, at(0), variable=self.temp)          # 10:00:00
        self.assertEqual((status(), when_result()), ("IDLE", None))
        self.send(25, at(5))                               # 10:00:05
        self.assertEqual((status(), when_result()), ("IDLE", False))
        self.send(32, at(40), variable=self.temp)         # 10:00:40
        self.assertEqual(status(), "IDLE")
        self.send(15, at(60))                              # 10:01:00
        self.assertEqual((status(), when_result()), ("PENDING", True))
        self.assertEqual(engine.tick(at(90)), [])          # 10:01:30
        [activated] = engine.tick(at(120))                 # 10:02:00
        self.assertEqual(activated.transition, "ACTIVATE")
        self.assertEqual(Alarm.objects.get().message,
                         "Greenhouse Node: Greenhouse Node · Soil moisture = 15, Greenhouse Node · Temperature = 32")
        self.assertEqual(self.send(27, at(300), variable=self.temp), [])   # 10:05:00, tetap ACTIVE
        self.assertEqual(self.send(22, at(450)), [])                         # 10:07:30, pita histeresis
        self.assertEqual(status(), "ACTIVE")
        [cleared] = self.send(31, at(900))                                   # 10:15:00
        self.assertEqual(cleared.transition, "CLEAR")
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_value, 0)
        self.assertFalse(Alarm.objects.active().exists())

    def test_exercise_2_short_max_age_never_activates(self):
        self.make_rule(when=[[(self.soil, "<", 20), (self.temp, ">", 30)], [(self.soil, "<", 10)]],
                       duration=60, max_age=30)
        self.send(32, at(40), variable=self.temp)
        self.send(15, at(60))                     # T berumur 20 s: PENDING
        self.assertEqual(RuleState.objects.get().status, "PENDING")
        ENGINES[RuleModel.ECA].tick(at(71))       # T berumur 31 s: tidak diketahui -> IDLE
        self.assertEqual(RuleState.objects.get().status, "IDLE")


class SampleRulesTests(TestCase):
    """Contoh di docs/contoh-data-dan-aturan.md: seed_demo + seed_rules, lalu skenarionya."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_rules", stdout=StringIO())

    def send(self, device, seconds, **values):
        """Seperti send_telemetry, tetapi dengan waktu yang ditentukan tes."""
        device = Device.objects.get(name=device)
        now = T0 + timedelta(days=1, seconds=seconds)   # sesudah semua data seed_demo
        rows = []
        for key, value in values.items():
            variable = device.variables.get(key=key)
            Telemetry.objects.create(variable=variable, value=value, ts=now)
            rows.append((variable, value, now))
        return queue.process(queue.telemetry_message(device, rows), now=now)

    def tick(self, seconds):
        return ENGINES[RuleModel.ECA].tick(T0 + timedelta(days=1, seconds=seconds))

    def status(self, name):
        return Rule.objects.get(name=name).state.status

    def desired(self, device, key):
        return Variable.objects.get(device__name=device, key=key).desired_value

    def test_seed_is_idempotent_and_complete(self):
        out = StringIO()
        call_command("seed_rules", stdout=out)
        self.assertIn("0 sample rule(s) created", out.getvalue())
        self.assertEqual(Rule.objects.count(), 6)
        self.assertFalse(Rule.objects.get(name="Weather station silent").is_enabled)
        self.assertEqual(len(Rule.objects.get(name="Smart irrigation").eca.when_groups), 2)
        for username in ("andi", "rina"):
            self.assertTrue(User.objects.get(username=username).check_password("demo12345"))

    def test_smart_irrigation(self):
        self.send("Greenhouse Weather Station", 0, rain=0)
        self.send("Greenhouse Node", 10, soil_moisture=25, temperature=34)
        self.assertEqual(self.status("Smart irrigation"), "PENDING")
        [activated] = self.tick(70)
        self.assertEqual(activated.rule.name, "Smart irrigation")
        self.assertEqual(self.desired("Greenhouse Node", "pump"), 1)
        self.send("Greenhouse Node", 100, soil_moisture=28)          # pita histeresis
        self.assertEqual(self.status("Smart irrigation"), "ACTIVE")
        [cleared] = self.send("Greenhouse Weather Station", 130, rain=3)   # clear grup 2
        self.assertEqual(cleared.transition, "CLEAR")
        self.assertEqual(self.desired("Greenhouse Node", "pump"), 0)

    def test_smart_irrigation_cooldown(self):
        self.send("Greenhouse Weather Station", 0, rain=0)
        self.send("Greenhouse Node", 10, soil_moisture=25, temperature=34)
        self.tick(70)                                                   # aktif pada t=70
        self.send("Greenhouse Weather Station", 100, rain=3)           # pulih
        self.send("Greenhouse Weather Station", 130, rain=0)
        self.send("Greenhouse Node", 140, soil_moisture=25, temperature=34)
        self.tick(250)
        self.assertEqual(self.status("Smart irrigation"), "PENDING")   # cooldown s.d. t=370
        [again] = self.tick(371)
        self.assertEqual(again.transition, "ACTIVATE")

    def test_weather_station_silent_when_enabled(self):
        rule = Rule.objects.get(name="Weather station silent")
        rule.is_enabled = True
        rule.save()
        [alarm_raised] = self.tick(0)          # seed_demo tidak mengisi last_seen_at: langsung diam
        self.assertEqual(alarm_raised.rule, rule)
        self.assertIn("has sent no data", Alarm.objects.get(rule=rule).message)

    def test_smart_irrigation_stale_rain_is_unknown(self):
        # Curah hujan dari seed_demo sudah berumur >15 menit: tidak diketahui.
        self.send("Greenhouse Node", 0, soil_moisture=15, temperature=34)
        self.assertEqual(self.status("Smart irrigation"), "IDLE")
        self.assertIsNone(Rule.objects.get(name="Smart irrigation").state.snapshot["when"]["result"])

    def test_poultry_heat_stress(self):
        self.send("Poultry House Node", 0, temperature=34, humidity=75, ammonia=12)
        [activated] = self.tick(31)
        self.assertEqual(self.desired("Poultry House Node", "exhaust_fan"), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["andi@example.com"])
        [cleared] = self.send("Poultry House Node", 60, temperature=28)  # amonia terakhir 12 < 15
        self.assertEqual(cleared.transition, "CLEAR")

    def test_poultry_ammonia_alone(self):
        self.send("Poultry House Node", 0, temperature=27, humidity=60, ammonia=30)
        self.tick(31)
        self.assertEqual(self.status("Poultry heat stress"), "ACTIVE")

    def test_pond_low_oxygen(self):
        [activated] = self.send("Fish Pond Node", 0, dissolved_oxygen=4.6, water_temp=31)
        self.assertEqual(self.desired("Fish Pond Node", "aerator"), 1)
        self.assertEqual(Alarm.objects.get().severity, "CRITICAL")
        [cleared] = self.send("Fish Pond Node", 10, dissolved_oxygen=6.5)
        self.assertEqual(cleared.transition, "CLEAR")
        self.assertFalse(Alarm.objects.active().exists())

    def test_cold_chain_breach(self):
        self.send("Cold Storage Unit", 0, temperature=7, humidity=90)
        self.assertEqual(self.tick(60), [])
        [activated] = self.tick(121)
        self.assertEqual(self.desired("Cold Storage Unit", "compressor_boost"), 1)
        command = Command.objects.get(device__name="Cold Storage Unit", method="buzzer")
        self.assertEqual(command.params, {"seconds": 10})
        [cleared] = self.send("Cold Storage Unit", 200, temperature=4, humidity=75)
        self.assertEqual(cleared.transition, "CLEAR")

    def test_server_room_cooling_across_devices(self):
        self.send("Server Room Node", 0, temperature=31, humidity=50)
        self.tick(61)
        self.assertEqual(self.desired("Lab Relay Board", "fan_speed"), 100)
        self.send("Server Room Node", 90, temperature=24)
        self.assertEqual(self.desired("Lab Relay Board", "fan_speed"), 25)

    def test_send_telemetry_command(self):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("send_telemetry", "Fish Pond Node", "dissolved_oxygen=3.5", stdout=out)
        self.assertIn("Sent to Fish Pond Node (andi)", out.getvalue())
        self.assertEqual(self.status("Pond low oxygen"), "ACTIVE")
        with self.assertRaisesMessage(CommandError, "No device"):
            call_command("send_telemetry", "Greenhouse Node", "pump=on", "--owner", "nobody", stdout=out)
        Device.objects.create(owner=User.objects.get(username="budi"), name="Fish Pond Node")
        with self.assertRaisesMessage(CommandError, "Several devices"):
            call_command("send_telemetry", "Fish Pond Node", "ph=7", stdout=out)


class VariableLinkTests(MultiVariableData):
    """Halaman aturan menautkan setiap variabel yang disebut ke halaman variabelnya."""

    def test_links_on_rule_page(self):
        rule = self.make_rule(when=[[(self.soil, "<", 20), (self.rain, "<", 1)]])
        self.send(0, at(0), variable=self.rain)
        self.send(15, at(10))                                  # aktif -> ada baris log
        self.client.force_login(self.siti)
        soil_url = reverse("devices:variable_detail", args=[self.node.pk, self.soil.pk])
        rain_url = reverse("devices:variable_detail", args=[self.weather.pk, self.rain.pk])
        pump_url = reverse("devices:variable_detail", args=[self.node.pk, self.pump.pk])
        page = self.client.get(reverse("rules:detail", args=[rule.pk])).content.decode()
        # When/If/Clear, aksi (pump), kotak Last evaluation, dan baris Values di log.
        self.assertGreaterEqual(page.count(f'href="{soil_url}"'), 5)
        self.assertGreaterEqual(page.count(f'href="{rain_url}"'), 3)
        self.assertIn(f'href="{pump_url}"', page)
        box = self.client.get(reverse("rules:evaluation", args=[rule.pk])).content.decode()
        self.assertIn(f'href="{rain_url}"', box)

    def test_inactivity_rule_links_device(self):
        rule = Rule.objects.create(owner=self.siti, name="Silent")
        EcaSpec.objects.create(rule=rule, event=EcaSpec.Event.INACTIVITY, device=self.weather, silence_seconds=60)
        self.client.force_login(self.siti)
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, f'href="{reverse("devices:detail", args=[self.weather.pk])}"')
