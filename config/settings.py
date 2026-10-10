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
    # Daphne harus PALING ATAS: ia mengganti perintah runserver bawaan dengan
    # server ASGI yang juga bisa melayani WebSocket.
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Filter tampilan angka/waktu, mis. intcomma (1234 -> 1,234).
    "django.contrib.humanize",
    # App milik project ini:
    "accounts",  # pengguna & role
    "devices",   # device & telemetri
    "api",       # API untuk device (HTTP), tanpa login/CSRF, pakai access token
    "mqtt_bridge",  # komunikasi device lewat MQTT (perintah: manage.py mqtt_bridge)
    "realtime",     # dashboard real-time lewat WebSocket (Django Channels)
    "dashboard",    # halaman Dashboard: semua variabel per lokasi & device
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
# Dipakai Daphne/Channels: aplikasi ASGI yang melayani HTTP dan WebSocket.
ASGI_APPLICATION = "config.asgi.application"


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
LOGIN_REDIRECT_URL = "dashboard:index"   # tujuan setelah berhasil login
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


# --- MQTT -------------------------------------------------------------------
# Semua nilai dibaca dari .env, sehingga broker bisa diganti tanpa mengubah kode.
# Bawaan: broker publik EMQX. Broker publik bisa dibaca SIAPA SAJA, jadi hanya
# untuk belajar; lihat docs/tahap-03-mqtt.md.

MQTT_HOST = os.getenv("MQTT_HOST", "broker.emqx.io")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
# True = koneksi terenkripsi (biasanya port 8883).
MQTT_TLS = os.getenv("MQTT_TLS", "False") == "True"
# Awal semua topik. Di broker publik WAJIB unik, mis. "filkom-iot-7f3a9c".
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "django-iot-demo").strip("/")
# Kosong = nama klien dibuat acak oleh pustaka paho-mqtt.
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "")
# Detik antar "ping" ke broker untuk menjaga koneksi tetap hidup.
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))


# --- WebSocket (Django Channels) ---------------------------------------------
# Channel layer = "papan pengumuman" tempat kabar data baru dikirim ke browser.
# - REDIS_URL kosong: InMemoryChannelLayer. Hanya bekerja di dalam proses
#   runserver: data dari API HTTP dan tombol dashboard langsung tampil, tetapi
#   data dari `mqtt_bridge` (proses lain) baru tampil lewat pemeriksaan
#   cadangan tiap 30 detik.
# - REDIS_URL diisi (mis. redis://127.0.0.1:6379/0): RedisChannelLayer, lintas
#   proses. Semua data, termasuk dari MQTT, tampil seketika.

REDIS_URL = os.getenv("REDIS_URL", "")
# Batas waktu membaca balasan Redis (detik). HARUS lebih besar dari 5 detik:
# setiap consumer WebSocket menunggu pesan dengan perintah Redis BZPOPMIN yang
# memang "menggantung" sampai 5 detik (brpop_timeout di channels-redis).
# Sejak redis-py 8, batas baca bawaannya juga 5 detik, sehingga keduanya
# berlomba: "Timeout reading from 127.0.0.1:6379", consumer berhenti, dan
# WebSocket terputus setiap beberapa detik. Lihat docs/tahap-04-websocket.md.
REDIS_SOCKET_TIMEOUT = 15
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                # Bentuk dict (bukan teks URL saja) supaya bisa membawa opsi koneksi.
                "hosts": [{
                    "address": REDIS_URL,
                    "socket_timeout": REDIS_SOCKET_TIMEOUT,
                    "socket_connect_timeout": 5,
                }],
            },
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

