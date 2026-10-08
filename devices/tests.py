"""Tes aturan kepemilikan, tampilan, desired state, command, dan simulator."""
import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Command, CommandStatus, DesiredStateLog, Device, Location, Telemetry, Variable

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
