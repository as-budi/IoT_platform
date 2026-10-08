"""Tes WebSocket: siapa boleh tersambung, dan kapan kabar dikirim."""
import json
from unittest import mock

from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from api.services import save_telemetry
from devices.models import Command, Device, Variable

from .events import HANDLER, group_name
from .routing import device_ws_path, websocket_urlpatterns

User = get_user_model()
IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


# TransactionTestCase: consumer membaca database dari thread lain
# (database_sync_to_async), jadi datanya harus benar-benar tersimpan.
@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class DeviceConsumerTests(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin1", password="rahasia-12345", role=User.Role.ADMIN)
        self.budi = User.objects.create_user("budi", password="rahasia-12345")
        self.siti = User.objects.create_user("siti", password="rahasia-12345")
        self.device = Device.objects.create(owner=self.budi, name="Node Budi")

    async def open(self, user):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), device_ws_path(self.device.pk)
        )
        # Di server sungguhan, AuthMiddlewareStack mengisi scope["user"] dari cookie.
        communicator.scope["user"] = user
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        return communicator

    async def test_owner_receives_device_events(self):
        communicator = await self.open(self.budi)
        event = {"type": "command", "id": 1, "status": "SENT"}
        await get_channel_layer().group_send(
            group_name(self.device.pk), {"type": HANDLER, "event": event}
        )
        self.assertEqual(await communicator.receive_json_from(), event)
        await communicator.disconnect()

    async def test_admin_can_watch_any_device(self):
        communicator = await self.open(self.admin)
        self.assertTrue(await communicator.receive_nothing())
        await communicator.disconnect()

    async def test_events_of_other_devices_are_not_received(self):
        communicator = await self.open(self.budi)
        await get_channel_layer().group_send(
            group_name(self.device.pk + 1000), {"type": HANDLER, "event": {"type": "command"}}
        )
        self.assertTrue(await communicator.receive_nothing())
        await communicator.disconnect()

    async def test_anonymous_is_closed_with_4401(self):
        communicator = await self.open(AnonymousUser())
        output = await communicator.receive_output()
        self.assertEqual(output, {"type": "websocket.close", "code": 4401})

    async def test_other_user_is_closed_with_4403(self):
        communicator = await self.open(self.siti)
        output = await communicator.receive_output()
        self.assertEqual(output, {"type": "websocket.close", "code": 4403})


class EventEmissionTests(TestCase):
    """Setiap jalur yang mengubah data harus mengirim kabar, dan baru SETELAH commit."""

    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi")
        cls.lamp = Variable.objects.create(
            device=cls.device, key="lamp", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )

    def setUp(self):
        patcher = mock.patch("realtime.events.deliver")
        self.deliver = patcher.start()
        self.addCleanup(patcher.stop)

    def events(self):
        return [call.args for call in self.deliver.call_args_list]

    def test_nothing_is_sent_before_commit(self):
        # TestCase tidak pernah commit, jadi tanpa captureOnCommitCallbacks
        # kabar tidak boleh terkirim.
        save_telemetry(self.device, [("temperature", 25.0, timezone.now())])
        self.deliver.assert_not_called()

    def test_saved_telemetry_is_announced(self):
        ts = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            save_telemetry(self.device, [("temperature", 25.5, ts), ("lamp", 1, ts)])
        [(device_id, event)] = self.events()
        self.assertEqual(device_id, self.device.pk)
        self.assertEqual(event["type"], "telemetry")
        points = {p["key"]: p for p in event["points"]}
        self.assertEqual(points["temperature"]["value"], 25.5)
        self.assertEqual(points["temperature"]["kind"], "SENSOR")
        self.assertEqual(points["temperature"]["ts"], int(ts.timestamp() * 1000))
        self.assertEqual(points["lamp"]["kind"], "ACTUATOR")
        self.assertTrue(points["lamp"]["boolean"])
        self.assertEqual(points["lamp"]["variable"], self.lamp.pk)

    def test_http_api_announces_telemetry(self):
        url = reverse("api:telemetry", args=[self.device.access_token])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url, json.dumps({"temperature": 21}), content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events()[0][1]["type"], "telemetry")

    def test_desired_state_is_announced(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.lamp.set_desired(1, self.budi)
        self.assertEqual(self.events(), [(self.device.pk, {"type": "desired", "variable": self.lamp.pk})])

    def test_command_lifecycle_is_announced(self):
        self.client.force_login(self.budi)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("devices:command_add", args=[self.device.pk]),
                {"method": "reboot", "params": "{}", "ttl": 300},
            )
        command = Command.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            command.mark_sent()
        with self.captureOnCommitCallbacks(execute=True):
            command.complete(True, {"ok": True})
        statuses = [event["status"] for _, event in self.events()]
        self.assertEqual(statuses, ["QUEUED", "SENT", "SUCCEEDED"])



class BrokenChannelLayerTests(TestCase):
    def test_saving_still_works_when_channel_layer_is_down(self):
        budi = User.objects.create_user("budi", password="rahasia-12345")
        device = Device.objects.create(owner=budi, name="Node Budi")
        with mock.patch("realtime.events.get_channel_layer", side_effect=ConnectionError("Redis down")):
            with self.assertLogs("realtime.events", "WARNING"):
                with self.captureOnCommitCallbacks(execute=True):
                    save_telemetry(device, [("temperature", 25.0, timezone.now())])
        self.assertEqual(device.variables.get().telemetry.count(), 1)


class PageTests(TestCase):
    def test_device_and_variable_pages_point_to_websocket(self):
        budi = User.objects.create_user("budi", password="rahasia-12345")
        device = Device.objects.create(owner=budi, name="Node Budi")
        variable = Variable.objects.create(device=device, key="temperature")
        self.client.force_login(budi)
        for url in (
            reverse("devices:detail", args=[device.pk]),
            reverse("devices:variable_detail", args=[device.pk, variable.pk]),
        ):
            response = self.client.get(url)
            self.assertContains(response, f'data-ws-path="/ws/devices/{device.pk}/"')
        self.assertContains(response, 'id="live-config"')
        self.assertEqual(response.context["live_config"]["variable"], variable.pk)
        self.assertTrue(response.context["live_config"]["chartLive"])
