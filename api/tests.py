"""
Tes API telemetri.

Client(enforce_csrf_checks=True) dipakai supaya tes membuktikan endpoint ini
memang bisa dipanggil tanpa token CSRF (client tes biasa melewati CSRF).
"""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from devices.models import Device, Telemetry, Variable

User = get_user_model()


class TelemetryApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="ESP32 Node")
        cls.lamp = Variable.objects.create(
            device=cls.device, key="lamp", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )

    def setUp(self):
        self.api = Client(enforce_csrf_checks=True)

    def post(self, payload, token=None, raw=None):
        url = reverse("api:telemetry", args=[token or self.device.access_token])
        body = raw if raw is not None else json.dumps(payload)
        return self.api.post(url, data=body, content_type="application/json")

    def values(self, key):
        return list(
            Telemetry.objects.filter(variable__device=self.device, variable__key=key)
            .order_by("ts").values_list("value", flat=True)
        )

    # --- berhasil ---

    def test_simple_payload_is_saved_and_new_variables_created(self):
        response = self.post({"temperature": 25.1, "humidity": 60})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"saved": 2, "new_variables": ["temperature", "humidity"]})
        self.assertEqual(self.values("temperature"), [25.1])
        created = Variable.objects.get(device=self.device, key="humidity")
        self.assertEqual(created.kind, Variable.Kind.SENSOR)

    def test_existing_variable_is_reused(self):
        self.post({"temperature": 25})
        response = self.post({"temperature": 26})
        self.assertEqual(response.json()["new_variables"], [])
        self.assertEqual(self.values("temperature"), [25, 26])

    def test_payload_with_timestamp(self):
        ts = timezone.now() - timedelta(hours=1)
        ms = int(ts.timestamp() * 1000)
        self.post({"ts": ms, "values": {"temperature": 20}})
        saved = Telemetry.objects.get(variable__key="temperature")
        self.assertLess(abs((saved.ts - ts).total_seconds()), 0.01)

    def test_list_payload(self):
        now_ms = int(timezone.now().timestamp() * 1000)
        response = self.post([
            {"ts": now_ms - 60000, "values": {"temperature": 20}},
            {"ts": now_ms, "values": {"temperature": 21}},
        ])
        self.assertEqual(response.json()["saved"], 2)
        self.assertEqual(self.values("temperature"), [20, 21])

    def test_url_with_trailing_slash_also_works(self):
        url = reverse("api:telemetry", args=[self.device.access_token]) + "/"
        response = self.api.post(url, data='{"t": 1}', content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_boolean_becomes_actuator_report(self):
        self.lamp.set_desired(1, self.budi)
        self.post({"lamp": True})
        self.lamp.refresh_from_db()
        self.assertEqual(self.values("lamp"), [1])
        # Laporan device lewat API membuat aktuator "In sync".
        self.assertEqual(self.lamp.sync_status, Variable.SyncStatus.SYNCED)

    # --- autentikasi ---

    def test_unknown_token_is_rejected(self):
        response = self.post({"t": 1}, token="not-a-real-token")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(Telemetry.objects.exists())

    def test_inactive_device_is_rejected(self):
        Device.objects.filter(pk=self.device.pk).update(is_active=False)
        response = self.post({"t": 1})
        self.assertEqual(response.status_code, 403)

    def test_only_post_is_allowed(self):
        url = reverse("api:telemetry", args=[self.device.access_token])
        self.assertEqual(self.api.get(url).status_code, 405)

    def test_regenerated_token_replaces_old_one(self):
        old_token = self.device.access_token
        self.client.force_login(self.budi)
        self.client.post(reverse("devices:token_regenerate", args=[self.device.pk]))
        self.device.refresh_from_db()
        self.assertNotEqual(self.device.access_token, old_token)
        self.assertEqual(self.post({"t": 1}, token=old_token).status_code, 401)
        self.assertEqual(self.post({"t": 1}).status_code, 200)

    def test_tokens_are_unique_and_url_safe(self):
        other = Device.objects.create(owner=self.budi, name="Other")
        self.assertNotEqual(other.access_token, self.device.access_token)
        self.assertRegex(other.access_token, r"^[A-Za-z0-9_-]{20}$")

    # --- validasi payload ---

    def test_invalid_json_is_rejected(self):
        response = self.post(None, raw="{temperature: 25}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid JSON", response.json()["error"])

    def test_nan_is_rejected(self):
        response = self.post(None, raw='{"t": NaN}')
        self.assertEqual(response.status_code, 400)

    def test_text_value_is_rejected_and_nothing_saved(self):
        response = self.post({"temperature": 25, "status": "ok"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.json()["error"])
        # Payload ditolak utuh: nilai yang valid pun tidak ikut tersimpan.
        self.assertFalse(Telemetry.objects.exists())
        self.assertFalse(Variable.objects.filter(key="temperature").exists())

    def test_invalid_key_is_rejected(self):
        self.assertEqual(self.post({"soil moisture": 40}).status_code, 400)

    def test_seconds_instead_of_milliseconds_is_explained(self):
        seconds = int(timezone.now().timestamp())
        response = self.post({"ts": seconds, "values": {"t": 1}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("milliseconds", response.json()["error"])


class DeviceTokenPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="ESP32 Node")

    def test_owner_sees_token_and_endpoint(self):
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(response, self.device.access_token)
        self.assertContains(response, f"/api/v1/{self.device.access_token}/telemetry")

    def test_other_user_cannot_regenerate_token(self):
        self.client.force_login(self.siti)
        old_token = self.device.access_token
        response = self.client.post(reverse("devices:token_regenerate", args=[self.device.pk]))
        self.assertEqual(response.status_code, 404)
        self.device.refresh_from_db()
        self.assertEqual(self.device.access_token, old_token)
