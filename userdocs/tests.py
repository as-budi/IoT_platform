"""Tes tahap 25: dokumentasi pengguna, pencarian, dan tidak adanya keterangan pengembangan di halaman."""
import re

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from devices.models import Device
from sharing.models import DeviceShare

from . import search
from .context import docs_context
from .pages import BY_SLUG, PAGES, neighbours, sections

User = get_user_model()

# Kata-kata yang hanya bermakna bagi pengembang/pengelola server. Tidak boleh terlihat
# pengguna biasa di halaman dokumentasi maupun halaman platform.
DEVELOPER_WORDS = re.compile(
    r"manage\.py|runserver|runall|\.env\b|mqtt_bridge|coap_server|rule_engine|housekeeping|"
    r"docs/[a-z]|tahap|brew install|this computer|DEBUG", re.I)


def visible_text(html):
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@override_settings(
    OTA_BASE_URL="https://iot.example.org", MQTT_PUBLIC_HOST="iot.example.org", MQTT_PUBLIC_PORT=8883,
    MQTT_PUBLIC_TLS=True, MQTT_DEVICE_LOGIN=True, MQTT_TOPIC_PREFIX="kampus-iot", COAP_PUBLIC_HOST="iot.example.org",
)
class DocsPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.siti, name="Greenhouse Node")
        cls.other = Device.objects.create(owner=cls.budi, name="Budi Node")

    def setUp(self):
        search._index.clear()

    def test_registry(self):
        self.assertEqual(len(BY_SLUG), len(PAGES), "slugs must be unique")
        self.assertEqual(sum(len(items) for _, items in sections()), len(PAGES))
        self.assertEqual(neighbours(PAGES[0].slug)[0], None)
        self.assertEqual(neighbours(PAGES[-1].slug)[1], None)

    def test_every_page_renders_for_visitors_and_users(self):
        for logged_in in (False, True):
            if logged_in:
                self.client.force_login(self.siti)
            for url in [reverse("userdocs:index")] + [reverse("userdocs:page", args=[p.slug]) for p in PAGES]:
                with self.subTest(url=url, logged_in=logged_in):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    text = visible_text(response.content.decode())
                    self.assertIsNone(DEVELOPER_WORDS.search(text), DEVELOPER_WORDS.search(text))
        self.assertEqual(self.client.get(reverse("userdocs:page", args=["nope"])).status_code, 404)

    def test_page_layout(self):
        page = self.client.get(reverse("userdocs:page", args=["rules-eca"]))
        self.assertContains(page, "On this page")
        self.assertContains(page, 'href="#timing"')
        self.assertContains(page, "Rules overview")                  # sebelumnya
        self.assertContains(page, "Rule chains")                     # berikutnya
        self.assertContains(page, 'class="docs-nav-link active"')
        self.assertContains(page, 'class="docs-onpage"')                 # kolom kanan

    def test_layout_has_fixed_side_columns(self):
        """Menu kiri dan "On this page" berlebar tetap; hanya kolom isi yang mengisi sisa ruang."""
        page = self.client.get(reverse("userdocs:page", args=["rules-eca"])).content.decode()
        self.assertIn('class="docs-grid has-onpage"', page)
        self.assertIn('class="docs-main"', page)
        self.assertNotIn("{#", visible_text(page))            # tidak ada komentar template yang bocor

    def test_nested_navigation(self):
        """Menu tiga tingkat: bagian (bukan tautan) > bab (tautan) > bagian di dalam bab."""
        page = self.client.get(reverse("userdocs:page", args=["rules-eca"])).content.decode()
        nav = page[page.index('id="docs-nav"'):page.index("</nav>", page.index('id="docs-nav"'))]
        # Judul bagian bukan tautan.
        self.assertIn('<div class="docs-nav-section" role="heading" aria-level="2">Automation</div>', nav)
        # Bab yang sedang dibaca: sub-menu terbuka, berisi bagian-bagiannya sebagai #anchor di halaman ini.
        self.assertIn('class="docs-nav-sub collapse show" id="docs-sub-rules-eca"', nav)
        self.assertIn('href="#timing" data-anchor="timing"', nav)
        # Bab lain: sub-menu tertutup, tautannya menuju halaman bab itu.
        self.assertIn('class="docs-nav-sub collapse" id="docs-sub-rule-chains"', nav)
        self.assertIn('href="/docs/rule-chains/#nodes"', nav)
        self.assertEqual(nav.count('class="docs-nav-link'), len(PAGES))

    def test_server_values_appear(self):
        page = self.client.get(reverse("userdocs:page", args=["device-api"]))
        self.assertContains(page, "https://iot.example.org/api/v1/&lt;token&gt;/telemetry")
        self.assertContains(page, "kampus-iot/&lt;token&gt;/telemetry")
        self.assertContains(page, "--tls-use-os-certs")

    def test_connect_page_device_and_token(self):
        url = reverse("userdocs:page", args=["connect-esp32"])
        page = self.client.get(url, {"device": self.device.pk})
        self.assertNotContains(page, self.device.access_token)         # belum login
        self.assertContains(page, "Log in to download")
        self.client.force_login(self.siti)
        page = self.client.get(url, {"device": self.device.pk})
        self.assertContains(page, f"kampus-iot/{self.device.access_token}/telemetry")
        self.assertContains(page, f"/devices/connect/firmware/mqtt.zip?device={self.device.pk}")
        # Device orang lain, atau dibagikan hanya sebagai Viewer: token tidak tampil.
        self.assertNotContains(self.client.get(url, {"device": self.other.pk}), self.other.access_token)
        DeviceShare.objects.create(device=self.other, user=self.siti, role=DeviceShare.Role.VIEWER, created_by=self.budi)
        self.assertNotContains(self.client.get(url, {"device": self.other.pk}), self.other.access_token)

    def test_menu_link(self):
        self.client.force_login(self.siti)
        page = self.client.get(reverse("dashboard:index"))
        self.assertContains(page, 'href="/docs/"')
        # Tombol tiga garis di bilah atas: sembunyikan/tampilkan menu utama (layar lebar) dan
        # buka menu (ponsel), plus skrip yang mengingat pilihannya.
        self.assertContains(page, 'aria-label="Hide or show menu"')
        self.assertContains(page, 'aria-label="Open menu"')
        self.assertContains(page, "js/sidebar.js")
        # Garis pembatas menu dokumentasi.
        self.assertContains(self.client.get(reverse("userdocs:index")), '<aside class="docs-aside">')
        self.client.logout()
        login = self.client.get(reverse("accounts:login"))
        self.assertContains(login, 'href="/docs/"')
        self.assertNotContains(login, "data-sidebar-toggle")     # halaman publik tanpa side menu


@override_settings(OTA_BASE_URL="", COAP_PUBLIC_HOST="")
class ServerNoticeTests(TestCase):
    """Peringatan konfigurasi server: nama pengaturannya hanya untuk Admin."""

    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.admin = User.objects.create_user("boss", password="rahasia-12345", role=User.Role.ADMIN)
        cls.device = Device.objects.create(owner=cls.siti, name="Greenhouse Node")

    def get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url, HTTP_HOST="127.0.0.1:8000")

    def test_user_sees_plain_notice_admin_sees_setting(self):
        for url in (reverse("devices:detail", args=[self.device.pk]),
                    reverse("userdocs:page", args=["connect-esp32"])):
            with self.subTest(url=url):
                page = self.get(self.siti, url)
                self.assertContains(page, "not available from devices on this server yet")
                self.assertNotContains(page, "COAP_PUBLIC_HOST")
                page = self.get(self.admin, url)
                self.assertContains(page, "Only administrators see this note")
                self.assertContains(page, "COAP_PUBLIC_HOST")

    def test_platform_pages_have_no_developer_notes(self):
        page = self.get(self.siti, reverse("devices:detail", args=[self.device.pk]))
        text = visible_text(page.content.decode())
        self.assertIsNone(DEVELOPER_WORDS.search(text), DEVELOPER_WORDS.search(text))


class SearchTests(TestCase):
    def setUp(self):
        search._index.clear()

    def results(self, query):
        response = self.client.get(reverse("userdocs:search"), {"q": query})
        self.assertEqual(response.status_code, 200)
        return response

    def test_finds_sections_and_highlights(self):
        response = self.results("mac address")
        self.assertContains(response, "/docs/firmware-updates/#mac")
        self.assertContains(response, "<mark>MAC</mark>", html=False)
        first = response.context["results"][0]
        self.assertEqual((first["page"].slug, first["anchor"]), ("firmware-updates", "mac"))

    def test_all_words_must_match(self):
        self.assertFalse(self.results("telegram xyzzyqq").context["results"])
        self.assertTrue(self.results("telegram recipients").context["results"])

    def test_empty_and_short_queries(self):
        self.assertContains(self.results(""), "Type at least two letters")
        self.assertEqual(self.results("a").context["results"], [])
        self.assertContains(self.results("zzzzqqqq"), "Nothing matches")

    def test_snippet_is_escaped(self):
        html = search.snippet('<script>alert(1)</script> token here', ["token"])
        self.assertNotIn("<script>", html)
        self.assertIn("<mark>token</mark>", html)

    def test_index_covers_every_page(self):
        request = RequestFactory().get("/docs/search/")
        context = docs_context(request)
        index = search.build_index(context)["sections"]
        self.assertEqual({section.page.slug for section in index}, set(BY_SLUG))
        self.assertTrue(all(section.text or section.anchor for section in index))
        render_to_string(BY_SLUG["connect-esp32"].template, context)   # tanpa pengguna pun bisa dirender
