"""
Tes otomatis untuk hak akses manajemen pengguna.

Jalankan dengan:  python manage.py test
Django membuat database sementara (test_<nama db>) lalu menghapusnya lagi.
"""
from django.test import TestCase
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
