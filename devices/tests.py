"""Tes aturan kepemilikan device dan isi halaman detail."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Device, Telemetry

User = get_user_model()


class DeviceAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admin1", password="rahasia-12345", role=User.Role.ADMIN
        )
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi_device = Device.objects.create(
            owner=cls.budi, name="Sensor Budi", variable="temperature", unit="°C"
        )
        cls.siti_device = Device.objects.create(
            owner=cls.siti, name="Sensor Siti", variable="humidity", unit="%"
        )

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

    def test_user_created_device_is_owned_by_that_user(self):
        self.client.force_login(self.budi)
        response = self.client.post(
            reverse("devices:add"),
            {
                "name": "Baru",
                "device_type": "",
                "variable": "temperature",
                "unit": "°C",
                "description": "",
                "is_active": "on",
            },
        )
        device = Device.objects.get(name="Baru")
        self.assertRedirects(response, reverse("devices:detail", args=[device.pk]))
        self.assertEqual(device.owner, self.budi)

    def test_admin_can_assign_owner(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("devices:add"),
            {
                "name": "Titipan",
                "variable": "voltage",
                "is_active": "on",
                "owner": self.siti.pk,
            },
        )
        self.assertEqual(Device.objects.get(name="Titipan").owner, self.siti)


class DeviceDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(
            owner=cls.budi, name="Sensor Budi", variable="temperature", unit="°C"
        )
        now = timezone.now()
        # 30 baris; nilai naik seiring waktu, jadi yang terbaru bernilai 29.
        Telemetry.objects.bulk_create(
            Telemetry(device=cls.device, value=i, ts=now - timedelta(minutes=30 - i))
            for i in range(30)
        )

    def setUp(self):
        self.client.force_login(self.budi)
        self.url = reverse("devices:detail", args=[self.device.pk])

    def test_latest_value_is_the_newest_row(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["latest"].value, 29)
        self.assertContains(response, "temperature")
        self.assertContains(response, "°C")

    def test_chart_points_are_oldest_first(self):
        response = self.client.get(self.url)
        points = response.context["chart_points"]
        self.assertEqual(len(points), 30)
        self.assertEqual(points[-1]["y"], 29)
        xs = [p["x"] for p in points]
        self.assertEqual(xs, sorted(xs))
        self.assertContains(response, 'id="chart-data"')

    def test_chart_only_covers_last_24_hours(self):
        Telemetry.objects.create(
            device=self.device, value=-1, ts=timezone.now() - timedelta(days=3)
        )
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["chart_points"]), 30)

    def test_device_without_data_renders(self):
        empty = Device.objects.create(owner=self.budi, name="Kosong", variable="x")
        response = self.client.get(reverse("devices:detail", args=[empty.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["latest"])
        self.assertEqual(response.context["chart_points"], [])
        self.assertNotContains(response, 'id="chart-data"')

    def test_history_is_paginated_newest_first(self):
        response = self.client.get(self.url)
        page = response.context["page_obj"]
        self.assertEqual(len(page), 25)
        self.assertEqual(page.paginator.count, 30)
        timestamps = [row.ts for row in page]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_second_page_renders(self):
        response = self.client.get(self.url, {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_obj"]), 5)

    def test_list_shows_latest_value(self):
        response = self.client.get(reverse("devices:list"))
        device = response.context["devices"][0]
        self.assertEqual(device.last_value, 29)


class SeedDemoTests(TestCase):
    def test_seed_demo_is_repeatable(self):
        call_command("seed_demo", verbosity=0)
        count = Telemetry.objects.count()
        self.assertGreater(count, 0)
        call_command("seed_demo", verbosity=0)
        self.assertEqual(Telemetry.objects.count(), count)
        self.assertEqual(Device.objects.count(), 5)
