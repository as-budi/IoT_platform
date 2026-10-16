# Tahap 11: Verifikasi email dan lupa password

Tahap ini membuat email menjadi bagian dari akun:

1. **Sign up dengan verifikasi email.** Akun baru belum bisa dipakai sampai pemiliknya
   mengklik tautan aktivasi yang dikirim ke emailnya.
2. **Konfirmasi perubahan email.** Mengganti email di halaman profil baru berlaku setelah
   tautan di alamat baru diklik.
3. **Lupa password.** Tautan *Forgot your password?* di halaman login mengirim tautan untuk
   membuat password baru.

Semua memakai perangkat bawaan Django. Tidak ada library tambahan.

## Alur singkat

```
Sign up ──► akun dibuat: is_active=False, activation_pending=True
        └─► email "Activate your IoT Platform account" (tautan /accounts/activate/<uid>/<token>/)
                 │
Klik tautan ──► halaman "Activate your account" (GET, tidak mengubah apa pun)
                 │
Tombol Activate (POST) ──► is_active=True, activation_pending=False, langsung login

Profil: ganti email ──► pending_email = alamat baru, email = tetap alamat lama
                     └─► email ke ALAMAT BARU (tautan /accounts/profile/email/<uid>/<token>/)
Klik tautan + Confirm (POST) ──► email = alamat baru, pending_email = ""

Forgot your password? ──► isi email ──► tautan /accounts/password/reset/<uid>/<token>/
                     ──► isi password baru ──► semua sesi lama keluar ──► login
```

## File yang berubah

| File | Perubahan |
|---|---|
| `accounts/models.py` + migrasi `0002_email_verification` | kolom `activation_pending`, `pending_email` |
| `accounts/tokens.py` (baru) | pembuat token aktivasi dan token ganti email |
| `accounts/emails.py` (baru) | menyusun dan mengirim email akun |
| `accounts/templates/accounts/email/` (baru) | isi email (teks): aktivasi, ganti email, reset password |
| `accounts/forms.py` | email wajib dan unik, `LoginForm` dengan pesan akun belum aktif, `ProfileForm` menunda email baru, form reset password |
| `accounts/views.py` | `RegisterView` (tanpa login otomatis), `RegisterDoneView`, `ActivateView`, `ResendActivationView`, `EmailChangeConfirmView`, `EmailChangeCancelView`, empat view reset password |
| `accounts/urls.py` | sembilan URL baru (tabel di bawah) |
| templates akun dan `templates/registration/login.html` | halaman baru; tautan lupa password; tombol kirim ulang aktivasi |
| `accounts/management/commands/delete_unactivated.py` (baru) | hapus akun yang tidak pernah diaktivasi |
| `config/settings.py` | `PASSWORD_RESET_TIMEOUT` (lama berlaku tautan, bawaan 24 jam) |
| `deploy/` | `iot-cleanup.service` + `.timer`; batas laju Nginx untuk halaman akun; contoh `.env` |
| `accounts/tests_email.py` (baru), `accounts/tests.py` | 19 tes baru, tes lama disesuaikan |

| URL | Nama url | Fungsi |
|---|---|---|
| `/accounts/register/done/` | `accounts:register_done` | "Check your email" |
| `/accounts/activate/resend/` | `accounts:activation_resend` | kirim ulang tautan aktivasi |
| `/accounts/activate/<uidb64>/<token>/` | `accounts:activate` | aktivasi akun |
| `/accounts/profile/email/<uidb64>/<token>/` | `accounts:email_confirm` | konfirmasi email baru |
| `/accounts/profile/email/cancel/` | `accounts:email_cancel` | batalkan ganti email |
| `/accounts/password/reset/` | `accounts:password_reset` | isi email |
| `/accounts/password/reset/done/` | `accounts:password_reset_done` | "Check your email" |
| `/accounts/password/reset/<uidb64>/<token>/` | `accounts:password_reset_confirm` | isi password baru |
| `/accounts/password/reset/complete/` | `accounts:password_reset_complete` | selesai |

## Konsep 1: token tanpa tabel

Tautan di email berisi dua bagian:

```
/accounts/activate/MQ/dgjos5-47acf82cee3f1d248086d6c354dd31cd/
                   ││  └────┬─┘ └──────────────┬─────────────┘
                   ││   timestamp          HMAC (tanda tangan)
                   │└─ uidb64: id pengguna (1) dalam base64
```

Token **tidak disimpan di database**. Saat tautan dibuka, Django menghitung ulang tanda
tangan dari data pengguna saat ini dan `SECRET_KEY`:

```
HMAC(SECRET_KEY + key_salt,  _make_hash_value(user, timestamp))
```

Bila hasilnya sama dengan yang ada di tautan dan umurnya belum lewat
`PASSWORD_RESET_TIMEOUT`, tautan sah. Tanpa `SECRET_KEY`, orang lain tidak bisa membuat
tanda tangan yang benar; mengubah satu huruf saja membuat tautan ditolak.

Yang menarik: tautan otomatis **hangus** begitu data yang ikut di-hash berubah. Karena itu
setiap jenis tautan memilih isi `_make_hash_value` sendiri (`accounts/tokens.py`):

| Tautan | Data yang di-hash | Akibatnya |
|---|---|---|
| Aktivasi | id, `is_active`, `activation_pending`, email | setelah aktivasi kedua kolom berubah, tautan tidak bisa dipakai lagi |
| Ganti email | id, email, `pending_email` | hangus bila email baru diganti lagi, dibatalkan, atau sudah dikonfirmasi |
| Reset password (bawaan Django) | id, hash password, waktu login terakhir | hangus begitu password diganti: hanya bisa dipakai sekali |

`key_salt` yang berbeda per jenis menjamin token satu jenis tidak bisa dipakai untuk jenis
lain. Tes `test_activation_token_cannot_reset_password` memeriksanya.

## Konsep 2: kolom `activation_pending`, bukan sekadar `is_active=False`

Akun yang **dinonaktifkan Admin** juga `is_active=False`. Kalau aktivasi hanya melihat
`is_active`, pengguna yang diblokir Admin bisa meminta tautan aktivasi baru lalu
mengaktifkan dirinya sendiri. Karena itu ada kolom terpisah:

| Keadaan | `is_active` | `activation_pending` |
|---|---|---|
| Baru Sign up, belum klik tautan | False | True |
| Aktif | True | False |
| Dinonaktifkan Admin | False | False |

Aktivasi, kirim ulang tautan, dan pembersihan otomatis hanya berlaku untuk baris pertama.
Bila Admin mencentang *Active* untuk akun yang belum diaktivasi, `activation_pending`
ikut dikosongkan.

## Konsep 3: GET hanya menampilkan, POST yang mengubah

Membuka tautan aktivasi (GET) hanya menampilkan tombol **Activate account**. Akun baru aktif
saat tombol ditekan (POST). Alasannya: banyak layanan email (terutama email kantor/kampus)
punya pemindai anti-phishing yang **membuka semua tautan** di email secara otomatis. Bila GET
langsung mengaktifkan dan me-login-kan, sesi login itu jatuh ke pemindai, bukan ke pemilik
akun. Aturan umum HTTP berlaku: GET tidak boleh mengubah data.

Konfirmasi ganti email memakai pola yang sama. Reset password bawaan Django juga: tautan
hanya membuka form password baru.

## Konsep 4: jangan membocorkan siapa yang terdaftar

Halaman **Forgot password** dan **Resend activation link** selalu menjawab sama, baik email
yang diketik terdaftar maupun tidak ("bila ada akun dengan alamat itu, email sudah
dikirim"). Tanpa ini, orang luar bisa mencoba ribuan alamat untuk mengetahui siapa saja
pengguna platform.

Pesan login khusus *"This account is not activated yet"* juga hanya muncul bila password
yang diketik **benar**. Dengan password salah, pesannya tetap pesan umum.

Satu pengecualian yang disengaja: Sign up memberi tahu bila email sudah dipakai, karena
email harus unik. Batas laju Nginx (10 per menit per IP) membuat cara ini lambat untuk
menebak massal.

## Konsep 5: email baru menunggu konfirmasi

Di halaman profil, email baru disimpan di `pending_email`; kolom `email` tetap alamat lama
sampai tautan di alamat baru diklik. Alasannya:

- salah ketik alamat tidak membuat pemilik akun kehilangan fitur lupa password, karena
  alamat lama tetap dipakai sampai alamat baru terbukti bisa menerima email;
- tidak ada akun yang memakai alamat milik orang lain yang tidak pernah mengonfirmasinya;
- saat dikonfirmasi, keunikan diperiksa ulang: alamat itu bisa saja sudah dipakai akun lain
  di antara waktu pengiriman dan klik.

## Konsep 6: email unik

Satu alamat email = satu akun, karena email dipakai untuk reset password. Keunikan diperiksa
di form (huruf besar/kecil dianggap sama), di Sign up, profil, dan form Admin. Tidak dibuat
constraint database karena data lama boleh berisi email kosong, dan MySQL tidak mendukung
*unique* bersyarat (unik hanya untuk yang tidak kosong).

## Konsep 7: akun yang tidak pernah diaktivasi

Tanpa pembersihan, siapa pun bisa "mengunci" username atau email orang lain dengan mendaftar
lalu tidak pernah mengaktifkannya.

```bash
python manage.py delete_unactivated --days 7 --dry-run   # lihat dulu
python manage.py delete_unactivated --days 7             # hapus
```

Di server, `iot-cleanup.timer` menjalankannya setiap hari pukul 03.15.

## Mencoba di laptop

Selama `EMAIL_HOST` di `.env` kosong, email tidak dikirim tetapi **dicetak di terminal** yang
menjalankan web server (`runall` atau `runserver`). Salin tautannya dari sana.

1. `python manage.py migrate` (migrasi `accounts 0002`).
2. Logout, buka **Sign up**, daftar dengan email apa saja.
3. Coba login: muncul pesan *not activated yet* dan tombol *Resend activation link*.
4. Cari blok `Subject: Activate your IoT Platform account` di terminal, buka tautannya,
   tekan **Activate account**. Anda langsung masuk ke dashboard.
5. Di **My profile**, ganti email. Email masih alamat lama dan muncul kotak *Waiting for
   confirmation*. Buka tautan dari terminal, tekan **Confirm**.
6. Logout, klik **Forgot your password?**, isi email Anda, buka tautan dari terminal, isi
   password baru, lalu login dengan password itu. Buka tautan yang sama sekali lagi: ditolak.

Tes otomatis:

```bash
python manage.py test accounts
```

Saat tes, Django memakai backend email *locmem*: email ditampung di `mail.outbox`, dan tes
membaca tautannya dari sana, persis seperti pengguna mengkliknya.

## Menerapkan di server

1. Commit dan push, lalu `sudo /srv/iot/app/deploy/scripts/update.sh` (menjalankan migrasi).
2. Isi bagian email di `.env` dan uji dengan `sendtestemail` (panduan deploy, langkah 10.1).
3. Unit pembersihan baru:
   ```bash
   sudo cp /srv/iot/app/deploy/systemd/iot-cleanup.* /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now iot-cleanup.timer
   ```
4. Batas laju Nginx untuk halaman akun. Di `/etc/nginx/sites-available/iot-platform`, ganti
   baris `location = /accounts/login/ {` menjadi
   ```nginx
   location ~ ^/accounts/(login|register|activate/resend|password/reset)/$ {
   ```
   lalu `sudo nginx -t && sudo systemctl reload nginx`.
5. Bila ingin membuka Sign up untuk umum: `DJANGO_ALLOW_SIGNUP=True` di `.env`, lalu
   `sudo systemctl restart iot-web`.

Akun yang sudah ada tidak terpengaruh: migrasi memberi `activation_pending=False` dan
`pending_email` kosong. Akun tanpa email tidak bisa memakai lupa password; mintalah pemiliknya
mengisi email di halaman profil.
