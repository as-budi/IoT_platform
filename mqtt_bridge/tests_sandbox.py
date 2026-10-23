"""Tes akun MQTT sandbox per pengguna (tahap 23)."""
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activity.models import ActivityEvent
from oauth.tests import OAuthTestCase

from .models import SandboxAccount
from .tests_accounts import MqttAccountsTests


@override_settings(MQTT_SANDBOX_ENABLED=True)
class SandboxPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")

    def setUp(self):
        self.client.force_login(self.budi)

    def test_enable_reset_disable(self):
        self.assertContains(self.client.get(reverse("accounts:profile")), "Create sandbox account")
        self.assertRedirects(self.client.post(reverse("sandbox:enable")), reverse("accounts:profile"))
        account = SandboxAccount.objects.get(user=self.budi)
        self.assertEqual((account.username, account.topic_root), (f"sbx-{self.budi.pk}", "sandbox/budi"))
        page = self.client.get(reverse("accounts:profile"))
        self.assertContains(page, account.username)
        self.assertContains(page, "sandbox/budi/#")
        old = account.password
        self.client.post(reverse("sandbox:reset"))
        account.refresh_from_db()
        self.assertNotEqual(account.password, old)
        self.client.post(reverse("sandbox:disable"))
        self.assertFalse(SandboxAccount.objects.exists())
        verbs = set(ActivityEvent.objects.values_list("verb", flat=True))
        self.assertTrue({"sandbox.enabled", "sandbox.reset", "sandbox.disabled"} <= verbs)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("sandbox:enable")).status_code, 405)

    @override_settings(MQTT_SANDBOX_ENABLED=False)
    def test_disabled_on_server(self):
        self.assertNotContains(self.client.get(reverse("accounts:profile")), "MQTT sandbox")
        self.assertEqual(self.client.post(reverse("sandbox:enable")).status_code, 404)

    def test_unsafe_username_gets_numeric_root(self):
        user = User.objects.create_user("a+b@x", password="rahasia-12345")
        self.assertEqual(SandboxAccount.objects.create(user=user).topic_root, f"sandbox/~{user.pk}")
        public = User.objects.create_user("public", password="rahasia-12345")
        self.assertEqual(SandboxAccount.objects.create(user=public).topic_root, f"sandbox/~{public.pk}")


class SandboxAccountsFileTests(MqttAccountsTests):
    """mqtt_accounts menulis akun + ACL sandbox (memakai mosquitto_passwd palsu dari MqttAccountsTests)."""

    @override_settings(MQTT_SANDBOX_ENABLED=True)
    def test_sandbox_accounts_in_files(self):
        budi = User.objects.create_user("budi", password="rahasia-12345")
        gone = User.objects.create_user("gone", password="rahasia-12345", is_active=False)
        account = SandboxAccount.objects.create(user=budi)
        SandboxAccount.objects.create(user=gone)
        self.run_command()
        passwd = (self.dir / "passwd").read_text()
        acl = (self.dir / "acl").read_text()
        self.assertIn(f"{account.username}:$7$fakehash-{account.password}", passwd)
        self.assertNotIn(f"sbx-{gone.pk}:", passwd)
        self.assertIn(f"user {account.username}\ntopic readwrite sandbox/budi/#\ntopic readwrite sandbox/public/#", acl)

    @override_settings(MQTT_SANDBOX_ENABLED=False)
    def test_sandbox_off(self):
        SandboxAccount.objects.create(user=User.objects.create_user("budi", password="rahasia-12345"))
        self.run_command()
        self.assertNotIn("sbx-", (self.dir / "passwd").read_text())


@override_settings(MQTT_SANDBOX_ENABLED=True)
class SandboxApiTests(OAuthTestCase):
    def test_sandbox_endpoint(self):
        tokens = self.tokens(scope="openid mqtt:sandbox")
        data = self.api("api:v2_sandbox", tokens["access_token"]).json()
        self.assertEqual((data["available"], data["enabled"]), (True, False))
        self.assertTrue(data["manage_url"].endswith("/accounts/profile/#mqtt-sandbox"))
        account = SandboxAccount.objects.create(user=self.budi)
        data = self.api("api:v2_sandbox", tokens["access_token"]).json()
        self.assertEqual((data["username"], data["password"], data["topic_root"], data["shared_root"]),
                         (account.username, account.password, "sandbox/budi", "sandbox/public"))
        self.assertEqual(data["broker"]["websocket"], "wss://iot.example.org/mqtt")

    def test_scope_required(self):
        tokens = self.tokens(scope="openid profile")
        self.assertEqual(self.api("api:v2_sandbox", tokens["access_token"]).status_code, 403)
