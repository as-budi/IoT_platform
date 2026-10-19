"""Tes jembatan MQTT. Tidak butuh broker: koneksi diganti fungsi tiruan."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from devices.models import Command, CommandStatus, Device, Telemetry, Variable

from . import topics
from .handlers import handle_message
from .outbox import Outbox

User = get_user_model()
PREFIX = "test-prefix"


class TopicTests(SimpleTestCase):
    def test_build_topics(self):
        self.assertEqual(topics.telemetry("p", "TOK"), "p/TOK/telemetry")
        self.assertEqual(topics.desired("p", "TOK"), "p/TOK/desired")
        self.assertEqual(topics.command("p", "TOK", 7), "p/TOK/commands/7")
        self.assertEqual(topics.command_response("p", "TOK", 7), "p/TOK/commands/7/response")
        self.assertEqual(topics.firmware("p", "TOK"), "p/TOK/firmware")
        self.assertEqual(topics.status("p", "TOK"), "p/TOK/status")
        self.assertEqual(
            topics.subscriptions("p"),
            ["p/+/telemetry", "p/+/commands/+/response", "p/+/firmware", "p/+/status"],
        )

    def test_parse_topics(self):
        self.assertEqual(topics.parse("p/TOK/telemetry", "p"), ("telemetry", "TOK", None))
        self.assertEqual(topics.parse("p/TOK/firmware", "p"), ("firmware", "TOK", None))
        self.assertEqual(
            topics.parse("p/TOK/commands/12/response", "p"), ("command_response", "TOK", 12)
        )
        # Prefix boleh terdiri dari beberapa level.
        self.assertEqual(topics.parse("a/b/TOK/telemetry", "a/b"), ("telemetry", "TOK", None))

    def test_foreign_or_malformed_topics_are_ignored(self):
        for topic in (
            "other/TOK/telemetry",
            "p/TOK/desired",
            "p//telemetry",
            "p/TOK/commands/abc/response",
            "p/TOK/telemetry/extra",
            "pp/TOK/telemetry",
        ):
            self.assertIsNone(topics.parse(topic, "p"), topic)


class MqttTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.budi, name="Node Budi")
        cls.other = Device.objects.create(owner=cls.budi, name="Node Lain")
        cls.lamp = Variable.objects.create(
            device=cls.device, key="lamp", kind=Variable.Kind.ACTUATOR,
            value_type=Variable.ValueType.BOOLEAN,
        )

    def receive(self, topic, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return handle_message(topic, raw, PREFIX)

    def make_command(self, device=None, **fields):
        fields.setdefault("expires_at", timezone.now() + timedelta(minutes=5))
        return Command.objects.create(device=device or self.device, method="reboot", **fields)


class IncomingTelemetryTests(MqttTestData):
    def topic(self, token=None):
        return topics.telemetry(PREFIX, token or self.device.access_token)

    def test_telemetry_is_saved_and_new_keys_become_variables(self):
        result = self.receive(self.topic(), {"temperature": 25.1, "humidity": 60})
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.device, self.device)
        self.assertEqual(Telemetry.objects.filter(variable__device=self.device).count(), 2)
        self.assertIn("temperature", result.message)

    def test_timestamped_payload_is_accepted(self):
        ts = int((timezone.now() - timedelta(minutes=1)).timestamp() * 1000)
        result = self.receive(self.topic(), {"ts": ts, "values": {"temperature": 20}})
        self.assertTrue(result.ok)

    def test_unknown_token(self):
        result = self.receive(self.topic("wrong-token"), {"temperature": 1})
        self.assertFalse(result.ok)
        self.assertIsNone(result.device)
        self.assertFalse(Telemetry.objects.exists())

    def test_inactive_device(self):
        self.device.is_active = False
        self.device.save()
        result = self.receive(self.topic(), {"temperature": 1})
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Device is inactive.")

    def test_invalid_payloads_are_rejected_whole(self):
        for payload in (b"not json", b'{"t": NaN}', {"temperature": "hot"}, {"temperature": 1, "bad key": 2}):
            result = self.receive(self.topic(), payload)
            self.assertFalse(result.ok, payload)
            self.assertEqual(result.device, self.device)
        self.assertFalse(Telemetry.objects.exists())

    def test_reported_state_syncs_actuator(self):
        self.lamp.set_desired(1, self.budi)
        self.receive(self.topic(), {"lamp": True})
        self.lamp.refresh_from_db()
        self.assertEqual(self.lamp.sync_status, Variable.SyncStatus.SYNCED)

    def test_foreign_topic_returns_none(self):
        self.assertIsNone(handle_message("someone/else/telemetry", b"{}", PREFIX))


class IncomingCommandResponseTests(MqttTestData):
    def respond(self, command, payload, device=None):
        token = (device or self.device).access_token
        return self.receive(topics.command_response(PREFIX, token, command.pk), payload)

    def test_success(self):
        command = self.make_command(status=CommandStatus.SENT)
        result = self.respond(command, {"success": True, "uptime_ms": 1234})
        self.assertTrue(result.ok, result.message)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.SUCCEEDED)
        self.assertEqual(command.response, {"success": True, "uptime_ms": 1234})

    def test_failure(self):
        command = self.make_command(status=CommandStatus.SENT)
        self.respond(command, {"success": False, "error": "jammed"})
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.FAILED)

    def test_missing_success_means_success(self):
        command = self.make_command(status=CommandStatus.SENT)
        self.respond(command, {"note": "done"})
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.SUCCEEDED)

    def test_success_must_be_boolean(self):
        command = self.make_command(status=CommandStatus.SENT)
        for payload in ({"success": "yes"}, [1, 2]):
            self.assertFalse(self.respond(command, payload).ok)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.SENT)

    def test_cannot_answer_another_devices_command(self):
        command = self.make_command(device=self.other, status=CommandStatus.SENT)
        result = self.respond(command, {"success": True})
        self.assertFalse(result.ok)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.SENT)

    def test_late_answer_to_expired_command_is_ignored(self):
        command = self.make_command(
            status=CommandStatus.SENT, expires_at=timezone.now() - timedelta(seconds=1)
        )
        result = self.respond(command, {"success": True})
        self.assertFalse(result.ok)
        self.assertIn("expired", result.message)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.EXPIRED)

    def test_second_answer_is_ignored(self):
        command = self.make_command(status=CommandStatus.SENT)
        self.respond(command, {"success": True})
        self.assertFalse(self.respond(command, {"success": False}).ok)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.SUCCEEDED)


class FakeBroker:
    """Pengganti koneksi MQTT: mencatat pesan yang di-publish."""

    def __init__(self, connected=True):
        self.connected = connected
        self.messages = []

    def publish(self, topic, payload, retain):
        if not self.connected:
            return False
        self.messages.append((topic, payload, retain))
        return True


class OutboxTests(MqttTestData):
    def setUp(self):
        self.outbox = Outbox(PREFIX)
        self.broker = FakeBroker()

    def flush(self):
        return self.outbox.flush(self.broker.publish)

    def test_desired_state_is_sent_retained_once(self):
        self.lamp.set_desired(1, self.budi)
        self.assertEqual(self.flush(), (1, 0))
        self.assertEqual(
            self.broker.messages, [(topics.desired(PREFIX, self.device.access_token), {"lamp": 1}, True)]
        )
        # Tidak berubah -> tidak dikirim ulang.
        self.assertEqual(self.flush(), (0, 0))
        # Permintaan baru -> dikirim lagi.
        self.lamp.set_desired(0, self.budi)
        self.flush()
        self.assertEqual(self.broker.messages[-1][1], {"lamp": 0})

    def test_reset_sends_everything_again(self):
        self.lamp.set_desired(1, self.budi)
        self.flush()
        self.outbox.reset()
        self.assertEqual(self.flush(), (1, 0))

    def test_all_actuators_of_a_device_share_one_message(self):
        fan = Variable.objects.create(device=self.device, key="fan_speed", kind=Variable.Kind.ACTUATOR)
        self.lamp.set_desired(1, self.budi)
        fan.set_desired(42.5, self.budi)
        self.flush()
        self.assertEqual(self.broker.messages[0][1], {"fan_speed": 42.5, "lamp": 1})

    def test_inactive_device_gets_nothing(self):
        self.lamp.set_desired(1, self.budi)
        self.make_command()
        self.device.is_active = False
        self.device.save()
        self.assertEqual(self.flush(), (0, 0))

    def test_queued_command_is_sent_and_marked(self):
        command = self.make_command(params={"seconds": 5})
        self.assertEqual(self.flush(), (0, 1))
        topic, payload, retain = self.broker.messages[0]
        self.assertEqual(topic, topics.command(PREFIX, self.device.access_token, command.pk))
        self.assertEqual(payload, {"id": command.pk, "method": "reboot", "params": {"seconds": 5}})
        self.assertFalse(retain)
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.SENT)
        self.assertIsNotNone(command.sent_at)
        # Sudah SENT -> tidak dikirim dua kali.
        self.assertEqual(self.flush(), (0, 0))

    def test_expired_command_is_never_sent(self):
        command = self.make_command(expires_at=timezone.now() - timedelta(seconds=1))
        self.flush()
        self.assertEqual(self.broker.messages, [])
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.EXPIRED)

    def test_nothing_is_marked_when_broker_is_down(self):
        self.lamp.set_desired(1, self.budi)
        command = self.make_command()
        self.broker.connected = False
        self.assertEqual(self.flush(), (0, 0))
        command.refresh_from_db()
        self.assertEqual(command.status, CommandStatus.QUEUED)
        # Setelah tersambung lagi, semuanya terkirim.
        self.broker.connected = True
        self.assertEqual(self.flush(), (1, 1))


@override_settings(MQTT_PUBLIC_HOST="broker.example.org", MQTT_PUBLIC_PORT=1883, MQTT_PUBLIC_TLS=False,
                   MQTT_DEVICE_LOGIN=False, MQTT_TOPIC_PREFIX=PREFIX)
class DevicePageMqttTests(MqttTestData):
    def test_device_page_lists_mqtt_topics(self):
        self.client.force_login(self.budi)
        response = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(response, "-h broker.example.org -p 1883")
        self.assertContains(response, topics.telemetry(PREFIX, self.device.access_token))
        self.assertContains(response, topics.desired(PREFIX, self.device.access_token))
