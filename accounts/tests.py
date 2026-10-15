"""
Tes otomatis untuk hak akses manajemen pengguna.

Jalankan dengan:  python manage.py test
Django membuat database sementara (test_<nama db>) lalu menghapusnya lagi.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User


class UserManagementAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admin1", password="rahasia-12345", role=User.Role.ADMIN
        )
        cls.user = User.objects.create_user("budi", password="rahasia-12345")

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("accounts:user_list"))
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:user_list')}",
        )

    def test_regular_user_is_forbidden(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_list_users(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "budi")

    def test_admin_can_create_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("accounts:user_add"),
            {
                "username": "siti",
                "first_name": "Siti",
                "last_name": "",
                "email": "",
                "role": User.Role.USER,
                "password1": "kata-Sandi-9876",
                "password2": "kata-Sandi-9876",
            },
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        self.assertTrue(User.objects.filter(username="siti").exists())

    def test_admin_cannot_delete_self(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("accounts:user_delete", args=[self.admin.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_login_page_renders(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)


class RegisterTests(TestCase):
    """Pendaftaran mandiri oleh pengunjung yang belum punya akun."""

    form_data = {
        "username": "dewi",
        "first_name": "Dewi",
        "last_name": "",
        "email": "",
        "password1": "kata-Sandi-9876",
        "password2": "kata-Sandi-9876",
    }

    def test_register_page_is_public(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)

    def test_register_creates_user_and_logs_in(self):
        response = self.client.post(reverse("accounts:register"), self.form_data)
        self.assertRedirects(response, reverse("dashboard:index"))
        user = User.objects.get(username="dewi")
        self.assertEqual(user.role, User.Role.USER)
        self.assertFalse(user.is_staff)
        # Sesi login sudah terbentuk: id pengguna tersimpan di session.
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_register_cannot_choose_admin_role(self):
        data = {**self.form_data, "role": User.Role.ADMIN, "is_superuser": "on"}
        self.client.post(reverse("accounts:register"), data)
        user = User.objects.get(username="dewi")
        self.assertEqual(user.role, User.Role.USER)
        self.assertFalse(user.is_admin)

    def test_mismatched_passwords_are_rejected(self):
        data = {**self.form_data, "password2": "berbeda-1234"}
        response = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="dewi").exists())

    def test_logged_in_user_is_redirected_away(self):
        self.client.force_login(User.objects.create_user("budi", password="x"))
        response = self.client.get(reverse("accounts:register"))
        self.assertRedirects(response, reverse("dashboard:index"))


class SignupSwitchTests(TestCase):
    """DJANGO_ALLOW_SIGNUP=False menutup pendaftaran mandiri (server publik)."""

    def test_open_by_default(self):
        self.assertContains(self.client.get(reverse("accounts:login")), "Sign up")
        self.assertEqual(self.client.get(reverse("accounts:register")).status_code, 200)

    @override_settings(ALLOW_SIGNUP=False)
    def test_closed(self):
        self.assertNotContains(self.client.get(reverse("accounts:login")), "Sign up")
        self.assertEqual(self.client.get(reverse("accounts:register")).status_code, 404)
        response = self.client.post(reverse("accounts:register"), {
            "username": "intruder", "password1": "Very-long-pass-123", "password2": "Very-long-pass-123",
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(User.objects.filter(username="intruder").exists())
