"""Mixin hak akses untuk view."""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Hanya pengguna ber-role Admin yang boleh membuka view ini.

    - Belum login      -> dialihkan ke halaman login (LoginRequiredMixin).
    - Login, bukan admin -> 403 Forbidden (UserPassesTestMixin).
    """

    def test_func(self):
        return self.request.user.is_admin
