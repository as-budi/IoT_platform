"""Tes tahap 14: penjadwalan aturan (ECA event "Schedule" dan rule chain Input "On a schedule").

Semua waktu di tes ini memakai jam lokal server (Asia/Jakarta). 19 Oktober 2026 = Senin.
"""
import json
from datetime import datetime, timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from devices.models import Telemetry

from . import schedules
from .engines import ENGINES
from .models import (
    Alarm, EcaCondition, EcaSpec, FlowSpec, Rule, RuleAction, RuleExecution, RuleModel, RuleSchedule, RuleState,
)
from .portable import export_rule
from .tests import MultiVariableData
from .tests_flow import FlowData
from .tests_portable import without_timestamp

SKIPPED = RuleExecution.Transition.SKIPPED
ACTIVATE = RuleExecution.Transition.ACTIVATE
CLEAR = RuleExecution.Transition.CLEAR


def local(day, hour, minute=0, second=0):
    """Waktu aware di zona waktu server, Oktober 2026 (day=19 adalah Senin)."""
    return timezone.make_aware(datetime(2026, 10, day, hour, minute, second), timezone.get_current_timezone())


def plan(**fields):
    """RuleSchedule tanpa aturan, untuk menguji perhitungan waktu saja."""
    return RuleSchedule(**fields)


class NextRunTests(TestCase):
    def test_daily_times_and_weekdays(self):
        schedule = plan(kind="DAILY", times="06:00,18:30", weekdays="01234")
        self.assertEqual(schedules.next_run(schedule, local(19, 5)), local(19, 6))
        self.assertEqual(schedules.next_run(schedule, local(19, 6)), local(19, 18, 30), "strictly after")
        # Jumat 19:00 -> Senin berikutnya 06:00 (Sabtu dan Minggu dilewati).
        self.assertEqual(schedules.next_run(schedule, local(23, 19)), local(26, 6))

    def test_interval_is_aligned_to_midnight(self):
        schedule = plan(kind="INTERVAL", interval_minutes=15, weekdays="0123456")
        self.assertEqual(schedules.next_run(schedule, local(19, 10, 7)), local(19, 10, 15))
        self.assertEqual(schedules.next_run(schedule, local(19, 23, 50)), local(20, 0, 0))
        # 90 menit: 00:00, 01:30, ..., 22:30, lalu mulai lagi 00:00 esok hari (bukan 24:00).
        ninety = plan(kind="INTERVAL", interval_minutes=90, weekdays="0123456")
        self.assertEqual(schedules.next_run(ninety, local(19, 22, 0)), local(19, 22, 30))
        self.assertEqual(schedules.next_run(ninety, local(19, 22, 30)), local(20, 0, 0))

    def test_interval_skips_days_that_are_not_selected(self):
        weekend = plan(kind="INTERVAL", interval_minutes=60, weekdays="56")
        self.assertEqual(schedules.next_run(weekend, local(19, 12)), local(24, 0))

    def test_once(self):
        schedule = plan(kind="ONCE", run_at=local(20, 7, 30))
        self.assertEqual(schedules.next_run(schedule, local(19, 12)), local(20, 7, 30))
        self.assertIsNone(schedules.next_run(schedule, local(20, 7, 30)))

    def test_nothing_to_run(self):
        self.assertIsNone(schedules.next_run(plan(kind="DAILY", times="", weekdays="0123456"), local(19, 0)))
        self.assertIsNone(schedules.next_run(plan(kind="DAILY", times="06:00", weekdays=""), local(19, 0)))

    def test_upcoming_and_describe(self):
        schedule = plan(kind="DAILY", times="18:00, 6:00", weekdays="01234")
        schedule.times = schedules.format_times(schedules.parse_times(schedule.times))
        self.assertEqual(schedule.times, "06:00,18:00")
        self.assertEqual(schedule.upcoming(3, local(19, 7)), [local(19, 18), local(20, 6), local(20, 18)])
        self.assertEqual(schedule.describe(), "Mon–Fri at 06:00, 18:00")
        self.assertEqual(plan(kind="INTERVAL", interval_minutes=15, weekdays="0123456").describe(),
                         "every 15 min, every day")
        self.assertEqual(plan(kind="ONCE", run_at=local(20, 7, 30)).describe(), "once on 20 Oct 2026 07:30")

    def test_parse_times_rejects_bad_text(self):
        with self.assertRaisesMessage(ValueError, "“25:00” is not a time"):
            schedules.parse_times("06:00, 25:00")


class EcaScheduleData(MultiVariableData):
    def scheduled_rule(self, when=None, active_for=600, times="06:00", actions=None, kind="DAILY",
                       interval=None, max_age=300, now=None):
        """Aturan ECA berjadwal: bawaan setiap hari 06:00, pompa ON lalu OFF setelah `active_for` detik."""
        rule = Rule.objects.create(owner=self.siti, name="Morning watering")
        spec = EcaSpec.objects.create(rule=rule, event=EcaSpec.Event.SCHEDULE, max_age_seconds=max_age)
        for number, group in enumerate(when or [], start=1):
            for order, (variable, op, threshold) in enumerate(group):
                EcaCondition.objects.create(spec=spec, group=number, order=order, variable=variable,
                                            operator=op, threshold=threshold)
        for fields in actions if actions is not None else [
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 0,
             "run_on": RuleAction.RunOn.CLEAR},
        ]:
            RuleAction.objects.create(rule=rule, **fields)
        schedule = RuleSchedule(rule=rule, kind=kind, times=times, interval_minutes=interval,
                                active_for_seconds=active_for)
        schedule.reschedule(now or local(19, 5), save=False)
        schedule.save()
        return rule

    def tick(self, now):
        return ENGINES[RuleModel.ECA].tick(now)

    def pump_value(self):
        self.pump.refresh_from_db()
        return self.pump.desired_value

    def store(self, variable, value, ts):
        Telemetry.objects.create(variable=variable, value=value, ts=ts)


class EcaScheduleTests(EcaScheduleData):
    def test_activates_on_time_and_clears_after_active_for(self):
        rule = self.scheduled_rule()
        self.assertEqual(rule.schedule.next_run_at, local(19, 6))
        self.assertEqual(self.tick(local(19, 5, 59)), [])
        [run] = self.tick(local(19, 6, 0, 1))
        self.assertEqual(run.transition, ACTIVATE)
        self.assertEqual(self.pump_value(), 1)
        self.assertEqual(RuleState.objects.get(rule=rule).status, RuleState.Status.ACTIVE)
        rule.schedule.refresh_from_db()
        self.assertEqual(rule.schedule.next_run_at, local(20, 6), "moved to the next day")
        self.assertEqual(rule.schedule.last_run_at, local(19, 6))

        self.assertEqual(self.tick(local(19, 6, 5)), [], "still within the 600 s")
        [cleared] = self.tick(local(19, 6, 10, 1))
        self.assertEqual(cleared.transition, CLEAR)
        self.assertEqual(self.pump_value(), 0)
        self.assertEqual(RuleState.objects.get(rule=rule).status, RuleState.Status.IDLE)

    def test_condition_false_skips_the_run(self):
        rule = self.scheduled_rule(when=[[(self.soil, "<", 40), (self.rain, "<", 0.5)]])
        self.store(self.soil, 55, local(19, 5, 58))
        self.store(self.rain, 0, local(19, 5, 58))
        [skipped] = self.tick(local(19, 6))
        self.assertEqual(skipped.transition, SKIPPED)
        self.assertIn("06:00 run skipped: condition is false", skipped.results[0]["detail"])
        self.assertIn("Soil moisture = 55", skipped.results[0]["detail"])
        self.assertIsNone(self.pump_value())
        state = RuleState.objects.get(rule=rule)
        self.assertEqual(state.status, RuleState.Status.IDLE)
        self.assertIs(state.snapshot["when"]["result"], False)
        self.assertEqual(state.snapshot["schedule"]["scheduled"], local(19, 6).isoformat())

    def test_condition_true_activates(self):
        self.scheduled_rule(when=[[(self.soil, "<", 40), (self.rain, "<", 0.5)]])
        self.store(self.soil, 30, local(19, 5, 58))
        self.store(self.rain, 0, local(19, 5, 58))
        [run] = self.tick(local(19, 6))
        self.assertEqual(run.transition, ACTIVATE)
        self.assertEqual(run.snapshot, {"Greenhouse Node · Soil moisture": 30.0, "Weather station · Rain": 0.0})

    def test_old_data_is_unknown_and_skips(self):
        self.scheduled_rule(when=[[(self.soil, "<", 40)]], max_age=600)
        self.store(self.soil, 10, local(19, 4))          # 2 jam sebelum jadwal
        [skipped] = self.tick(local(19, 6))
        self.assertIn("condition is unknown", skipped.results[0]["detail"])

    def test_missed_run_is_logged_not_run(self):
        rule = self.scheduled_rule()
        [skipped] = self.tick(local(19, 7))               # worker mati selama satu jam
        self.assertEqual(skipped.transition, SKIPPED)
        self.assertIn("Missed the 06:00 run", skipped.results[0]["detail"])
        self.assertIsNone(self.pump_value())
        rule.schedule.refresh_from_db()
        self.assertEqual(rule.schedule.next_run_at, local(20, 6))

    @override_settings(RULE_SCHEDULE_GRACE_SECONDS=3600)
    def test_grace_period_is_configurable(self):
        self.scheduled_rule()
        [run] = self.tick(local(19, 6, 30))
        self.assertEqual(run.transition, ACTIVATE)

    def test_without_active_for_only_activate_actions_run(self):
        rule = self.scheduled_rule(active_for=0, actions=[
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
        ])
        [run] = self.tick(local(19, 6))
        self.assertEqual(run.transition, ACTIVATE)
        self.assertEqual(RuleState.objects.get(rule=rule).status, RuleState.Status.IDLE, "ready for the next run")
        self.pump.set_desired(0, None)
        [again] = self.tick(local(20, 6))
        self.assertEqual(again.transition, ACTIVATE)
        self.assertEqual(self.pump_value(), 1)

    def test_still_active_skips_the_next_time(self):
        self.scheduled_rule(times="06:00,07:00", active_for=7200)
        self.tick(local(19, 6))
        [skipped] = self.tick(local(19, 7))
        self.assertEqual(skipped.transition, SKIPPED)
        self.assertIn("still active", skipped.results[0]["detail"])
        [cleared] = self.tick(local(19, 8))
        self.assertEqual(cleared.transition, CLEAR)

    def test_interval_schedule(self):
        self.scheduled_rule(kind="INTERVAL", interval=30, times="", active_for=0, now=local(19, 6, 10))
        self.assertEqual(self.tick(local(19, 6, 20)), [])
        self.assertEqual([e.transition for e in self.tick(local(19, 6, 30))], [ACTIVATE])
        self.assertEqual([e.transition for e in self.tick(local(19, 7, 0))], [ACTIVATE])

    def test_telemetry_does_not_trigger_a_scheduled_rule(self):
        from . import queue

        self.scheduled_rule(when=[[(self.soil, "<", 40)]])
        Telemetry.objects.create(variable=self.soil, value=10, ts=local(19, 5))
        message = queue.telemetry_message(self.node, [(self.soil, 10, local(19, 5))])
        self.assertEqual(queue.process(message, now=local(19, 5)), [])

    def test_disabled_rule_does_not_run(self):
        rule = self.scheduled_rule()
        Rule.objects.filter(pk=rule.pk).update(is_enabled=False)
        self.assertEqual(self.tick(local(19, 6)), [])

    def test_a_run_is_claimed_only_once(self):
        rule = self.scheduled_rule()
        first = RuleSchedule.objects.get(rule=rule)
        second = RuleSchedule.objects.get(rule=rule)     # proses lain membaca baris yang sama
        self.assertEqual(schedules.claim(first, local(19, 6))[0], schedules.FIRE)
        self.assertIsNone(schedules.claim(second, local(19, 6)), "already advanced by the first claim")

    def test_alarm_without_a_device_fails_clearly_email_works(self):
        self.scheduled_rule(active_for=0, actions=[
            {"kind": RuleAction.Kind.ALARM, "severity": "WARNING", "message": "Check {time}"},
            {"kind": RuleAction.Kind.EMAIL, "message": "Good morning, it is {time}"},
        ])
        [run] = self.tick(local(19, 6))
        self.assertFalse(run.results[0]["ok"])
        self.assertIn("No device to attach the alarm to", run.results[0]["detail"])
        self.assertTrue(run.results[1]["ok"])
        self.assertEqual(mail.outbox[0].body, "Good morning, it is 06:00")

    def test_alarm_uses_the_condition_device(self):
        self.scheduled_rule(when=[[(self.soil, "<", 40)]], active_for=60, actions=[
            {"kind": RuleAction.Kind.ALARM, "severity": "WARNING", "message": "Dry at {time}: {values}"},
        ])
        self.store(self.soil, 20, local(19, 5, 59))
        self.tick(local(19, 6))
        alarm = Alarm.objects.get()
        self.assertEqual((alarm.device, alarm.message), (self.node, "Dry at 06:00: Greenhouse Node · Soil moisture = 20"))
        self.tick(local(19, 6, 1, 1))
        alarm.refresh_from_db()
        self.assertIsNotNone(alarm.cleared_at, "the alarm clears with the rule")

    def test_describe(self):
        rule = self.scheduled_rule(when=[[(self.soil, "<", 40)]])
        summary = ENGINES[RuleModel.ECA].describe(Rule.objects.get(pk=rule.pk))
        self.assertEqual(summary, {"when": "every day at 06:00", "if": "Soil moisture < 40", "clear": "after 600 s"})


class FlowScheduleTests(FlowData):
    def scheduled_chain(self, nodes, links, device="node", times="06:00"):
        rule = self.chain(nodes, links, device=device)
        FlowSpec.objects.filter(rule=rule).update(trigger=FlowSpec.Trigger.SCHEDULE)
        schedule = RuleSchedule(rule=rule, kind="DAILY", times=times)
        schedule.reschedule(local(19, 5), save=False)
        schedule.save()
        return rule

    def tick(self, now):
        return ENGINES[RuleModel.FLOW].tick(now)

    def fan_chain(self, **kwargs):
        return self.scheduled_chain(
            [("temp_now", "LATEST", {"variable": self.temp, "key": "temperature", "seconds": 3600}),
             ("morning", "CHECK", {"key": "hour", "operator": "<", "threshold": 12}),
             ("hot", "CHECK", {"key": "temperature", "operator": ">", "threshold": 28}),
             ("fan_on", "SET_ACTUATOR", {"target_variable": self.fan, "value": 1})],
            [("Input", "SUCCESS", "temp_now"), ("temp_now", "SUCCESS", "morning"),
             ("morning", "TRUE", "hot"), ("hot", "TRUE", "fan_on")],
            **kwargs,
        )

    def test_scheduled_message_walks_the_chain(self):
        rule = self.fan_chain()
        Telemetry.objects.create(variable=self.temp, value=30, ts=local(19, 5, 55))
        [execution] = self.tick(local(19, 6))
        self.assertEqual(execution.transition, RuleExecution.Transition.RUN)
        self.fan.refresh_from_db()
        self.assertEqual(self.fan.desired_value, 1)
        snapshot = RuleState.objects.get(rule=rule).snapshot
        self.assertEqual(snapshot["trigger"], "schedule")
        self.assertEqual(snapshot["input"], {"hour": 6, "minute": 0, "weekday": 0, "day": 19, "month": 10})
        self.assertEqual(self.path(rule), [("temp_now", "SUCCESS"), ("morning", "TRUE"), ("hot", "TRUE"),
                                           ("fan_on", "SUCCESS")])
        self.assertEqual(execution.snapshot["Greenhouse Node · hour"], 6, "the message as it entered Input")

    def test_telemetry_does_not_start_a_scheduled_chain(self):
        rule = self.fan_chain()
        self.send(local(19, 5, 30), temperature=35)
        self.assertFalse(RuleState.objects.filter(rule=rule).exists())

    def test_alarm_node_needs_a_device(self):
        rule = self.scheduled_chain(
            [("alarm", "CREATE_ALARM", {"severity": "WARNING", "message": "Daily check"})],
            [("Input", "SUCCESS", "alarm")], device=None,
        )
        [execution] = self.tick(local(19, 6))
        self.assertFalse(execution.ok)
        self.assertIn("Alarms need a device", execution.results[0]["detail"])
        self.assertEqual(self.path(rule), [("alarm", "FAILURE")])
        self.assertEqual(execution.snapshot["Schedule · hour"], 6)

    def test_email_placeholders_include_the_time(self):
        self.scheduled_chain(
            [("report", "EMAIL", {"message": "Report {day}/{month} {hour}:{minute}"})],
            [("Input", "SUCCESS", "report")], device=None, times="07:05",
        )
        self.tick(local(19, 7, 5))
        self.assertEqual(mail.outbox[0].body, "Report 19/10 7:5")

    def test_missed_run(self):
        rule = self.fan_chain()
        [skipped] = self.tick(local(19, 9))
        self.assertEqual(skipped.transition, SKIPPED)
        self.assertFalse(RuleState.objects.filter(rule=rule, snapshot__has_key="trace").exists())

    def test_describe(self):
        rule = self.fan_chain()
        rule = Rule.objects.get(pk=rule.pk)
        self.assertEqual(ENGINES[RuleModel.FLOW].describe(rule)["when"], "schedule: every day at 06:00")


class ScheduleFormTests(EcaScheduleData):
    """Form dan halaman: lewat impor JSON (memakai form yang sama dengan halaman Edit)."""

    def setUp(self):
        self.client.force_login(self.siti)

    def eca_json(self, schedule, when=None, actions=None):
        return {
            "format": "iot-platform.rule", "version": 1, "model": "ECA", "name": "Morning watering",
            "description": "", "enabled": True,
            "eca": {"event": "SCHEDULE", "schedule": schedule, "device": None, "silence_seconds": None,
                    "duration_seconds": 0, "cooldown_seconds": 0, "max_age_seconds": 900,
                    "when": when if when is not None else [
                        [{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": "<", "threshold": 40}]
                    ],
                    "clear": []},
            "actions": actions if actions is not None else [
                {"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "Greenhouse Node", "variable": "pump",
                 "value": 1},
                {"kind": "SET_ACTUATOR", "run_on": "CLEAR", "device": "Greenhouse Node", "variable": "pump",
                 "value": 0},
            ],
        }

    def post_import(self, payload, url=None):
        return self.client.post(url or reverse("rules:import"), {"text": json.dumps(payload)})

    DAILY = {"kind": "DAILY", "times": ["18:00", "06:00"], "weekdays": ["mon", "tue", "wed", "thu", "fri"],
             "interval_minutes": None, "run_at": None, "active_for_seconds": 600}

    def test_import_creates_schedule_and_round_trips(self):
        response = self.post_import(self.eca_json(self.DAILY))
        rule = Rule.objects.get(name="Morning watering")
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        schedule = rule.schedule
        self.assertEqual((schedule.times, schedule.weekdays, schedule.active_for_seconds), ("06:00,18:00", "01234", 600))
        self.assertIsNotNone(schedule.next_run_at)
        self.assertGreater(schedule.next_run_at, timezone.now())
        data = export_rule(rule)
        self.assertEqual(data["eca"]["schedule"], {**self.DAILY, "times": ["06:00", "18:00"]})
        # Ekspor -> impor -> ekspor menghasilkan JSON yang sama.
        self.post_import({**data, "name": "Copy"})
        copy = export_rule(Rule.objects.get(name="Copy"))
        self.assertEqual(without_timestamp(copy), {**without_timestamp(data), "name": "Copy"})

    def test_detail_page_shows_schedule(self):
        self.post_import(self.eca_json(self.DAILY))
        rule = Rule.objects.get(name="Morning watering")
        response = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(response, "Mon–Fri at 06:00, 18:00")
        self.assertContains(response, "Next:")
        self.assertContains(response, "after 600 s")

    def test_edit_page_renders_schedule_fields(self):
        self.post_import(self.eca_json(self.DAILY))
        rule = Rule.objects.get(name="Morning watering")
        response = self.client.get(reverse("rules:edit", args=[rule.pk]))
        self.assertContains(response, 'name="schedule-times"')
        self.assertContains(response, 'value="06:00,18:00"')
        self.assertContains(response, 'value="SCHEDULE"')

    def assert_rejected(self, payload, *texts):
        response = self.post_import(payload)
        self.assertEqual(response.status_code, 200, "the import page is shown again")
        self.assertFalse(Rule.objects.filter(name=payload["name"]).exists())
        for text in texts:
            self.assertContains(response, text)

    def test_schedule_validation(self):
        self.assert_rejected(self.eca_json({**self.DAILY, "times": []}), "Enter at least one time")
        self.assert_rejected(self.eca_json({**self.DAILY, "times": ["25:00"]}), "is not a time like 06:00")
        self.assert_rejected(self.eca_json({**self.DAILY, "weekdays": []}), "Choose at least one day")
        self.assert_rejected(self.eca_json({**self.DAILY, "weekdays": ["monday", "xyz"]}), "unknown weekday")
        self.assert_rejected(self.eca_json({"kind": "INTERVAL", "interval_minutes": None}), "Enter the number of minutes")
        self.assert_rejected(self.eca_json({"kind": "ONCE", "run_at": "2020-01-01T06:00"}), "Choose a time in the future")
        self.assert_rejected(self.eca_json({**self.DAILY, "active_for_seconds": 0}), "need “Keep active for”")
        self.assert_rejected({**self.eca_json(None)}, "&quot;schedule&quot; must be an object")

    def test_conditions_are_optional_for_a_schedule(self):
        self.post_import(self.eca_json({"kind": "ONCE", "run_at": "2030-01-01T06:00", "active_for_seconds": 60},
                                       when=[]))
        rule = Rule.objects.get(name="Morning watering")
        self.assertEqual(rule.eca.when_groups, [])
        self.assertEqual(rule.schedule.next_run_at, local(1, 6).replace(year=2030, month=1))

    def test_switching_away_from_schedule_deletes_it(self):
        self.post_import(self.eca_json(self.DAILY))
        rule = Rule.objects.get(name="Morning watering")
        telemetry = self.eca_json(None)
        telemetry["eca"]["event"] = "TELEMETRY"
        telemetry["eca"]["clear"] = [[{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": ">",
                                       "threshold": 50}]]
        response = self.post_import(telemetry, url=reverse("rules:import_into", args=[rule.pk]))
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        self.assertFalse(RuleSchedule.objects.filter(rule=rule).exists())
        self.assertNotIn("schedule", export_rule(Rule.objects.get(pk=rule.pk))["eca"])

    def test_enabling_plans_from_now(self):
        rule = self.scheduled_rule(now=local(1, 5).replace(year=2020))
        Rule.objects.filter(pk=rule.pk).update(is_enabled=False)
        self.client.post(reverse("rules:toggle", args=[rule.pk]))
        rule.schedule.refresh_from_db()
        self.assertGreater(rule.schedule.next_run_at, timezone.now(), "old runs are not caught up")

    def test_flow_schedule_round_trip(self):
        data = {
            "format": "iot-platform.rule", "version": 1, "model": "FLOW", "name": "Daily report",
            "description": "", "enabled": True,
            "flow": {
                "trigger": "SCHEDULE",
                "schedule": {"kind": "INTERVAL", "times": [], "weekdays": ["sat", "sun"],
                             "interval_minutes": 120, "run_at": None},
                "device": "Greenhouse Node",
                "nodes": [{"name": "report", "kind": "EMAIL", "recipients": "", "message": "{hour}:00"}],
                "links": [{"source": "Input", "label": "SUCCESS", "target": "report"}],
            },
        }
        response = self.client.post(reverse("rules:import"), {"text": json.dumps(data)})
        rule = Rule.objects.get(name="Daily report")
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]))
        self.assertEqual(rule.flow.trigger, FlowSpec.Trigger.SCHEDULE)
        self.assertEqual((rule.schedule.kind, rule.schedule.weekdays, rule.schedule.interval_minutes),
                         ("INTERVAL", "56", 120))
        self.assertEqual(without_timestamp(export_rule(rule)), without_timestamp(data))
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, "every 120 min, Sat–Sun")

    def test_telemetry_rule_has_no_schedule(self):
        payload = self.eca_json(None)
        payload["eca"]["event"] = "TELEMETRY"
        payload["actions"] = payload["actions"][:1]
        self.post_import(payload)
        rule = Rule.objects.get(name="Morning watering")
        self.assertFalse(RuleSchedule.objects.filter(rule=rule).exists())


class SeededScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_rules", stdout=StringIO())

    def test_seeded_schedules(self):
        watering = Rule.objects.get(name="Morning watering")
        self.assertEqual((watering.eca.event, watering.is_enabled), (EcaSpec.Event.SCHEDULE, False))
        self.assertEqual(watering.schedule.describe(), "every day at 06:00")
        report = Rule.objects.get(name="Cold room daily report")
        self.assertEqual((report.flow.trigger, report.schedule.describe()), ("SCHEDULE", "Mon–Fri at 07:00"))
        self.assertIsNotNone(report.schedule.next_run_at)

    def test_seeded_schedules_round_trip(self):
        for name in ("Morning watering", "Cold room daily report"):
            rule = Rule.objects.get(name=name)
            self.client.force_login(rule.owner)
            data = export_rule(rule)
            response = self.client.post(reverse("rules:import"), {"text": json.dumps({**data, "name": name + " 2"})})
            self.assertEqual(response.status_code, 302, name)
            copy = export_rule(Rule.objects.get(name=name + " 2"))
            self.assertEqual(without_timestamp(copy), {**without_timestamp(data), "name": name + " 2"})

    def test_report_runs_and_emails(self):
        report = Rule.objects.get(name="Cold room daily report")
        Rule.objects.filter(pk=report.pk).update(is_enabled=True)
        now = report.schedule.next_run_at
        temperature = report.nodes.get(name="temp_now").variable
        humidity = report.nodes.get(name="hum_now").variable
        Telemetry.objects.create(variable=temperature, value=4.5, ts=now - timedelta(minutes=5))
        Telemetry.objects.create(variable=humidity, value=70, ts=now - timedelta(minutes=5))
        [execution] = ENGINES[RuleModel.FLOW].tick(now)
        self.assertTrue(execution.ok)
        self.assertIn("temperature 4.5 °C, humidity 70 %", mail.outbox[-1].body)
