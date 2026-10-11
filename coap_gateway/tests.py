"""Tes CoAP: aturan di handlers.py (tanpa jaringan) dan satu uji ujung-ke-ujung
dengan server aiocoap sungguhan di localhost (dilewati bila aiocoap belum terpasang)."""
import asyncio
import json
import os
import unittest
from datetime import timedelta
from unittest import mock

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from devices.models import Command, CommandStatus, Device, Telemetry, Variable

from . import handlers
from .handlers import handle

User = get_user_model()

try:
    import aiocoap
except ImportError:  # pragma: no cover
    aiocoap = None


def body(payload):
    return json.dumps(payload).encode()


class CoapTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Greenhouse Node")
        cls.other = Device.objects.create(owner=cls.budi, name="Other")
        cls.pump = Variable.objects.create(
            device=cls.device, key="pump", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )

    @property
    def token(self):
        return self.device.access_token

    def command(self, device=None, **fields):
        fields.setdefault("expires_at", timezone.now() + timedelta(minutes=5))
        return Command.objects.create(device=device or self.device, method="reboot", **fields)


class RoutingTests(CoapTestData):
    def test_routes(self):
        self.assertEqual(handlers.route(["T", "telemetry"]), ("telemetry", "T", None))
        self.assertEqual(handlers.route(["T", "desired"]), ("desired", "T", None))
        self.assertEqual(handlers.route(["T", "commands"]), ("commands", "T", None))
        self.assertEqual(
            handlers.route(["T", "commands", "12", "response"]), ("command_response", "T", 12)
        )
        for path in (["T"], ["T", "other"], ["", "telemetry"], ["T", "commands", "x", "response"]):
            self.assertIsNone(handlers.route(path), path)

    def test_unknown_resource_is_404(self):
        self.assertEqual(handle("GET", [self.token, "nope"]).code, handlers.NOT_FOUND)

    def test_wrong_method_is_405(self):
        self.assertEqual(handle("GET", [self.token, "telemetry"]).code, handlers.METHOD_NOT_ALLOWED)
        self.assertEqual(handle("POST", [self.token, "desired"], b"{}").code, handlers.METHOD_NOT_ALLOWED)

    def test_wrong_token_is_401(self):
        reply = handle("POST", ["wrong", "telemetry"], body({"t": 1}))
        self.assertEqual(reply.code, handlers.UNAUTHORIZED)

    def test_inactive_device_is_403(self):
        self.device.is_active = False
        self.device.save()
        self.assertEqual(handle("POST", [self.token, "telemetry"], body({"t": 1})).code, handlers.FORBIDDEN)


class TelemetryTests(CoapTestData):
    def test_telemetry_is_saved_and_protocol_recorded(self):
        reply = handle("POST", [self.token, "telemetry"], body({"temperature": 25.1, "pump": True}))
        self.assertEqual(reply.code, handlers.CHANGED)
        self.assertEqual(reply.body, {"saved": 2, "new_variables": ["temperature"]})
        self.assertEqual(Telemetry.objects.filter(variable__device=self.device).count(), 2)
        self.device.refresh_from_db()
        self.assertEqual(self.device.last_protocol, Device.Protocol.COAP)
        self.assertIsNotNone(self.device.last_seen_at)

    def test_invalid_payload_is_400_with_reason(self):
        for payload in (b"not json", b'{"t": NaN}', body({"temperature": "hot"})):
            reply = handle("POST", [self.token, "telemetry"], payload)
            self.assertEqual(reply.code, handlers.BAD_REQUEST, payload)
            self.assertIn("error", reply.body)
        self.assertFalse(Telemetry.objects.exists())


class DesiredAndCommandTests(CoapTestData):
    def test_desired_state(self):
        self.assertEqual(handle("GET", [self.token, "desired"]).body, {})
        self.pump.set_desired(1, self.budi)
        reply = handle("GET", [self.token, "desired"])
        self.assertEqual(reply.code, handlers.CONTENT)
        self.assertEqual(reply.body, {"pump": 1})

    def test_commands_are_delivered_and_marked_sent(self):
        command = self.command(params={"times": 3})
        reply = handle("GET", [self.token, "commands"])
        self.assertEqual(reply.body, {"commands": [{"id": command.pk, "method": "reboot", "params": {"times": 3}}]})
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.SENT)
        # Masih ikut dikirim sampai device melaporkan hasilnya.
        self.assertEqual(len(handle("GET", [self.token, "commands"]).body["commands"]), 1)

    def test_expired_and_foreign_commands_are_not_delivered(self):
        self.command(expires_at=timezone.now() - timedelta(seconds=1))
        self.command(device=self.other)
        self.assertEqual(handle("GET", [self.token, "commands"]).body, {"commands": []})

    def test_command_result(self):
        command = self.command(status=CommandStatus.SENT)
        path = [self.token, "commands", str(command.pk), "response"]
        reply = handle("POST", path, body({"success": False, "error": "jammed"}))
        self.assertEqual(reply.code, handlers.CHANGED)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.FAILED)
        self.assertEqual(command.response, {"success": False, "error": "jammed"})
        # Jawaban kedua ditolak dengan alasan yang jelas.
        again = handle("POST", path, body({"success": True}))
        self.assertEqual(again.code, handlers.BAD_REQUEST)

    def test_cannot_answer_other_devices_command(self):
        command = self.command(device=self.other, status=CommandStatus.SENT)
        reply = handle("POST", [self.token, "commands", str(command.pk), "response"], body({}))
        self.assertEqual(reply.code, handlers.BAD_REQUEST)

    def test_observe_signatures_change_only_when_content_changes(self):
        first = handlers.observe_signature(handlers.DESIRED, self.token)
        self.assertEqual(first, handlers.observe_signature(handlers.DESIRED, self.token))
        self.pump.set_desired(1, self.budi)
        self.assertNotEqual(handlers.observe_signature(handlers.DESIRED, self.token), first)

        before = handlers.observe_signature(handlers.COMMANDS, self.token)
        command = self.command()
        after = handlers.observe_signature(handlers.COMMANDS, self.token)
        self.assertNotEqual(after, before)
        # QUEUED -> SENT tidak mengubah ringkasan (tidak memicu kiriman ulang)...
        handle("GET", [self.token, "commands"])
        self.assertEqual(handlers.observe_signature(handlers.COMMANDS, self.token), after)
        # ...tetapi selesainya command mengubahnya.
        command.refresh_from_db()
        command.complete(True)
        self.assertNotEqual(handlers.observe_signature(handlers.COMMANDS, self.token), after)
        self.assertIsNone(handlers.observe_signature(handlers.DESIRED, "wrong"))


class ProtocolRecordedTests(CoapTestData):
    def test_http_api_records_http(self):
        self.client.post(
            reverse("api:telemetry", args=[self.token]), body({"t": 1}), content_type="application/json"
        )
        self.device.refresh_from_db()
        self.assertEqual(self.device.last_protocol, Device.Protocol.HTTP)

    def test_mqtt_records_mqtt(self):
        from mqtt_bridge import topics
        from mqtt_bridge.handlers import handle_message

        handle_message(topics.telemetry("p", self.token), body({"t": 1}), "p")
        self.device.refresh_from_db()
        self.assertEqual(self.device.last_protocol, Device.Protocol.MQTT)

    def test_device_page_shows_coap_tab_and_protocol(self):
        handle("POST", [self.token, "telemetry"], body({"t": 1}))
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(response, 'id="pane-coap"')
        self.assertContains(response, f"/api/v1/{self.token}/desired")
        self.assertContains(response, "via CoAP")


@unittest.skipIf(aiocoap is None, "aiocoap is not installed")
class ServerPlanTests(unittest.TestCase):
    def test_linux_listens_everywhere(self):
        from .resources import server_plan

        self.assertEqual(server_plan(platform="linux"), ("udp6", ["::"]))
        self.assertEqual(server_plan("10.0.0.5", platform="linux"), ("udp6", ["10.0.0.5"]))

    def test_macos_needs_explicit_addresses(self):
        from .resources import server_plan

        self.assertEqual(
            server_plan(platform="darwin", detect=lambda: "192.168.1.10"),
            ("simplesocketserver", ["127.0.0.1", "192.168.1.10"]),
        )
        # IP di COAP_PUBLIC_HOST lebih diutamakan daripada hasil deteksi.
        self.assertEqual(
            server_plan(public_host="10.1.2.3", platform="darwin", detect=lambda: "192.168.1.10")[1],
            ["127.0.0.1", "10.1.2.3"],
        )
        self.assertEqual(server_plan(platform="darwin", detect=lambda: None)[1], ["127.0.0.1"])
        self.assertEqual(server_plan("127.0.0.1, 10.0.0.2", platform="darwin")[1], ["127.0.0.1", "10.0.0.2"])


@unittest.skipIf(aiocoap is None, "aiocoap is not installed")
@mock.patch.dict(
    os.environ, {"AIOCOAP_SERVER_TRANSPORT": "simplesocketserver", "AIOCOAP_CLIENT_TRANSPORT": "simple6"}
)
class CoapServerEndToEndTests(TransactionTestCase):
    """Server aiocoap sungguhan di localhost; klien aiocoap mengirim permintaan lewat UDP."""

    PORT = 56831

    def setUp(self):
        self.budi = User.objects.create_user("budi", password="rahasia-12345")
        self.device = Device.objects.create(owner=self.budi, name="Node")
        self.pump = Variable.objects.create(
            device=self.device, key="pump", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )

    def test_post_get_and_observe(self):
        asyncio.run(self.scenario())

    async def scenario(self):
        from .resources import build_site, start_servers

        # simplesocketserver = transport yang dipakai di macOS; juga berjalan di Linux.
        site, api = build_site()
        servers, hosts = await start_servers(site, self.PORT, bind="127.0.0.1")
        self.assertEqual(hosts, ["127.0.0.1"])
        watcher = asyncio.create_task(api.watch(0.2))
        client = await aiocoap.Context.create_client_context()
        base = f"coap://127.0.0.1:{self.PORT}/api/v1/{self.device.access_token}"
        try:
            # 1) Telemetri
            request = aiocoap.Message(code=aiocoap.POST, uri=f"{base}/telemetry", payload=body({"temperature": 21.5}))
            response = await asyncio.wait_for(client.request(request).response, 5)
            self.assertEqual(response.code, aiocoap.CHANGED)
            self.assertEqual(json.loads(response.payload)["saved"], 1)

            # 2) Token salah -> 4.01
            request = aiocoap.Message(code=aiocoap.GET, uri=f"coap://127.0.0.1:{self.PORT}/api/v1/wrong/desired")
            response = await asyncio.wait_for(client.request(request).response, 5)
            self.assertEqual(response.code, aiocoap.UNAUTHORIZED)

            # 3) Observe desired: jawaban pertama {}, lalu kabar setelah ON ditekan.
            request = aiocoap.Message(code=aiocoap.GET, uri=f"{base}/desired", observe=0)
            pending = client.request(request)
            first = await asyncio.wait_for(pending.response, 5)
            self.assertEqual(json.loads(first.payload), {})
            await sync_to_async(self.pump.set_desired)(1, self.budi)
            notification = await asyncio.wait_for(pending.observation.__aiter__().__anext__(), 5)
            self.assertEqual(json.loads(notification.payload), {"pump": 1})
            pending.observation.cancel()
        finally:
            watcher.cancel()
            await client.shutdown()
            for server in servers:
                await server.shutdown()
