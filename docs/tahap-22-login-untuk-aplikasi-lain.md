# Tahap 22: Login untuk aplikasi lain (OAuth 2.0 + OpenID Connect)

Tahap ini menjadikan platform **penyedia login** untuk aplikasi lain, seperti tombol "Sign in
with Google". Pemakai pertamanya adalah aplikasi MQTT client web (repo `mqtt_client`, rencana
tahap M1). Pengguna tidak perlu membuat akun kedua: mereka login dengan akun platform, dan
aplikasi itu bisa membaca daftar device mereka.

## 1. Masalah yang diselesaikan

Cara naif: aplikasi MQTT client meminta username dan password platform, lalu login atas nama
pengguna. Cara ini buruk:

- password pengguna ikut dipegang aplikasi lain;
- aplikasi itu bisa melakukan **apa saja** atas nama pengguna, tidak hanya yang dibutuhkan;
- akses tidak bisa dicabut tanpa mengganti password.

**OAuth 2.0** memecahkannya dengan **token**: aplikasi tidak pernah melihat password. Pengguna
login di platform, menyetujui izin tertentu (*scope*), lalu platform memberi aplikasi itu token
yang terbatas, berumur pendek, dan bisa dicabut.

**OpenID Connect (OIDC)** adalah lapisan di atas OAuth 2.0 untuk *login*. Selain token akses,
aplikasi menerima **id_token**: JWT bertanda tangan digital yang menyatakan "ini pengguna budi,
id 5, baru saja login di platform".

## 2. Istilah

| Istilah | Dalam project ini |
|---|---|
| Authorization server / OpenID provider | platform ini (`/o/...`) |
| Client / relying party | aplikasi MQTT client web |
| Resource owner | pengguna (pemilik device) |
| Resource server | API platform `/api/v2/...` |
| Scope | izin: `openid`, `profile`, `email`, `devices:read` |
| Authorization code | kode sekali pakai (60 detik) yang ditukar menjadi token |
| PKCE | bukti bahwa yang menukar kode adalah yang memintanya |
| Access token | "kartu akses" ke API, 10 menit |
| Refresh token | untuk meminta access token baru tanpa login ulang, 14 hari, berganti setiap dipakai |
| id_token | JWT berisi identitas pengguna, ditandatangani kunci RSA platform |

## 3. Alur lengkap (authorization code + PKCE)

```
Browser             MQTT client (mqtt.domain)           Platform (iot.domain)
   │ klik "Sign in"         │                                   │
   │───────────────────────▶│ buat state, nonce, code_verifier  │
   │◀── 302 ────────────────│ code_challenge = SHA256(verifier) │
   │ GET /o/authorize/?client_id&redirect_uri&scope&state&nonce&code_challenge
   │───────────────────────────────────────────────────────────▶│ belum login? -> /accounts/login/?next=...
   │                        │                                   │ login, lalu halaman persetujuan
   │ "Allow"                │                                   │ (dilewati bila sudah pernah setuju)
   │◀── 302 redirect_uri?code=...&state=...&iss=... ────────────│
   │───────────────────────▶│ cek state                         │
   │                        │ POST /o/token/ code + code_verifier + client_secret
   │                        │──────────────────────────────────▶│ cek kode, PKCE, secret
   │                        │◀── access_token, refresh_token, id_token
   │                        │ cek tanda tangan id_token (kunci publik dari /o/.well-known/jwks.json),
   │                        │ cek iss, aud, nonce -> buat sesi pengguna di aplikasi client
   │                        │ GET /api/v2/me/devices  (Authorization: Bearer <access_token>)
   │                        │──────────────────────────────────▶│
```

Mengapa serumit ini?

- **state**: mencegah penyerang menyisipkan kode miliknya ke sesi korban (CSRF pada login).
- **nonce**: id_token terikat pada satu percobaan login, jadi tidak bisa diputar ulang.
- **PKCE**: kode yang bocor di tengah jalan (riwayat browser, log proxy) tidak berguna tanpa
  `code_verifier`, yang tidak pernah lewat browser. Di project ini PKCE **wajib**.
- **client_secret**: hanya server aplikasi client yang tahu, jadi aplikasi palsu tidak bisa
  menukar kode.
- **redirect_uri harus persis sama** dengan yang didaftarkan. Bila tidak, platform menolak
  tanpa mengalihkan, supaya kode tidak terkirim ke situs penipu.

## 4. Yang ditambahkan

| File | Isi |
|---|---|
| `requirements.txt` | `django-oauth-toolkit` (app `oauth2_provider`), `oauthlib`, `jwcrypto` |
| `config/settings.py` | `OAUTH2_PROVIDER`, `OAUTH_SCOPES`, `OIDC_RSA_PRIVATE_KEY_FILE`, `OIDC_ISSUER` |
| `oauth/` (app baru) | `views.py` (halaman persetujuan, Connected apps), `models.py` (`Consent`), `validators.py` (klaim, akun nonaktif), `auth.py` (`@oauth_required`), `signals.py`, `checks.py`, `keys.py`, perintah `oidc_key` dan `oauth_client` |
| `oauth/urls.py` | `/o/authorize/`, `/o/token/`, `/o/revoke_token/`, `/o/userinfo/`, `/o/logout/`, `/o/.well-known/openid-configuration`, `/o/.well-known/jwks.json` |
| `api/v2.py` | `GET /api/v2/me`, `GET /api/v2/me/devices` |
| `accounts/` | `next` terbawa lewat Sign up → email aktivasi → aktivasi; tautan Sign up di halaman login membawa `next` |
| Profil | kartu **Connected apps** → `/accounts/apps/` (daftar dan *Revoke access*) |
| `deploy/nginx/iot-platform.conf` | batas laju untuk `/o/` |
| `oauth/tests.py` | 25 tes: alur penuh, PKCE, redirect URI, consent, refresh token, scope, API, Sign up |

## 5. Keputusan penting

**Hanya alur authorization code.** Implicit grant dan password grant ditolak, juga PKCE `plain`
dan token di query string. Semua rekomendasi RFC 9700 (*OAuth 2.0 Security Best Current
Practice*) diaktifkan (`COMPLIANT_BCP_RFC9700_*`), sehingga `check --deploy` bersih.

**Token disimpan sebagai hash.** Seperti password, database hanya menyimpan sidik jari token. Bila
database bocor, tokennya tidak bisa dipakai. Konsekuensinya, `REFRESH_TOKEN_GRACE_PERIOD_SECONDS`
harus 0.

**Refresh token berganti setiap dipakai, dengan deteksi pemakaian ulang.** Bila refresh token lama
dipakai lagi (tanda dicuri), seluruh "keluarga" token itu dicabut dan pengguna harus login ulang.

**Persetujuan diingat (model `Consent`).** django-oauth-toolkit hanya melewati halaman persetujuan
bila masih ada access token yang berlaku (10 menit). Tanpa `Consent`, pengguna ditanya hampir
setiap kali login. Aturannya sekarang:

- scope yang sama atau lebih sedikit → langsung lanjut;
- scope baru → ditanya lagi;
- aplikasi mengirim `prompt=consent` → selalu ditanya.

**Access token device hanya untuk Editor ke atas.** `/api/v2/me/devices` mengikuti aturan yang
sama dengan kartu "Connect a device" (tahap 19): Viewer dan Operator mendapat
`"access_token": null` dan `"topics": null`.

**Admin tidak mendapat semua device.** Di halaman web Admin melihat semua device, tetapi API
untuk aplikasi luar hanya mengembalikan device miliknya dan yang dibagikan kepadanya. Aplikasi
lain tidak perlu memegang token seluruh platform.

**Akun nonaktif langsung kehilangan akses.** Sinyal `post_save` mencabut semua token. Validator
juga menolak akun nonaktif saat API dipanggil dan saat refresh token ditukar.

**Pendaftaran klien hanya oleh Admin**, lewat perintah:

```bash
python manage.py oauth_client "MQTT Client" \
       --redirect-uri https://mqtt.domain-anda/oidc/callback/ --logout-uri https://mqtt.domain-anda/
```

`client_secret` dicetak **sekali** saja. Database hanya menyimpan hash-nya, jadi salin langsung
ke `.env` aplikasi client. `--rotate-secret` membuat secret baru, dan secret lama langsung tidak
berlaku.

**Kunci RSA di `private_media/oidc/private.pem`** (izin 600, ikut backup). Kunci ini
menandatangani id_token; kunci publiknya diumumkan di `/o/.well-known/jwks.json`. Bila kunci
privat bocor, siapa pun bisa membuat id_token palsu. Ganti dengan `oidc_key --force`, lalu
restart.

## 6. Pengguna baru dari aplikasi lain

Aplikasi client bisa mengirim `prompt=create` ke `/o/authorize/`. Platform lalu mengarahkan ke
Sign up dengan `next` = alamat authorize semula:

```
/o/authorize/?...&prompt=create
  -> /accounts/register/?next=https://iot.domain/o/authorize/?...
  -> email aktivasi: /accounts/activate/<uid>/<token>/?next=...
  -> Activate -> kembali ke /o/authorize/ -> persetujuan -> aplikasi client
```

Setiap `next` diperiksa dengan `url_has_allowed_host_and_scheme`. Hanya alamat di situs ini
yang diterima; `?next=https://situs-palsu` diabaikan, untuk mencegah *open redirect*.

Keterbatasan: tautan dari **Resend activation link** tidak membawa `next`. Setelah aktivasi,
pengguna sampai di dashboard dan perlu menekan "Sign in" lagi di aplikasi client.

## 7. API v2

```bash
curl -H "Authorization: Bearer <access_token>" https://iot.domain-anda/api/v2/me/devices
```

```json
{
  "broker": {
    "mqtt": {"host": "iot.domain-anda", "port": 8883, "tls": true},
    "websocket": "wss://iot.domain-anda/mqtt",
    "device_login": true,
    "topic_prefix": "iot"
  },
  "devices": [
    {
      "id": 7, "name": "Greenhouse", "location": "Lab", "owner": "budi", "role": "owner",
      "is_active": true, "is_online": true, "last_seen_at": "2026-10-21T08:15:02+00:00",
      "access_token": "Xy...", "topics": {"telemetry": "iot/Xy.../telemetry", "...": "..."},
      "variables": [{"key": "temp", "name": "Temperature", "kind": "sensor", "unit": "°C", "...": "..."}]
    }
  ]
}
```

| Kode | Arti |
|---|---|
| 200 | berhasil |
| 401 `invalid_token` | token tidak ada, salah, kedaluwarsa, dicabut, atau akun nonaktif |
| 403 `insufficient_scope` | pengguna tidak menyetujui scope yang dibutuhkan (header `WWW-Authenticate` menyebutnya) |

Sesi login browser **tidak** berlaku di `/api/v2/`: API ini khusus untuk aplikasi luar yang
membawa token.

## 8. Mencoba di laptop

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py oidc_key              # sekali; kunci di private_media/oidc/private.pem
python manage.py oauth_client "Test" --redirect-uri http://127.0.0.1:9000/callback/
python manage.py runserver
```

Buka `http://127.0.0.1:8000/o/.well-known/openid-configuration` untuk melihat semua alamat.
Untuk mencoba alur lengkap tanpa aplikasi client, jalankan tesnya:

```bash
python manage.py test oauth
```

Di laptop (`DEBUG=True`) redirect URI `http://` diizinkan. Di server hanya `https://`.

## 9. Di server

Lihat `docs/deploy-vps.md` bagian 14.3. Ringkasnya: tarik kode dan paket baru, buat
kunci dengan `manage.py oidc_key` sebagai user `iot`, isi `OIDC_ISSUER` di `.env`, lalu
`update.sh`. `check --deploy` di `update.sh`
berhenti bila kunci atau issuer belum diatur (`oauth.E001`, `oauth.E002`).
