"""
Tes tahap 22: platform sebagai penyedia login (OAuth 2.0 / OpenID Connect) dan API v2.

Alur yang diuji sama dengan yang dijalankan aplikasi MQTT client sungguhan:

    1. browser -> /o/authorize/?client_id&redirect_uri&scope&state&nonce&code_challenge
    2. login + persetujuan -> redirect_uri?code=...&state=...
    3. server aplikasi -> POST /o/token/ (code + code_verifier + client secret)
       -> access_token, refresh_token, id_token (JWT bertanda tangan RS256)
    4. server aplikasi -> GET /api/v2/me/devices dengan "Authorization: Bearer ..."
"""
import base64
import hashlib
import io
import json
import os
import secrets
import stat
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from jwcrypto import jwk, jwt

from accounts.models import User
from activity.models import ActivityEvent
from devices.models import Device, Variable
from sharing.models import DeviceShare, Role

from .checks import oidc_configuration
from .keys import generate_private_key_pem
from .models import Consent

KEY = generate_private_key_pem()
ISSUER = "http://testserver/o"
REDIRECT = "https://mqtt.example.org/oidc/callback/"
PROVIDER = {
    **settings.OAUTH2_PROVIDER,
    "OIDC_RSA_PRIVATE_KEY": KEY,
    "OIDC_ISS_ENDPOINT": ISSUER,
    "ALLOWED_REDIRECT_URI_SCHEMES": ["https"],
    "ALWAYS_RELOAD_OAUTHLIB_CORE": True,
}


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@override_settings(OAUTH2_PROVIDER=PROVIDER, OIDC_ISSUER="https://iot.example.org/o",
                   MQTT_PUBLIC_HOST="iot.example.org", MQTT_PUBLIC_PORT=8883, MQTT_PUBLIC_TLS=True,
                   MQTT_DEVICE_LOGIN=True, MQTT_TOPIC_PREFIX="iot", MQTT_PUBLIC_WS_URL="wss://iot.example.org/mqtt")
class OAuthTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345", email="budi@example.com",
                                            first_name="Budi", last_name="Santoso")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.boss = User.objects.create_user("boss", password="rahasia-12345", role=User.Role.ADMIN)
        out = io.StringIO()
        call_command("oauth_client", "MQTT Client", "--redirect-uri", REDIRECT,
                     "--logout-uri", "https://mqtt.example.org/", stdout=out)
        lines = dict(line.split(":", 1) for line in out.getvalue().splitlines() if line.startswith("client_"))
        cls.client_id = lines["client_id"].strip()
        cls.client_secret = lines["client_secret"].strip()

    def authorize_url(self, scope="openid profile email devices:read", **extra):
        self.verifier, challenge = pkce_pair()
        self.state, self.nonce = secrets.token_urlsafe(8), secrets.token_urlsafe(8)
        params = {
            "response_type": "code", "client_id": self.client_id, "redirect_uri": REDIRECT, "scope": scope,
            "state": self.state, "nonce": self.nonce, "code_challenge": challenge, "code_challenge_method": "S256",
        }
        params.update(extra)
        return reverse("oauth2_provider:authorize") + "?" + urlencode(params)

    def approve(self, user=None, scope="openid profile email devices:read"):
        """Login + setujui -> kode otorisasi."""
        self.client.force_login(user or self.budi)
        page = self.client.get(self.authorize_url(scope))
        if page.status_code == 200:   # halaman persetujuan
            form = page.context["form"]
            data = {name: field.value() or "" for name, field in form.fields.items() if name != "allow"
                    for field in [form[name]]}
            data["allow"] = "Authorize"
            page = self.client.post(reverse("oauth2_provider:authorize"), data)
        self.assertEqual(page.status_code, 302, page.content[:300])
        query = parse_qs(urlparse(page["Location"]).query)
        self.assertEqual(query["state"], [self.state])
        return query["code"][0]

    def exchange(self, code, verifier=None):
        return self.client.post(reverse("oauth2_provider:token"), {
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
            "client_id": self.client_id, "client_secret": self.client_secret,
            "code_verifier": verifier or self.verifier,
        })

    def tokens(self, user=None, scope="openid profile email devices:read"):
        response = self.exchange(self.approve(user, scope))
        self.assertEqual(response.status_code, 200, response.content)
        self.client.logout()          # API dipanggil server aplikasi, tanpa cookie sesi
        return response.json()

    def api(self, name, access_token):
        return self.client.get(reverse(name), HTTP_AUTHORIZATION=f"Bearer {access_token}")


class AuthorizationFlowTests(OAuthTestCase):
    def test_anonymous_user_is_sent_to_login_and_back(self):
        url = self.authorize_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith(reverse("accounts:login")))
        self.assertEqual(parse_qs(urlparse(location).query)["next"], [url])

    def test_consent_page_then_code_and_tokens(self):
        self.client.force_login(self.budi)
        page = self.client.get(self.authorize_url())
        self.assertContains(page, "Allow MQTT Client?")
        # Scope devices:read ditandai mencolok: memberi akses ke access token device.
        self.assertContains(page, "list-group-item-warning")
        self.assertContains(page, "including the access tokens")

        code = self.approve()
        self.assertTrue(Consent.objects.filter(user=self.budi, scope__contains="devices:read").exists())
        self.assertTrue(ActivityEvent.objects.filter(verb="oauth.authorized", owner=self.budi).exists())

        response = self.exchange(code)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["token_type"], "Bearer")
        self.assertEqual(data["expires_in"], settings.OAUTH2_PROVIDER["ACCESS_TOKEN_EXPIRE_SECONDS"])
        self.assertIn("refresh_token", data)

        # id_token: JWT yang ditandatangani kunci privat platform; diperiksa dengan kunci
        # PUBLIK dari /o/.well-known/jwks.json, seperti yang dilakukan aplikasi klien.
        jwks = jwk.JWKSet.from_json(self.client.get(reverse("oauth2_provider:jwks-info")).content)
        claims = json.loads(jwt.JWT(jwt=data["id_token"], key=jwks).claims)
        self.assertEqual(claims["iss"], ISSUER)
        self.assertEqual(claims["aud"], self.client_id)
        self.assertEqual(claims["sub"], str(self.budi.pk))
        self.assertEqual(claims["nonce"], self.nonce)
        self.assertEqual(claims["preferred_username"], "budi")
        self.assertEqual(claims["name"], "Budi Santoso")
        self.assertEqual(claims["email"], "budi@example.com")

    def test_code_works_once_and_needs_the_pkce_verifier(self):
        code = self.approve()
        self.assertEqual(self.exchange(code, verifier="x" * 50).status_code, 400)   # verifier salah
        code = self.approve()
        self.assertEqual(self.exchange(code).status_code, 200)
        self.assertEqual(self.exchange(code).status_code, 400)                     # dipakai dua kali

    def test_pkce_is_required(self):
        self.client.force_login(self.budi)
        url = self.authorize_url()
        url = url.replace("code_challenge=", "x_challenge=")
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("code=", response.get("Location", ""))

    def test_unknown_redirect_uri_is_refused_without_redirecting(self):
        self.client.force_login(self.budi)
        response = self.client.get(self.authorize_url(redirect_uri="https://evil.example.com/cb"))
        # Alamat kembali tidak terdaftar: JANGAN dialihkan ke sana (bisa situs penipu).
        self.assertContains(response, "cannot be completed", status_code=400)
        self.assertNotIn("Location", response)

    def test_remembered_consent_skips_the_page(self):
        self.approve()
        self.client.force_login(self.budi)
        response = self.client.get(self.authorize_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("code=", response["Location"])
        # Scope yang belum pernah disetujui -> ditanya lagi.
        Consent.objects.filter(user=self.budi).update(scope="openid profile")
        self.assertEqual(self.client.get(self.authorize_url()).status_code, 200)
        # prompt=consent selalu menampilkan halaman.
        Consent.objects.filter(user=self.budi).update(scope="openid profile email devices:read")
        self.assertEqual(self.client.get(self.authorize_url(prompt="consent")).status_code, 200)

    def test_cancel_sends_access_denied(self):
        self.client.force_login(self.budi)
        page = self.client.get(self.authorize_url())
        form = page.context["form"]
        data = {name: form[name].value() or "" for name in form.fields if name != "allow"}
        response = self.client.post(reverse("oauth2_provider:authorize"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlparse(response["Location"]).query)["error"], ["access_denied"])
        self.assertFalse(Consent.objects.exists())

    def test_refresh_token_rotates_and_reuse_revokes_the_family(self):
        first = self.tokens()
        url = reverse("oauth2_provider:token")
        data = {"grant_type": "refresh_token", "client_id": self.client_id, "client_secret": self.client_secret}
        second = self.client.post(url, {**data, "refresh_token": first["refresh_token"]})
        self.assertEqual(second.status_code, 200)
        second = second.json()
        self.assertNotEqual(second["refresh_token"], first["refresh_token"])
        self.assertEqual(self.api("api:v2_me", second["access_token"]).status_code, 200)
        # Refresh token lama dipakai lagi (tanda dicuri) -> ditolak, dan token baru ikut dicabut.
        self.assertEqual(self.client.post(url, {**data, "refresh_token": first["refresh_token"]}).status_code, 400)
        self.assertEqual(self.api("api:v2_me", second["access_token"]).status_code, 401)

    def test_userinfo_follows_scopes(self):
        tokens = self.tokens(scope="openid profile")
        response = self.client.get(reverse("oauth2_provider:user-info"),
                                   HTTP_AUTHORIZATION=f"Bearer {tokens['access_token']}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["preferred_username"], "budi")
        self.assertNotIn("email", data)

    def test_discovery_document(self):
        data = self.client.get("/o/.well-known/openid-configuration").json()
        self.assertEqual(data["issuer"], ISSUER)
        self.assertEqual(data["authorization_endpoint"], "http://testserver/o/authorize/")
        self.assertEqual(data["response_types_supported"], ["code"])
        self.assertIn("devices:read", data["scopes_supported"])
        self.assertIn("create", data["prompt_values_supported"])

    def test_prompt_create_goes_to_sign_up_and_back(self):
        url = self.authorize_url(prompt="create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        location = urlparse(response["Location"])
        self.assertEqual(location.path, reverse("accounts:register"))
        back = parse_qs(location.query)["next"][0]
        self.assertTrue(back.startswith("http://testserver/o/authorize/"))
        self.assertNotIn("prompt=create", back)


class ApiV2Tests(OAuthTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.own = Device.objects.create(owner=cls.budi, name="Greenhouse")
        Variable.objects.create(device=cls.own, key="temp", unit="°C")
        cls.viewed = Device.objects.create(owner=cls.siti, name="Siti viewer device")
        cls.edited = Device.objects.create(owner=cls.siti, name="Siti editor device")
        cls.private = Device.objects.create(owner=cls.siti, name="Siti private")
        DeviceShare.objects.create(device=cls.viewed, user=cls.budi, role=Role.VIEWER)
        DeviceShare.objects.create(device=cls.edited, user=cls.budi, role=Role.EDITOR)

    def test_requires_a_valid_bearer_token(self):
        self.assertEqual(self.client.get(reverse("api:v2_me")).status_code, 401)
        response = self.api("api:v2_me", "not-a-token")
        self.assertEqual(response.status_code, 401)
        self.assertIn('error="invalid_token"', response["WWW-Authenticate"])
        # Sesi login browser saja tidak cukup: API ini untuk aplikasi luar.
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(reverse("api:v2_me")).status_code, 401)

    def test_token_in_query_string_is_refused(self):
        tokens = self.tokens()
        response = self.client.get(reverse("api:v2_me") + "?access_token=" + tokens["access_token"])
        self.assertEqual(response.status_code, 401)

    def test_me(self):
        tokens = self.tokens()
        data = self.api("api:v2_me", tokens["access_token"]).json()
        self.assertEqual((data["id"], data["username"], data["email"], data["role"]),
                         (self.budi.pk, "budi", "budi@example.com", "user"))

    def test_scope_is_enforced(self):
        tokens = self.tokens(scope="openid profile")
        response = self.api("api:v2_devices", tokens["access_token"])
        self.assertEqual(response.status_code, 403)
        self.assertIn('scope="devices:read"', response["WWW-Authenticate"])
        self.assertNotIn("email", self.api("api:v2_me", tokens["access_token"]).json())

    def test_devices_with_roles_tokens_and_topics(self):
        tokens = self.tokens()
        data = self.api("api:v2_devices", tokens["access_token"]).json()
        self.assertEqual(data["broker"], {
            "mqtt": {"host": "iot.example.org", "port": 8883, "tls": True},
            "websocket": "wss://iot.example.org/mqtt", "device_login": True, "topic_prefix": "iot",
        })
        devices = {d["name"]: d for d in data["devices"]}
        self.assertEqual(set(devices), {"Greenhouse", "Siti viewer device", "Siti editor device"})

        own = devices["Greenhouse"]
        token = self.own.access_token
        self.assertEqual((own["role"], own["owner"], own["access_token"]), ("owner", "budi", token))
        self.assertEqual(own["topics"]["telemetry"], f"iot/{token}/telemetry")
        self.assertEqual(own["topics"]["command_response"], f"iot/{token}/commands/{{id}}/response")
        self.assertEqual(own["variables"][0]["key"], "temp")
        self.assertEqual(own["variables"][0]["unit"], "°C")

        # Editor mendapat token (sama dengan kartu "Connect a device"); Viewer tidak.
        self.assertEqual(devices["Siti editor device"]["access_token"], self.edited.access_token)
        self.assertIsNone(devices["Siti viewer device"]["access_token"])
        self.assertIsNone(devices["Siti viewer device"]["topics"])
        self.assertEqual(devices["Siti viewer device"]["role"], "viewer")

    def test_admin_gets_only_own_and_shared_devices(self):
        tokens = self.tokens(user=self.boss)
        names = [d["name"] for d in self.api("api:v2_devices", tokens["access_token"]).json()["devices"]]
        self.assertEqual(names, [])

    def test_deactivated_account_loses_access_at_once(self):
        tokens = self.tokens()
        self.budi.is_active = False
        self.budi.save()
        self.assertEqual(self.api("api:v2_me", tokens["access_token"]).status_code, 401)
        refresh = self.client.post(reverse("oauth2_provider:token"), {
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": self.client_id, "client_secret": self.client_secret,
        })
        self.assertEqual(refresh.status_code, 400)


class ConnectedAppsTests(OAuthTestCase):
    def test_list_and_revoke(self):
        tokens = self.tokens()
        self.client.force_login(self.budi)
        page = self.client.get(reverse("oauth:connected_apps"))
        self.assertContains(page, "MQTT Client")
        self.assertContains(page, "including the access tokens")
        self.assertContains(self.client.get(reverse("accounts:profile")), "1 app can sign you in")

        consent = Consent.objects.get(user=self.budi)
        self.assertIsNotNone(consent.last_used_at)
        response = self.client.post(reverse("oauth:revoke", args=[consent.pk]))
        self.assertRedirects(response, reverse("oauth:connected_apps"))
        self.assertFalse(Consent.objects.exists())
        self.assertTrue(ActivityEvent.objects.filter(verb="oauth.revoked").exists())
        self.client.logout()
        self.assertEqual(self.api("api:v2_me", tokens["access_token"]).status_code, 401)

    def test_cannot_revoke_someone_elses_consent(self):
        self.tokens()
        consent = Consent.objects.get(user=self.budi)
        self.client.force_login(self.siti)
        self.assertEqual(self.client.post(reverse("oauth:revoke", args=[consent.pk])).status_code, 404)
        self.assertTrue(Consent.objects.exists())

    def test_rotate_secret(self):
        out = io.StringIO()
        call_command("oauth_client", "MQTT Client", "--redirect-uri", REDIRECT, "--rotate-secret", stdout=out)
        self.assertIn(self.client_id, out.getvalue())
        new_secret = [line for line in out.getvalue().splitlines() if line.startswith("client_secret")][0]
        self.assertNotIn(self.client_secret, new_secret)
        code = self.approve()
        self.assertEqual(self.exchange(code).status_code, 401)    # secret lama tidak berlaku


@override_settings(ALLOW_SIGNUP=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SignUpNextTests(TestCase):
    """Pengguna baru dari aplikasi lain kembali ke aplikasi itu setelah akunnya aktif."""

    NEXT = "/o/authorize/?client_id=abc&state=xyz"

    def sign_up(self, next_url):
        return self.client.post(reverse("accounts:register") + "?" + urlencode({"next": next_url}), {
            "username": "nina", "email": "nina@example.com",
            "password1": "sangat-rahasia-123", "password2": "sangat-rahasia-123", "next": next_url,
        })

    def activation_link(self):
        body = mail.outbox[-1].body
        return next(line.strip() for line in body.splitlines() if "/accounts/activate/" in line)

    def test_next_survives_sign_up_and_activation(self):
        login = self.client.get(reverse("accounts:login") + "?" + urlencode({"next": self.NEXT}))
        self.assertContains(login, reverse("accounts:register") + "?" + urlencode({"next": self.NEXT}))

        response = self.sign_up(self.NEXT)
        self.assertEqual(response.status_code, 302)
        self.assertIn(urlencode({"next": self.NEXT}), response["Location"])
        link = self.activation_link()
        self.assertIn(urlencode({"next": self.NEXT}), link)

        path = urlparse(link).path + "?" + urlparse(link).query
        response = self.client.post(path)
        self.assertRedirects(response, self.NEXT, fetch_redirect_response=False)
        self.assertTrue(User.objects.get(username="nina").is_active)

    def test_foreign_next_is_ignored(self):
        self.sign_up("https://evil.example.com/steal")
        link = self.activation_link()
        self.assertNotIn("evil", link)
        response = self.client.post(urlparse(link).path)
        self.assertRedirects(response, reverse("dashboard:index"), fetch_redirect_response=False)


class KeyAndChecksTests(TestCase):
    def test_oidc_key_command_writes_a_private_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "oidc" / "private.pem"
            with override_settings(OIDC_RSA_PRIVATE_KEY_FILE=path):
                call_command("oidc_key", stdout=io.StringIO())
                self.assertIn("BEGIN PRIVATE KEY", path.read_text())
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                # Tidak menimpa kunci yang sudah ada tanpa --force.
                with self.assertRaises(Exception):
                    call_command("oidc_key", stdout=io.StringIO())

    def test_deploy_checks(self):
        with override_settings(OAUTH2_PROVIDER={**settings.OAUTH2_PROVIDER, "OIDC_RSA_PRIVATE_KEY": ""},
                               OIDC_ISSUER="http://iot.example.org/o"):
            ids = {error.id for error in oidc_configuration(None)}
        self.assertEqual(ids, {"oauth.E001", "oauth.E002"})
        with override_settings(OAUTH2_PROVIDER=PROVIDER, OIDC_ISSUER="https://iot.example.org/o"):
            self.assertEqual(oidc_configuration(None), [])
