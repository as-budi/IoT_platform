"""Tes Publish JSON aturan ke device (percobaan sebelum tahap 27)."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from devices.models import Device, Variable
from mqtt_bridge import topics
from mqtt_bridge.handlers import handle_message
from mqtt_bridge.management.commands.mqtt_accounts import acl_text
from mqtt_bridge.outbox import Outbox
from sharing.models import DeviceShare

from .models import EcaCondition, EcaSpec, Rule, RuleAction, RuleDelivery, RulePublication
from .portable import export_rule
from .publish import handle_ack, publish_rule, rule_body, rule_rev, withdraw_rule

User = get_user_model()
PREFIX = "test-prefix"


class Broker:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, retain):
        self.messages.append((topic, payload, retain))
        return True


@override_settings(MQTT_TOPIC_PREFIX=PREFIX)
class PublishData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.sensor = Device.objects.create(owner=cls.siti, name="Soil probe", mac_address="AA:BB:CC:00:00:01")
        cls.pump = Device.objects.create(owner=cls.siti, name="Pump", mac_address="AA:BB:CC:00:00:02")
        cls.soil = Variable.objects.create(device=cls.sensor, key="soil")
        cls.relay = Variable.objects.create(device=cls.pump, key="pump", kind=Variable.Kind.ACTUATOR,
                                            value_type=Variable.ValueType.BOOLEAN)

    def make_rule(self, owner=None, extra_device=None):
        rule = Rule.objects.create(owner=owner or self.siti, name="Irrigation")
        spec = EcaSpec.objects.create(rule=rule)
        EcaCondition.objects.create(spec=spec, variable=self.soil, operator="<", threshold=20)
        RuleAction.objects.create(rule=rule, kind="SET_ACTUATOR", target_variable=self.relay, value=1)
        if extra_device is not None:
            RuleAction.objects.create(rule=rule, kind="SEND_COMMAND", target_device=extra_device, method="beep")
        return rule

    def flush(self):
        broker = Broker()
        Outbox(PREFIX).flush(broker.publish)
        return broker.messages


class JsonTests(PublishData):
    def test_devices_with_mac(self):
        data = export_rule(self.make_rule())
        self.assertEqual(data["devices"], [{"name": "Pump", "mac": "AA:BB:CC:00:00:02"},
                                           {"name": "Soil probe", "mac": "AA:BB:CC:00:00:01"}])
        self.assertEqual(list(data)[:8], ["format", "version", "model", "exported_at", "name", "description",
                                          "enabled", "devices"])
        Device.objects.filter(pk=self.pump.pk).update(mac_address="")
        self.assertIsNone(export_rule(Rule.objects.get(name="Irrigation"))["devices"][0]["mac"])

    def test_rev_ignores_export_time(self):
        rule = self.make_rule()
        self.assertEqual(rule_rev(rule_body(rule)), rule_rev(rule_body(Rule.objects.get(pk=rule.pk))))


class PublishTests(PublishData):
    def test_publish_flow(self):
        rule = self.make_rule()
        publication = publish_rule(rule, self.siti)
        self.assertEqual(publication.number, 1)
        deliveries = {d.device.name: d for d in publication.deliveries.all()}
        self.assertEqual(set(deliveries), {"Pump", "Soil probe"})
        payload = deliveries["Pump"].payload
        self.assertEqual(payload["type"], "rule")
        self.assertEqual(payload["publish"]["id"], 1)
        self.assertEqual(payload["device"], {"name": "Pump", "mac": "AA:BB:CC:00:00:02"})
        self.assertEqual(payload["ack"], topics.rule_ack(PREFIX, self.pump.access_token, rule.pk))
        self.assertEqual(payload["rule"]["devices"][0]["mac"], "AA:BB:CC:00:00:02")
        self.assertGreater(publication.size_bytes, 200)

        sent = self.flush()
        self.assertEqual({t for t, _, _ in sent}, {topics.rules(PREFIX, d.access_token, rule.pk)
                                                   for d in (self.pump, self.sensor)})
        self.assertTrue(all(retain for _, _, retain in sent))
        self.assertEqual(RuleDelivery.objects.filter(status="SENT").count(), 2)
        self.assertEqual(self.flush(), [])                             # tidak dikirim dua kali

        # tanda terima lewat MQTT
        ack = topics.rule_ack(PREFIX, self.pump.access_token, rule.pk)
        result = handle_message(ack, json.dumps({"publish": 1, "ok": True, "size": 1200}).encode(), PREFIX)
        self.assertTrue(result.ok, result.message)
        delivery = RuleDelivery.objects.get(device=self.pump)
        self.assertEqual(delivery.status, "RECEIVED")
        self.assertIn("1200 bytes", delivery.detail)
        bad = handle_message(topics.rule_ack(PREFIX, self.sensor.access_token, rule.pk),
                             b'{"publish": 1, "ok": false, "error": "too large"}', PREFIX)
        self.assertTrue(bad.ok)
        self.assertEqual(RuleDelivery.objects.get(device=self.sensor).status, "REJECTED")
        unknown = handle_message(topics.rule_ack(PREFIX, self.pump.access_token, 999), b'{"publish": 1}', PREFIX)
        self.assertFalse(unknown.ok)

    def test_skipped_devices(self):
        foreign = Device.objects.create(owner=self.budi, name="Budi fan", mac_address="AA:BB:CC:00:00:09")
        DeviceShare.objects.create(device=foreign, user=self.siti, role="VIEWER", created_by=self.budi)
        Device.objects.filter(pk=self.sensor.pk).update(is_active=False)
        publication = publish_rule(self.make_rule(extra_device=foreign))
        status = {d.device.name: (d.status, d.detail) for d in publication.deliveries.all()}
        self.assertEqual(status["Pump"][0], "QUEUED")
        self.assertEqual(status["Soil probe"], ("SKIPPED", "The device is inactive."))
        self.assertIn("Operator access", status["Budi fan"][1])
        self.assertEqual(len(self.flush()), 1)

    def test_republish_withdraw_and_delete(self):
        rule = self.make_rule()
        publish_rule(rule)
        second = publish_rule(rule)                                   # sebelum bridge sempat mengirim
        self.assertEqual(RuleDelivery.objects.filter(status="SUPERSEDED").count(), 2)
        messages = self.flush()
        self.assertEqual(len(messages), 2)
        self.assertTrue(all(payload["publish"]["id"] == second.number for _, payload, _ in messages))

        withdraw_rule(rule)
        cleared = self.flush()
        self.assertEqual(len(cleared), 2)
        self.assertTrue(all(payload is None and retain for _, payload, retain in cleared))
        self.assertEqual(RuleDelivery.objects.filter(status="CLEARED").count(), 2)

        publish_rule(rule)
        self.flush()
        rule.delete()
        on_delete = self.flush()
        self.assertEqual(len(on_delete), 2)
        self.assertTrue(all(payload is None for _, payload, _ in on_delete))
        self.assertFalse(RulePublication.objects.exists())

    def test_ack_must_be_object(self):
        from api.payload import PayloadError

        with self.assertRaises(PayloadError):
            handle_ack(self.pump, 1, ["x"])


class PageTests(PublishData):
    def setUp(self):
        self.client.force_login(self.siti)

    def test_card_publish_and_status(self):
        rule = self.make_rule()
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, "Send to devices")
        self.assertContains(page, "AA:BB:CC:00:00:02")                       # di JSON dan di tabel
        response = self.client.post(reverse("rules:publish", args=[rule.pk]))
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]) + "#publish",
                             fetch_redirect_response=False)
        status = self.client.get(reverse("rules:publish_status", args=[rule.pk]))
        self.assertContains(status, "Waiting to be sent")
        self.assertContains(status, "data-publish-waiting")
        self.flush()
        handle_ack(self.pump, rule.pk, {"publish": 1})
        status = self.client.get(reverse("rules:publish_status", args=[rule.pk]))
        self.assertContains(status, "Received")
        self.client.post(reverse("rules:withdraw", args=[rule.pk]))
        self.assertTrue(RulePublication.objects.filter(rule=rule, action="WITHDRAW").exists())

    def test_hidden_and_forbidden(self):
        rule = self.make_rule()
        with override_settings(MICRO_RULES={"VISIBILITY": "off"}):
            self.assertNotContains(self.client.get(reverse("rules:detail", args=[rule.pk])), "Send to devices")
            self.assertEqual(self.client.post(reverse("rules:publish", args=[rule.pk])).status_code, 404)
        self.client.force_login(self.budi)
        self.assertEqual(self.client.post(reverse("rules:publish", args=[rule.pk])).status_code, 404)
        self.assertFalse(RulePublication.objects.exists())

    def test_acl_and_api_topics(self):
        acl = acl_text("iot", "bridge")
        self.assertIn("pattern read  iot/%u/rules/+", acl)
        self.assertIn("pattern write iot/%u/rules/+/ack", acl)
        from api.v2 import _topics

        self.assertEqual(_topics("TOK")["rules"], f"{PREFIX}/TOK/rules/+")
        self.assertEqual(_topics("TOK")["rule_ack"], f"{PREFIX}/TOK/rules/{{id}}/ack")
