"""
Pengaturan project Django "IoT Platform".

File ini adalah pusat konfigurasi: app apa saja yang aktif, database yang
dipakai, lokasi template, zona waktu, dan seterusnya.

Referensi: https://docs.djangoproject.com/en/5.2/ref/settings/
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# BASE_DIR = folder paling luar project (tempat manage.py berada).
BASE_DIR = Path(__file__).resolve().parent.parent

# Baca file .env (jika ada) sehingga isinya tersedia lewat os.getenv().
# Tujuannya: rahasia seperti password database tidak ditulis di kode.
load_dotenv(BASE_DIR / ".env")


# --- Keamanan dasar ---------------------------------------------------------

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key-ganti-di-env")

# DEBUG=True menampilkan halaman error yang detail. WAJIB False di produksi.
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]


# --- Daftar app -------------------------------------------------------------
# Sebuah "project" Django terdiri dari banyak "app". Setiap app mengurus satu
# urusan. App buatan kita sendiri didaftarkan di bagian bawah.

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # App milik project ini:
    "accounts",  # pengguna & role
    "devices",   # device & telemetri
]

# Middleware = lapisan yang dilewati setiap request sebelum sampai ke view
# (dan setiap response sebelum dikirim ke browser).
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Modul URL utama: titik awal Django mencocokkan alamat yang diminta browser.
ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Template umum lintas-app (base.html, halaman login) ada di sini.
        "DIRS": [BASE_DIR / "templates"],
        # APP_DIRS=True: Django juga mencari di folder templates/ tiap app.
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                # Menyediakan variabel `user` di semua template.
                "django.contrib.auth.context_processors.auth",
                # Menyediakan variabel `messages` (notifikasi sekali tampil).
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- Database ---------------------------------------------------------------
# MySQL (dan MariaDB) memakai backend "mysql" milik Django; driver-nya paket
# mysqlclient. Berganti di antara keduanya tidak mengubah kode, hanya server.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME", "iot_platform"),
        "USER": os.getenv("DB_USER", "iot_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
        },
    }
}

# Tipe primary key default untuk model yang tidak mendefinisikannya sendiri.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Autentikasi ------------------------------------------------------------

# Kita memakai model User buatan sendiri (accounts.User) yang punya kolom role.
# Ini harus ditetapkan SEBELUM migrasi pertama; mengubahnya belakangan sulit.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Nilai di bawah adalah NAMA url (lihat accounts/urls.py dan devices/urls.py).
LOGIN_URL = "accounts:login"             # tujuan bila belum login
LOGIN_REDIRECT_URL = "devices:list"      # tujuan setelah berhasil login
LOGOUT_REDIRECT_URL = "accounts:login"   # tujuan setelah logout


# --- Bahasa & zona waktu ----------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
# USE_TZ=True: waktu disimpan sebagai UTC di database, lalu dikonversi ke
# TIME_ZONE saat ditampilkan. Ini praktik baku untuk data time-series.
USE_TZ = True


# --- File statis (CSS, JS, gambar) -----------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
