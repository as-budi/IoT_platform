"""Tes tahap 17: notifikasi Telegram (klien API, menyambungkan akun, aksi ECA dan node rule chain)."""
import io
import json
import urllib.error
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from rules.models import RuleAction, RuleExecution
from rules.portable import export_rule
from rules.tests import RuleTestData, at
from rules.tests_flow import FlowData

from . import link, telegram
from .models import TelegramBotState, TelegramLink, TelegramLinkCode

TOKEN = "123456:SECRET-token"
BOT = override_settings(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_BOT_USERNAME="iot_test_bot",
                        TELEGRAM_API_URL="https://api.telegram.test")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def answer(result=True, ok=True, description=""):
    body = {"ok": ok, "result": result} if ok else {"ok": False, "description": description}
    return FakeResponse(json.dumps(body).encode())


@BOT
class ClientTests(TestCase):
    def test_send_message_posts_json(self):
        with mock.patch("urllib.request.urlopen", return_value=answer({"message_id": 5})) as urlopen:
            self.assertEqual(telegram.send_message("42", "Hello"), {"message_id": 5})
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, f"https://api.telegram.test/bot{TOKEN}/sendMessage")
        self.assertEqual(json.loads(request.data)["chat_id"], "42")
        self.assertEqual(json.loads(request.data)["text"], "Hello")

    def test_long_text_is_cut(self):
        with mock.patch("urllib.request.urlopen", return_value=answer()) as urlopen:
            telegram.send_message("42", "x" * 5000)
        self.assertEqual(len(json.loads(urlopen.call_args[0][0].data)["text"]), telegram.MAX_TEXT)

    def test_errors_never_contain_the_token(self):
        blocked = urllib.error.HTTPError(
            "url", 403, "Forbidden", {},
            io.BytesIO(b'{"ok": false, "description": "Forbidden: bot was blocked by the user"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=blocked):
            with self.assertRaisesMessage(telegram.TelegramError, "Forbidden: bot was blocked by the user"):
                telegram.send_message("42", "Hi")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(f"no route to {TOKEN}")):
            try:
                telegram.send_message("42", "Hi")
            except telegram.TelegramError as exc:
                self.assertIn("Cannot reach Telegram", str(exc))

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_not_configured(self):
        self.assertFalse(telegram.enabled())
        with self.assertRaisesMessage(telegram.TelegramError, "not configured"):
            telegram.send_message("42", "Hi")


def start_update(update_id, chat_id, code, first_name="Budi"):
    return {"update_id": update_id, "message": {
        "chat": {"id": chat_id, "first_name": first_name, "username": first_name.lower()},
        "text": f"/start {code}"}}


@BOT
class LinkTests(RuleTestData):
    def setUp(self):
        self.client.force_login(self.siti)

    def test_connect_page_and_check(self):
        page = self.client.get(reverse("notifications:telegram_connect"))
        code = TelegramLinkCode.objects.get(user=self.siti).code
        self.assertContains(page, f"https://t.me/iot_test_bot?start={code}")

        updates = [start_update(10, 777, code, "Siti")]
        with mock.patch.object(telegram, "get_updates", return_value=updates) as get_updates, \
                mock.patch.object(telegram, "send_message") as reply:
            response = self.client.post(reverse("notifications:telegram_connect"))
        self.assertRedirects(response, reverse("accounts:profile"))
        get_updates.assert_called_once_with(0)
        chat = TelegramLink.objects.get(user=self.siti)
        self.assertEqual((chat.chat_id, chat.chat_name), ("777", "Siti (@siti)"))
        self.assertIn('account "siti"', reply.call_args[0][1])
        self.assertEqual(TelegramBotState.objects.get().next_update_id, 11, "update confirmed")
        self.assertFalse(TelegramLinkCode.objects.exists(), "code used once")

    def test_not_started_yet(self):
        self.client.get(reverse("notifications:telegram_connect"))
        with mock.patch.object(telegram, "get_updates", return_value=[]):
            response = self.client.post(reverse("notifications:telegram_connect"), follow=True)
        self.assertContains(response, "No Start message from you yet")

    def test_expired_code_and_other_users_in_the_same_batch(self):
        old = link.new_code(self.siti)
        TelegramLinkCode.objects.filter(code=old).update(created_at=timezone.now() - timedelta(minutes=20))
        fresh = link.new_code(self.budi)
        updates = [start_update(1, 111, old), start_update(2, 222, fresh), {"update_id": 3, "message": {"text": "hi"}}]
        with mock.patch.object(telegram, "get_updates", return_value=updates), \
                mock.patch.object(telegram, "send_message") as reply:
            linked = link.check_updates()
        self.assertEqual(linked, [self.budi])
        self.assertFalse(TelegramLink.objects.filter(user=self.siti).exists())
        self.assertIn("expired", reply.call_args_list[0][0][1])
        self.assertEqual(TelegramBotState.objects.get().next_update_id, 4)

    def test_profile_card_test_message_and_disconnect(self):
        self.assertContains(self.client.get(reverse("accounts:profile")), "Connect Telegram")
        TelegramLink.objects.create(user=self.siti, chat_id="777", chat_name="Siti")
        page = self.client.get(reverse("accounts:profile"))
        self.assertContains(page, "Connected to <strong>Siti</strong>")
        with mock.patch.object(telegram, "send_message") as send:
            self.client.post(reverse("notifications:telegram_test"))
        self.assertEqual(send.call_args[0][0], "777")
        self.client.post(reverse("notifications:telegram_disconnect"))
        self.assertFalse(TelegramLink.objects.exists())

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_profile_without_bot(self):
        self.assertContains(self.client.get(reverse("accounts:profile")), "no Telegram bot configured")


@BOT
class EcaTelegramTests(RuleTestData):
    def telegram_rule(self, recipients=""):
        return self.make_rule(clear=None, actions=[
            {"kind": RuleAction.Kind.TELEGRAM, "message": "{device}: soil {value}", "recipients": recipients},
        ])

    def test_sends_to_the_owners_chat(self):
        TelegramLink.objects.create(user=self.siti, chat_id="777")
        self.telegram_rule()
        with mock.patch.object(telegram, "send_message") as send:
            [execution] = self.send(15, at(0))
        send.assert_called_once_with("777", "Greenhouse Node: soil 15")
        self.assertEqual(execution.results[0], {"action": "Send Telegram message", "ok": True,
                                                "detail": "Telegram to the owner"})

    def test_owner_not_connected(self):
        self.telegram_rule()
        [execution] = self.send(15, at(0))
        self.assertFalse(execution.ok)
        self.assertIn("has not connected Telegram", execution.results[0]["detail"])

    def test_explicit_chats_and_partial_failure(self):
        self.telegram_rule("111, -100222")

        def fake(chat, text):
            if chat == "-100222":
                raise telegram.TelegramError("Telegram: Bad Request: chat not found")

        with mock.patch.object(telegram, "send_message", side_effect=fake):
            [execution] = self.send(15, at(0))
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)
        self.assertEqual(execution.results[0]["detail"],
                         "-100222: Telegram: Bad Request: chat not found (sent to 111)")

    def test_form_validation_and_json_round_trip(self):
        self.client.force_login(self.siti)
        rule = self.telegram_rule("111")
        data = export_rule(rule)
        self.assertEqual(data["actions"][0], {"kind": "TELEGRAM", "run_on": "ACTIVATE",
                                              "message": "{device}: soil {value}", "recipients": "111"})
        data["actions"][0]["recipients"] = "123, me@example.com"
        response = self.client.post(reverse("rules:import"), {"text": json.dumps({**data, "name": "Copy"})})
        self.assertContains(response, "Not a Telegram chat id: me@example.com")
        data["actions"][0]["recipients"] = " 123 ,-100456,@my_channel "
        self.client.post(reverse("rules:import"), {"text": json.dumps({**data, "name": "Copy"})})
        copy = RuleAction.objects.get(rule__name="Copy")
        self.assertEqual(copy.recipients, "123, -100456, @my_channel")


@BOT
class FlowTelegramTests(FlowData):
    def test_node_sends_with_message_keys(self):
        TelegramLink.objects.create(user=self.siti, chat_id="777")
        rule = self.chain(
            [("notify", "TELEGRAM", {"message": "{deviceName}: {temperature} °C"})],
            [("Input", "SUCCESS", "notify")],
        )
        with mock.patch.object(telegram, "send_message") as send:
            self.send(timezone.now(), temperature=31)
        send.assert_called_once_with("777", "Greenhouse Node: 31 °C")
        self.assertEqual(self.path(rule), [("notify", "SUCCESS")])

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_failure_branch_when_not_configured(self):
        rule = self.chain([("notify", "TELEGRAM", {})], [("Input", "SUCCESS", "notify")])
        self.send(timezone.now(), temperature=31)
        self.assertEqual(self.path(rule), [("notify", "FAILURE")])
