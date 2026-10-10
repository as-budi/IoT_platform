"""Tes aturan kepemilikan, tampilan, desired state, command, dan simulator."""
import io
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Command, CommandStatus, DesiredStateLog, Device, Location, Telemetry, Variable
from .views import data_version

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
            {"key": "", "label": "", "unit": "", "kind": "SENSOR", "value_type": "NUMBER",
             "min_value": "", "max_value": "", **data},
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

    def test_add_actuator_with_range(self):
        self.add(self.device, key="servo", kind="ACTUATOR", min_value="0", max_value="180")
        servo = Variable.objects.get(device=self.device, key="servo")
        self.assertTrue(servo.is_actuator)
        self.assertEqual((servo.min_value, servo.max_value), (0, 180))

    def test_min_greater_than_max_is_rejected(self):
        response = self.add(self.device, key="servo", kind="ACTUATOR", min_value="10", max_value="5")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.device.variables.filter(key="servo").exists())

    def test_edit_keeps_own_key(self):
        variable = self.device.variables.get(key="temperature")
        response = self.client.post(
            reverse("devices:variable_edit", args=[self.device.pk, variable.pk]),
            {"key": "temperature", "label": "Room temperature", "unit": "°C",
             "kind": "SENSOR", "value_type": "NUMBER", "min_value": "", "max_value": ""},
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
        cls.server_room = Location.objects.create(name="Server room", latitude=-7.95, longitude=112.61)
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi", location=cls.server_room)
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
        # Halaman device memisahkan aktuator dan sensor; di sini semuanya sensor.
        self.assertEqual(response.context["actuators"], [])
        cards = {v.key: v for v in response.context["sensors"]}
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
        # stdout=io.StringIO(): tampung keluaran perintah supaya tidak
        # bercampur dengan laporan tes di terminal.
        call_command("seed_demo", stdout=io.StringIO())
        count = Telemetry.objects.count()
        self.assertGreater(count, 0)
        call_command("seed_demo", stdout=io.StringIO())
        self.assertEqual(Telemetry.objects.count(), count)
        self.assertEqual(Device.objects.count(), 4)
        self.assertEqual(Variable.objects.count(), 11)
        # Aktuator contoh dibuat dalam keadaan "In sync".
        for variable in Variable.objects.filter(kind=Variable.Kind.ACTUATOR):
            self.assertEqual(variable.sync_status, Variable.SyncStatus.SYNCED, variable.key)


class DesiredStateTests(TestCase):
    """Pola 1: desired / reported state."""

    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Relay Board")
        cls.lamp = Variable.objects.create(
            device=cls.device, key="lamp", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )
        cls.fan = Variable.objects.create(
            device=cls.device, key="fan_speed", unit="%", kind=Variable.Kind.ACTUATOR,
            min_value=0, max_value=100,
        )
        cls.sensor = Variable.objects.create(device=cls.device, key="temperature")

    def setUp(self):
        self.client.force_login(self.budi)

    def set(self, variable, value):
        return self.client.post(
            reverse("devices:variable_set", args=[self.device.pk, variable.pk]),
            {"value": value},
        )

    def test_set_desired_records_request_and_log(self):
        response = self.set(self.lamp, "1")
        self.assertRedirects(response, reverse("devices:detail", args=[self.device.pk]))
        self.lamp.refresh_from_db()
        self.assertEqual(self.lamp.desired_value, 1)
        self.assertEqual(self.lamp.desired_version, 1)
        self.assertEqual(self.lamp.desired_by, self.budi)
        log = DesiredStateLog.objects.get(variable=self.lamp)
        self.assertEqual((log.value, log.version, log.changed_by), (1, 1, self.budi))

    def test_setting_desired_does_not_fake_a_report(self):
        # Mengubah desired TIDAK boleh menambah telemetri: itu tugas device.
        self.set(self.lamp, "1")
        self.assertFalse(Telemetry.objects.filter(variable=self.lamp).exists())
        self.lamp.refresh_from_db()
        self.assertEqual(self.lamp.sync_status, Variable.SyncStatus.PENDING)

    def test_sync_status_follows_the_device_report(self):
        self.set(self.fan, "50")
        self.fan.refresh_from_db()
        Telemetry.objects.create(variable=self.fan, value=50, ts=timezone.now())
        self.assertEqual(self.fan.sync_status, Variable.SyncStatus.SYNCED)
        Telemetry.objects.create(variable=self.fan, value=20, ts=timezone.now())
        self.assertEqual(self.fan.sync_status, Variable.SyncStatus.MISMATCH)

    def test_old_report_does_not_count_as_sync(self):
        Telemetry.objects.create(
            variable=self.lamp, value=1, ts=timezone.now() - timedelta(minutes=5)
        )
        self.set(self.lamp, "1")
        self.lamp.refresh_from_db()
        # Laporan itu dibuat SEBELUM permintaan, jadi belum membuktikan apa-apa.
        self.assertEqual(self.lamp.sync_status, Variable.SyncStatus.PENDING)

    def test_version_increases_with_each_request(self):
        for value in ("1", "0", "1"):
            self.set(self.lamp, value)
        self.lamp.refresh_from_db()
        self.assertEqual(self.lamp.desired_version, 3)
        self.assertEqual(self.lamp.desired_log.count(), 3)

    def test_out_of_range_value_is_rejected(self):
        self.set(self.fan, "150")
        self.fan.refresh_from_db()
        self.assertIsNone(self.fan.desired_value)

    def test_boolean_accepts_only_on_or_off(self):
        self.set(self.lamp, "0.5")
        self.lamp.refresh_from_db()
        self.assertIsNone(self.lamp.desired_value)

    def test_sensor_cannot_be_controlled(self):
        response = self.set(self.sensor, "1")
        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self):
        url = reverse("devices:variable_set", args=[self.device.pk, self.lamp.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_other_user_cannot_control(self):
        self.client.force_login(self.siti)
        self.assertEqual(self.set(self.lamp, "1").status_code, 404)

    def test_unsafe_next_url_is_ignored(self):
        response = self.client.post(
            reverse("devices:variable_set", args=[self.device.pk, self.lamp.pk]),
            {"value": "1", "next": "https://evil.example.com/"},
        )
        self.assertRedirects(response, reverse("devices:detail", args=[self.device.pk]))

    def test_device_page_shows_controls(self):
        self.set(self.lamp, "1")
        response = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertEqual([v.key for v in response.context["actuators"]], ["fan_speed", "lamp"])
        self.assertEqual([v.key for v in response.context["sensors"]], ["temperature"])
        self.assertContains(response, "Waiting for device")


class CommandTests(TestCase):
    """Pola 2: command (aksi sekali jalan)."""

    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Valve Controller")

    def setUp(self):
        self.client.force_login(self.budi)
        self.url = reverse("devices:command_add", args=[self.device.pk])

    def test_create_command(self):
        response = self.client.post(
            self.url, {"method": "open_valve", "params": '{"seconds": 5}', "ttl": "300"}
        )
        self.assertRedirects(
            response, reverse("devices:detail", args=[self.device.pk]) + "#commands",
            fetch_redirect_response=False,
        )
        command = Command.objects.get(device=self.device)
        self.assertEqual(command.method, "open_valve")
        self.assertEqual(command.params, {"seconds": 5})
        self.assertEqual(command.status, CommandStatus.QUEUED)
        self.assertEqual(command.created_by, self.budi)
        self.assertGreater(command.expires_at, timezone.now())

    def test_invalid_json_is_rejected(self):
        self.client.post(self.url, {"method": "open_valve", "params": "{seconds: 5}", "ttl": "300"})
        self.client.post(self.url, {"method": "open_valve", "params": "[1, 2]", "ttl": "300"})
        self.assertFalse(Command.objects.exists())

    def test_invalid_method_is_rejected(self):
        self.client.post(self.url, {"method": "open valve", "params": "", "ttl": "300"})
        self.assertFalse(Command.objects.exists())

    def test_other_user_cannot_send(self):
        self.client.force_login(self.siti)
        response = self.client.post(self.url, {"method": "reboot", "params": "", "ttl": "60"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Command.objects.exists())

    def test_expired_commands_are_marked_and_never_completed(self):
        command = Command.objects.create(
            device=self.device, method="reboot", expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.client.get(reverse("devices:detail", args=[self.device.pk]))
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.EXPIRED)
        self.assertFalse(command.complete(True, {"ok": True}))
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.EXPIRED)


class SimulatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Relay Board")
        cls.lamp = Variable.objects.create(
            device=cls.device, key="lamp", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )

    def test_simulator_applies_desired_state_and_answers_commands(self):
        self.lamp.set_desired(1, self.budi)
        queued = Command.objects.create(
            device=self.device, method="reboot", expires_at=timezone.now() + timedelta(minutes=5)
        )
        expired = Command.objects.create(
            device=self.device, method="reboot", expires_at=timezone.now() - timedelta(minutes=5)
        )
        call_command("simulate_devices", stdout=io.StringIO())

        self.lamp.refresh_from_db()
        self.assertEqual(self.lamp.sync_status, Variable.SyncStatus.SYNCED)
        queued.refresh_from_db()
        self.assertEqual(queued.status, CommandStatus.SUCCEEDED)
        self.assertIsNotNone(queued.sent_at)
        expired.refresh_from_db()
        self.assertEqual(expired.status, CommandStatus.EXPIRED)

    def test_simulator_failure_mode(self):
        self.lamp.set_desired(1, self.budi)
        command = Command.objects.create(
            device=self.device, method="reboot", expires_at=timezone.now() + timedelta(minutes=5)
        )
        call_command("simulate_devices", fail_rate=1.0, stdout=io.StringIO())
        self.lamp.refresh_from_db()
        self.assertEqual(self.lamp.sync_status, Variable.SyncStatus.MISMATCH)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.FAILED)


class LocationTests(TestCase):
    """Lokasi: dikelola Admin, dipilih (bukan diketik) saat menambah device."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin1", password="rahasia-12345", role=User.Role.ADMIN)
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.lab = Location.objects.create(name="Electronics lab", latitude=-7.9533, longitude=112.6141)
        cls.greenhouse = Location.objects.create(name="Greenhouse A", latitude=-7.9465, longitude=112.6098)
        cls.budi_lab = Device.objects.create(owner=cls.budi, name="Budi Lab Node", location=cls.lab)
        cls.budi_free = Device.objects.create(owner=cls.budi, name="Budi Loose Node")
        cls.siti_lab = Device.objects.create(owner=cls.siti, name="Siti Lab Node", location=cls.lab)

    def location_data(self, **overrides):
        return {"name": "Rooftop", "latitude": "-7.95", "longitude": "112.61", "description": "", **overrides}

    # --- pengelolaan lokasi ---

    def test_admin_can_add_location(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("devices:location_add"), self.location_data())
        location = Location.objects.get(name="Rooftop")
        self.assertRedirects(response, reverse("devices:location_detail", args=[location.pk]))
        self.assertEqual(float(location.latitude), -7.95)

    def test_regular_user_cannot_add_edit_or_delete_location(self):
        self.client.force_login(self.budi)
        for url in (
            reverse("devices:location_add"),
            reverse("devices:location_edit", args=[self.lab.pk]),
            reverse("devices:location_delete", args=[self.lab.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.post(reverse("devices:location_add"), self.location_data())
        self.assertFalse(Location.objects.filter(name="Rooftop").exists())

    def test_coordinates_out_of_range_are_rejected(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("devices:location_add"), self.location_data(latitude="95"))
        self.client.post(reverse("devices:location_add"), self.location_data(name="Far", longitude="-200"))
        self.assertFalse(Location.objects.filter(name__in=["Rooftop", "Far"]).exists())

    def test_location_names_are_unique(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("devices:location_add"), self.location_data(name="Greenhouse A"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Location.objects.filter(name="Greenhouse A").count(), 1)

    def test_location_in_use_cannot_be_deleted(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("devices:location_delete", args=[self.lab.pk]))
        self.assertRedirects(response, reverse("devices:location_detail", args=[self.lab.pk]))
        self.assertTrue(Location.objects.filter(pk=self.lab.pk).exists())

    def test_unused_location_can_be_deleted(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("devices:location_delete", args=[self.greenhouse.pk]))
        self.assertRedirects(response, reverse("devices:location_list"))
        self.assertFalse(Location.objects.filter(pk=self.greenhouse.pk).exists())

    # --- memilih lokasi di form device ---

    def device_data(self, **overrides):
        return {"name": "New Node", "device_type": "", "description": "", "is_active": "on", **overrides}

    def test_device_form_offers_existing_locations(self):
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:add"))
        choices = list(response.context["form"].fields["location"].queryset)
        self.assertEqual(choices, [self.lab, self.greenhouse])

    def test_device_can_pick_existing_location(self):
        self.client.force_login(self.budi)
        self.client.post(reverse("devices:add"), self.device_data(location=self.greenhouse.pk))
        self.assertEqual(Device.objects.get(name="New Node").location, self.greenhouse)

    def test_device_form_cannot_create_new_location(self):
        self.client.force_login(self.budi)
        response = self.client.post(reverse("devices:add"), self.device_data(location="Rooftop"))
        self.assertEqual(response.status_code, 200)  # form ditampilkan lagi dengan error
        self.assertFalse(Device.objects.filter(name="New Node").exists())
        self.assertFalse(Location.objects.filter(name="Rooftop").exists())

    def test_device_form_rejects_unknown_location_id(self):
        self.client.force_login(self.budi)
        response = self.client.post(reverse("devices:add"), self.device_data(location="99999"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Device.objects.filter(name="New Node").exists())

    # --- tampilan berdasarkan lokasi ---

    def test_device_list_can_be_filtered_by_location(self):
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:list"), {"location": self.lab.pk})
        self.assertEqual(list(response.context["devices"]), [self.budi_lab])
        response = self.client.get(reverse("devices:list"), {"location": "none"})
        self.assertEqual(list(response.context["devices"]), [self.budi_free])
        response = self.client.get(reverse("devices:list"))
        self.assertEqual(len(response.context["devices"]), 2)

    def test_location_page_shows_only_visible_devices(self):
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:location_detail", args=[self.lab.pk]))
        self.assertEqual(list(response.context["devices"]), [self.budi_lab])
        self.assertEqual(response.context["map_points"][0]["name"], "Electronics lab")

    def test_location_list_counts_only_visible_devices(self):
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:location_list"))
        counts = {loc.name: loc.device_count for loc in response.context["locations"]}
        self.assertEqual(counts, {"Electronics lab": 1, "Greenhouse A": 0})
        self.client.force_login(self.admin)
        response = self.client.get(reverse("devices:location_list"))
        counts = {loc.name: loc.device_count for loc in response.context["locations"]}
        self.assertEqual(counts["Electronics lab"], 2)


class ChartPeriodTests(TestCase):
    """Pilihan periode chart di halaman variabel."""

    # Umur tiap bacaan dan nilainya. Sengaja jauh dari batas periode.
    AGES = [
        (timedelta(minutes=2), 10),
        (timedelta(minutes=20), 20),
        (timedelta(hours=2), 30),
        (timedelta(days=2), 40),
        (timedelta(days=20), 50),
        (timedelta(days=200), 60),
    ]

    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi")
        cls.temperature = Variable.objects.create(device=cls.device, key="temperature")
        now = timezone.now()
        Telemetry.objects.bulk_create(
            Telemetry(variable=cls.temperature, value=value, ts=now - age) for age, value in cls.AGES
        )

    def setUp(self):
        self.client.force_login(self.budi)
        self.url = reverse("devices:variable_detail", args=[self.device.pk, self.temperature.pk])

    def points(self, **params):
        return self.client.get(self.url, params).context["chart_points"]

    def test_each_period_shows_only_its_window(self):
        expected = {"5m": 1, "10m": 1, "30m": 2, "1h": 2, "1d": 3, "1w": 4, "1mo": 5, "1y": 6}
        for period, count in expected.items():
            self.assertEqual(len(self.points(period=period)), count, period)

    def test_default_period_is_one_day(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["chart"]["period"], "1d")
        self.assertEqual(len(response.context["chart_points"]), 3)

    def test_unknown_period_falls_back_to_default(self):
        response = self.client.get(self.url, {"period": "2 days"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["chart"]["period"], "1d")

    def test_custom_range(self):
        fmt = "%Y-%m-%dT%H:%M"
        now = timezone.now()
        response = self.client.get(self.url, {
            "period": "custom",
            "start": timezone.localtime(now - timedelta(hours=3)).strftime(fmt),
            "end": timezone.localtime(now - timedelta(hours=1)).strftime(fmt),
        })
        self.assertEqual(response.context["chart"]["period"], "custom")
        self.assertEqual([p["y"] for p in response.context["chart_points"]], [30])

    def test_custom_without_end_runs_until_now(self):
        start = timezone.localtime(timezone.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
        points = self.points(period="custom", start=start)
        self.assertEqual([p["y"] for p in points], [30, 20, 10])

    def test_custom_start_after_end_is_rejected(self):
        response = self.client.get(self.url, {
            "period": "custom", "start": "2026-10-07T12:00", "end": "2026-10-07T08:00",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["custom_open"])
        self.assertContains(response, "Start must be earlier than end.")

    def test_period_dropdown_is_rendered(self):
        response = self.client.get(self.url, {"period": "1w"})
        self.assertContains(response, 'id="period-select"')
        for key in ("5m", "10m", "30m", "1h", "1d", "1w", "1mo", "1y", "custom"):
            self.assertContains(response, f'<option value="{key}"')
        # Periode yang sedang tampil terpilih di dropdown.
        self.assertContains(response, '<option value="1w" selected>Last 1 week</option>', html=True)

    def test_many_points_are_averaged(self):
        # Batas titik dikecilkan menjadi 2, sehingga 3 bacaan dalam 1 hari
        # harus diringkas. Lebar ember = 12 jam; ketiganya jatuh di ember
        # yang sama (2 jam terakhir), jadi hasilnya satu titik rata-rata.
        with mock.patch("devices.charts.CHART_MAX_POINTS", 2):
            response = self.client.get(self.url, {"period": "1d"})
        chart = response.context["chart"]
        self.assertEqual(chart["rows"], 3)
        self.assertEqual(chart["bucket"], "12 h")
        points = response.context["chart_points"]
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0]["y"], 20)
        self.assertContains(response, "average per 12 h")

    def test_boolean_buckets_keep_the_highest_value(self):
        lamp = Variable.objects.create(
            device=self.device, key="lamp", value_type=Variable.ValueType.BOOLEAN
        )
        now = timezone.now()
        for minutes, value in ((3, 0), (2, 1), (1, 0)):
            Telemetry.objects.create(variable=lamp, value=value, ts=now - timedelta(minutes=minutes))
        url = reverse("devices:variable_detail", args=[self.device.pk, lamp.pk])
        with mock.patch("devices.charts.CHART_MAX_POINTS", 1):
            points = self.client.get(url, {"period": "1h"}).context["chart_points"]
        self.assertEqual([p["y"] for p in points], [1])


class AutoRefreshTests(TestCase):
    """Endpoint versi data yang dipakai auto-refresh."""

    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi")
        cls.temperature = Variable.objects.create(device=cls.device, key="temperature")
        cls.humidity = Variable.objects.create(device=cls.device, key="humidity")
        cls.lamp = Variable.objects.create(
            device=cls.device, key="lamp", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )

    def setUp(self):
        self.client.force_login(self.budi)
        self.url = reverse("devices:updates", args=[self.device.pk])

    def version(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response.json()["version"]

    def add_reading(self, variable, value=1):
        Telemetry.objects.create(variable=variable, value=value, ts=timezone.now())

    def test_anonymous_gets_json_401(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())

    def test_other_users_device_is_not_found(self):
        self.client.force_login(self.siti)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_variable_must_belong_to_device(self):
        other = Device.objects.create(owner=self.budi, name="Other")
        stranger = Variable.objects.create(device=other, key="x")
        self.assertEqual(self.client.get(self.url, {"variable": stranger.pk}).status_code, 404)
        self.assertEqual(self.client.get(self.url, {"variable": "abc"}).status_code, 400)

    def test_version_is_stable_without_new_data(self):
        self.assertEqual(self.version(), self.version())

    def test_new_telemetry_changes_version(self):
        before = self.version()
        self.add_reading(self.temperature)
        self.assertNotEqual(self.version(), before)

    def test_variable_version_ignores_other_variables(self):
        before = self.version(variable=self.temperature.pk)
        self.add_reading(self.humidity)
        self.assertEqual(self.version(variable=self.temperature.pk), before)
        self.add_reading(self.temperature)
        self.assertNotEqual(self.version(variable=self.temperature.pk), before)

    def test_desired_state_and_commands_change_version(self):
        before = self.version()
        self.lamp.set_desired(1, self.budi)
        after_desired = self.version()
        self.assertNotEqual(after_desired, before)
        command = Command.objects.create(
            device=self.device, method="reboot", expires_at=timezone.now() + timedelta(minutes=5)
        )
        after_command = self.version()
        self.assertNotEqual(after_command, after_desired)
        command.mark_sent()
        self.assertNotEqual(self.version(), after_command)

    def test_pages_embed_the_current_version(self):
        response = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(response, f'data-version="{data_version(self.device)}"')
        self.assertContains(response, 'id="auto-refresh"')
        url = reverse("devices:variable_detail", args=[self.device.pk, self.temperature.pk])
        response = self.client.get(url)
        self.assertContains(
            response, f'data-version="{data_version(self.device, self.temperature)}"'
        )

