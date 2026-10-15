"""Tes `manage.py mqtt_accounts` (akun broker Mosquitto untuk server produksi)."""
import os
import shutil
import stat
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from devices.models import Device

# Pengganti mosquitto_passwd untuk tes: "-U file" mengganti setiap password teks
# dengan tanda hash palsu, seperti program aslinya (yang memakai PBKDF2-SHA512).
FAKE_PASSWD = """#!/bin/sh
[ "$1" = "-U" ] || exit 2
sed -i 's/^\\([^:]*\\):\\(.*\\)$/\\1:$7$fakehash-\\2/' "$2"
"""


@override_settings(MQTT_USERNAME="iot-bridge", MQTT_PASSWORD="bridge-secret", MQTT_TOPIC_PREFIX="iot")
class MqttAccountsTests(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mqtt-accounts-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.tool = self.dir / "mosquitto_passwd"
        self.tool.write_text(FAKE_PASSWD)
        self.tool.chmod(0o755)
        owner = get_user_model().objects.create_user("siti", password="rahasia-12345")
        self.a = Device.objects.create(owner=owner, name="A")
        self.b = Device.objects.create(owner=owner, name="B", is_active=False)

    def run_command(self):
        out = StringIO()
        call_command("mqtt_accounts", "--passwd-file", str(self.dir / "passwd"), "--acl-file",
                     str(self.dir / "acl"), "--mosquitto-passwd", str(self.tool), stdout=out)
        return out.getvalue()

    def test_accounts_and_acl(self):
        self.assertIn("Wrote 1 device account(s)", self.run_command())
        lines = (self.dir / "passwd").read_text().splitlines()
        self.assertEqual(lines, ["iot-bridge:$7$fakehash-bridge-secret",
                                 f"{self.a.access_token}:$7$fakehash-{self.a.access_token}"])
        self.assertNotIn(self.b.access_token, (self.dir / "passwd").read_text(), "inactive devices get no account")
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "passwd").st_mode), 0o640)
        acl = (self.dir / "acl").read_text()
        self.assertIn("user iot-bridge\ntopic readwrite iot/#", acl)
        self.assertIn("pattern write iot/%u/telemetry", acl)
        self.assertIn("pattern read  iot/%u/commands/+", acl)
        self.assertNotIn("pattern readwrite", acl, "devices never get blanket access")

    def test_rewrites_only_on_change(self):
        self.run_command()
        self.assertIn("No changes", self.run_command())
        self.a.regenerate_token()
        self.assertIn("Wrote 1", self.run_command())
        self.assertIn(self.a.access_token, (self.dir / "passwd").read_text())

    def test_configuration_errors(self):
        with override_settings(MQTT_PASSWORD=""), self.assertRaisesMessage(CommandError, "MQTT_PASSWORD"):
            self.run_command()
        with override_settings(MQTT_TOPIC_PREFIX="iot/#"), self.assertRaisesMessage(CommandError, "must not contain"):
            self.run_command()
        with self.assertRaisesMessage(CommandError, "mosquitto_passwd not found"):
            call_command("mqtt_accounts", "--passwd-file", str(self.dir / "p"), "--acl-file", str(self.dir / "a"),
                         "--mosquitto-passwd", "no-such-program", stdout=StringIO())
