"""Tes aturan kepemilikan, tampilan per device, dan tampilan per variabel."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Device, Telemetry, Variable

User = get_user_model()


class DeviceAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admin1", password="rahasia-12345", role=User.Role.ADMIN
        )
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi_device = Device.objects.create(owner=cls.budi, name="Node Budi")
        cls.siti_device = Device.objects.create(owner=cls.siti, name="Node Siti")
        cls.siti_variable = Variable.objects.create(device=cls.siti_device, key="humidity")

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("devices:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_user_sees_only_own_devices(self):
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:list"))
        self.assertEqual(list(response.context["devices"]), [self.budi_device])

    def test_admin_sees_all_devices(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("devices:list"))
        self.assertEqual(len(response.context["devices"]), 2)

    def test_user_cannot_open_other_users_device(self):
        self.client.force_login(self.budi)
        for name in ("devices:detail", "devices:edit", "devices:delete"):
            response = self.client.get(reverse(name, args=[self.siti_device.pk]))
            self.assertEqual(response.status_code, 404, name)

    def test_user_cannot_open_other_users_variable(self):
        self.client.force_login(self.budi)
        args = [self.siti_device.pk, self.siti_variable.pk]
        for name in ("devices:variable_detail", "devices:variable_edit", "devices:variable_delete"):
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 404, name)
        response = self.client.get(reverse("devices:variable_add", args=[self.siti_device.pk]))
        self.assertEqual(response.status_code, 404)

    def test_variable_must_match_device_in_url(self):
        # Variabel siti diakses lewat URL device budi: harus 404, walaupun
        # yang login adalah admin yang boleh melihat keduanya.
        self.client.force_login(self.admin)
        url = reverse("devices:variable_detail", args=[self.budi_device.pk, self.siti_variable.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_user_created_device_is_owned_by_that_user(self):
        self.client.force_login(self.budi)
        response = self.client.post(
            reverse("devices:add"),
            {"name": "Baru", "device_type": "", "location": "", "description": "", "is_active": "on"},
        )
        device = Device.objects.get(name="Baru")
        self.assertRedirects(response, reverse("devices:detail", args=[device.pk]))
        self.assertEqual(device.owner, self.budi)

    def test_admin_can_assign_owner(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("devices:add"),
            {"name": "Titipan", "is_active": "on", "owner": self.siti.pk},
        )
        self.assertEqual(Device.objects.get(name="Titipan").owner, self.siti)


class VariableFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi")
        cls.other = Device.objects.create(owner=cls.budi, name="Node Lain")
        Variable.objects.create(device=cls.device, key="temperature", unit="°C")

    def setUp(self):
        self.client.force_login(self.budi)

    def add(self, device, **data):
        return self.client.post(
            reverse("devices:variable_add", args=[device.pk]),
            {"key": "", "label": "", "unit": "", **data},
        )

    def test_add_variable(self):
        response = self.add(self.device, key="humidity", label="Humidity", unit="%")
        variable = Variable.objects.get(device=self.device, key="humidity")
        self.assertRedirects(
            response, reverse("devices:variable_detail", args=[self.device.pk, variable.pk])
        )
        self.assertEqual(variable.display_name, "Humidity")

    def test_duplicate_key_on_same_device_is_rejected(self):
        response = self.add(self.device, key="temperature")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has a variable with this key")
        self.assertEqual(self.device.variables.count(), 1)

    def test_same_key_on_another_device_is_allowed(self):
        self.add(self.other, key="temperature")
        self.assertTrue(self.other.variables.filter(key="temperature").exists())

    def test_invalid_key_is_rejected(self):
        response = self.add(self.device, key="soil moisture")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.device.variables.filter(key="soil moisture").exists())

    def test_edit_keeps_own_key(self):
        variable = self.device.variables.get(key="temperature")
        response = self.client.post(
            reverse("devices:variable_edit", args=[self.device.pk, variable.pk]),
            {"key": "temperature", "label": "Room temperature", "unit": "°C"},
        )
        self.assertEqual(response.status_code, 302)
        variable.refresh_from_db()
        self.assertEqual(variable.label, "Room temperature")

    def test_delete_variable_returns_to_device(self):
        variable = self.device.variables.get(key="temperature")
        response = self.client.post(
            reverse("devices:variable_delete", args=[self.device.pk, variable.pk])
        )
        self.assertRedirects(response, reverse("devices:detail", args=[self.device.pk]))
        self.assertFalse(Variable.objects.filter(pk=variable.pk).exists())


class DeviceAndVariablePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi", location="Server room")
        cls.temperature = Variable.objects.create(
            device=cls.device, key="temperature", label="Temperature", unit="°C"
        )
        cls.humidity = Variable.objects.create(device=cls.device, key="humidity", unit="%")
        cls.empty = Variable.objects.create(device=cls.device, key="pressure")
        now = timezone.now()
        # 30 baris suhu; nilai naik seiring waktu, jadi yang terbaru bernilai 29.
        Telemetry.objects.bulk_create(
            Telemetry(variable=cls.temperature, value=i, ts=now - timedelta(minutes=30 - i))
            for i in range(30)
        )
        Telemetry.objects.create(variable=cls.humidity, value=60, ts=now)

    def setUp(self):
        self.client.force_login(self.budi)

    # --- tampilan per device ---

    def test_device_page_has_one_card_per_variable(self):
        response = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        cards = {v.key: v for v in response.context["variables"]}
        self.assertEqual(set(cards), {"temperature", "humidity", "pressure"})
        self.assertEqual(cards["temperature"].last_value, 29)
        self.assertEqual(cards["temperature"].telemetry_count, 30)
        self.assertEqual(cards["humidity"].last_value, 60)
        self.assertIsNone(cards["pressure"].last_value)
        self.assertEqual(cards["pressure"].telemetry_count, 0)
        self.assertContains(response, "Temperature")

    def test_device_list_counts_rows_across_variables(self):
        Device.objects.create(owner=self.budi, name="Empty node")
        response = self.client.get(reverse("devices:list"))
        counts = {d.name: d.telemetry_count for d in response.context["devices"]}
        self.assertEqual(counts, {"Empty node": 0, "Node Budi": 31})
        self.assertContains(response, "Server room")

    # --- tampilan per variabel ---

    def url(self, variable, **params):
        return reverse("devices:variable_detail", args=[self.device.pk, variable.pk])

    def test_variable_latest_value(self):
        response = self.client.get(self.url(self.temperature))
        self.assertEqual(response.context["latest"].value, 29)
        self.assertContains(response, "°C")

    def test_variable_without_data_renders(self):
        response = self.client.get(self.url(self.empty))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["latest"])
        self.assertEqual(response.context["chart_points"], [])
        self.assertNotContains(response, 'id="chart-data"')

    def test_chart_points_are_oldest_first(self):
        response = self.client.get(self.url(self.temperature))
        points = response.context["chart_points"]
        self.assertEqual(len(points), 30)
        self.assertEqual(points[-1]["y"], 29)
        xs = [p["x"] for p in points]
        self.assertEqual(xs, sorted(xs))
        self.assertContains(response, 'id="chart-data"')

    def test_chart_only_covers_last_24_hours(self):
        Telemetry.objects.create(
            variable=self.temperature, value=-1, ts=timezone.now() - timedelta(days=3)
        )
        response = self.client.get(self.url(self.temperature))
        self.assertEqual(len(response.context["chart_points"]), 30)

    def test_history_only_contains_this_variable(self):
        response = self.client.get(self.url(self.humidity))
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_history_is_paginated_newest_first(self):
        response = self.client.get(self.url(self.temperature))
        page = response.context["page_obj"]
        self.assertEqual(len(page), 25)
        self.assertEqual(page.paginator.count, 30)
        timestamps = [row.ts for row in page]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_second_page_renders(self):
        response = self.client.get(self.url(self.temperature), {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_obj"]), 5)


class SeedDemoTests(TestCase):
    def test_seed_demo_is_repeatable(self):
        call_command("seed_demo", verbosity=0)
        count = Telemetry.objects.count()
        self.assertGreater(count, 0)
        call_command("seed_demo", verbosity=0)
        self.assertEqual(Telemetry.objects.count(), count)
        self.assertEqual(Device.objects.count(), 3)
        self.assertEqual(Variable.objects.count(), 8)
