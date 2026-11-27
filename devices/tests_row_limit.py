"""Tes batas baris telemetri per variabel (devices/row_limit.py, docs/tahap-27-tambahan-batas-baris-variabel.md)."""
import io
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from activity.models import ActivityEvent
from api.services import save_telemetry
from coap_gateway import handlers as coap
from mqtt_bridge import topics
from mqtt_bridge.handlers import handle_message

from .models import Device, Telemetry, TelemetryRollup, Variable
from .row_limit import VariableFull, recount

User = get_user_model()


# Batas interval 10 detik dimatikan: di sini setiap tes mengirim beberapa pesan berturut-turut.
@override_settings(TELEMETRY_MIN_INTERVAL_SECONDS=0, TELEMETRY_MAX_ROWS_PER_VARIABLE=3)
class RowLimitData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi")
        cls.temperature = Variable.objects.create(device=cls.device, key="temperature")
        cls.humidity = Variable.objects.create(device=cls.device, key="humidity")
        cls.lamp = Variable.objects.create(
            device=cls.device, key="lamp", kind=Variable.Kind.ACTUATOR, value_type=Variable.ValueType.BOOLEAN,
        )

    def post(self, payload):
        url = reverse("api:telemetry", args=[self.device.access_token])
        return Client().post(url, data=json.dumps(payload), content_type="application/json")

    def rows(self, variable):
        variable.refresh_from_db()
        return variable.telemetry_rows

    def fill(self, variable, n):
        for i in range(n):
            save_telemetry(self.device, [(variable.key, i, timezone.now())])


class EnforcementTests(RowLimitData):
    def test_counter_follows_saved_rows(self):
        self.post({"temperature": 1, "humidity": 2})
        self.assertEqual((self.rows(self.temperature), self.rows(self.humidity)), (1, 1))
        now = int(timezone.now().timestamp() * 1000)
        # Satu pesan, dua bacaan untuk variabel yang sama -> +2.
        self.post([{"ts": now - 1000, "values": {"temperature": 3}}, {"ts": now, "values": {"temperature": 4}}])
        self.assertEqual(self.rows(self.temperature), 3)

    def test_full_variable_refuses_the_whole_message_with_409(self):
        self.fill(self.temperature, 3)
        response = self.post({"temperature": 9, "humidity": 50})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["full"], ["temperature"])
        self.assertEqual(response.json()["limit"], 3)
        self.assertIn("is full (3 rows", response.json()["error"])
        # Seluruh pesan ditolak: humidity juga tidak tersimpan, penghitungnya tidak berubah.
        self.assertFalse(Telemetry.objects.filter(variable=self.humidity).exists())
        self.assertEqual((self.rows(self.temperature), self.rows(self.humidity)), (3, 0))

    def test_message_that_would_go_over_the_limit_is_refused(self):
        self.fill(self.temperature, 2)
        now = int(timezone.now().timestamp() * 1000)
        response = self.post([{"ts": now - 1000, "values": {"temperature": 1}}, {"ts": now, "values": {"temperature": 2}}])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.rows(self.temperature), 2)
        self.assertEqual(self.post({"temperature": 3}).status_code, 200)      # tepat 3: masih boleh
        self.assertEqual(self.rows(self.temperature), 3)

    def test_actuators_are_limited_too(self):
        self.fill(self.lamp, 3)
        self.assertEqual(self.post({"lamp": 1}).status_code, 409)

    def test_new_variable_created_by_a_refused_message_is_rolled_back(self):
        self.fill(self.temperature, 3)
        self.post({"temperature": 1, "pressure": 1000})
        self.assertFalse(Variable.objects.filter(device=self.device, key="pressure").exists())

    def test_limit_per_account(self):
        User.objects.filter(pk=self.budi.pk).update(telemetry_max_rows=5)
        self.fill(self.temperature, 5)
        with self.assertRaises(VariableFull):
            self.fill(self.temperature, 1)
        User.objects.filter(pk=self.budi.pk).update(telemetry_max_rows=0)      # 0 = tanpa batas
        self.fill(self.temperature, 2)
        self.assertEqual(self.rows(self.temperature), 7)

    @override_settings(TELEMETRY_MAX_ROWS_PER_VARIABLE=0)
    def test_zero_default_means_no_limit_but_still_counts(self):
        self.fill(self.temperature, 5)
        self.assertEqual(self.rows(self.temperature), 5)

    def test_reaching_the_limit_is_logged_once(self):
        self.fill(self.temperature, 3)
        for _ in range(2):
            self.post({"temperature": 1})
        events = ActivityEvent.objects.filter(verb="variable.full", device=self.device)
        self.assertEqual(events.count(), 1)
        self.assertIn("temperature reached its limit of 3 rows", events.get().message)

    def test_mqtt_reports_full_variable_on_errors(self):
        self.fill(self.temperature, 3)
        result = handle_message(topics.telemetry("p", self.device.access_token), b'{"temperature": 1}', "p")
        self.assertFalse(result.ok)
        self.assertEqual(result.device, self.device)
        self.assertIn("Variable temperature is full", result.message)

    def test_coap_answers_4_09(self):
        self.fill(self.temperature, 3)
        reply = coap.handle("POST", [self.device.access_token, "telemetry"], b'{"temperature": 1}')
        self.assertEqual(reply.code, coap.CONFLICT)
        self.assertEqual(reply.body["full"], ["temperature"])

    def test_saving_a_variable_does_not_overwrite_the_counter(self):
        stale = Variable.objects.get(pk=self.temperature.pk)       # dibaca sebelum data masuk
        self.fill(self.temperature, 2)
        stale.label = "Temperature"
        stale.save()
        self.assertEqual(self.rows(self.temperature), 2)

    def test_recount_command(self):
        self.fill(self.temperature, 2)
        Telemetry.objects.filter(variable=self.temperature).delete()        # "SQL manual"
        Variable.objects.filter(pk=self.humidity.pk).update(telemetry_rows=99)
        out = io.StringIO()
        call_command("recount_telemetry", stdout=out)
        self.assertIn("2 corrected", out.getvalue())
        self.assertEqual((self.rows(self.temperature), self.rows(self.humidity)), (0, 0))
        self.assertEqual(recount(), 0)


class DeleteDataTests(RowLimitData):
    def setUp(self):
        self.client.force_login(self.budi)
        self.url = reverse("devices:variable_data_delete", args=[self.device.pk, self.temperature.pk])

    def test_variable_page_shows_usage_and_button(self):
        self.fill(self.temperature, 3)
        page = self.client.get(reverse("devices:variable_detail", args=[self.device.pk, self.temperature.pk]))
        self.assertContains(page, "3 / 3 rows stored")
        self.assertContains(page, ">Full<")
        self.assertContains(page, 'id="delete-data"')

    def test_confirm_page_downloads_exactly_the_rows_it_deletes(self):
        self.fill(self.temperature, 2)
        last = Telemetry.objects.filter(variable=self.temperature).order_by("-pk").first()
        page = self.client.get(self.url)
        self.assertContains(page, f'name="up_to" value="{last.pk}"')
        self.assertContains(page, "Download CSV and delete")
        self.assertContains(page, "js/delete_data.js")
        # Data baru setelah halaman dibuka: tidak ada di CSV dan tidak ikut dihapus.
        self.fill(self.temperature, 1)
        csv_text = b"".join(self.client.get(page.context["csv_url"]).streaming_content).decode("utf-8-sig")
        self.assertEqual(len(csv_text.strip().splitlines()), 1 + 2)           # header + 2 baris
        TelemetryRollup.objects.create(variable=self.temperature, resolution=60, bucket=last.ts - timedelta(minutes=1),
                                       count=2, total=1, min_value=0, max_value=1)
        response = self.client.post(self.url, {"up_to": last.pk}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.json()["redirect"],
                         reverse("devices:variable_detail", args=[self.device.pk, self.temperature.pk]))
        self.assertEqual(Telemetry.objects.filter(variable=self.temperature).count(), 1)
        self.assertEqual(self.rows(self.temperature), 1)
        self.assertFalse(TelemetryRollup.objects.filter(variable=self.temperature).exists())
        self.assertTrue(ActivityEvent.objects.filter(verb="variable.data_deleted", device=self.device).exists())

    def test_full_variable_accepts_data_again_after_delete(self):
        self.fill(self.temperature, 3)
        self.assertEqual(self.post({"temperature": 1}).status_code, 409)
        up_to = self.client.get(self.url).context["up_to"]
        self.assertRedirects(self.client.post(self.url, {"up_to": up_to}),
                             reverse("devices:variable_detail", args=[self.device.pk, self.temperature.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(self.post({"temperature": 1}).status_code, 200)

    def test_viewer_cannot_delete(self):
        from sharing.models import DeviceShare, Role

        siti = User.objects.create_user("siti", password="rahasia-12345")
        DeviceShare.objects.create(device=self.device, user=siti, role=Role.VIEWER)
        self.fill(self.temperature, 1)
        self.client.force_login(siti)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"up_to": 10**9}).status_code, 403)
        self.assertEqual(Telemetry.objects.filter(variable=self.temperature).count(), 1)

    def test_missing_up_to_deletes_nothing(self):
        self.fill(self.temperature, 1)
        response = self.client.post(self.url, {}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Telemetry.objects.filter(variable=self.temperature).count(), 1)


class AdminLimitTests(TestCase):
    def test_admin_sets_a_limit_per_account(self):
        admin = User.objects.create_user("admin1", password="x", role=User.Role.ADMIN)
        budi = User.objects.create_user("budi", password="x", email="budi@example.com")
        self.client.force_login(admin)
        self.client.post(reverse("accounts:user_edit", args=[budi.pk]), {
            "username": "budi", "first_name": "", "last_name": "", "email": "budi@example.com",
            "role": User.Role.USER, "is_active": "on", "telemetry_max_rows": "5000",
        })
        budi.refresh_from_db()
        self.assertEqual(budi.telemetry_row_limit, 5000)
        page = self.client.get(reverse("accounts:user_list"))
        self.assertContains(page, "Row limit")
        self.assertContains(page, "5,000")
