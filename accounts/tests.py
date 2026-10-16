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
        "email": "dewi@example.com",
        "password1": "kata-Sandi-9876",
        "password2": "kata-Sandi-9876",
    }

    def test_register_page_is_public(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)

    def test_register_creates_inactive_user_awaiting_activation(self):
        # Sejak tahap 11 akun baru belum aktif dan TIDAK langsung login;
        # alur aktivasinya diuji di accounts/tests_email.py.
        response = self.client.post(reverse("accounts:register"), self.form_data)
        self.assertRedirects(response, reverse("accounts:register_done"))
        user = User.objects.get(username="dewi")
        self.assertEqual(user.role, User.Role.USER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_active)
        self.assertTrue(user.activation_pending)
        self.assertNotIn("_auth_user_id", self.client.session)

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


class ProfileTests(TestCase):
    """Halaman "My profile": setiap pengguna mengubah data dirinya SENDIRI."""

    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345", email="budi@example.com")
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")

    def setUp(self):
        self.client.force_login(self.budi)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        for name in ("accounts:profile", "accounts:password_change"):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse(name)}")

    def test_regular_user_can_open_profile(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["object"], self.budi)
        self.assertContains(response, "budi@example.com")

    def test_update_own_details(self):
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Budi", "last_name": "Santoso", "email": "budi.s@example.com",
        })
        self.assertRedirects(response, reverse("accounts:profile"))
        self.budi.refresh_from_db()
        # Nama langsung berubah; email baru menunggu konfirmasi (tests_email.py).
        self.assertEqual((self.budi.first_name, self.budi.last_name, self.budi.email),
                         ("Budi", "Santoso", "budi@example.com"))
        self.assertEqual(self.budi.pending_email, "budi.s@example.com")
        self.siti.refresh_from_db()
        self.assertEqual(self.siti.first_name, "")

    def test_cannot_raise_own_privileges(self):
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Budi", "last_name": "", "email": "budi@example.com",
            "username": "root", "role": User.Role.ADMIN,
            "is_staff": "on", "is_superuser": "on", "is_active": "",
        })
        self.assertRedirects(response, reverse("accounts:profile"))   # form valid, tersimpan
        self.budi.refresh_from_db()
        self.assertEqual(self.budi.first_name, "Budi")
        self.assertEqual(self.budi.username, "budi")
        self.assertEqual(self.budi.role, User.Role.USER)
        self.assertFalse(self.budi.is_admin or self.budi.is_staff)
        self.assertTrue(self.budi.is_active)

    def test_invalid_email_is_rejected(self):
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "", "last_name": "", "email": "not-an-email",
        })
        self.assertEqual(response.status_code, 200)
        self.budi.refresh_from_db()
        self.assertEqual(self.budi.email, "budi@example.com")

    def test_sidebar_links_to_profile(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, f'href="{reverse("accounts:profile")}"')


class PasswordChangeTests(TestCase):
    """Ubah password: wajib password lama, sesi ini tetap login, sesi lain keluar."""

    old = "rahasia-12345"
    new = "Kata-Sandi-Baru-2026"

    def setUp(self):
        self.budi = User.objects.create_user("budi", password=self.old, email="budi@example.com")
        # Login sungguhan (bukan force_login) supaya session menyimpan hash password.
        self.client.login(username="budi", password=self.old)

    def post(self, old_password, new1, new2=None):
        return self.client.post(reverse("accounts:password_change"), {
            "old_password": old_password, "new_password1": new1, "new_password2": new2 or new1,
        })

    def test_change_password(self):
        other_browser = self.client_class()
        other_browser.login(username="budi", password=self.old)

        response = self.post(self.old, self.new)
        self.assertRedirects(response, reverse("accounts:profile"))
        self.budi.refresh_from_db()
        self.assertTrue(self.budi.check_password(self.new))
        # Browser ini tetap login...
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)
        # ...sedangkan browser lain dikeluarkan dan diarahkan ke halaman login.
        self.assertEqual(other_browser.get(reverse("accounts:profile")).status_code, 302)

    def test_wrong_old_password_is_rejected(self):
        response = self.post("salah-total", self.new)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["old_password"])
        self.budi.refresh_from_db()
        self.assertTrue(self.budi.check_password(self.old))

    def test_weak_or_mismatched_password_is_rejected(self):
        for new1, new2 in (("12345678", "12345678"), (self.new, self.new + "x")):
            response = self.post(self.old, new1, new2)
            self.assertEqual(response.status_code, 200)
        self.budi.refresh_from_db()
        self.assertTrue(self.budi.check_password(self.old))
