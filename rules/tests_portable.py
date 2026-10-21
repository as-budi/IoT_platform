"""Tes tahap 13: ekspor dan impor aturan ECA dan rule chain (FLOW) sebagai JSON."""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from devices.models import Variable

from .models import EcaCondition, FlowLink, FlowNode, Rule, RuleAction, RuleExecution, RuleModel
from .portable import export_rule
from .tests import MultiVariableData
from .tests_flow import FlowData


def without_timestamp(data):
    return {k: v for k, v in data.items() if k != "exported_at"}


class PortableRuleTests(MultiVariableData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.fan = Variable.objects.create(
            device=cls.node, key="fan_speed", kind=Variable.Kind.ACTUATOR, min_value=0, max_value=100,
        )

    def setUp(self):
        self.client.force_login(self.siti)
        # (soil < 20 AND rain < 1) OR temperature > 35; clear bila soil > 30. Empat jenis aksi.
        self.rule = self.make_rule(
            when=[[(self.soil, "<", 20), (self.rain, "<", 1)], [(self.temp, ">", 35)]],
            actions=[
                {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 1},
                {"kind": RuleAction.Kind.SEND_COMMAND, "target_device": self.node, "method": "blink",
                 "params": {"times": 3}, "ttl_seconds": 60},
                {"kind": RuleAction.Kind.ALARM, "severity": "WARNING", "message": "Dry: {values}"},
                {"kind": RuleAction.Kind.SET_ACTUATOR, "target_variable": self.pump, "value": 0,
                 "run_on": RuleAction.RunOn.CLEAR},
            ],
        )
        self.rule.description = "Water the greenhouse"
        self.rule.save()

    def post_import(self, payload, url=None, **extra):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return self.client.post(url or reverse("rules:import"), {"text": text, **extra})

    # --- ekspor ---

    def test_export_uses_names_and_groups(self):
        data = export_rule(self.rule)
        self.assertEqual((data["format"], data["version"], data["model"]), ("iot-platform.rule", 1, "ECA"))
        self.assertEqual(data["eca"]["when"], [
            [{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": "<", "threshold": 20},
             {"device": "Weather station", "variable": "rain", "operator": "<", "threshold": 1}],
            [{"device": "Greenhouse Node", "variable": "temperature", "operator": ">", "threshold": 35}],
        ])
        self.assertEqual(data["eca"]["clear"], [
            [{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": ">", "threshold": 30}],
        ])
        self.assertEqual(data["actions"][0], {
            "kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "Greenhouse Node", "variable": "pump", "value": 1,
        })
        self.assertEqual(data["actions"][1], {
            "kind": "SEND_COMMAND", "run_on": "ACTIVATE", "device": "Greenhouse Node", "method": "blink",
            "params": {"times": 3}, "ttl_seconds": 60,
        })
        self.assertNotIn("owner", data)

    def test_json_view_and_download(self):
        response = self.client.get(reverse("rules:json", args=[self.rule.pk]))
        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")
        self.assertNotIn("Content-Disposition", response)
        self.assertEqual(json.loads(response.content)["name"], "Irrigation")
        response = self.client.get(reverse("rules:json", args=[self.rule.pk]), {"download": "1"})
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="rule-irrigation.json"')

    def test_detail_page_shows_json_card_and_buttons(self):
        response = self.client.get(reverse("rules:detail", args=[self.rule.pk]))
        self.assertContains(response, reverse("rules:import_into", args=[self.rule.pk]))
        self.assertContains(response, f'{reverse("rules:json", args=[self.rule.pk])}?download=1')
        self.assertContains(response, "&quot;soil_moisture&quot;")   # isi JSON di kartu, di-escape HTML

    def test_other_users_and_planned_models_cannot_be_exported(self):
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(reverse("rules:json", args=[self.rule.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("rules:import_into", args=[self.rule.pk])).status_code, 404)
        planned = Rule.objects.create(owner=self.budi, name="Streaming", model=RuleModel.SQL)
        self.assertEqual(self.client.get(reverse("rules:json", args=[planned.pk])).status_code, 404)

    # --- impor ---

    def test_round_trip_creates_identical_rule(self):
        exported = export_rule(self.rule)
        response = self.post_import(exported)
        new = Rule.objects.exclude(pk=self.rule.pk).get()
        self.assertRedirects(response, reverse("rules:detail", args=[new.pk]))
        self.assertEqual(new.owner, self.siti)
        self.assertEqual(without_timestamp(export_rule(new)), without_timestamp(exported))

    def test_file_upload(self):
        upload = SimpleUploadedFile("rule.json", json.dumps(export_rule(self.rule)).encode(), "application/json")
        response = self.client.post(reverse("rules:import"), {"file": upload, "text": ""})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Rule.objects.count(), 2)

    def test_replace_existing_rule(self):
        RuleExecution.objects.create(rule=self.rule, transition="ACTIVATE")
        data = export_rule(self.rule)
        data["name"] = "Irrigation v2"
        data["eca"]["when"] = [[{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": "<", "threshold": 15}]]
        data["eca"]["clear"] = []
        data["actions"] = [{"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "Greenhouse Node",
                            "variable": "fan_speed", "value": 50}]
        response = self.post_import(data, reverse("rules:import_into", args=[self.rule.pk]))
        self.assertRedirects(response, reverse("rules:detail", args=[self.rule.pk]))
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.name, "Irrigation v2")
        self.assertEqual(Rule.objects.count(), 1)
        conditions = list(self.rule.eca.conditions.values_list("role", "threshold"))
        self.assertEqual(conditions, [(EcaCondition.Role.WHEN, 15)])
        self.assertEqual(list(self.rule.actions.values_list("target_variable__key", "value")), [("fan_speed", 50)])
        self.assertEqual(self.rule.executions.count(), 1)   # riwayat tetap

    def test_rejected_import_saves_nothing(self):
        cases = {
            "not json": "Not valid JSON",
            json.dumps({"format": "x"}): "&quot;format&quot; must be",
            json.dumps({**export_rule(self.rule), "model": "SQL"}): "must be &quot;ECA&quot;, &quot;FLOW&quot; or &quot;STATE_MACHINE&quot;",
            json.dumps({**export_rule(self.rule), "model": "FLOW"}): "&quot;flow&quot; must be an object",
        }
        for text, message in cases.items():
            self.assertContains(self.post_import(text), message)
        self.assertEqual(Rule.objects.count(), 1)

    def test_names_must_match_owners_devices(self):
        data = export_rule(self.rule)
        data["eca"]["when"][0][0]["device"] = "Budi's node"          # milik orang lain
        data["eca"]["when"][1][0]["variable"] = "humidity"            # tidak ada
        response = self.post_import(data)
        self.assertContains(response, "no device called &quot;Budi&#x27;s node&quot;")
        self.assertContains(response, "has no variable &quot;humidity&quot;")
        self.assertEqual(Rule.objects.count(), 1)

    def test_form_validation_still_applies(self):
        data = export_rule(self.rule)
        data["actions"] = [{"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "Greenhouse Node",
                            "variable": "fan_speed", "value": 150}]
        self.assertContains(self.post_import(data), "Must be at most 100")
        data = export_rule(self.rule)
        data["eca"]["when"] = [[{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": "<", "threshold": 20}]]
        data["eca"]["clear"] = [[{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": ">", "threshold": 10}]]
        self.assertContains(self.post_import(data), "Clear threshold must be at least 20")
        # Sensor (bukan aktuator) tidak boleh menjadi target aksi.
        data = export_rule(self.rule)
        data["actions"][0]["variable"] = "soil_moisture"
        self.assertContains(self.post_import(data), "Action 1 target_variable")
        self.assertEqual(Rule.objects.count(), 1)


class FlowPortableTests(FlowData):
    """Rule chain: node dan panah lewat nama, impor lewat form rule chain."""

    def setUp(self):
        self.client.force_login(self.siti)
        # Input -> hot (CHECK) -TRUE-> fan_on (SET_ACTUATOR), -FALSE-> outside (LATEST) -SUCCESS-> mail (EMAIL)
        self.rule = self.chain(
            [
                ("hot", "CHECK", {"key": "temperature", "operator": ">", "threshold": 30}),
                ("fan_on", "SET_ACTUATOR", {"target_variable": self.fan, "value": 1}),
                ("outside", "LATEST", {"variable": self.rain, "key": "rain", "seconds": 600}),
                ("limit", "THROTTLE", {"seconds": 300}),
                ("mail", "EMAIL", {"message": "Rain {rain}"}),
            ],
            [
                ("Input", "SUCCESS", "hot"),
                ("hot", "TRUE", "fan_on"),
                ("hot", "FALSE", "outside"),
                ("outside", "SUCCESS", "limit"),
                ("limit", "TRUE", "mail"),
            ],
        )
        FlowNode.objects.filter(rule=self.rule, name="limit").update(memory={"1": {"last": 123}})

    def post_import(self, payload, url=None):
        return self.client.post(url or reverse("rules:import"), {"text": json.dumps(payload)})

    def test_export_nodes_and_links(self):
        data = export_rule(self.rule)
        self.assertEqual(data["model"], "FLOW")
        self.assertNotIn("eca", data)
        self.assertEqual(data["flow"]["device"], "Greenhouse Node")
        nodes = {node["name"]: node for node in data["flow"]["nodes"]}
        self.assertEqual(nodes["hot"], {"name": "hot", "kind": "CHECK", "key": "temperature", "operator": ">", "threshold": 30})
        self.assertEqual(nodes["fan_on"], {"name": "fan_on", "kind": "SET_ACTUATOR", "device": "Greenhouse Node",
                                           "variable": "fan", "value": 1})
        self.assertEqual(nodes["outside"], {"name": "outside", "kind": "LATEST", "device": "Weather Station",
                                            "variable": "rain", "key": "rain", "seconds": 600})
        self.assertNotIn("memory", nodes["limit"])
        self.assertEqual(data["flow"]["links"][0], {"source": "Input", "label": "SUCCESS", "target": "hot"})
        self.assertEqual(len(data["flow"]["links"]), 5)

    def test_detail_page_shows_json_card_and_buttons(self):
        response = self.client.get(reverse("rules:detail", args=[self.rule.pk]))
        self.assertContains(response, reverse("rules:import_into", args=[self.rule.pk]))
        self.assertContains(response, "&quot;SET_ACTUATOR&quot;")

    def test_round_trip_creates_identical_chain(self):
        exported = export_rule(self.rule)
        response = self.post_import(exported)
        new = Rule.objects.exclude(pk=self.rule.pk).get()
        self.assertRedirects(response, reverse("rules:detail", args=[new.pk]))
        self.assertEqual((new.model, new.owner), (RuleModel.FLOW, self.siti))
        self.assertEqual(without_timestamp(export_rule(new)), without_timestamp(exported))
        self.assertEqual(new.links.count(), 5)

    def test_replace_existing_chain(self):
        data = export_rule(self.rule)
        data["flow"]["nodes"] = [{"name": "cold", "kind": "CHECK", "key": "temperature", "operator": "<", "threshold": 5},
                                 {"name": "fan_off", "kind": "SET_ACTUATOR", "device": "Greenhouse Node",
                                  "variable": "fan", "value": 0}]
        data["flow"]["links"] = [{"source": "Input", "label": "SUCCESS", "target": "cold"},
                                 {"source": "cold", "label": "TRUE", "target": "fan_off"}]
        response = self.post_import(data, reverse("rules:import_into", args=[self.rule.pk]))
        self.assertRedirects(response, reverse("rules:detail", args=[self.rule.pk]))
        self.assertEqual(list(self.rule.nodes.values_list("name", flat=True)), ["cold", "fan_off"])
        self.assertEqual(FlowLink.objects.filter(rule=self.rule).count(), 2)
        self.assertEqual(Rule.objects.count(), 1)

    def test_model_must_match_when_replacing(self):
        eca = {"format": "iot-platform.rule", "version": 1, "model": "ECA", "name": "x",
               "eca": {}, "actions": []}
        response = self.post_import(eca, reverse("rules:import_into", args=[self.rule.pk]))
        self.assertContains(response, "This is a FLOW rule, but the JSON is a ECA rule")

    def test_graph_and_node_validation_still_apply(self):
        data = export_rule(self.rule)
        data["flow"]["links"].append({"source": "mail", "label": "SUCCESS", "target": "hot"})      # siklus
        self.assertContains(self.post_import(data), "loop")
        data = export_rule(self.rule)
        data["flow"]["links"].append({"source": "hot", "label": "TRUE", "target": "nowhere"})
        self.assertContains(self.post_import(data), "there is no node called")
        data = export_rule(self.rule)
        data["flow"]["nodes"][1]["value"] = 5                                                      # aktuator on/off
        self.assertContains(self.post_import(data), "accept 1 (ON) or 0 (OFF)")
        data = export_rule(self.rule)
        data["flow"]["nodes"][0]["kind"] = "TELEPORT"
        self.assertContains(self.post_import(data), "Unknown node kind TELEPORT")
        data = export_rule(self.rule)
        data["flow"]["nodes"][2]["device"] = "Budi's node"   # milik orang lain
        self.assertContains(self.post_import(data), "no device called")
        self.assertEqual(Rule.objects.count(), 1)
