"""
Model = representasi tabel database dalam bentuk class Python.

Django sudah punya model User lengkap (username, password ter-hash, email,
is_active, dst.). Kita mewarisinya lewat AbstractUser dan menambah kolom:

    role                 Admin / User (tahap 1)
    activation_pending   akun hasil Sign up yang emailnya belum dikonfirmasi (tahap 11)
    pending_email        alamat email baru yang menunggu konfirmasi (tahap 11)
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        # NAMA = "nilai yang disimpan di database", "label yang ditampilkan"
        ADMIN = "ADMIN", "Admin"
        USER = "USER", "User"

    role = models.CharField(
        "role",
        max_length=10,
        choices=Role.choices,
        default=Role.USER,
    )
    # True hanya untuk akun dari halaman Sign up yang tautan aktivasinya belum
    # diklik. Sengaja kolom tersendiri, bukan sekadar is_active=False: akun yang
    # DINONAKTIFKAN Admin juga is_active=False, dan akun seperti itu tidak boleh
    # bisa "mengaktifkan diri sendiri" lewat tautan aktivasi.
    activation_pending = models.BooleanField("awaiting email confirmation", default=False)
    # Email baru yang diketik di halaman profil. Kolom `email` baru diganti
    # setelah pemiliknya mengklik tautan yang dikirim ke alamat baru ini.
    pending_email = models.EmailField("new email (unconfirmed)", blank=True)

    class UiStyle(models.TextChoices):
        STANDARD = "standard", "Standard"
        FILKOM = "filkom", "FILKOM"

    # Gaya tampilan pilihan pengguna; kosong = gaya bawaan situs (.env UI_STYLE).
    ui_style = models.CharField("interface style", max_length=10, choices=UiStyle.choices, blank=True)

    # Batas baris telemetri per variabel untuk device milik akun ini (devices/row_limit.py).
    # Kosong = bawaan server (.env TELEMETRY_MAX_ROWS_PER_VARIABLE); 0 = tanpa batas. Diatur Admin.
    telemetry_max_rows = models.PositiveIntegerField(
        "telemetry rows per variable",
        null=True,
        blank=True,
        help_text="Empty = server default. 0 = no limit. Applies to every variable of this account's devices.",
    )

    @property
    def telemetry_row_limit(self):
        """Batas yang berlaku untuk akun ini: angka, atau 0 bila tanpa batas."""
        from django.conf import settings

        if self.telemetry_max_rows is not None:
            return self.telemetry_max_rows
        return settings.TELEMETRY_MAX_ROWS_PER_VARIABLE

    @property
    def is_admin(self):
        """True bila pengguna boleh mengelola seluruh platform.

        Superuser (dibuat lewat `createsuperuser`) selalu dianggap admin,
        supaya kita tidak terkunci dari aplikasi sendiri.
        """
        return self.is_superuser or self.role == self.Role.ADMIN

    def __str__(self):
        return self.get_full_name() or self.username
