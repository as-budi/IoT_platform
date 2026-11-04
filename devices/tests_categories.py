"""Tes value type "Categories": label kategori disimpan sebagai kode 0, 1, 2, ..."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from devices.forms import VariableForm
from devices.models import Device, Telemetry, Variable
from rules.models import EcaCondition, EcaSpec, Rule, RuleAction, RuleExecution
from rules import queue

User = get_user_model()
DOOR = ["closed", "open", "locked"]


class CategoryData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.siti, name="Store room")
        cls.door = Variable.objects.create(device=cls.device, key="door", label="Door",
                                           value_type=Variable.ValueType.CATEGORY, categories=DOOR)
        cls.mode = Variable.objects.create(device=cls.device, key="mode", kind=Variable.Kind.ACTUATOR,
                                           value_type=Variable.ValueType.CATEGORY, categories=["auto", "manual", "off"])
        cls.temp = Variable.objects.create(device=cls.device, key="temperature", unit="°C")

    def setUp(self):
        self.client.force_login(self.siti)

    def post_telemetry(self, payload):
        return self.client.post(reverse("api:telemetry", args=[self.device.access_token]),
                                data=json.dumps(payload), content_type="application/json")


class ModelTests(CategoryData):
    def test_encode(self):
        self.assertEqual(self.door.encode("open"), 1.0)
        self.assertEqual(self.door.encode(" LOCKED "), 2.0)
        self.assertEqual(self.door.encode(0), 0.0)
        self.assertEqual(self.door.encode("2"), 2.0)
        for bad in ("ajar", 3, 1.5, -1):
            with self.assertRaises(ValueError):
                self.door.encode(bad)
        with self.assertRaises(ValueError):
            self.temp.encode("hot")

    def test_format_and_validation(self):
        self.assertEqual(self.door.format_value(1), "open")
        self.assertEqual(self.door.format_value(7), "#7")
        self.assertEqual(self.door.value_labels, DOOR)
        self.assertTrue(self.door.is_discrete)
        self.assertIsNone(self.mode.value_error(2))
        self.assertIsNotNone(self.mode.value_error(3))
        self.assertIsNotNone(self.mode.value_error(0.5))


class FormTests(CategoryData):
    def form(self, text, instance=None, value_type="CATEGORY"):
        data = {"key": "state", "label": "", "unit": "", "kind": "SENSOR", "value_type": value_type,
                "categories_text": text}
        if instance is not None:
            data["key"] = instance.key
        return VariableForm(data, instance=instance, device=self.device)

    def test_valid_list(self):
        form = self.form("idle\n\n running \nerror\n")
        self.assertTrue(form.is_valid(), form.errors)
        variable = form.save(commit=False)
        variable.device = self.device
        variable.save()
        self.assertEqual(variable.categories, ["idle", "running", "error"])

    def test_invalid_lists(self):
        self.assertFalse(self.form("only").is_valid())
        self.assertFalse(self.form("a\nA").is_valid())
        self.assertFalse(self.form("ok\n5").is_valid())
        self.assertFalse(self.form("\n".join(f"c{i}" for i in range(40))).is_valid())

    def test_stored_codes_keep_their_category(self):
        Telemetry.objects.create(variable=self.door, value=2, ts=timezone.now())
        form = self.form("closed\nopen", instance=self.door)
        self.assertFalse(form.is_valid())
        self.assertIn("keep at least 3", str(form.errors))
        self.assertTrue(self.form("closed\nopen\nlocked\njammed", instance=self.door).is_valid())

    def test_number_clears_categories(self):
        form = self.form("", instance=self.door, value_type="NUMBER")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().categories, [])

    def test_form_page_shows_field(self):
        response = self.client.get(reverse("devices:variable_edit", args=[self.device.pk, self.door.pk]))
        self.assertContains(response, "closed\nopen\nlocked")
        self.assertContains(response, "Categories")


class IngestionTests(CategoryData):
    def test_names_and_codes(self):
        response = self.post_telemetry({"door": "Open", "temperature": 21.5})
        self.assertEqual(response.status_code, 200, response.content)
        self.post_telemetry({"door": 2})
        self.assertEqual(list(self.door.telemetry.order_by("ts").values_list("value", flat=True)), [1.0, 2.0])

    def test_rejected_whole_payload(self):
        for payload, text in (({"door": "ajar", "temperature": 20}, "unknown category"),
                              ({"door": 3}, "whole number from 0 to 2"),
                              ({"temperature": "hot"}, "value type to Categories"),
                              ({"new_state": "on"}, "add the variable")):
            response = self.post_telemetry(payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(text, response.json()["error"])
        self.assertEqual(Telemetry.objects.count(), 0)
        self.assertFalse(Variable.objects.filter(key="new_state").exists())   # dibatalkan bersama

    def test_mqtt(self):
        from mqtt_bridge.handlers import handle_message
        from django.conf import settings

        prefix = settings.MQTT_TOPIC_PREFIX
        ok = handle_message(f"{prefix}/{self.device.access_token}/telemetry", b'{"door": "locked"}', prefix)
        self.assertTrue(ok.ok, ok.message)
        bad = handle_message(f"{prefix}/{self.device.access_token}/telemetry", b'{"door": "ajar"}', prefix)
        self.assertFalse(bad.ok)


class PageTests(CategoryData):
    def test_device_page_and_control(self):
        Telemetry.objects.create(variable=self.door, value=1, ts=timezone.now())
        page = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(page, "open")
        self.assertContains(page, 'data-labels="[&quot;closed&quot;, &quot;open&quot;, &quot;locked&quot;]"')
        self.assertContains(page, 'name="value" value="1"')      # tombol "manual" pada aktuator mode
        self.assertContains(page, ">manual</button>")
        url = reverse("devices:variable_set", args=[self.device.pk, self.mode.pk])
        self.client.post(url, {"value": "2"})
        self.mode.refresh_from_db()
        self.assertEqual(self.mode.desired_value, 2)
        self.client.post(url, {"value": "5"})
        self.mode.refresh_from_db()
        self.assertEqual(self.mode.desired_value, 2)              # ditolak

    def test_variable_page_and_export(self):
        Telemetry.objects.create(variable=self.door, value=2, ts=timezone.now())
        page = self.client.get(reverse("devices:variable_detail", args=[self.device.pk, self.door.pk]))
        self.assertContains(page, "locked")
        export = self.client.get(reverse("devices:variable_export", args=[self.device.pk, self.door.pk, "csv"]))
        text = b"".join(export.streaming_content).decode("utf-8-sig")
        self.assertIn("category", text.splitlines()[0])
        self.assertIn("locked", text)


class RuleTests(CategoryData):
    def make_rule(self):
        rule = Rule.objects.create(owner=self.siti, name="Door open")
        spec = EcaSpec.objects.create(rule=rule)
        EcaCondition.objects.create(spec=spec, variable=self.door, operator="==", threshold=1)
        RuleAction.objects.create(rule=rule, kind="SET_ACTUATOR", target_variable=self.mode, value=2)
        RuleAction.objects.create(rule=rule, kind="ALARM", severity="MAJOR", message="{variable} is {value}")
        return rule

    def test_engine_and_texts(self):
        rule = self.make_rule()
        now = timezone.now()
        Telemetry.objects.create(variable=self.door, value=1, ts=now)
        queue.process(queue.telemetry_message(self.device, [(self.door, 1.0, now)]), now=now)
        self.assertEqual(RuleExecution.objects.get(rule=rule).transition, "ACTIVATE")
        self.mode.refresh_from_db()
        self.assertEqual(self.mode.desired_value, 2)
        self.assertEqual(rule.alarms.get().message, "Door is open")
        page = self.client.get(reverse("rules:detail", args=[rule.pk]))
        self.assertContains(page, "= open")           # ekspresi dan log memakai label
        self.assertContains(page, "<strong>off</strong>")

    def test_form_options_and_validation(self):
        page = self.client.get(reverse("rules:new_model", args=["eca"]))
        self.assertContains(page, 'data-category-target="threshold"')
        self.assertContains(page, 'data-labels="[&quot;closed&quot;, &quot;open&quot;, &quot;locked&quot;]"')
        self.assertContains(page, "category_inputs")
        from rules.forms import RuleActionForm

        form = RuleActionForm({"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "target_variable": self.mode.pk,
                               "value": "4", "ttl_seconds": "300"}, devices=Device.objects.all())
        self.assertFalse(form.is_valid())
        self.assertIn("Choose one of the categories", str(form.errors))
