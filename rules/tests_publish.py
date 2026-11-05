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
from .publish import (
    compact_body, handle_ack, message_size, publish_rule, rule_body, rule_message, rule_rev, withdraw_rule,
)

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
        # Disimpan dan dikirim sebagai teks: "publish" tetap di depan apa pun database-nya.
        self.assertTrue(deliveries["Pump"].payload.startswith('{"publish":{"id":1,"rev":'))
        payload = deliveries["Pump"].message
        # Full: JSON halaman aturan (tanpa exported_at) + "publish" di depan; tanpa amplop lain.
        body = export_rule(rule)
        body.pop("exported_at")
        self.assertEqual(payload, {"publish": {"id": 1, "rev": rule_rev(rule_body(rule))}, **body})
        self.assertEqual(list(payload)[:2], ["publish", "format"])
        self.assertEqual(deliveries["Soil probe"].payload, deliveries["Pump"].payload)   # sama untuk semua device
        self.assertFalse(publication.compact)
        self.assertEqual(publication.size_bytes, message_size(payload))
        self.assertEqual(publication.size_bytes, len(deliveries["Pump"].payload.encode()))

        sent = self.flush()
        self.assertEqual({t for t, _, _ in sent}, {topics.rules(PREFIX, d.access_token, rule.pk)
                                                   for d in (self.pump, self.sensor)})
        self.assertTrue(all(retain for _, _, retain in sent))
        # Bridge menerima teks yang sudah jadi, bukan dict yang di-encode ulang.
        self.assertTrue(all(isinstance(text, str) and text.startswith('{"publish":') for _, text, _ in sent))
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

    def test_compact_message(self):
        rule = self.make_rule(extra_device=self.sensor)
        full = rule_body(rule)
        compact = compact_body(full)
        # Tanpa format/description/nilai kosong; device sebagai indeks di "devices".
        self.assertNotIn("format", compact)
        self.assertNotIn("description", compact)
        self.assertNotIn("device", compact["eca"])                          # null di Full
        self.assertEqual(compact["devices"], full["devices"])
        names = [d["name"] for d in full["devices"]]
        condition = compact["eca"]["when"][0][0]
        self.assertEqual(names[condition["device"]], "Soil probe")
        self.assertEqual([names[a["device"]] for a in compact["actions"]], ["Pump", "Soil probe"])
        self.assertNotIn("params", compact["actions"][1])                   # {} di Full
        publication = publish_rule(rule, compact=True)
        self.assertTrue(publication.compact)
        self.assertEqual(publication.deliveries.first().message, rule_message(full, rule_rev(full), 1, compact=True))
        self.assertLess(publication.size_bytes, message_size(rule_message(full, "x" * 8, 1)))

    def test_compact_keeps_names_when_not_unique(self):
        body = {"name": "x", "devices": [{"name": "Fan", "mac": None}, {"name": "Fan", "mac": "AA"}],
                "eca": {"when": [[{"device": "Fan", "variable": "t"}]]}}
        compact = compact_body(body)
        self.assertEqual(compact["eca"]["when"][0][0]["device"], "Fan")
        self.assertEqual(compact["devices"], [{"name": "Fan"}, {"name": "Fan", "mac": "AA"}])

    def test_republish_withdraw_and_delete(self):
        rule = self.make_rule()
        publish_rule(rule)
        second = publish_rule(rule)                                   # sebelum bridge sempat mengirim
        self.assertEqual(RuleDelivery.objects.filter(status="SUPERSEDED").count(), 2)
        messages = self.flush()
        self.assertEqual(len(messages), 2)
        self.assertTrue(all(json.loads(payload)["publish"]["id"] == second.number for _, payload, _ in messages))

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
        # Satu kartu: JSON + tombol Publish + tab Devices + badge ringkasan di judul.
        self.assertContains(page, "Publish to devices")
        self.assertContains(page, 'id="rule-devices-tab"')
        self.assertContains(page, "Not published")
        self.assertNotContains(page, 'id="publish" open')
        self.assertContains(page, "AA:BB:CC:00:00:02")                       # di JSON dan di tabel
        response = self.client.post(reverse("rules:publish", args=[rule.pk]))
        self.assertRedirects(response, reverse("rules:detail", args=[rule.pk]) + "#publish",
                             fetch_redirect_response=False)
        status = self.client.get(reverse("rules:publish_status", args=[rule.pk]))
        self.assertContains(status, "Waiting to be sent")
        self.assertContains(status, "data-publish-waiting")
        self.assertContains(status, "0/2 received · 2 waiting")             # badge baru untuk judul
        # Masih menunggu: kartu terbuka dengan tab Devices aktif.
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, 'id="publish" open')
        self.assertContains(page, 'class="nav-link active" id="rule-devices-tab"')
        self.flush()
        handle_ack(self.pump, rule.pk, {"publish": 1})
        status = self.client.get(reverse("rules:publish_status", args=[rule.pk]))
        self.assertContains(status, "Received")
        self.assertContains(status, "1/2 received · 1 waiting")
        self.assertContains(status, "Message sent in publish #1 (full,")
        response = self.client.post(reverse("rules:publish", args=[rule.pk]), {"format": "compact"})
        self.assertTrue(RulePublication.objects.get(rule=rule, number=2).compact)
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, '<option value="compact" selected>')
        self.client.post(reverse("rules:withdraw", args=[rule.pk]))
        self.assertTrue(RulePublication.objects.filter(rule=rule, action="WITHDRAW").exists())

    def test_hidden_and_forbidden(self):
        rule = self.make_rule()
        with override_settings(MICRO_RULES={"VISIBILITY": "off"}):
            page = self.client.get(reverse("rules:detail", args=[rule.pk]))
            # Kartu JSON tetap ada, tanpa tombol, tab, maupun badge publish.
            self.assertContains(page, 'id="rule-json"')
            self.assertNotContains(page, "Publish to devices")
            self.assertNotContains(page, "rule-devices-tab")
            self.assertNotContains(page, "data-publish-badge")
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
