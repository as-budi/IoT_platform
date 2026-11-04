"""Tes perintah fill_test_macs (MAC tiruan untuk mencoba micro rule)."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from devices.management.commands.fill_test_macs import token_mac
from devices.models import Device, Variable
from rules.models import EcaCondition, EcaSpec, EdgePlan, Rule, RuleAction

User = get_user_model()


class FillTestMacsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.sensor = Device.objects.create(owner=cls.siti, name="Sensor")
        cls.pump = Device.objects.create(owner=cls.siti, name="Pump")
        cls.real = Device.objects.create(owner=cls.siti, name="Real", mac_address="24:6F:28:AA:BB:CC")
        cls.other = Device.objects.create(owner=cls.budi, name="Other")

    def run_command(self, *args):
        out = StringIO()
        call_command("fill_test_macs", *args, stdout=out)
        return out.getvalue()

    @override_settings(DEBUG=True)
    def test_fills_only_missing(self):
        self.run_command("--owner", "siti")
        self.sensor.refresh_from_db()
        self.real.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.sensor.mac_address, token_mac(self.sensor.access_token))
        self.assertTrue(self.sensor.mac_address.startswith("02:"))
        self.assertIsNone(self.sensor.mac_reported_at)
        self.assertEqual(self.real.mac_address, "24:6F:28:AA:BB:CC")
        self.assertEqual(self.other.mac_address, "")

    @override_settings(DEBUG=True)
    def test_random_all_and_dry_run(self):
        self.run_command("--dry-run", "--all")
        self.assertEqual(Device.objects.exclude(mac_address="").count(), 1)
        self.run_command("--all", "--random", "--device", "Real")
        self.real.refresh_from_db()
        self.assertNotEqual(self.real.mac_address, "24:6F:28:AA:BB:CC")
        self.assertRegex(self.real.mac_address, r"^02(:[0-9A-F]{2}){5}$")

    @override_settings(DEBUG=False)
    def test_production_needs_yes(self):
        with self.assertRaises(CommandError):
            self.run_command()
        self.run_command("--yes")
        self.assertFalse(Device.objects.filter(mac_address="").exists())

    @override_settings(DEBUG=True)
    def test_edge_plans_updated(self):
        soil = Variable.objects.create(device=self.sensor, key="soil")
        pump = Variable.objects.create(device=self.pump, key="pump", kind=Variable.Kind.ACTUATOR)
        rule = Rule.objects.create(owner=self.siti, name="R", edge_enabled=True)
        spec = EcaSpec.objects.create(rule=rule)
        EcaCondition.objects.create(spec=spec, variable=soil, operator="<", threshold=20)
        RuleAction.objects.create(rule=rule, kind="SET_ACTUATOR", target_variable=pump, value=1)
        output = self.run_command()
        self.assertIn("1 rule(s)", output)
        plan = EdgePlan.objects.get(rule=rule, strategy="actuator", is_current=True)
        self.assertEqual(plan.verdict, "FULL")
