"""Gaya tampilan Standard / FILKOM (docs/tahap-26-tambahan-gaya-filkom.md)."""
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User


class UiStyleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")

    def page(self):
        return self.client.get(reverse("devices:list"))

    def test_default_is_standard(self):
        self.client.force_login(self.siti)
        page = self.page()
        self.assertContains(page, 'data-ui="standard"')
        self.assertNotContains(page, "css/filkom.css")
        # Tata letak, logo, dan identitas sama di kedua gaya; yang berbeda hanya warna (filkom.css).
        self.assertContains(page, "Robotic and Embedded System Laboratory")
        self.assertContains(page, "Computer Engineering Study Program")
        self.assertContains(page, 'aria-label="IoT Platform logo"')
        self.assertContains(page, 'class="ui-topbar-end d-none d-lg-flex"')
        self.assertNotContains(page, "fonts.googleapis.com/css2?family=Archivo")
        self.assertContains(page, 'name="style" value="filkom"')            # tombol pilihan

    def test_choose_filkom_per_account(self):
        self.client.force_login(self.siti)
        back = reverse("devices:list") + "?q=node"
        response = self.client.post(reverse("accounts:ui_style"), {"style": "filkom", "next": back})
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.siti.refresh_from_db()
        self.assertEqual(self.siti.ui_style, "filkom")
        page = self.page()
        self.assertContains(page, 'data-ui="filkom"')
        self.assertContains(page, "css/filkom.css")
        self.assertContains(page, "Robotic and Embedded System Laboratory")
        self.assertContains(page, "Computer Engineering Study Program - Faculty of Computer Science - Universitas Brawijaya")
        self.assertContains(page, 'aria-label="IoT Platform logo"')
        self.assertContains(page, "data-sidebar-toggle")                    # side menu tetap bisa disembunyikan
        self.assertContains(page, 'class="ui-topbar-end d-none d-lg-flex"')  # gaya, tema, profil di kanan atas
        self.assertContains(page, 'class="ui-user')
        # Kembali ke Standard; nilai lain dan alamat luar diabaikan.
        self.client.post(reverse("accounts:ui_style"), {"style": "standard"})
        response = self.client.post(reverse("accounts:ui_style"), {"style": "pink", "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.siti.refresh_from_db()
        self.assertEqual(self.siti.ui_style, "standard")

    @override_settings(UI_STYLE="filkom")
    def test_site_default_from_env_also_for_visitors(self):
        login = self.client.get(reverse("accounts:login"))
        self.assertContains(login, 'data-ui="filkom"')
        self.assertContains(login, "Robotic and Embedded System Laboratory")
        self.client.force_login(self.siti)
        self.assertContains(self.page(), 'data-ui="filkom"')
        self.siti.ui_style = "standard"
        self.siti.save()
        self.assertContains(self.page(), 'data-ui="standard"')            # pilihan akun menang

    @override_settings(UI_STYLE="filkom", UI_STYLE_SELECTABLE=False)
    def test_locked_style(self):
        self.siti.ui_style = "standard"
        self.siti.save()
        self.client.force_login(self.siti)
        page = self.page()
        self.assertContains(page, 'data-ui="filkom"')
        self.assertNotContains(page, 'name="style"')
        self.client.post(reverse("accounts:ui_style"), {"style": "standard"})
        self.siti.refresh_from_db()
        self.assertEqual(self.siti.ui_style, "standard")                    # tidak berubah, tetap terkunci
        self.assertContains(self.page(), 'data-ui="filkom"')
