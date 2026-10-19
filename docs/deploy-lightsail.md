# Deploy ke Amazon Lightsail (dengan pengamanan server)

Panduan ini memasang seluruh platform di **satu server Lightsail**:

- web, API HTTP, dan WebSocket,
- MQTT dengan broker sendiri,
- rule engine,
- OTA,
- MySQL dan Redis.

Setiap langkah disertai alasan keamanannya. Semua berkas konfigurasi yang dirujuk ada di
folder [`deploy/`](../deploy/README.md).

```
                         Internet
                            │
             ┌──────────────┼──────────────────────┐
             │  Firewall Lightsail + ufw di server │  hanya 22 (IP Anda), 80, 443, 8883
             └──────────────┼──────────────────────┘
                            │
   ┌──────── server Ubuntu 24.04 ──────────────────────────────────────────┐
   │  Nginx :80/:443  (HTTPS Let's Encrypt, batas laju login & API)       │
   │     └─► Daphne 127.0.0.1:8001  (iot-web)  ── halaman, API, WebSocket │
   │  Mosquitto :8883 TLS + akun per device + ACL                         │
   │            :1883 hanya 127.0.0.1 ◄── mqtt_bridge (iot-mqtt)          │
   │  rule_engine (iot-rules)    coap_server (iot-coap, opsional)         │
   │  MySQL (socket lokal)       Redis 127.0.0.1                          │
   └──────────────────────────────────────────────────────────────────────┘
```

Prinsip yang dipakai di seluruh panduan:

1. **Sesedikit mungkin pintu terbuka.** Hanya Nginx dan Mosquitto (TLS) yang menghadap
   internet. Database, Redis, Daphne, dan port MQTT tanpa enkripsi hanya bisa diakses dari
   server itu sendiri.
2. **Setiap proses dengan hak sekecil mungkin.** Aplikasi berjalan sebagai user `iot`, bukan
   root. Unit systemd membuat sistem berkas read-only bagi proses aplikasi.
3. **Semua lalu lintas dienkripsi.** HTTPS untuk browser, MQTT TLS untuk device.
4. **Rahasia tidak ada di kode.** Semua rahasia ada di `.env` dengan izin `600`.
5. **Bisa dipulihkan.** Ada snapshot harian Lightsail dan backup database harian.

Perkiraan waktu: 2–3 jam untuk pertama kali.

Daftar isi:

1. [Mendapatkan domain](#1-mendapatkan-domain)
2. [Membuat server di Lightsail](#2-membuat-server-di-lightsail)
3. [Mengarahkan domain ke server](#3-mengarahkan-domain-ke-server)
4. [Masuk lewat SSH dan mengamankan sistem operasi](#4-masuk-lewat-ssh-dan-mengamankan-sistem-operasi)
5. [Memasang paket](#5-memasang-paket)
6. [Database MySQL](#6-database-mysql)
7. [Memasang aplikasi](#7-memasang-aplikasi)
8. [Web: Daphne, Nginx, dan HTTPS](#8-web-daphne-nginx-dan-https)
9. [Broker MQTT sendiri (Mosquitto)](#9-broker-mqtt-sendiri-mosquitto)
10. [Menjalankan semua layanan](#10-menjalankan-semua-layanan)
11. [Menghubungkan device](#11-menghubungkan-device)
12. [Daftar periksa keamanan](#12-daftar-periksa-keamanan)
13. [Backup dan pemulihan](#13-backup-dan-pemulihan)
14. [Memperbarui aplikasi](#14-memperbarui-aplikasi)
15. [Pemantauan dan pemecahan masalah](#15-pemantauan-dan-pemecahan-masalah)
16. [Risiko yang masih ada](#16-risiko-yang-masih-ada)
17. [Perkiraan biaya](#17-perkiraan-biaya)

Di seluruh panduan, ganti nilai contoh berikut dengan milik Anda:

| Contoh | Arti |
|---|---|
| `iot.contoh.my.id` | nama domain server |
| `203.0.113.10` | static IP server dari Lightsail |
| `198.51.100.7` | IP publik internet Anda (cek di whatismyip.com) |

---

## 1. Mendapatkan domain

Domain dibutuhkan untuk sertifikat HTTPS gratis dari Let's Encrypt dan untuk alamat yang
mudah diingat device. Pilih **salah satu** cara berikut.

### Pilihan A — Subdomain kampus (gratis, paling meyakinkan)

Minta ke unit TIK universitas atau fakultas sebuah subdomain, mis. `iot.filkom.ub.ac.id`,
yang diarahkan ke static IP server (langkah 2). Biasanya cukup mengirim permohonan berisi
nama subdomain, IP tujuan, dan kegunaannya. Kebijakan dan prosedurnya mengikuti kampus
masing-masing. Cara ini juga paling cocok untuk platform praktikum yang dipakai mahasiswa.

### Pilihan B — Domain .id dari registrar Indonesia (murah)

Domain `.id` dikelola PANDI dan dijual oleh registrar terakreditasi (mis. Hostinger,
Niagahoster, Rumahweb, IDCloudHost).

- **`.my.id`** ditujukan untuk perorangan. Untuk mendaftar cukup email aktif, tanpa KTP.
  Harganya sangat murah, misalnya mulai Rp16.900 per tahun di Hostinger (belum PPN, Oktober
  2026).
- Ekstensi lain (`.web.id`, `.id`, `.co.id`) punya harga dan persyaratan dokumen
  masing-masing.

Langkahnya:

1. Cari nama yang tersedia di situs registrar, lalu bayar.
2. Buka **panel DNS** domain itu di registrar. Pengisiannya di langkah 3.

### Pilihan C — Langsung di Lightsail (Route 53)

Di konsol Lightsail pilih **Domains & DNS → Register domain**.

1. Ketik nama, lalu **Check availability**.
2. Isi data kontak sesuai identitas resmi dan aktifkan **privacy protection**.
3. Setujui perjanjian registrasi.

Setelah itu ada dua hal penting:

- **Klik tautan verifikasi** di email registrant. Domain yang tidak diverifikasi akan
  ditangguhkan.
- Lightsail otomatis membuat zona DNS gratis.

Nama domain **tidak bisa diubah** setelah terdaftar, jadi periksa ejaannya sebelum membayar.
Tarif mengikuti Route 53; sejak Juli 2026 `.com` sekitar US$16 per tahun. Domain geografis
seperti `.id` tidak bisa didaftarkan dari Lightsail.

### Tanpa domain?

Aplikasi bisa dicoba lewat `http://<static-IP>`, tetapi:

- tanpa HTTPS, password login dan access token terkirim tanpa enkripsi;
- MQTT TLS juga butuh nama domain untuk sertifikatnya.

Let's Encrypt kini juga menerbitkan sertifikat untuk alamat IP, tetapi hanya berlaku 6 hari
(profil `shortlived`) dan perlu klien ACME yang mendukungnya. Panduan ini tidak membahasnya.
Untuk server yang dipakai orang lain, gunakan domain.

---

## 2. Membuat server di Lightsail

### 2.1 Amankan akun AWS dulu

1. Aktifkan **MFA** untuk akun root AWS (*Security credentials → Assign MFA device*).
2. Untuk pekerjaan sehari-hari, buat user IAM dengan izin Lightsail, bukan memakai root.
3. Pasang batas biaya: **Billing → Budgets → Create budget**, misalnya US$20 per bulan dengan
   peringatan email.

### 2.2 Buat instance

Di [konsol Lightsail](https://lightsail.aws.amazon.com/) pilih **Create instance**:

| Pilihan | Nilai | Alasan |
|---|---|---|
| Region | **Singapore** atau **Jakarta** | dekat dengan device di Indonesia. Kuota transfer region Jakarta hanya setengah dari region lain, tetapi lalu lintas IoT kecil |
| Platform | Linux/Unix | |
| Blueprint | **OS Only → Ubuntu 24.04 LTS** | Python 3.12 bawaan sama dengan lingkungan pengembangan; dukungan keamanan sampai 2029 |
| SSH key | buat key pair baru, lalu **unduh dan simpan** file `.pem` | satu-satunya cara masuk; jangan dibagikan |
| Network type | Dual-stack (IPv4 + IPv6) | ESP32 dan kebanyakan jaringan masih butuh IPv4 |
| Plan | **2 GB RAM, 2 vCPU, 60 GB SSD** (US$12 per bulan dengan IPv4) | MySQL, Redis, Mosquitto, dan empat proses Python cukup lega. Paket 1 GB bisa untuk percobaan, dengan swap (langkah 4) |
| Nama | `iot-platform` | |

Pengguna Lightsail baru mendapat masa gratis untuk beberapa paket kecil (termasuk yang
US$12, sampai 3 bulan). Perhatikan juga bahwa **instance yang di-stop tetap ditagih**; hanya
instance yang dihapus yang berhenti ditagih.

### 2.3 Static IP

Tanpa static IP, alamat server berubah setiap kali instance di-stop lalu dinyalakan lagi.

1. Buka **Networking → Create static IP**, lalu sambungkan ke `iot-platform`.
2. Catat alamatnya (contoh di panduan ini: `203.0.113.10`).

Static IP gratis selama tersambung ke instance yang berjalan.

### 2.4 Firewall Lightsail

Buka instance → tab **Networking → IPv4 Firewall**. Hapus aturan bawaan yang tidak perlu,
lalu isi:

| Application | Protocol | Port | Batasi ke IP |
|---|---|---|---|
| SSH | TCP | 22 | **Ya**: `198.51.100.7` (IP Anda), dan centang **Allow Lightsail browser SSH** |
| HTTP | TCP | 80 | tidak (dibutuhkan Let's Encrypt dan pengalihan ke HTTPS) |
| HTTPS | TCP | 443 | tidak |
| Custom | TCP | 8883 | tidak (MQTT TLS untuk device) |

**Jangan** membuka 3306 (MySQL), 6379 (Redis), 1883 (MQTT tanpa TLS), atau 8001 (Daphne).
UDP 5683 (CoAP) baru dibuka bila CoAP memang dipakai (bagian 11.3).

Bila IPv6 aktif, isi **IPv6 Firewall** dengan aturan yang sama. Firewall IPv4 dan IPv6
terpisah, dan yang terlupa menjadi celah.

IP rumah atau kantor bisa berubah. Bila SSH tiba-tiba ditolak, masuk lewat **Connect using
SSH** di konsol (browser SSH), lalu perbarui aturan port 22.

### 2.5 Snapshot otomatis

Buka instance → tab **Snapshots → Automatic snapshots → Enable**, lalu pilih jam sepi
(mis. 03:00). Lightsail menyimpan 7 snapshot harian terakhir, sehingga seluruh server bisa
dipulihkan ke kondisi kemarin.

---

## 3. Mengarahkan domain ke server

Buat **satu record A** di panel DNS: registrar (Pilihan B), TIK kampus (Pilihan A), atau
**Domains & DNS** di Lightsail (Pilihan C).

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `iot` (atau `@` untuk domain utama) | `203.0.113.10` | 300 |

Periksa dari laptop. Hasilnya harus static IP Anda; perubahan DNS bisa butuh beberapa menit
sampai beberapa jam.

```bash
dig +short iot.contoh.my.id
```

---

## 4. Masuk lewat SSH dan mengamankan sistem operasi

Dari laptop:

```bash
chmod 400 ~/Downloads/iot-platform-key.pem          # wajib: SSH menolak key yang bisa dibaca orang lain
ssh -i ~/Downloads/iot-platform-key.pem ubuntu@203.0.113.10
```

Semua perintah di bawah dijalankan di server sebagai user `ubuntu` (punya `sudo`).

### 4.1 Pembaruan keamanan otomatis

```bash
sudo apt update && sudo apt full-upgrade -y
sudo timedatectl set-timezone Asia/Jakarta
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades     # pilih "Yes"
```

`unattended-upgrades` memasang pembaruan keamanan Ubuntu setiap hari. Sebagian pembaruan,
seperti kernel, baru berlaku setelah reboot. Cek sesekali dengan
`ls /var/run/reboot-required`, lalu `sudo reboot` di jam sepi.

### 4.2 Perketat SSH

Lightsail sudah mematikan login dengan password. Pengaturan berikut membuatnya eksplisit,
supaya tidak terbuka kembali tanpa sengaja:

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
X11Forwarding no
MaxAuthTries 3
EOF
sudo sshd -t && sudo systemctl reload ssh
```

**Jangan tutup** sesi SSH yang sedang terbuka. Buka terminal baru dan coba masuk sekali
lagi. Bila gagal, perbaiki dari sesi lama.

### 4.3 Firewall di dalam server (ufw)

Ini lapisan kedua di belakang firewall Lightsail. Bila suatu saat aturan Lightsail terbuka
tanpa sengaja, port internal tetap tertutup.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH          # IZINKAN SSH DULU, sebelum enable, agar tidak terkunci di luar
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 8883/tcp
sudo ufw enable
sudo ufw status verbose
```

### 4.4 fail2ban

`fail2ban` memblokir sementara alamat yang berulang kali gagal login SSH:

```bash
sudo apt install -y fail2ban
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

### 4.5 Swap (penting untuk paket 1–2 GB)

Saat memori habis, sistem tidak langsung mematikan MySQL; swap memberi cadangan:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 5. Memasang paket

```bash
sudo apt install -y python3-venv python3-dev build-essential pkg-config \
    default-libmysqlclient-dev mysql-server redis-server nginx \
    mosquitto mosquitto-clients certbot git
```

- `build-essential`, `pkg-config`, dan `default-libmysqlclient-dev` dibutuhkan untuk
  memasang `mysqlclient`.
- Redis bawaan Ubuntu hanya mendengarkan `127.0.0.1` dan `::1`. Pastikan dengan
  `sudo ss -tlpn | grep 6379`.

---

## 6. Database MySQL

```bash
sudo mysql_secure_installation
```

Jawab:

- *VALIDATE PASSWORD component*: **Y**, level **1 (MEDIUM)** atau lebih;
- hapus *anonymous users*: **Y**;
- *disallow root login remotely*: **Y**;
- hapus *test database*: **Y**;
- *reload privilege tables*: **Y**.

Buat database dan user khusus aplikasi. Buat password acak dulu, lalu simpan untuk `.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
sudo mysql
```

```sql
CREATE DATABASE iot_platform CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'iot_user'@'localhost' IDENTIFIED BY 'PASSWORD-ACAK-TADI';
GRANT ALL PRIVILEGES ON iot_platform.* TO 'iot_user'@'localhost';
EXIT;
```

- `'iot_user'@'localhost'` hanya bisa masuk dari server ini, lewat socket lokal.
- Hak user ini terbatas pada satu database, bukan seluruh server MySQL.
- MySQL di Ubuntu hanya mendengarkan `127.0.0.1`. Cek dengan `sudo ss -tlpn | grep 3306`.

---

## 7. Memasang aplikasi

### 7.1 User dan folder

```bash
sudo adduser --system --group --home /srv/iot --shell /bin/bash iot
sudo install -d -o iot -g iot -m 750 /srv/iot/backups
```

Aplikasi berjalan sebagai `iot`, user tanpa password dan tanpa `sudo`. Bila ada celah di
aplikasi, penyerang hanya mendapat hak user ini.

### 7.2 Mengambil kode

**Cara 1 — git** (disarankan, karena `update.sh` di bagian 14 memakainya). Bila repository
ada di GitHub dan bersifat privat, buat **deploy key** read-only khusus server ini:

```bash
sudo -u iot -H ssh-keygen -t ed25519 -N "" -f /srv/iot/.ssh/id_ed25519
sudo cat /srv/iot/.ssh/id_ed25519.pub
```

Tempel hasilnya di GitHub: repository → **Settings → Deploy keys → Add deploy key**,
**tanpa** centang *Allow write access*. Lalu:

```bash
sudo -u iot -H git clone git@github.com:NAMA/IoT_platform.git /srv/iot/app
```

**Cara 2 — salin dari Mac** (tanpa repository online):

```bash
# di Mac, dari folder project:
rsync -av --exclude .env --exclude .git --exclude private_media --exclude __pycache__ \
      --exclude "Claude outputs" -e "ssh -i ~/Downloads/iot-platform-key.pem" \
      ./ ubuntu@203.0.113.10:/tmp/iot-app/
# di server:
sudo mv /tmp/iot-app /srv/iot/app && sudo chown -R iot:iot /srv/iot/app
```

`.env` dari laptop **tidak** ikut dikirim: server punya rahasianya sendiri.

### 7.3 Virtualenv dan paket Python

```bash
sudo -u iot -H python3 -m venv /srv/iot/venv
sudo -u iot -H /srv/iot/venv/bin/pip install --upgrade pip
sudo -u iot -H /srv/iot/venv/bin/pip install -r /srv/iot/app/requirements.txt
```

### 7.4 File `.env`

```bash
sudo -u iot cp /srv/iot/app/deploy/env.production.example /srv/iot/app/.env
sudo chmod 600 /srv/iot/app/.env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # untuk DJANGO_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # untuk MQTT_PASSWORD
sudo -u iot nano /srv/iot/app/.env
```

Yang **wajib** diubah:

| Nama | Isi |
|---|---|
| `DJANGO_SECRET_KEY` | 50+ karakter acak. Aplikasi menolak jalan dengan kunci contoh |
| `DJANGO_ALLOWED_HOSTS` | `iot.contoh.my.id` (hanya domain Anda) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://iot.contoh.my.id` |
| `DB_PASSWORD` | password MySQL dari langkah 6 |
| `MQTT_PASSWORD` | acak, untuk akun `iot-bridge` di broker |
| `COAP_PUBLIC_HOST`, `OTA_BASE_URL`, `MQTT_PUBLIC_HOST` | domain Anda |

`DJANGO_DEBUG=False` dan `DJANGO_ALLOW_SIGNUP=False` sudah terisi di contoh:

- **`DEBUG=False`**: dengan `True`, halaman error menampilkan potongan kode dan pengaturan
  kepada siapa saja.
- **`ALLOW_SIGNUP=False`**: di internet, siapa pun bisa membuat akun bila pendaftaran
  dibiarkan terbuka. Akun dibuat Admin di menu **Users**. Sign up baru boleh dibuka
  (`True`) setelah pengiriman email berfungsi (langkah 10.1), karena akun baru harus
  diaktivasi lewat tautan di email.

`DJANGO_HTTPS` tetap `False` sampai sertifikat terpasang (langkah 8.3).

### 7.5 Database, admin, file statis

```bash
cd /srv/iot/app
sudo -u iot -H /srv/iot/venv/bin/python manage.py migrate
sudo -u iot -H /srv/iot/venv/bin/python manage.py createsuperuser
sudo -u iot -H /srv/iot/venv/bin/python manage.py collectstatic --noinput
sudo install -d -o iot -g iot -m 750 /srv/iot/app/private_media
sudo chmod 711 /srv/iot     # Nginx (www-data) boleh MELEWATI folder ini menuju staticfiles
```

- `/srv/iot` dibuat dengan mode `750`, sehingga Nginx (user `www-data`) tidak bisa
  masuk dan semua `/static/...` dijawab `403`: halaman tampil tanpa CSS dan live update
  macet di *Connecting...* karena JavaScript-nya juga tidak termuat. Mode `711` hanya
  memberi izin *lewat* (bukan melihat isi folder); `.env` (600), `private_media` (750)
  dan backup tetap tertutup.
- Cek: `curl -sI https://DOMAIN-ANDA/static/css/app.css | head -1` harus `HTTP/2 200`.

- Untuk `createsuperuser`, pakai username yang tidak mudah ditebak (bukan `admin`) dan
  password panjang.
- **Jangan** menjalankan `seed_demo` atau `seed_rules` di server publik. Perintah itu
  membuat akun contoh dengan password `demo12345` yang tertulis di dokumentasi.

---

## 8. Web: Daphne, Nginx, dan HTTPS

### 8.1 Layanan systemd

```bash
# Folder /srv/iot hanya bisa dibuka user iot (izin 750), jadi tanda * tidak bisa
# diuraikan oleh shell user ubuntu. Salin isi foldernya dengan "/." sebagai gantinya.
sudo cp -rv /srv/iot/app/deploy/systemd/. /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start iot-web
sudo systemctl status iot-web --no-pager
curl -sI -H "Host: iot.contoh.my.id" http://127.0.0.1:8001/accounts/login/ | head -1   # HTTP/1.1 200 OK
```

Unit `iot-web.service` menjalankan Daphne sebagai user `iot` di `127.0.0.1:8001`, sehingga
Daphne tidak bisa dijangkau dari luar. Pengamanan prosesnya (`ProtectSystem=strict`,
`NoNewPrivileges`, dan lainnya) dijelaskan di komentar berkas.

### 8.2 Nginx tahap awal (HTTP)

```bash
sudo mkdir -p /var/www/certbot
sudo cp /srv/iot/app/deploy/nginx/iot-platform-http.conf /etc/nginx/sites-available/iot-platform
sudo sed -i 's/iot.contoh.my.id/DOMAIN-ANDA/g' /etc/nginx/sites-available/iot-platform
sudo ln -s /etc/nginx/sites-available/iot-platform /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Buka `http://iot.contoh.my.id`. Halaman login harus tampil. Login belum disarankan karena
belum ada HTTPS.

### 8.3 Sertifikat HTTPS (Let's Encrypt)

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d iot.contoh.my.id \
     --email email-anda@contoh.com --agree-tos --no-eff-email
```

Lalu ganti ke konfigurasi akhir, yang memuat HTTPS, pengalihan, header keamanan, dan batas
laju:

```bash
sudo cp /srv/iot/app/deploy/nginx/iot-platform.conf /etc/nginx/sites-available/iot-platform
sudo sed -i 's/iot.contoh.my.id/DOMAIN-ANDA/g' /etc/nginx/sites-available/iot-platform
sudo nginx -t && sudo systemctl reload nginx
```

Aktifkan pengaturan HTTPS di Django:

```bash
sudo -u iot sed -i 's/^DJANGO_HTTPS=False/DJANGO_HTTPS=True/' /srv/iot/app/.env
sudo systemctl restart iot-web
sudo -u iot -H /srv/iot/venv/bin/python /srv/iot/app/manage.py check --deploy   # "no issues"
```

Arti `DJANGO_HTTPS=True`:

- cookie login dan CSRF hanya dikirim lewat HTTPS;
- browser diminta selalu memakai HTTPS (HSTS, mulai 1 jam). Setelah semuanya berjalan baik
  selama beberapa hari, naikkan `DJANGO_HSTS_SECONDS` menjadi `31536000` (1 tahun).

Sertifikat berlaku 90 hari dan diperpanjang otomatis oleh `certbot.timer`. Pasang hook
supaya Nginx dan Mosquitto memakai sertifikat baru setelah perpanjangan:

```bash
printf '#!/bin/sh\nsystemctl reload nginx\n' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/nginx.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/nginx.sh
sudo certbot renew --dry-run          # uji perpanjangan
```

Uji dari laptop:

```bash
curl -sI http://iot.contoh.my.id/ | head -2                  # 301 -> https://
curl -sI https://iot.contoh.my.id/accounts/login/ | grep -iE "HTTP/|strict-transport|x-frame"
```

Hasil:

- konfigurasi TLS bisa dinilai di [SSL Labs](https://www.ssllabs.com/ssltest/); targetnya
  A atau A+;
- permintaan yang memakai IP langsung (`http://203.0.113.10`) ditolak, karena pemindai
  otomatis biasanya datang lewat IP.

---

## 9. Broker MQTT sendiri (Mosquitto)

Broker publik `broker.emqx.io` bisa dibaca siapa saja, **termasuk access token di nama
topik**. Di server sendiri, Mosquitto diatur sebagai berikut:

- **tanpa akun = ditolak**;
- **setiap device punya akun sendiri**: username dan password sama dengan access token;
- **ACL**: device hanya boleh memakai topik yang memuat tokennya sendiri;
- **TLS di port 8883** untuk device dari internet;
- port 1883 hanya untuk `mqtt_bridge` di `127.0.0.1`.

Akun dibuat otomatis dari database oleh `python manage.py mqtt_accounts`, lewat timer setiap
2 menit. Device baru, device yang dinonaktifkan, atau token yang diganti otomatis tercermin
di broker. Alurnya:

```
iot-mqtt-accounts.timer (2 menit) ─► manage.py mqtt_accounts (user iot) ─► /srv/iot/mosquitto/{passwd,acl}
                                           hanya bila daftar akun berubah ─┘
iot-mqtt-reload.path melihat perubahan ─► iot-mqtt-reload.service (root)
      ─► salin ke /etc/mosquitto/iot (pemilik mosquitto, izin 600) ─► reload Mosquitto
```

```bash
# folder kerja akun: hanya user iot
sudo install -d -o iot -g iot -m 700 /srv/iot/mosquitto
# buat akun & ACL pertama kali, lalu pasang ke /etc/mosquitto/iot
sudo systemctl start iot-mqtt-accounts.service
sudo systemctl start iot-mqtt-reload.service
sudo ls -l /etc/mosquitto/iot            # passwd, acl: pemilik mosquitto, -rw-------

# sertifikat untuk port 8883 (hook ini juga berjalan otomatis setiap perpanjangan)
sudo cp /srv/iot/app/deploy/mosquitto/certbot-deploy-hook.sh /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo sed -i 's/iot.contoh.my.id/DOMAIN-ANDA/' /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh

# konfigurasi broker
sudo cp /srv/iot/app/deploy/mosquitto/iot.conf /etc/mosquitto/conf.d/iot.conf
sudo systemctl restart mosquitto
sudo ss -tlpn | grep mosquitto           # 127.0.0.1:1883 dan *:8883

# sinkron akun berkala + muat ulang broker bila akun berubah
sudo systemctl enable --now iot-mqtt-accounts.timer iot-mqtt-reload.path
```

Uji bahwa broker menolak tamu:

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t '#' -C 1 -W 3
# Connection error: Connection Refused: not authorised.
```

Isi ACL yang dibuat (`/etc/mosquitto/iot/acl`), dengan `%u` = username = token device:

```
user iot-bridge
topic readwrite iot/#

pattern write iot/%u/telemetry
pattern write iot/%u/firmware
pattern write iot/%u/commands/+/response
pattern read  iot/%u/desired
pattern read  iot/%u/commands/+
pattern read  iot/%u/errors
```

---

## 10. Menjalankan semua layanan

```bash
sudo systemctl enable --now iot.target          # web, MQTT bridge, rule engine, housekeeping
sudo systemctl enable --now iot-backup.timer
sudo systemctl enable --now iot-cleanup.timer   # hapus akun Sign up yang tidak diaktivasi > 7 hari
systemctl list-units 'iot-*' --no-pager
systemctl list-timers 'iot-*' --no-pager
```

`iot.target` mengelompokkan layanan, sehingga satu perintah cukup untuk semuanya
(`sudo systemctl restart iot.target`), dan semuanya ikut menyala saat server reboot.

Di produksi, `runall` **tidak** dipakai. Perintah itu untuk pengembangan; systemd
menggantikannya dengan restart otomatis, log terpusat, dan sandboxing.

Uji reboot sekali. Setelah server menyala lagi, semua layanan harus aktif sendiri:

```bash
sudo reboot
# tunggu ±1 menit, lalu SSH lagi:
systemctl is-active iot-web iot-mqtt iot-rules iot-housekeeping mosquitto nginx mysql redis-server
```

### 10.1 Email: aktivasi akun dan lupa password

Sejak tahap 11, aplikasi mengirim email untuk tiga hal: tautan aktivasi akun baru (Sign up),
konfirmasi saat pengguna mengganti email di profil, dan tautan **Forgot your password?**.
Selama `EMAIL_HOST` kosong, email hanya dicetak ke log `iot-web` dan tidak pernah sampai ke
pengguna. Isi bagian email di `/srv/iot/app/.env` dengan akun SMTP:

| Penyedia | `EMAIL_HOST` | `EMAIL_HOST_USER` | `EMAIL_HOST_PASSWORD` | Catatan |
|---|---|---|---|---|
| Gmail / Google Workspace | `smtp.gmail.com` | alamat Gmail | *App Password* 16 huruf (myaccount.google.com → Security → 2-Step Verification → App passwords) | ±500 email/hari; cocok untuk kelas |
| Brevo | `smtp-relay.brevo.com` | login SMTP dari dasbor Brevo | SMTP key | gratis 300 email/hari; verifikasi domain pengirim |
| Amazon SES | `email-smtp.<region>.amazonaws.com` | SMTP username SES | SMTP password SES | murah untuk jumlah besar; awalnya *sandbox* (hanya ke alamat terverifikasi) |

```ini
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=akun.anda@gmail.com
EMAIL_HOST_PASSWORD=abcdefghijklmnop
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=IoT Platform <akun.anda@gmail.com>
```

- Port **587** (STARTTLS). Lightsail memblokir port 25 keluar, jadi jangan memakai 25.
- Untuk Gmail, `DEFAULT_FROM_EMAIL` harus alamat Gmail itu sendiri; alamat lain akan
  ditulis ulang oleh Gmail. Untuk Brevo/SES, pakai alamat di domain yang sudah diverifikasi.
- `.env` tetap mode 600: password SMTP sama sensitifnya dengan password database.

Terapkan dan uji:

```bash
sudo systemctl restart iot-web iot-rules
cd /srv/iot/app
sudo -u iot -H /srv/iot/venv/bin/python manage.py sendtestemail alamat-anda@contoh.com
```

Bila email uji sampai (periksa juga folder spam), coba alur lengkapnya di browser:
**Forgot your password?** di halaman login, dan bila `DJANGO_ALLOW_SIGNUP=True`, Sign up
dengan alamat email lain. Kegagalan kirim tercatat di `journalctl -u iot-web`.

---

## 11. Menghubungkan device

Buat device di halaman web (login sebagai superuser), lalu salin access token-nya. Dalam
2 menit akun broker untuk device itu dibuat otomatis.

### 11.1 HTTP

```bash
curl -X POST https://iot.contoh.my.id/api/v1/<TOKEN>/telemetry \
     -H "Content-Type: application/json" -d '{"temperature": 25.1}'
```

Device lama yang memakai `http://` (mis. `firmware/esp32_telemetry`) masih diterima di
`/api/`, tetapi token dan datanya tidak terenkripsi. Lihat bagian 16.

### 11.2 MQTT (TLS)

| Pengaturan | Nilai |
|---|---|
| Host | `iot.contoh.my.id` |
| Port | `8883` |
| TLS | ya, CA = ISRG Root X1 (Let's Encrypt) |
| Username, password | access token device |
| Prefix topik | `iot` (sesuai `MQTT_TOPIC_PREFIX`) |

Nilai-nilai ini juga tampil di tab **MQTT** halaman device, lengkap dengan contoh perintah
`mosquitto_pub`/`mosquitto_sub` yang siap disalin. Sumbernya empat baris di `.env`:

```ini
MQTT_PUBLIC_HOST=iot.contoh.my.id   # alamat untuk DEVICE (MQTT_HOST tetap 127.0.0.1 untuk mqtt_bridge)
MQTT_PUBLIC_PORT=8883
MQTT_PUBLIC_TLS=True
MQTT_DEVICE_LOGIN=True              # device login dengan username = password = access token
```

Server yang sudah berjalan sebelum pengaturan ini ada: tambahkan keempat baris itu ke
`/srv/iot/app/.env` setelah `update.sh`, lalu `sudo systemctl restart iot-web`.

Device tiruan dari laptop:

```bash
python scripts/fake_mqtt_device.py <TOKEN> --host iot.contoh.my.id --port 8883 --tls \
       --username <TOKEN> --password <TOKEN> --prefix iot
```

Untuk ESP32 (`firmware/esp32_mqtt_ota/esp32_mqtt_ota.ino`):

1. Set `#define USE_TLS 1`, `MQTT_HOST = "iot.contoh.my.id"`, `MQTT_PORT = 8883`, dan
   `TOPIC_PREFIX = "iot"`.
2. Tempel sertifikat **ISRG Root X1** (PEM, dari letsencrypt.org/certificates) ke `ROOT_CA`.

Sketch itu sudah login dengan access token dan memakai CA yang sama untuk unduhan OTA lewat
`https://`.

Uji ACL dari laptop: device A tidak bisa membaca topik device B. Untuk perintah di bawah,
CA di macOS ada di `--cafile /etc/ssl/cert.pem`; di Linux pakai `--capath /etc/ssl/certs`.

```bash
mosquitto_sub -h iot.contoh.my.id -p 8883 --cafile /etc/ssl/cert.pem \
      -u <TOKEN_A> -P <TOKEN_A> -t 'iot/<TOKEN_B>/#' -v
# tidak ada pesan yang muncul, walaupun device B aktif mengirim data
```

### 11.3 CoAP (opsional)

CoAP di project ini berjalan di atas UDP **tanpa enkripsi** (DTLS belum dikerjakan), jadi
token terlihat di jaringan. Aktifkan hanya bila memang dibutuhkan, misalnya untuk praktikum
CoAP:

1. Buka UDP 5683 di firewall Lightsail (IPv4 dan IPv6), lalu `sudo ufw allow 5683/udp`.
2. Jalankan `sudo systemctl enable --now iot-coap`.

Setelah praktikum selesai, tutup lagi.

### 11.4 OTA

`OTA_BASE_URL=https://iot.contoh.my.id` membuat device mengunduh firmware lewat HTTPS.
File firmware disimpan di `/srv/iot/app/private_media` (tidak publik), dan hanya
`iot-web.service` yang boleh menulis ke sana.

---

## 12. Daftar periksa keamanan

Centang setelah selesai. Kolom "Cara cek" bisa dijalankan kapan saja untuk memastikan
pengaturan masih berlaku.

| # | Lapisan | Tindakan | Cara cek |
|---|---|---|---|
| 1 | Akun AWS | MFA di akun root, user IAM untuk pekerjaan harian, budget alarm | konsol IAM: *MFA: Assigned* |
| 2 | Jaringan | Firewall Lightsail: 22 hanya IP Anda; 80, 443, 8883; IPv6 sama | tab Networking |
| 3 | Jaringan | ufw aktif dengan aturan yang sama | `sudo ufw status` |
| 4 | Jaringan | Hanya Nginx, Mosquitto 8883, dan SSH yang mendengarkan alamat publik | `sudo ss -tulpn` (MySQL, Redis, 1883, 8001 harus `127.0.0.1`/`::1`) |
| 5 | SSH | Key saja, tanpa root, `MaxAuthTries 3`, fail2ban | `sudo sshd -T \| grep -E "passwordauth\|permitroot"` |
| 6 | OS | Pembaruan keamanan otomatis | `systemctl status unattended-upgrades` |
| 7 | Proses | Aplikasi berjalan sebagai `iot` dengan sandbox systemd | `systemd-analyze security iot-web` (skor makin kecil makin baik) |
| 8 | Django | `DEBUG=False`, `SECRET_KEY` acak, `ALLOWED_HOSTS` hanya domain | `manage.py check --deploy` → no issues |
| 9 | Django | HTTPS, cookie secure, HSTS | `curl -sI https://…` menampilkan `strict-transport-security` |
| 10 | Django | Pendaftaran mandiri ditutup; tidak ada akun demo; superuser berpassword kuat | `/accounts/register/` → 404 |
| 11 | Login | Batas laju 10 percobaan per menit per IP (Nginx, burst 5) untuk login, sign up, kirim ulang aktivasi, dan lupa password | percobaan ke-7 yang beruntun dibalas 503 |
| 12 | Rahasia | `.env` milik `iot`, izin `600`; tidak masuk git | `sudo ls -l /srv/iot/app/.env` → `-rw-------` |
| 13 | Database | User MySQL khusus, hanya `localhost`, hanya satu database | `sudo mysql -e "SELECT user,host FROM mysql.user"` |
| 14 | MQTT | Tanpa tamu, akun per device, ACL per token, TLS | `mosquitto_sub` tanpa akun → *not authorised* |
| 15 | OTA | File firmware di luar folder publik; link unduhan sekali pakai | `curl https://…/private_media/` → 404 |
| 16 | Backup | Snapshot Lightsail harian + dump database harian | `sudo ls /srv/iot/backups`, tab Snapshots |
| 17 | Pemantauan | Alarm metrik Lightsail (CPU, status check) ke email | tab Metrics → Add alarm |

---

## 13. Backup dan pemulihan

Ada dua lapis backup:

1. **Snapshot otomatis Lightsail** (langkah 2.5). Isinya seluruh disk, disimpan 7 hari, dan
   memulihkan server utuh. Pemulihan: tab Snapshots → snapshot → **Create new instance**,
   lalu pindahkan static IP ke instance baru.
2. **`iot-backup.timer`** (pukul 02:30). Isinya dump MySQL dan file firmware, disimpan 14
   hari di `/srv/iot/backups`. Berguna untuk mengembalikan data tanpa mengembalikan seluruh
   server, atau untuk memindahkan data ke server lain.

Backup yang hanya ada di server yang sama ikut hilang bila server rusak. Sesekali (mis.
mingguan) salin ke laptop:

User `ubuntu` tidak bisa membaca `/srv/iot` (izin 750), jadi salin dulu ke folder sementara
miliknya, di server:

```bash
sudo sh -c 'mkdir -p /tmp/iot-backup && cp /srv/iot/backups/db-*.sql.gz /tmp/iot-backup/ && chown -R ubuntu: /tmp/iot-backup'
```

Lalu dari laptop:

```bash
scp -i ~/Downloads/iot-platform-key.pem "ubuntu@203.0.113.10:/tmp/iot-backup/*" ~/Backups/iot/
```

Setelah selesai, hapus salinannya di server dengan `rm -r /tmp/iot-backup`.

Memulihkan database dari dump:

```bash
sudo systemctl stop iot.target
sudo sh -c 'gunzip < /srv/iot/backups/db-20261015-0230.sql.gz | mysql iot_platform'
sudo systemctl start iot.target
```

Uji pemulihan sekali, misalnya ke instance percobaan dari snapshot. Backup yang belum pernah
diuji belum bisa dianggap backup.

---

## 14. Memperbarui aplikasi

Setelah perubahan kode di-commit dan di-push dari Mac:

```bash
sudo /srv/iot/app/deploy/scripts/update.sh
```

Skrip itu menjalankan langkah berikut:

1. `git pull`,
2. memasang paket baru,
3. `check --deploy` (berhenti bila ada peringatan keamanan),
4. migrasi database,
5. `collectstatic` (di produksi juga membuat salinan ber-hash, mis. `app.3f9a1c0b7d2e.css`,
   sehingga browser langsung memakai CSS/JS terbaru; lihat `docs/tahap-15-cache-busting-file-statis.md`),
6. restart semua layanan.

Perubahan `deploy/systemd/` (mis. unit baru `iot-housekeeping.service` di tahap 16) juga tidak
ikut diterapkan: salin ulang seperti langkah 8.1, lalu `sudo systemctl daemon-reload` dan
`sudo systemctl restart iot.target`.

Perubahan `deploy/nginx/iot-platform.conf` **tidak** ikut diterapkan oleh skrip ini. Bila
file itu berubah, salin ulang seperti di langkah 8.3 (buat cadangan dulu).

Bila satu langkah gagal, skrip berhenti dan layanan lama tetap berjalan. Untuk perubahan
besar, buat snapshot manual dulu (tab Snapshots → **Create snapshot**).

Bila kode disalin dengan rsync (cara 2 di langkah 7.2), jalankan rsync lagi, lalu baris
`pip install` sampai `restart` dari skrip secara manual.

Pembaruan paket Python tidak otomatis. Cek sebulan sekali:

```bash
sudo -u iot -H /srv/iot/venv/bin/pip list --outdated
```

Perhatikan terutama Django: setiap rilis patch (5.2.x) berisi perbaikan keamanan. Naikkan
versinya di `requirements.txt`, uji di laptop, lalu jalankan `update.sh`.

---

## 15. Pemantauan dan pemecahan masalah

Perintah sehari-hari:

```bash
systemctl status iot-web iot-mqtt iot-rules --no-pager
journalctl -u iot-web -f                     # log web secara langsung
journalctl -u iot-mqtt --since "1 hour ago"
sudo tail -f /var/log/nginx/error.log
sudo journalctl -u mosquitto -f
sudo fail2ban-client status sshd             # IP yang sedang diblokir
```

| Gejala | Penyebab umum | Perbaikan |
|---|---|---|
| `400 Bad Request` | domain tidak ada di `DJANGO_ALLOWED_HOSTS` | perbaiki `.env`, lalu `restart iot-web` |
| `403 CSRF verification failed` saat login | `DJANGO_CSRF_TRUSTED_ORIGINS` kosong atau tanpa `https://` | isi `https://domain` |
| `502 Bad Gateway` | `iot-web` mati | `journalctl -u iot-web -n 50` |
| Setelah update, tampilan setengah lama (mis. JS baru tetapi CSS lama) | browser memakai CSS dari cache; konfigurasi Nginx atau settings belum versi tahap 15 | pastikan HTML memuat `/static/css/app.<hash>.css` (`curl -s https://DOMAIN-ANDA/accounts/login/ \| grep app`); bila belum, `update.sh` dan salin ulang konfigurasi Nginx (langkah 8.3) |
| Halaman tanpa CSS, live update *Connecting...* | `/static/` dijawab 403 (`/srv/iot` mode 750) atau `collectstatic` belum dijalankan | `sudo chmod 711 /srv/iot`; jalankan `collectstatic`; cek `sudo tail /var/log/nginx/error.log` |
| Live update tidak jalan | WebSocket diblokir proxy atau `REDIS_URL` salah | cek blok `/ws/` di Nginx dan `redis-cli ping` |
| Device MQTT ditolak *not authorised* | akun belum dibuat (tunggu 2 menit) atau device nonaktif | `sudo systemctl start iot-mqtt-accounts iot-mqtt-reload`; cek `journalctl -u mosquitto` |
| `iot-mqtt` terus restart | `MQTT_USERNAME`/`MQTT_PASSWORD` di `.env` tidak sama dengan akun broker | jalankan ulang `iot-mqtt-accounts` setelah mengubah `.env` |
| ESP32 gagal TLS | `ROOT_CA` salah atau jam ESP32 belum sinkron | tempel ulang ISRG Root X1; tunggu NTP |
| SSH ditolak tiba-tiba | IP rumah berubah atau diblokir fail2ban | browser SSH di konsol → perbarui firewall / `fail2ban-client unban <ip>` |
| Server lambat, MySQL mati | memori habis | cek `free -h`; pastikan swap aktif; naikkan paket |

Aktifkan juga **alarm metrik** Lightsail: tab Metrics → *CPU utilization* dan *Status check
failures* → kirim email. Status check gagal berarti server tidak menjawab sama sekali.

---

## 16. Risiko yang masih ada

Server ini sudah layak dipakai untuk praktikum dan proyek kecil. Hal berikut sengaja belum
ditangani dan perlu diketahui:

| Risiko | Mengapa masih ada | Langkah berikutnya |
|---|---|---|
| `http://…/api/` masih diterima | firmware contoh lama memakai HTTP biasa | ubah firmware ke `https://` / MQTT TLS, lalu hapus blok `location /api/` di server port 80 |
| CoAP tanpa enkripsi | DTLS belum diimplementasikan | biarkan port 5683 tertutup kecuali saat dipakai |
| Access token = password MQTT | satu rahasia per device | bocor satu token hanya membuka satu device; ganti tokennya (tombol *Generate new token*) |
| Satu server untuk semua | paling praktis dan murah | untuk ketersediaan tinggi: database terkelola, beberapa instance, load balancer |
| File OTA dilayani Django | cukup untuk ukuran kecil | `X-Accel-Redirect` ke Nginx bila device banyak |
| Firmware tidak ditandatangani | HTTPS + MD5 hanya melindungi perjalanan | tanda tangan firmware (Tahap 3 rencana OTA) |
| Halaman `/admin/` publik | tetap butuh login superuser | batasi IP dengan `location /admin/ { allow <IP>; deny all; … }` di Nginx |

---

## 17. Perkiraan biaya

Harga Lightsail per Oktober 2026; cek ulang di halaman harga resmi sebelum membuat server.

| Komponen | Biaya |
|---|---|
| Instance 2 GB, IPv4 (Singapore) | US$12 per bulan (gratis 3 bulan pertama untuk pengguna Lightsail baru, untuk paket tertentu) |
| Static IP | gratis selama tersambung ke instance yang berjalan |
| Snapshot otomatis | US$0,05 per GB per bulan (inkremental; biasanya US$1–3) |
| Transfer data | termasuk 3 TB per bulan (setengahnya di Jakarta). Telemetri IoT jauh di bawahnya |
| Domain | `.my.id` mulai sekitar Rp17 ribu per tahun; `.com` di Route 53 sekitar US$16 per tahun; subdomain kampus gratis |
| Let's Encrypt | gratis |
| **Total** | **sekitar US$13–15 per bulan**, ditambah domain |

Ingat: instance yang di-*stop* tetap ditagih penuh. Untuk menghentikan biaya, buat snapshot
lalu **hapus** instance.
