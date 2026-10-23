"""
Pengaturan project Django "IoT Platform".

File ini adalah pusat konfigurasi: app apa saja yang aktif, database yang
dipakai, lokasi template, zona waktu, dan seterusnya.

Referensi: https://docs.djangoproject.com/en/5.2/ref/settings/
"""
import os
import sys
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
    "coap_gateway", # komunikasi device lewat CoAP (perintah: manage.py coap_server)
    "rules",        # rule engine & alarm (perintah: manage.py rule_engine)
    "launcher",     # perintah manage.py runall: menjalankan semua layanan sekaligus
    "ota",          # pembaruan firmware over-the-air (tahap 9)
    "notifications",  # notifikasi Telegram untuk aksi aturan (tahap 17)
    "activity",       # log aktivitas seluruh platform (tahap 18)
    "sharing",        # berbagi device antar-pengguna dan grup (tahap 19)
    # Login untuk aplikasi lain lewat OAuth 2.0 / OpenID Connect (tahap 22).
    # "oauth" (milik project) SEBELUM "oauth2_provider" (paket django-oauth-toolkit),
    # supaya template halaman izin milik kita yang dipakai.
    "oauth",
    "oauth2_provider",
]

# Middleware = lapisan yang dilewati setiap request sebelum sampai ke view
# (dan setiap response sebelum dikirim ke browser).
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Mencatat siapa pelaku setiap kejadian di log aktivitas (tahap 18).
    "activity.middleware.ActorMiddleware",
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
                # Menyediakan `active_alarm_count` untuk lencana di side menu.
                "rules.context_processors.alarms",
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
# Pendaftaran mandiri (/accounts/register/). Di server publik sebaiknya False:
# siapa pun di internet bisa membuat akun bila dibiarkan terbuka.
ALLOW_SIGNUP = os.getenv("DJANGO_ALLOW_SIGNUP", "True") == "True"
# Lama berlaku (detik) tautan di email: aktivasi akun, konfirmasi email baru,
# dan reset password (tahap 11). Bawaan Django 3 hari; di sini 24 jam.
PASSWORD_RESET_TIMEOUT = int(os.getenv("DJANGO_EMAIL_LINK_HOURS", "24")) * 3600


# --- Login untuk aplikasi lain: OAuth 2.0 / OpenID Connect (tahap 22) ---------------
# Platform menjadi "penyedia identitas" (seperti "Sign in with Google") untuk aplikasi
# lain, mis. aplikasi MQTT client web. Paket: django-oauth-toolkit (oauth2_provider).
# Penjelasan: docs/tahap-22-login-untuk-aplikasi-lain.md.

# Kunci RSA privat untuk menandatangani id_token (JWT). File PEM, dibuat sekali dengan
# `python manage.py oidc_key` (atau openssl di server). RAHASIA: jangan di-commit.
# Folder private_media sudah ada di .gitignore dan ikut backup.
OIDC_RSA_PRIVATE_KEY_FILE = Path(
    os.getenv("OIDC_RSA_PRIVATE_KEY_FILE", "") or BASE_DIR / "private_media" / "oidc" / "private.pem"
)
try:
    OIDC_RSA_PRIVATE_KEY = OIDC_RSA_PRIVATE_KEY_FILE.read_text()
except OSError:
    OIDC_RSA_PRIVATE_KEY = ""   # belum dibuat; `check --deploy` akan mengingatkan (oauth/checks.py)

# Alamat "issuer": awal semua alamat OpenID Connect platform ini, mis.
# https://iot.domain-anda/o . Harus sama persis dengan yang dikenal aplikasi klien.
# Kosong = dihitung dari alamat request (cukup di laptop).
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "").rstrip("/")

# Izin (scope) yang bisa diminta aplikasi luar. Teksnya tampil di halaman persetujuan.
OAUTH_SCOPES = {
    "openid": "Sign you in with your IoT Platform account",
    "profile": "See your username and name",
    "email": "See your email address",
    "devices:read": "See your devices and the devices shared with you, including the access "
                    "tokens of devices you can edit (anyone with a token can act as that device)",
    "mqtt:sandbox": "Use your MQTT sandbox account (topics under sandbox/), including its password",
}

# Model aplikasi klien bawaan paket (ditulis eksplisit karena oauth.Consent merujuknya).
OAUTH2_PROVIDER_APPLICATION_MODEL = "oauth2_provider.Application"

OAUTH2_PROVIDER = {
    "OIDC_ENABLED": True,
    "OIDC_RSA_PRIVATE_KEY": OIDC_RSA_PRIVATE_KEY,
    "OIDC_ISS_ENDPOINT": OIDC_ISSUER,
    # Hanya alur "authorization code" (yang paling aman untuk aplikasi web).
    "OIDC_RESPONSE_TYPES_SUPPORTED": ["code"],
    "OAUTH2_VALIDATOR_CLASS": "oauth.validators.PlatformOAuth2Validator",
    "SCOPES": OAUTH_SCOPES,
    "DEFAULT_SCOPES": ["openid"],
    # PKCE wajib: kode otorisasi yang dicuri di tengah jalan tidak bisa ditukar.
    "PKCE_REQUIRED": True,
    # Umur token. Access token pendek (10 menit); aplikasi memperbaruinya dengan
    # refresh token, yang BERGANTI setiap dipakai dan bisa dicabut pengguna.
    "AUTHORIZATION_CODE_EXPIRE_SECONDS": 60,
    "ACCESS_TOKEN_EXPIRE_SECONDS": int(os.getenv("OAUTH_ACCESS_TOKEN_SECONDS", "600")),
    "ID_TOKEN_EXPIRE_SECONDS": int(os.getenv("OAUTH_ACCESS_TOKEN_SECONDS", "600")),
    "REFRESH_TOKEN_EXPIRE_SECONDS": int(os.getenv("OAUTH_REFRESH_TOKEN_DAYS", "14")) * 86400,
    "ROTATE_REFRESH_TOKEN": True,
    # Refresh token lama dipakai lagi (tanda dicuri) -> seluruh "keluarga" token dicabut.
    "REFRESH_TOKEN_REUSE_PROTECTION": True,
    "REFRESH_TOKEN_GRACE_PERIOD_SECONDS": 0,   # wajib 0 bila token disimpan sebagai hash
    # Alamat kembali (redirect URI) harus https; http hanya di laptop (DEBUG).
    "ALLOWED_REDIRECT_URI_SCHEMES": ["https", "http"] if DEBUG else ["https"],
    # Halaman persetujuan diatur sendiri (oauth/views.py): sekali disetujui, tidak
    # ditanya lagi sampai izinnya dicabut di Profile -> Connected apps.
    "REQUEST_APPROVAL_PROMPT": "force",
    # "Create account" dari aplikasi klien (prompt=create) -> halaman Sign up platform.
    "OIDC_RP_INITIATED_REGISTRATION_ENABLED": ALLOW_SIGNUP,
    "OIDC_RP_INITIATED_REGISTRATION_URL": "accounts:register",
    # Logout dari aplikasi klien sekaligus dari platform (dengan konfirmasi).
    "OIDC_RP_INITIATED_LOGOUT_ENABLED": True,
    # Rekomendasi keamanan OAuth terbaru (RFC 9700): tolak implicit/password grant,
    # PKCE "plain", token di URL; simpan token sebagai hash; sertakan `iss` di redirect.
    "COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT": True,
    "COMPLIANT_BCP_RFC9700_PASSWORD_GRANT": True,
    "COMPLIANT_BCP_RFC9700_PKCE_METHOD": True,
    "COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT": True,
    "COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS": True,
    "COMPLIANT_BCP_RFC9700_TOKEN_STORAGE": True,
    "COMPLIANT_BCP_RFC9700_REFRESH_TOKEN": True,
    "COMPLIANT_BCP_RFC9700_REDIRECT_URI_SCHEME": True,
    "COMPLIANT_BCP_RFC9700_REDIRECT_URI_MATCHING": True,
    "COMPLIANT_BCP_RFC9700_PKCE_REQUIRED": True,
}


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
# Tujuan `python manage.py collectstatic` di server produksi. Nginx melayani
# folder ini langsung, tanpa melewati Django (lihat deploy/nginx/).
STATIC_ROOT = Path(os.getenv("DJANGO_STATIC_ROOT", "") or BASE_DIR / "staticfiles")
# Nama file ber-hash (cache busting, tahap 15): di produksi `{% static 'css/app.css' %}`
# menjadi /static/css/app.<hash>.css, sehingga browser selalu memakai versi terbaru.
# Butuh `collectstatic` (yang membuat staticfiles.json), jadi hanya aktif bila
# DEBUG=False dan bukan saat tes. Bisa dipaksa lewat DJANGO_STATIC_HASHED=True/False.
STATIC_HASHED = os.getenv("DJANGO_STATIC_HASHED", "") == "True" or (
    os.getenv("DJANGO_STATIC_HASHED", "") == "" and not DEBUG and sys.argv[1:2] != ["test"]
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "config.storage.HashedStaticStorage" if STATIC_HASHED
        else "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


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

# Alamat broker yang dipakai DEVICE, untuk ditampilkan di tab MQTT halaman device.
# Bisa berbeda dari MQTT_HOST di atas (alamat yang dipakai mqtt_bridge). Contoh di
# server produksi: mqtt_bridge tersambung ke 127.0.0.1:1883 tanpa TLS, sedangkan
# device dari internet memakai iot.domain-anda:8883 dengan TLS.
# Kosong = sama dengan pengaturan mqtt_bridge (cocok untuk broker publik saat belajar).
MQTT_PUBLIC_HOST = os.getenv("MQTT_PUBLIC_HOST", "") or MQTT_HOST
MQTT_PUBLIC_PORT = int(os.getenv("MQTT_PUBLIC_PORT", "") or MQTT_PORT)
MQTT_PUBLIC_TLS = (os.getenv("MQTT_PUBLIC_TLS", "") or str(MQTT_TLS)) == "True"
# True = broker meminta device login dengan username = password = access token
# (broker sendiri + `python manage.py mqtt_accounts`, lihat docs/deploy-vps.md).
MQTT_DEVICE_LOGIN = os.getenv("MQTT_DEVICE_LOGIN", "False") == "True"
# Alamat MQTT over WebSocket untuk BROWSER (tahap 21), mis. wss://iot.domain-anda/mqtt:
# listener WebSocket Mosquitto di belakang Nginx (deploy/mosquitto/iot.conf). Browser
# tidak bisa membuka TCP 1883/8883, hanya WebSocket. Dipakai aplikasi MQTT client web
# dan ditampilkan di tab MQTT halaman device. Kosong = tidak ada, tidak ditampilkan.
MQTT_PUBLIC_WS_URL = os.getenv("MQTT_PUBLIC_WS_URL", "").strip()
# Akun MQTT sandbox per pengguna (tahap 23): belajar publish/subscribe di sandbox/<username>/#
# tanpa device. Hanya bermakna di broker sendiri yang memakai akun (MQTT_DEVICE_LOGIN=True).
MQTT_SANDBOX_ENABLED = os.getenv("MQTT_SANDBOX_ENABLED", "") == "True" or (
    os.getenv("MQTT_SANDBOX_ENABLED", "") == "" and MQTT_DEVICE_LOGIN)


# --- CoAP ---------------------------------------------------------------------
# Server CoAP (UDP) untuk device; dijalankan dengan `python manage.py coap_server`.
# Berbeda dengan MQTT, tidak ada broker: device harus bisa menjangkau komputer
# ini secara langsung (satu jaringan, atau server dengan IP publik).

# Alamat IP yang didengarkan, boleh lebih dari satu dipisah koma.
# Kosong = otomatis: Linux mendengarkan semua alamat; macOS/Windows mendengarkan
# 127.0.0.1 ditambah IP komputer ini di jaringan (lihat coap_gateway/resources.py).
COAP_BIND = os.getenv("COAP_BIND", "")
COAP_PORT = int(os.getenv("COAP_PORT", "5683"))
# Alamat yang ditulis di halaman device untuk dipakai device, mis. IP komputer
# ini di jaringan Wi-Fi (192.168.1.10). Kosong = nama host di address bar browser.
COAP_PUBLIC_HOST = os.getenv("COAP_PUBLIC_HOST", "")
# Seberapa sering (detik) server memeriksa perubahan untuk device yang memakai Observe.
COAP_OBSERVE_INTERVAL = float(os.getenv("COAP_OBSERVE_INTERVAL", "1"))


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


# --- Rule engine --------------------------------------------------------------
# Lihat rules/queue.py dan docs/tahap-07-rule-engine-eca.md.
#   worker : pesan lewat antrean Redis, dinilai oleh `manage.py rule_engine`
#   inline : dinilai langsung setelah data disimpan (tanpa Redis)
#   off    : rule engine dimatikan
# Kosong = otomatis: worker bila REDIS_URL diisi, inline bila tidak.
RULE_ENGINE_MODE = os.getenv("RULE_ENGINE_MODE", "") or ("worker" if REDIS_URL else "inline")
# Saat `manage.py test`, pesan TIDAK boleh masuk antrean Redis sungguhan: id
# device/variabel di database tes bisa sama dengan id di database asli.
if sys.argv[1:2] == ["test"]:
    RULE_ENGINE_MODE = "inline"
# Seberapa sering (detik) worker memeriksa aturan berbasis waktu.
RULE_ENGINE_TICK = float(os.getenv("RULE_ENGINE_TICK", "1"))
# Penjadwalan (tahap 14): jadwal yang terlambat lebih dari ini (detik), mis. karena
# worker mati saat jadwal tiba, TIDAK dijalankan lagi tetapi dicatat "Skipped".
RULE_SCHEDULE_GRACE_SECONDS = int(os.getenv("RULE_SCHEDULE_GRACE_SECONDS", "300"))

# --- Housekeeping (tahap 16): status online/offline dan tabel ringkasan ------------
# Worker `manage.py housekeeping` (systemd: iot-housekeeping, runall: "housekeeping").
# Device tanpa data selama ini (detik) dianggap offline, kecuali punya sesi MQTT.
DEVICE_OFFLINE_SECONDS = int(os.getenv("DEVICE_OFFLINE_SECONDS", "300"))
# Ringkasan per menit dihitung ulang mundur sejauh ini (detik), supaya data yang
# datang terlambat (device menyimpan data saat offline) ikut terhitung.
TELEMETRY_ROLLUP_LATE_SECONDS = int(os.getenv("TELEMETRY_ROLLUP_LATE_SECONDS", "600"))

# --- Telegram (tahap 17, docs/tahap-17-notifikasi-telegram.md) ----------------------
# Token dari @BotFather. Kosong = aksi "Send Telegram message" tidak tersedia.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Opsional: nama bot tanpa @ (bila kosong ditanyakan ke Telegram lewat getMe).
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")
TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "https://api.telegram.org")

# --- Log aktivitas (tahap 18) -------------------------------------------------------
# Baris yang lebih tua dari ini (hari) dihapus worker housekeeping. 0 = simpan selamanya.
ACTIVITY_LOG_DAYS = int(os.getenv("ACTIVITY_LOG_DAYS", "180"))


# --- Email (aksi "Send email" pada aturan, aktivasi akun, lupa password) ----------
# EMAIL_HOST kosong: email TIDAK dikirim, melainkan dicetak di terminal proses
# yang menjalankan aksi (console backend) — cukup untuk belajar dan menguji.
# Tautan aktivasi akun dan reset password juga muncul di terminal web server.
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
    EMAIL_TIMEOUT = 10
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "IoT Platform <iot@localhost>")


# --- Pembaruan firmware OTA (tahap 9) ----------------------------------------------
# Lihat ota/services.py dan docs/tahap-09-ota.md.
# Folder file firmware. SENGAJA di luar static/media: file hanya boleh diambil
# lewat URL bertoken sekali pakai, bukan lewat alamat yang bisa ditebak.
OTA_STORAGE_DIR = Path(os.getenv("OTA_STORAGE_DIR", "") or BASE_DIR / "private_media" / "firmware")
# Alamat server yang dipakai DEVICE untuk mengunduh, mis. http://192.168.1.10:8000
# Kosong = alamat dari address bar browser (127.0.0.1 tidak bisa dijangkau ESP32!).
OTA_BASE_URL = os.getenv("OTA_BASE_URL", "").rstrip("/")
OTA_MAX_FIRMWARE_SIZE = int(os.getenv("OTA_MAX_FIRMWARE_SIZE", str(4 * 1024 * 1024)))  # 4 MB
OTA_COMMAND_TTL = int(os.getenv("OTA_COMMAND_TTL", "600"))       # device harus mengambil command dalam 10 menit
OTA_DOWNLOAD_TTL = int(os.getenv("OTA_DOWNLOAD_TTL", "1800"))    # link unduhan berlaku 30 menit
OTA_UPDATE_TIMEOUT = int(os.getenv("OTA_UPDATE_TIMEOUT", "900")) # seluruh update harus selesai dalam 15 menit


# --- Produksi (lihat docs/deploy-vps.md) ----------------------------------------
# Semua di bawah hanya berlaku bila DJANGO_DEBUG=False. Di laptop (DEBUG=True)
# tidak ada yang berubah.

# Alamat lengkap yang boleh mengirim form (POST) ke situs ini, mis. https://iot.contoh.my.id
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()
]

if not DEBUG:
    from django.core.exceptions import ImproperlyConfigured

    # Kunci bawaan tertulis di kode yang publik: dengan kunci itu orang bisa
    # memalsukan cookie sesi. Server produksi WAJIB punya kuncinya sendiri.
    if SECRET_KEY.startswith("dev-only") or len(SECRET_KEY) < 40:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY in .env to a long random value (50+ characters).")

    # Django berada di belakang Nginx. Nginx yang menerima HTTPS lalu meneruskan
    # ke Daphne lewat HTTP biasa, sambil memberi tahu "aslinya https" lewat header ini.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # DJANGO_HTTPS=True setelah sertifikat terpasang (langkah 8 panduan).
    if os.getenv("DJANGO_HTTPS", "False") == "True":
        SESSION_COOKIE_SECURE = True    # cookie login hanya dikirim lewat HTTPS
        CSRF_COOKIE_SECURE = True
        # HSTS: browser diminta SELALU memakai HTTPS untuk domain ini. Mulai kecil
        # (mis. 3600), naikkan ke 31536000 setelah yakin HTTPS berjalan baik.
        SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "3600"))
        SECURE_HSTS_INCLUDE_SUBDOMAINS = False
        # Pengalihan http -> https dilakukan Nginx untuk SEMUA alamat (lihat
        # deploy/nginx/iot-platform.conf). Karena itu Django sendiri tidak mengalihkan.
        SECURE_SSL_REDIRECT = False
        # Peringatan `check --deploy` yang SENGAJA dibungkam, beserta alasannya:
        #   W008  pengalihan ke https dikerjakan Nginx, bukan Django (lihat di atas)
        #   W005  HSTS tidak dipaksakan ke subdomain lain milik domain yang sama
        #   W021  domain tidak didaftarkan ke daftar preload browser (sulit dibatalkan)
        SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W005", "security.W021"]

    SESSION_COOKIE_HTTPONLY = True      # JavaScript tidak bisa membaca cookie sesi
    X_FRAME_OPTIONS = "DENY"            # halaman tidak boleh dibungkus <iframe> situs lain

    # Log ke stdout/stderr; systemd menyimpannya di journal (journalctl -u iot-web).
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "{levelname} {name}: {message}", "style": "{"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
        "root": {"handlers": ["console"], "level": "INFO"},
        "loggers": {
            "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
            # Percobaan Host palsu (pemindai otomatis) cukup dicatat sebagai peringatan.
            "django.security.DisallowedHost": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        },
    }
