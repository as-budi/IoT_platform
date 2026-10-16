"""
Tes tahap 11: verifikasi email saat Sign up, konfirmasi email baru, dan lupa password.

Saat `manage.py test`, Django otomatis memakai backend email "locmem": email
tidak dikirim, melainkan ditampung di django.core.mail.outbox (sebuah list).
Tes membaca tautan dari isi email itu, persis seperti pengguna mengkliknya.
"""
import re
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import User

LINK = re.compile(r"https?://[^\s]+")


def link_path(message):
    """Path tautan pertama di badan email (tanpa http://testserver)."""
    url = LINK.search(message.body).group(0)
    return re.sub(r"^https?://[^/]+", "", url)


class SignupActivationTests(TestCase):
    form = {
        "username": "dewi", "first_name": "Dewi", "last_name": "", "email": "dewi@example.com",
        "password1": "kata-Sandi-9876", "password2": "kata-Sandi-9876",
    }

    def register(self, **changes):
        return self.client.post(reverse("accounts:register"), {**self.form, **changes})

    def test_signup_sends_activation_link_and_cannot_log_in_yet(self):
        self.register()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["dewi@example.com"])
        self.assertIn("/accounts/activate/", mail.outbox[0].body)
        # Belum bisa login; pesannya menjelaskan alasannya dan menawarkan kirim ulang.
        response = self.client.post(reverse("accounts:login"), {"username": "dewi", "password": "kata-Sandi-9876"})
        self.assertContains(response, "not activated yet")
        self.assertContains(response, reverse("accounts:activation_resend"))

    def test_wrong_password_does_not_reveal_pending_account(self):
        self.register()
        response = self.client.post(reverse("accounts:login"), {"username": "dewi", "password": "salah-salah-1"})
        self.assertNotContains(response, "not activated yet")

    def test_activation_needs_post_then_logs_in(self):
        self.register()
        path = link_path(mail.outbox[0])
        # GET (misalnya pemindai tautan di layanan email) tidak mengubah apa pun.
        response = self.client.get(path)
        self.assertContains(response, "Activate account")
        self.assertFalse(User.objects.get(username="dewi").is_active)
        # POST dari tombol mengaktifkan akun dan membuat sesi login.
        response = self.client.post(path)
        self.assertRedirects(response, reverse("dashboard:index"))
        user = User.objects.get(username="dewi")
        self.assertTrue(user.is_active)
        self.assertFalse(user.activation_pending)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        # Tautan yang sama tidak bisa dipakai lagi.
        self.client.logout()
        self.assertContains(self.client.get(path), "Link not valid")

    def test_tampered_or_garbage_link_is_rejected(self):
        self.register()
        path = link_path(mail.outbox[0])
        for bad in (path[:-3] + "xyz/", "/accounts/activate/AAAA/abc-def/", "/accounts/activate/%%%/x/"):
            self.client.post(bad)
        self.assertFalse(User.objects.get(username="dewi").is_active)

    def test_admin_deactivated_account_cannot_self_activate(self):
        user = User.objects.create_user("eko", password="kata-Sandi-9876", email="eko@example.com", is_active=False)
        self.client.post(reverse("accounts:activation_resend"), {"email": "eko@example.com"})
        self.assertEqual(mail.outbox, [])
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_resend_gives_same_answer_for_unknown_email(self):
        self.register()
        mail.outbox.clear()
        for email in ("dewi@example.com", "DEWI@example.com", "nobody@example.com"):
            response = self.client.post(reverse("accounts:activation_resend"), {"email": email})
            self.assertRedirects(response, reverse("accounts:register_done"))
        self.assertEqual(len(mail.outbox), 2)   # hanya alamat yang terdaftar

    def test_email_is_required_and_unique(self):
        User.objects.create_user("lama", email="Dewi@Example.com")
        for changes in ({"email": ""}, {"email": "dewi@example.com"}):
            response = self.register(**changes)
            self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="dewi").exists())

    def test_smtp_failure_keeps_account_and_shows_message(self):
        # assertLogs: kegagalan tercatat di log (journal di server), bukan error 500.
        with mock.patch("accounts.emails.send_mail", side_effect=OSError("SMTP down")), \
                self.assertLogs("accounts.emails", "ERROR"):
            response = self.client.post(reverse("accounts:register"), self.form, follow=True)
        self.assertContains(response, "could not send the activation email")
        self.assertTrue(User.objects.filter(username="dewi", activation_pending=True).exists())

    def test_admin_activating_clears_pending_flag(self):
        self.register()
        admin = User.objects.create_user("admin1", password="x", role=User.Role.ADMIN)
        self.client.force_login(admin)
        dewi = User.objects.get(username="dewi")
        self.client.post(reverse("accounts:user_edit", args=[dewi.pk]), {
            "username": "dewi", "first_name": "Dewi", "last_name": "", "email": "dewi@example.com",
            "role": User.Role.USER, "is_active": "on",
        })
        dewi.refresh_from_db()
        self.assertTrue(dewi.is_active)
        self.assertFalse(dewi.activation_pending)

    def test_delete_unactivated_command(self):
        self.register()
        old = User.objects.create_user("lama", email="lama@example.com", is_active=False, activation_pending=True)
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=8))
        disabled = User.objects.create_user("nonaktif", is_active=False)
        User.objects.filter(pk=disabled.pk).update(date_joined=timezone.now() - timedelta(days=30))
        call_command("delete_unactivated", stdout=StringIO())
        names = set(User.objects.values_list("username", flat=True))
        self.assertEqual(names, {"dewi", "nonaktif"})   # baru (< 7 hari) dan nonaktif-oleh-Admin tetap ada


class EmailChangeTests(TestCase):
    def setUp(self):
        self.budi = User.objects.create_user("budi", password="rahasia-12345", email="budi@example.com")
        self.client.force_login(self.budi)

    def change(self, email):
        return self.client.post(reverse("accounts:profile"), {"first_name": "", "last_name": "", "email": email})

    def test_new_email_is_used_only_after_confirmation(self):
        self.change("budi.baru@example.com")
        self.budi.refresh_from_db()
        self.assertEqual(self.budi.email, "budi@example.com")
        self.assertEqual(self.budi.pending_email, "budi.baru@example.com")
        self.assertEqual(mail.outbox[0].to, ["budi.baru@example.com"])

        path = link_path(mail.outbox[0])
        other_browser = self.client_class()   # tautan dibuka di HP yang belum login
        self.assertContains(other_browser.get(path), "budi.baru@example.com")
        response = other_browser.post(path)
        self.assertRedirects(response, reverse("accounts:login"))
        self.budi.refresh_from_db()
        self.assertEqual((self.budi.email, self.budi.pending_email), ("budi.baru@example.com", ""))
        self.assertContains(other_browser.get(path), "Link not valid")

    def test_cancel_and_superseded_links_stop_working(self):
        self.change("satu@example.com")
        first = link_path(mail.outbox[0])
        self.change("dua@example.com")
        self.client.post(first)
        self.budi.refresh_from_db()
        self.assertEqual(self.budi.email, "budi@example.com")   # tautan pertama hangus

        second = link_path(mail.outbox[1])
        self.client.post(reverse("accounts:email_cancel"))
        self.client.post(second)
        self.budi.refresh_from_db()
        self.assertEqual((self.budi.email, self.budi.pending_email), ("budi@example.com", ""))

    def test_email_of_another_account_is_rejected(self):
        User.objects.create_user("siti", email="siti@example.com")
        response = self.change("SITI@example.com")
        self.assertContains(response, "already exists")
        self.assertEqual(mail.outbox, [])

    def test_address_taken_before_confirmation(self):
        self.change("rebutan@example.com")
        User.objects.create_user("siti", email="rebutan@example.com")
        response = self.client.post(link_path(mail.outbox[0]))
        self.assertContains(response, "Another account started using this email")
        self.budi.refresh_from_db()
        self.assertEqual((self.budi.email, self.budi.pending_email), ("budi@example.com", ""))

    def test_saving_names_only_sends_nothing(self):
        self.client.post(reverse("accounts:profile"), {"first_name": "Budi", "last_name": "", "email": "BUDI@example.com"})
        self.assertEqual(mail.outbox, [])
        self.budi.refresh_from_db()
        self.assertEqual((self.budi.first_name, self.budi.pending_email), ("Budi", ""))


class PasswordResetTests(TestCase):
    old = "rahasia-12345"
    new = "Kata-Sandi-Baru-2026"

    def setUp(self):
        self.budi = User.objects.create_user("budi", password=self.old, email="budi@example.com")

    def request_reset(self, email):
        return self.client.post(reverse("accounts:password_reset"), {"email": email})

    def test_login_page_links_to_reset(self):
        self.assertContains(self.client.get(reverse("accounts:login")), reverse("accounts:password_reset"))

    def test_full_reset_flow(self):
        response = self.request_reset("Budi@Example.com")
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(mail.outbox[0].to, ["budi@example.com"])
        self.assertIn("budi", mail.outbox[0].body)   # username ikut disebut

        # Django mengalihkan tautan token ke URL tanpa token (token disimpan di sesi),
        # supaya token tidak bocor lewat header Referer.
        response = self.client.get(link_path(mail.outbox[0]), follow=True)
        self.assertContains(response, "Choose a new password")
        form_url = response.redirect_chain[-1][0]
        response = self.client.post(form_url, {"new_password1": self.new, "new_password2": self.new})
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.budi.refresh_from_db()
        self.assertTrue(self.budi.check_password(self.new))

        # Tautan hanya bisa dipakai sekali: hash password sudah berubah.
        response = self.client.get(link_path(mail.outbox[0]), follow=True)
        self.assertContains(response, "Link not valid")

    def test_unknown_inactive_or_pending_accounts_get_same_page_and_no_email(self):
        User.objects.create_user("eko", password="x", email="eko@example.com", is_active=False)
        User.objects.create_user("dewi", password="x", email="dewi@example.com",
                                 is_active=False, activation_pending=True)
        for email in ("nobody@example.com", "eko@example.com", "dewi@example.com"):
            response = self.request_reset(email)
            self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(mail.outbox, [])

    def test_activation_token_cannot_reset_password(self):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from .tokens import activation_token

        uid = urlsafe_base64_encode(force_bytes(self.budi.pk))
        token = activation_token.make_token(self.budi)
        url = reverse("accounts:password_reset_confirm", args=[uid, token])
        self.assertContains(self.client.get(url, follow=True), "Link not valid")
