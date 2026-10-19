"""Tes tahap 15: nama file statis ber-hash (cache busting) untuk produksi."""
import re
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

HASHED = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "config.storage.HashedStaticStorage"},
}
# Pola yang sama dengan location ber-hash di deploy/nginx/iot-platform.conf.
NGINX_HASHED = re.compile(r"^/static/(.+\.[0-9a-f]{12}\.[a-z0-9]+)$", re.IGNORECASE)


class HashedStaticTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def render(self, path):
        return Template("{% load static %}{% static path %}").render(Context({"path": path}))

    def collect(self):
        call_command("collectstatic", interactive=False, verbosity=0, stdout=StringIO())

    def test_collectstatic_writes_hashed_copies_and_static_tag_uses_them(self):
        with self.settings(STORAGES=HASHED, STATIC_ROOT=self.root, DEBUG=False):
            self.collect()
            url = self.render("css/app.css")
            self.assertRegex(url, r"^/static/css/app\.[0-9a-f]{12}\.css$")
            self.assertTrue((self.root / url.removeprefix("/static/")).exists())
            self.assertTrue((self.root / "css/app.css").exists(), "the original name is kept too")
            self.assertTrue((self.root / "staticfiles.json").exists())
            self.assertRegex(self.render("js/flow_export.js"), r"^/static/js/flow_export\.[0-9a-f]{12}\.js$")
            self.assertTrue(NGINX_HASHED.match(url), "Nginx gives this URL the one-year cache")
            self.assertIsNone(NGINX_HASHED.match("/static/css/app.css"), "original names stay short-lived")

    def test_pages_link_the_hashed_files(self):
        with self.settings(STORAGES=HASHED, STATIC_ROOT=self.root, DEBUG=False):
            self.collect()
            html = self.client.get("/accounts/login/").content.decode()
            self.assertRegex(html, r'href="/static/css/app\.[0-9a-f]{12}\.css"')
            self.assertRegex(html, r'src="/static/js/theme\.[0-9a-f]{12}\.js"')

    def test_new_content_gets_a_new_name(self):
        with self.settings(STORAGES=HASHED, STATIC_ROOT=self.root, DEBUG=False):
            self.collect()
            first = staticfiles_storage.hashed_name("css/app.css", content=None)
            from django.core.files.base import ContentFile

            changed = staticfiles_storage.hashed_name("css/app.css", ContentFile(b"body { color: red; }"))
            self.assertNotEqual(first, changed)

    def test_missing_file_falls_back_to_original_name(self):
        # manifest_strict = False: file yang belum di-collectstatic tidak membuat error 500.
        with self.settings(STORAGES=HASHED, STATIC_ROOT=self.root, DEBUG=False):
            self.collect()
            self.assertEqual(self.render("js/not-collected-yet.js"), "/static/js/not-collected-yet.js")

    @override_settings(DEBUG=True)
    def test_development_and_tests_use_plain_names(self):
        self.assertEqual(self.render("css/app.css"), "/static/css/app.css")
