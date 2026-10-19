"""Tes tahap 18: log aktivitas (siapa melihat apa, kejadian yang tercatat) dan pencarian aturan."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.services import save_telemetry
from devices.models import Command, Variable
from rules.models import Alarm, FlowNode, FlowSpec, Rule, RuleAction, RuleModel
from rules.tests import RuleTestData, at

from .models import ActivityEvent

User = get_user_model()


def verbs(user=None):
    events = ActivityEvent.objects.all() if user is None else ActivityEvent.objects.filter(owner=user)
    return list(events.order_by("pk").values_list("verb", flat=True))


class ActivityTests(RuleTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_user("admin", password="rahasia-12345", role=User.Role.ADMIN)

    def setUp(self):
        ActivityEvent.objects.all().delete()     # kejadian dari setUpTestData (device dibuat, dst.)

    def page(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("activity:list"), params)

    def test_device_created_by_a_user_is_logged_with_owner_and_actor(self):
        self.client.force_login(self.budi)
        self.client.post(reverse("devices:add"), {"name": "Budi sensor", "is_active": "on"})
        event = ActivityEvent.objects.get(verb="device.created")
        self.assertEqual((event.owner, event.actor, event.actor_name), (self.budi, self.budi, "budi"))
        self.assertEqual(event.device.name, "Budi sensor")

    def test_each_user_sees_only_their_own_events_admin_sees_all(self):
        save_telemetry(self.node, [("soil_moisture", 25.0, timezone.now())])        # siti
        save_telemetry(self.foreign, [("temperature", 20.0, timezone.now())])       # budi
        siti_page = self.page(self.siti)
        self.assertContains(siti_page, "Greenhouse Node is online")
        self.assertNotContains(siti_page, "Budi&#x27;s node")
        budi_page = self.page(self.budi)
        self.assertContains(budi_page, "Budi&#x27;s node is online")
        self.assertNotContains(budi_page, "Greenhouse Node")
        everyone = self.page(self.admin)
        self.assertContains(everyone, "Greenhouse Node is online")
        self.assertContains(everyone, "Budi&#x27;s node is online")
        self.assertContains(everyone, "<th>Owner</th>", html=True)
        # Pengguna biasa tidak bisa memakai filter owner untuk mengintip log orang lain.
        self.assertNotContains(self.page(self.siti, owner=self.budi.pk), "Budi&#x27;s node")

    def test_variable_created_from_telemetry_is_by_the_device(self):
        save_telemetry(self.node, [("light", 300.0, timezone.now())])
        event = ActivityEvent.objects.get(verb="variable.created")
        self.assertEqual((event.actor, event.actor_name, event.owner), (None, "device", self.siti))

    def test_rule_lifecycle_actuator_and_alarm(self):
        rule = self.make_rule(actions=[
            {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
            {"kind": RuleAction.Kind.ALARM, "severity": "MAJOR", "message": "Dry"},
        ], clear=None)
        self.send(15, at(0))
        self.assertEqual(verbs(self.siti), ["rule.created", "actuator.set", "alarm.raised", "rule.activate"])
        activated = ActivityEvent.objects.get(verb="rule.activate")
        self.assertEqual((activated.rule, activated.device, activated.actor_name), (rule, self.node, "rule engine"))
        self.assertIn("Water pump = ON", activated.message)
        self.assertEqual(ActivityEvent.objects.get(verb="alarm.raised").level, ActivityEvent.Level.ERROR)
        self.assertEqual(ActivityEvent.objects.get(verb="actuator.set").actor_name, "rule engine")

        alarm = Alarm.objects.get()
        self.client.force_login(self.siti)
        self.client.post(reverse("rules:alarm_ack", args=[alarm.pk]))
        self.assertEqual(ActivityEvent.objects.get(verb="alarm.acknowledged").actor, self.siti)
        self.client.post(reverse("rules:toggle", args=[rule.pk]))
        self.client.post(reverse("rules:delete", args=[rule.pk]))
        self.assertEqual(verbs(self.siti)[-2:], ["rule.disabled", "rule.deleted"])

    def test_failed_action_is_an_error(self):
        self.make_rule(clear=None, actions=[{"kind": RuleAction.Kind.EMAIL, "message": "x"}])
        self.siti.email = ""
        self.siti.save()
        self.send(15, at(0))
        self.assertEqual(ActivityEvent.objects.get(verb="rule.activate").level, ActivityEvent.Level.ERROR)

    def test_commands_and_admin_acting_on_another_users_device(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("devices:command_add", args=[self.node.pk]),
                         {"method": "reboot", "params": "{}", "ttl": "300"})
        command = Command.objects.get()
        created = ActivityEvent.objects.get(verb="command.created")
        self.assertEqual((created.owner, created.actor), (self.siti, self.admin), "siti sees what admin did")
        command.mark_sent()
        command.complete(False, {"error": "busy"})
        failed = ActivityEvent.objects.get(verb="command.failed")
        self.assertEqual(failed.level, ActivityEvent.Level.WARNING)
        self.assertFalse(ActivityEvent.objects.filter(verb="command.sent").exists(), "only final states")

    def test_login_and_failed_login(self):
        self.client.post(reverse("accounts:login"), {"username": "siti", "password": "rahasia-12345"})
        self.client.post(reverse("accounts:login"), {"username": "siti", "password": "wrong"})
        self.client.post(reverse("accounts:login"), {"username": "nobody", "password": "wrong"})
        self.assertIn("account.login", verbs(self.siti))
        self.assertIn("account.login_failed", verbs(self.siti))
        unknown = ActivityEvent.objects.get(verb="account.login_failed", owner=None)
        self.assertIn('"nobody"', unknown.message)
        self.assertNotContains(self.page(self.siti), "nobody")
        self.assertContains(self.page(self.admin), "nobody")

    def test_deleting_a_user_removes_their_log_without_errors(self):
        save_telemetry(self.foreign, [("temperature", 20.0, timezone.now())])
        self.budi.delete()
        self.assertFalse(ActivityEvent.objects.filter(device_name="Budi's node").exists())
        self.assertFalse(ActivityEvent.objects.filter(verb="device.deleted").exists(), "cascade is not logged")

    def test_deleted_device_stays_readable(self):
        save_telemetry(self.node, [("soil_moisture", 25.0, timezone.now())])
        self.client.force_login(self.siti)
        self.client.post(reverse("devices:delete", args=[self.node.pk]))
        online = ActivityEvent.objects.get(verb="device.online")
        self.assertIsNone(online.device)
        self.assertEqual(online.device_name, "Greenhouse Node")
        self.assertEqual(ActivityEvent.objects.get(verb="device.deleted").owner, self.siti)

    def test_filters(self):
        save_telemetry(self.node, [("soil_moisture", 25.0, timezone.now())])
        self.pump.set_desired(1, self.siti)
        page = self.page(self.siti, q="pump on")
        self.assertContains(page, "Water pump on Greenhouse Node set to ON")
        self.assertNotContains(page, "is online")
        self.assertNotContains(self.page(self.siti, category="DEVICE"), "set to ON")
        self.assertContains(self.page(self.siti, device=self.node.pk), "is online")
        self.assertContains(self.page(self.siti, level="ERROR"), "Nothing matches these filters")
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertContains(self.page(self.siti, start=tomorrow), "Nothing matches these filters")

    def test_backfill_rebuilds_history_once(self):
        rule = self.make_rule(clear=None)
        self.send(15, at(0))
        self.pump.set_desired(0, self.siti)
        before = ActivityEvent.objects.count()
        executed_at = rule.executions.get().created_at
        ActivityEvent.objects.all().delete()
        call_command("activity_backfill", stdout=StringIO())
        self.assertGreaterEqual(ActivityEvent.objects.count(), before - 1)
        self.assertEqual(ActivityEvent.objects.get(verb="rule.activate").at, executed_at, "original time")
        self.assertTrue(ActivityEvent.objects.filter(verb="device.created", device=self.node).exists())
        count = ActivityEvent.objects.count()
        call_command("activity_backfill", stdout=StringIO())
        self.assertEqual(ActivityEvent.objects.count(), count, "second run adds nothing")

    def test_old_events_are_removed_by_housekeeping(self):
        ActivityEvent.objects.create(verb="old", message="old", category="DEVICE", owner=self.siti,
                                     at=timezone.now() - timedelta(days=400))
        ActivityEvent.objects.create(verb="new", message="new", category="DEVICE", owner=self.siti)
        with self.settings(ACTIVITY_LOG_DAYS=180):
            call_command("housekeeping", "--once", stdout=StringIO())
        self.assertEqual(list(ActivityEvent.objects.values_list("verb", flat=True)), ["new"])


class RuleSearchTests(RuleTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_user("admin", password="rahasia-12345", role=User.Role.ADMIN)
        cls.temp = Variable.objects.create(device=cls.node, key="air_temp", label="Air temperature")

    def setUp(self):
        self.client.force_login(self.siti)
        self.irrigation = self.make_rule()                        # soil_moisture, pump
        self.irrigation.description = "Water the greenhouse"
        self.irrigation.save()
        self.heat = self.make_rule(when=[[(self.temp, ">", 35)]], clear=None,
                                   actions=[{"kind": RuleAction.Kind.EMAIL, "message": "Too hot in {device}"}])
        Rule.objects.filter(pk=self.heat.pk).update(name="Heat alert", is_enabled=False)
        self.chain = Rule.objects.create(owner=self.siti, name="Chain", model=RuleModel.FLOW)
        FlowSpec.objects.create(rule=self.chain, device=self.node)
        FlowNode.objects.create(rule=self.chain, name="notify_team", kind="TELEGRAM", message="Fan check")
        self.other = self.make_rule(owner=self.budi)
        Rule.objects.filter(pk=self.other.pk).update(name="Budi irrigation")

    def names(self, **params):
        response = self.client.get(reverse("rules:list"), params)
        return [rule.name for rule in response.context["rules"]]

    def test_search_fields(self):
        self.assertEqual(self.names(q="greenhouse water"), ["Irrigation"], "name or description, all words")
        self.assertEqual(self.names(q="soil_moisture"), ["Irrigation"], "variable in a condition")
        self.assertEqual(self.names(q="air temperature"), ["Heat alert"], "variable label")
        self.assertEqual(self.names(q="too hot"), ["Heat alert"], "action message")
        self.assertEqual(self.names(q="email"), ["Heat alert"], "action kind")
        self.assertEqual(self.names(q="notify_team"), ["Chain"], "flow node name")
        self.assertEqual(self.names(q="telegram"), ["Chain"], "node kind")
        self.assertEqual(self.names(q="Greenhouse Node"), ["Chain", "Heat alert", "Irrigation"],
                         "device, no duplicates")
        self.assertEqual(self.names(q="flow"), ["Chain"], "rule model")
        self.assertEqual(self.names(q="nothing-like-this"), [])

    def test_filters_and_ownership(self):
        self.assertEqual(self.names(state="disabled"), ["Heat alert"])
        self.assertEqual(self.names(model="FLOW"), ["Chain"])
        self.assertEqual(self.names(q="irrigation"), ["Irrigation"], "budi's rule stays hidden")
        self.assertEqual(self.names(q="budi"), [], "owner names are searchable for Admin only")
        self.client.force_login(self.admin)
        self.assertEqual(self.names(q="budi"), ["Budi irrigation"])

    def test_page(self):
        response = self.client.get(reverse("rules:list"), {"q": "pump"})
        self.assertContains(response, 'value="pump"')
        self.assertContains(response, "1 of 3 rules match “pump”")
        self.assertContains(self.client.get(reverse("rules:list"), {"q": "zzz"}), "No matching rules")


class ActorMiddlewareTests(TestCase):
    def test_lazy_user_is_not_touched_when_switching_to_async(self):
        """Regresi: asgiref memeriksa semua contextvar dengan isinstance() saat async_to_sync.
        Menyimpan request.user (lazy) di contextvar memicu query sesi di konteks async
        (SynchronousOnlyOperation di Daphne). Yang disimpan kini request-nya."""
        from asgiref.sync import async_to_sync
        from django.test import RequestFactory
        from django.utils.functional import SimpleLazyObject

        from .middleware import ActorMiddleware

        def explode():
            raise AssertionError("request.user was evaluated")

        async def noop():
            return "ok"

        request = RequestFactory().get("/")
        request.user = SimpleLazyObject(explode)
        response = ActorMiddleware(lambda r: async_to_sync(noop)())(request)
        self.assertEqual(response, "ok")
