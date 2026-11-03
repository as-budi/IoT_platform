"""Tes tahap 24: unduhan template firmware ESP32 (panduannya kini di userdocs, tahap 25)."""
import io
import re
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from sharing.models import DeviceShare

from . import connect
from .models import Device

User = get_user_model()


@override_settings(
    OTA_BASE_URL="http://192.168.1.10:8000", MQTT_PUBLIC_HOST="iot.example.org", MQTT_PUBLIC_PORT=8883,
    MQTT_PUBLIC_TLS=True, MQTT_TOPIC_PREFIX="kampus-iot", COAP_PUBLIC_HOST="192.168.1.10", COAP_PORT=5683,
)
class ConnectGuideTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.siti, name="Greenhouse Node")
        cls.other = Device.objects.create(owner=cls.budi, name="Budi Node")

    def setUp(self):
        self.client.force_login(self.siti)

    def download(self, protocol, device=None):
        url = reverse("devices:connect_firmware", args=[protocol])
        return self.client.get(url, {"device": device.pk} if device else {})

    def unzip(self, response):
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        return {name: archive.read(name).decode() for name in archive.namelist()}

    def test_old_guide_address_redirects_to_documentation(self):
        response = self.client.get("/devices/connect/", {"device": self.device.pk})
        self.assertRedirects(response, f"/docs/connect-esp32/?device={self.device.pk}")

    def test_downloads_fill_in_server_settings_and_token(self):
        expected = {
            "http": ['const char* SERVER       = "http://192.168.1.10:8000";'],
            "mqtt": ['const char* MQTT_HOST     = "iot.example.org";', "const uint16_t MQTT_PORT  = 8883;",
                     'const char* TOPIC_PREFIX  = "kampus-iot";', "#define USE_TLS 1"],
            "coap": ['const char* SERVER_HOST   = "192.168.1.10";', "const int   SERVER_PORT   = 5683;"],
        }
        for protocol, lines in expected.items():
            with self.subTest(protocol=protocol):
                response = self.download(protocol, self.device)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/zip")
                folder = f"esp32-{protocol}-greenhouse-node"
                self.assertIn(f'filename="{folder}.zip"', response["Content-Disposition"])
                files = self.unzip(response)
                sketch = connect.PROTOCOLS[protocol]["sketch"]
                source = files[f"{folder}/{sketch}/{sketch}.ino"]
                for line in lines:
                    self.assertIn(line, source)
                self.assertRegex(source, rf'ACCESS_TOKEN\s*=\s*"{self.device.access_token}";')
                self.assertIn(f"{folder}/README.txt", files)
                self.assertIn("BEGIN CERTIFICATE", files[f"{folder}/isrg-root-x1.pem"])
                self.assertIn("WiFi.macAddress()", source)   # laporan MAC (tahap 24)
                if "ROOT_CA" in source:
                    self.assertNotIn("paste ISRG Root X1 here", source)
                    self.assertIn("MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAw", source)

    def test_download_without_device_keeps_placeholder(self):
        files = self.unzip(self.download("mqtt"))
        source = files["esp32-mqtt-template/esp32_mqtt/esp32_mqtt.ino"]
        self.assertIn(connect.TOKEN_PLACEHOLDER, source)
        self.assertIn("and ACCESS_TOKEN below", source)

    def test_download_rules(self):
        self.assertEqual(self.download("lora").status_code, 404)
        self.assertEqual(self.download("http", self.other).status_code, 404)       # bukan miliknya
        self.assertEqual(self.client.get(reverse("devices:connect_firmware", args=["http"]),
                                         {"device": "abc"}).status_code, 404)
        # Viewer tidak boleh mendapatkan token; Editor boleh (tahap 19).
        share = DeviceShare.objects.create(device=self.other, user=self.siti, role=DeviceShare.Role.VIEWER,
                                             created_by=self.budi)
        self.assertEqual(self.download("http", self.other).status_code, 404)
        share.role = DeviceShare.Role.EDITOR
        share.save()
        self.assertEqual(self.download("http", self.other).status_code, 200)
        self.client.logout()
        self.assertEqual(self.download("http").status_code, 302)

    def test_root_ca_download(self):
        response = self.client.get(reverse("devices:connect_root_ca"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"-----BEGIN CERTIFICATE-----", response.content)
        self.assertIn('filename="isrg-root-x1.pem"', response["Content-Disposition"])

    def test_c_strings_are_escaped(self):
        text = connect.render_sketch("http", {"SERVER": 'http://a"b\\c'})
        self.assertIn(r'const char* SERVER       = "http://a\"b\\c";', text)

    def test_every_setting_matches_once(self):
        for protocol, info in connect.PROTOCOLS.items():
            values = {"http": {"SERVER": "x"}, "mqtt": {"MQTT_HOST": "x", "MQTT_PORT": 1, "TOPIC_PREFIX": "x",
                      "USE_TLS": 0}, "coap": {"SERVER_HOST": "x", "SERVER_PORT": 1}}[protocol]
            text = connect.render_sketch(protocol, values, token="T0KEN")
            self.assertEqual(len(re.findall(r'ACCESS_TOKEN\s*=\s*"T0KEN"', text)), 1)
        with self.assertRaises(connect.TemplateMismatch):
            connect.render_sketch("http", {"NOT_THERE": "x"})

    def test_device_page_links_to_guide(self):
        page = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(page, f"/docs/connect-esp32/?device={self.device.pk}")
        self.assertContains(page, "MAC address")
