"""Tes dashboard: pengelompokan, pengaturan tampil/sembunyi, dan WebSocket-nya."""
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from devices.models import Device, Location, Telemetry, Variable
from realtime.events import HANDLER, group_name
from realtime.routing import DASHBOARD_WS_PATH, websocket_urlpatterns

from .models import DashboardPreference

User = get_user_model()
IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


class DashboardTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.lab = Location.objects.create(owner=cls.budi, name="Lab", latitude=-7.9, longitude=112.6)
        cls.attic = Location.objects.create(owner=cls.budi, name="Attic", latitude=-7.9, longitude=112.6)
        # Siti punya lokasi bernama sama: nama lokasi unik per pemilik (tahap 12).
        cls.siti_lab = Location.objects.create(owner=cls.siti, name="Lab", latitude=-7.9, longitude=112.6)
        cls.node = Device.objects.create(owner=cls.budi, name="Node", location=cls.lab)
        cls.roof = Device.objects.create(owner=cls.budi, name="Roof", location=cls.attic)
        cls.loose = Device.objects.create(owner=cls.budi, name="Loose")  # tanpa lokasi
        cls.other = Device.objects.create(owner=cls.siti, name="Siti's node", location=cls.siti_lab)
        cls.temp = Variable.objects.create(device=cls.node, key="temperature")
        cls.hum = Variable.objects.create(device=cls.node, key="humidity")
        cls.wind = Variable.objects.create(device=cls.roof, key="wind")
        cls.misc = Variable.objects.create(device=cls.loose, key="misc")
        cls.secret = Variable.objects.create(device=cls.other, key="secret")
        Telemetry.objects.create(variable=cls.temp, value=25, ts=timezone.now())
        Device.objects.filter(pk=cls.node.pk).update(is_online=True)   # tahap 16: status disimpan

    def setUp(self):
        self.client.force_login(self.budi)

    def dashboard(self):
        return self.client.get(reverse("dashboard:index"))

    def shown_variables(self, response):
        return {
            v.key
            for group in response.context["groups"]
            for device in group["devices"]
            for v in device.shown_variables
        }


class DashboardPageTests(DashboardTestData):
    def test_login_required(self):
        self.client.logout()
        response = self.dashboard()
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_home_goes_to_dashboard(self):
        self.assertRedirects(self.client.get("/"), reverse("dashboard:index"))

    def test_grouped_by_location_then_device(self):
        response = self.dashboard()
        names = [g["location"].name if g["location"] else None for g in response.context["groups"]]
        # Urut nama lokasi, "No location" paling akhir.
        self.assertEqual(names, ["Attic", "Lab", None])
        lab = response.context["groups"][1]
        self.assertEqual([d.name for d in lab["devices"]], ["Node"])  # device siti tidak ikut
        self.assertEqual(self.shown_variables(response), {"temperature", "humidity", "wind", "misc"})
        self.assertNotContains(response, "Siti&#x27;s node")

    def test_latest_value_and_online_status(self):
        response = self.dashboard()
        node = response.context["groups"][1]["devices"][0]
        self.assertTrue(node.online)
        temperature = next(v for v in node.shown_variables if v.key == "temperature")
        self.assertEqual(temperature.last_value, 25)
        self.assertEqual(response.context["stats"]["online"], 1)
        self.assertContains(response, 'data-ws-path="/ws/dashboard/"')

    def test_hidden_variable_device_and_location(self):
        preference = DashboardPreference.for_user(self.budi)
        preference.hidden_variables.add(self.hum)
        response = self.dashboard()
        self.assertNotIn("humidity", self.shown_variables(response))
        self.assertEqual(response.context["live_config"]["hiddenVariables"], [self.hum.pk])

        preference.hidden_devices.add(self.roof)
        self.assertNotIn("wind", self.shown_variables(self.dashboard()))

        preference.hidden_locations.add(self.lab)
        preference.hide_unlocated = True
        preference.save()
        response = self.dashboard()
        self.assertEqual(self.shown_variables(response), set())
        self.assertContains(response, "Everything is hidden")

    def test_preferences_are_per_user(self):
        DashboardPreference.for_user(self.siti).hidden_variables.add(self.secret)
        self.assertIn("temperature", self.shown_variables(self.dashboard()))


class DashboardSettingsTests(DashboardTestData):
    url = reverse("dashboard:settings")

    def test_settings_page_lists_everything(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'name="variable" value="{self.hum.pk}"')
        self.assertContains(response, 'name="unlocated"')
        self.assertNotContains(response, f'value="{self.secret.pk}"')

    def test_unchecked_items_become_hidden(self):
        data = {
            "location": [self.lab.pk],              # Attic tidak dicentang
            "device": [self.node.pk, self.roof.pk, self.loose.pk],
            "variable": [self.temp.pk, self.wind.pk, self.misc.pk],  # humidity tidak
            # "unlocated" tidak dikirim -> kelompok tanpa lokasi disembunyikan
        }
        response = self.client.post(self.url, data)
        self.assertRedirects(response, reverse("dashboard:index"))
        hidden = DashboardPreference.for_user(self.budi).hidden_ids()
        self.assertEqual(hidden["locations"], {self.attic.pk})
        self.assertEqual(hidden["devices"], set())
        self.assertEqual(hidden["variables"], {self.hum.pk})
        self.assertTrue(DashboardPreference.for_user(self.budi).hide_unlocated)

    def test_cannot_touch_other_users_items(self):
        self.client.post(self.url, {"variable": [self.secret.pk, "abc"], "unlocated": "1"})
        hidden = DashboardPreference.for_user(self.budi).hidden_ids()
        self.assertNotIn(self.secret.pk, hidden["variables"])

    def test_check_everything_shows_everything(self):
        DashboardPreference.for_user(self.budi).hidden_variables.add(self.hum)
        self.client.post(self.url, {
            "location": [self.lab.pk, self.attic.pk],
            "device": [self.node.pk, self.roof.pk, self.loose.pk],
            "variable": [self.temp.pk, self.hum.pk, self.wind.pk, self.misc.pk],
            "unlocated": "1",
        })
        self.assertEqual(self.shown_variables(self.dashboard()), {"temperature", "humidity", "wind", "misc"})


class DashboardUpdatesTests(DashboardTestData):
    url = reverse("dashboard:updates")

    def test_anonymous_gets_401(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_version_changes_with_own_data_only(self):
        before = self.client.get(self.url).json()["version"]
        Telemetry.objects.create(variable=self.secret, value=1, ts=timezone.now())
        self.assertEqual(self.client.get(self.url).json()["version"], before)
        Telemetry.objects.create(variable=self.wind, value=3, ts=timezone.now())
        self.assertNotEqual(self.client.get(self.url).json()["version"], before)


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class DashboardConsumerTests(TransactionTestCase):
    def setUp(self):
        self.budi = User.objects.create_user("budi", password="rahasia-12345")
        self.siti = User.objects.create_user("siti", password="rahasia-12345")
        self.mine = Device.objects.create(owner=self.budi, name="Mine")
        self.theirs = Device.objects.create(owner=self.siti, name="Theirs")

    async def test_receives_events_from_all_own_devices_only(self):
        communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), DASHBOARD_WS_PATH)
        communicator.scope["user"] = self.budi
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        layer = get_channel_layer()
        await layer.group_send(group_name(self.theirs.pk), {"type": HANDLER, "event": {"type": "x"}})
        self.assertTrue(await communicator.receive_nothing())
        event = {"type": "command", "id": 1, "status": "SENT"}
        await layer.group_send(group_name(self.mine.pk), {"type": HANDLER, "event": event})
        self.assertEqual(await communicator.receive_json_from(), event)
        await communicator.disconnect()
