# Deploy ke VPS (dengan pengamanan server)

Panduan ini memasang seluruh platform di **satu VPS Ubuntu**, dari penyedia mana pun
(Amazon Lightsail, DigitalOcean, Vultr, Hetzner, IDCloudHost, Biznet Gio, server kampus, dan
lainnya):

- web, API HTTP, dan WebSocket,
- MQTT dengan broker sendiri,
- rule engine,
- OTA,
- MySQL dan Redis.

Setiap langkah disertai alasan keamanannya. Semua berkas konfigurasi yang dirujuk ada di
folder [`deploy/`](../deploy/README.md). Langkah yang bergantung pada penyedia (membuat
server, IP tetap, firewall di panel, snapshot) dijelaskan secara umum di bagian 2, dengan
contoh konkret di [Lampiran A (Amazon Lightsail)](#lampiran-a-contoh-amazon-lightsail) dan
[Lampiran B (penyedia lain)](#lampiran-b-catatan-untuk-penyedia-lain).

```
                         Internet
                            │
             ┌──────────────┼──────────────────────────┐
             │ firewall penyedia (bila ada) + ufw      │  hanya 22 (IP Anda), 80, 443, 8883
             └──────────────┼──────────────────────────┘
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
5. **Bisa dipulihkan.** Ada snapshot server dari penyedia dan backup database harian.

Perkiraan waktu: 2–3 jam untuk pertama kali.

Daftar isi:

1. [Mendapatkan domain](#1-mendapatkan-domain)
2. [Menyiapkan VPS](#2-menyiapkan-vps)
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
17. [Ukuran server dan biaya](#17-ukuran-server-dan-biaya)
- [Lampiran A: contoh Amazon Lightsail](#lampiran-a-contoh-amazon-lightsail)
- [Lampiran B: catatan untuk penyedia lain](#lampiran-b-catatan-untuk-penyedia-lain)

Di seluruh panduan, ganti nilai contoh berikut dengan milik Anda:

| Contoh | Arti |
|---|---|
| `iot.contoh.my.id` | nama domain server |
| `203.0.113.10` | IP publik (tetap) server |
| `198.51.100.7` | IP publik internet Anda (cek di whatismyip.com) |
| `~/.ssh/iot-server` | kunci SSH privat di laptop untuk masuk ke server (langkah 4) |
| `ubuntu` | user admin di server yang punya `sudo` (langkah 4.0) |

### Urutan pemasangan dan titik periksa

Banyak langkah bergantung pada langkah sebelumnya. Bila dikerjakan tidak berurutan, layanan
gagal start dengan pesan yang tidak jelas. Contohnya, Mosquitto berhenti dengan
`status=13` bila `iot.conf` dipasang sebelum file akun dan sertifikatnya ada. Ikuti urutan
ini, dan **jangan lanjut sebelum titik periksa (✔) sebuah langkah terpenuhi**:

```
 1–3  domain + server + DNS        ✔ dig +short DOMAIN  = IP server
 4    user admin, amankan OS, swap ✔ ufw: 22, 80, 443, 8883 terbuka
 5    paket (MySQL, Redis, Nginx, Mosquitto bawaan, certbot)
 6    database + user MySQL
 7    kode, venv, .env, migrate, kunci OIDC   ✔ manage.py check tanpa error
 8.1  salin unit systemd, start HANYA iot-web       ✔ curl 127.0.0.1:8001 → 200
 8.2  Nginx HTTP                    ✔ http://DOMAIN tampil
 8.3  sertifikat → Nginx HTTPS → DJANGO_HTTPS=True  ✔ /etc/letsencrypt/live/DOMAIN ada
 9.1  akun & ACL Mosquitto          ✔ /etc/mosquitto/iot/passwd dan acl ada
 9.2  sertifikat untuk Mosquitto    ✔ /etc/mosquitto/certs/*.pem ada
 9.3  izin AppArmor, lalu iot.conf (PALING AKHIR)
                                    ✔ mosquitto active, port 1883 & 9001 (lokal), 8883
 9.4  timer sinkron akun
 10   iot.target + backup + cleanup ✔ semua layanan active, juga setelah reboot
```

Layanan yang membutuhkan layanan lain:

| Layanan | Butuh |
|---|---|
| `iot-web` (Daphne) | MySQL, Redis, `.env`, `migrate`, `collectstatic` |
| `certbot` | DNS sudah mengarah ke server, port 80 terbuka, Nginx HTTP melayani `/var/www/certbot` |
| `iot-mqtt-accounts` | `.env` (`MQTT_USERNAME`, `MQTT_PASSWORD`), MySQL, folder `/srv/iot/mosquitto`, program `mosquitto_passwd` |
| Mosquitto + `iot.conf` | `/etc/mosquitto/iot/{passwd,acl}`, `/etc/mosquitto/certs/{fullchain,privkey}.pem`, dan izin AppArmor untuk kedua folder itu |
| `iot-mqtt` (bridge) | Mosquitto dengan `iot.conf` dan akun `iot-bridge` |
| `iot-rules`, `iot-housekeeping` | MySQL, Redis |

---

## 1. Mendapatkan domain

Domain dibutuhkan untuk sertifikat HTTPS gratis dari Let's Encrypt dan untuk alamat yang
mudah diingat device. Pilih **salah satu** cara berikut.

### Pilihan A — Subdomain kampus (gratis, paling meyakinkan)

Minta ke unit TIK universitas atau fakultas sebuah subdomain, mis. `iot.filkom.ub.ac.id`,
yang diarahkan ke IP server (langkah 2). Biasanya cukup mengirim permohonan berisi nama
subdomain, IP tujuan, dan kegunaannya. Kebijakan dan prosedurnya mengikuti kampus
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

### Pilihan C — Registrar internasional atau penyedia VPS

Domain `.com`, `.net`, dan sejenisnya bisa dibeli di registrar internasional, dan sebagian
penyedia VPS juga menjual domain atau menyediakan pengelola DNS gratis (contoh Lightsail di
Lampiran A). Hal yang perlu diperhatikan di mana pun:

- isi data kontak sesuai identitas resmi dan aktifkan **privacy protection** bila tersedia;
- **klik tautan verifikasi** di email registrant, karena domain yang tidak diverifikasi akan
  ditangguhkan;
- nama domain **tidak bisa diubah** setelah terdaftar, jadi periksa ejaannya sebelum membayar.

### Tanpa domain?

Aplikasi bisa dicoba lewat `http://<IP-server>`, tetapi:

- tanpa HTTPS, password login dan access token terkirim tanpa enkripsi;
- MQTT TLS juga butuh nama domain untuk sertifikatnya.

Let's Encrypt kini juga menerbitkan sertifikat untuk alamat IP, tetapi hanya berlaku 6 hari
(profil `shortlived`) dan perlu klien ACME yang mendukungnya. Panduan ini tidak membahasnya.
Untuk server yang dipakai orang lain, gunakan domain.

---

## 2. Menyiapkan VPS

Nama menu berbeda di setiap penyedia, tetapi yang perlu disiapkan selalu sama. Contoh
langkah per menu ada di Lampiran A dan B.

### 2.1 Spesifikasi server

| Pilihan | Nilai | Alasan |
|---|---|---|
| Sistem operasi | **Ubuntu 24.04 LTS**, image bersih (tanpa panel hosting seperti cPanel/Plesk) | Python 3.12 bawaan sama dengan lingkungan pengembangan; dukungan keamanan sampai 2029. Seluruh perintah panduan ini untuk Ubuntu |
| Virtualisasi | **KVM** (atau VM penuh lain), bukan OpenVZ/LXC | kontainer seperti OpenVZ/LXC sering tidak mengizinkan AppArmor, sandbox systemd, swap, atau ufw yang dipakai panduan ini |
| Memori | **2 GB** (minimal 1 GB untuk percobaan, dengan swap di langkah 4.5) | MySQL, Redis, Mosquitto, dan empat proses Python cukup lega di 2 GB |
| CPU | 1–2 vCPU | |
| Disk | 25 GB SSD atau lebih | OS, database, log, backup 14 hari, file firmware |
| Jaringan | **IPv4 publik**; IPv6 boleh sebagai tambahan | ESP32 dan kebanyakan jaringan masih butuh IPv4 |
| Lokasi (region) | dekat dengan device, mis. **Jakarta** atau **Singapura** | latensi kecil untuk MQTT dan dashboard |
| Login | **kunci SSH** (langkah 4), bukan password | |

Bila penyedia meminta kunci SSH saat membuat server, buat dulu di laptop lalu tempelkan isi
kunci **publik**-nya:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/iot-server -C "iot-server"
cat ~/.ssh/iot-server.pub          # yang ditempel di panel penyedia: HANYA file .pub
```

Kunci privat (`~/.ssh/iot-server`, tanpa `.pub`) tidak pernah dikirim ke mana pun. Bila
penyedia membuatkan kunci dan Anda mengunduh file `.pem`, simpan di `~/.ssh/iot-server` dan
pakai jalur itu di seluruh panduan.

### 2.2 Amankan akun penyedia dulu

Siapa pun yang masuk ke akun penyedia bisa menghapus atau mengambil alih server, jadi akun ini
dijaga lebih ketat daripada server itu sendiri:

1. Aktifkan **MFA / verifikasi dua langkah**.
2. Pakai email yang aman dan tidak dipakai bersama.
3. Pasang **batas atau peringatan biaya** bila tersedia, mis. peringatan email di US$20 per
   bulan.

### 2.3 IP publik yang tetap

Device dan DNS menunjuk ke satu alamat, jadi alamat server **tidak boleh berubah**.

- Di kebanyakan VPS, IP tetap selama server tidak dihapus. Periksa dokumentasi penyedia:
  sebagian mengubah IP bila server dimatikan lalu dinyalakan (contoh Lightsail tanpa static
  IP).
- Bila penyedia punya fitur IP yang bisa dipindah (*Static IP*, *Reserved IP*, *Floating
  IP*, *Primary IP*), pakai fitur itu. Saat server diganti (mis. dipulihkan dari snapshot),
  IP cukup dipindah ke server baru tanpa mengubah DNS.

Catat alamatnya (contoh di panduan ini: `203.0.113.10`).

### 2.4 Firewall di panel penyedia

Banyak penyedia punya firewall di luar server (*Firewall*, *Cloud Firewall*, *Security
group*, *Firewall group*). Bila ada, isi aturan **masuk** berikut dan tolak sisanya:

| Layanan | Protokol | Port | Sumber |
|---|---|---|---|
| SSH | TCP | 22 | **hanya IP Anda**: `198.51.100.7` (atau rentang IP kampus/kantor) |
| HTTP | TCP | 80 | semua (dibutuhkan Let's Encrypt dan pengalihan ke HTTPS) |
| HTTPS | TCP | 443 | semua |
| MQTT TLS | TCP | 8883 | semua (device) |

**Jangan** membuka 3306 (MySQL), 6379 (Redis), 1883 (MQTT tanpa TLS), atau 8001 (Daphne).
UDP 5683 (CoAP) baru dibuka bila CoAP memang dipakai (bagian 11.3).

- Bila IPv6 aktif, pastikan aturan IPv6 sama. Di sebagian panel, firewall IPv4 dan IPv6
  diatur terpisah, dan yang terlupa menjadi celah.
- Bila penyedia **tidak** punya firewall di panel, `ufw` di langkah 4.3 menjadi satu-satunya
  firewall. Panduan tetap aman, tetapi kerjakan 4.3 dengan teliti.
- IP rumah atau kantor bisa berubah. Ketahui cara masuk ke **konsol darurat** penyedia
  (*web console*, *VNC*, *browser SSH*, *recovery console*) supaya tetap bisa memperbarui
  aturan port 22 bila SSH tiba-tiba ditolak. Konsol ini biasanya meminta password user,
  jadi siapkan dulu (langkah 4.0).

### 2.5 Snapshot otomatis

Aktifkan **snapshot / backup otomatis** di panel penyedia bila tersedia, harian bila bisa,
di jam sepi (mis. 03:00). Snapshot menyimpan seluruh disk, sehingga server bisa dipulihkan ke
kondisi kemarin. Fitur ini biasanya berbayar kecil, dan tidak menggantikan backup database
di bagian 13.

Bila penyedia tidak punya snapshot, backup database harian (bagian 13) tetap jalan; salin
hasilnya secara rutin ke luar server.

---

## 3. Mengarahkan domain ke server

Buat **satu record A** di panel DNS: registrar (Pilihan B/C), TIK kampus (Pilihan A), atau
pengelola DNS penyedia VPS.

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `iot` (atau `@` untuk domain utama) | `203.0.113.10` | 300 |

Bila server punya IPv6 dan firewall IPv6 sudah diatur (2.4), boleh tambahkan record `AAAA`
dengan alamat IPv6-nya. Bila ragu, cukup record A.

Periksa dari laptop. Hasilnya harus IP server Anda; perubahan DNS bisa butuh beberapa menit
sampai beberapa jam.

```bash
dig +short iot.contoh.my.id
```

Bila memakai Cloudflare sebagai DNS, set record ini ke **DNS only** (awan abu-abu). Proxy
Cloudflare tidak meneruskan MQTT di port 8883.

---

## 4. Masuk lewat SSH dan mengamankan sistem operasi

Seluruh panduan memakai user admin bernama **`ubuntu`** yang punya `sudo`, dan login dengan
kunci SSH `~/.ssh/iot-server`. Image Ubuntu di sebagian penyedia sudah membuat user itu
(mis. Lightsail); di banyak penyedia lain Anda hanya mendapat `root`.

### 4.0 User admin (bila penyedia hanya memberi `root`)

Lewati bagian ini bila `ssh -i ~/.ssh/iot-server ubuntu@203.0.113.10` sudah bisa masuk.

Masuk sebagai root (dengan kunci SSH, atau password dari email/panel penyedia):

```bash
ssh -i ~/.ssh/iot-server root@203.0.113.10
# bila penyedia memberi password root: ssh root@203.0.113.10
```

Di server, sebagai root:

```bash
id ubuntu 2>/dev/null || adduser --gecos "" ubuntu     # buat user; isi password kuat (untuk sudo & konsol darurat)
usermod -aG sudo ubuntu
install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/.ssh
# salin kunci yang dipakai root; bila root masuk dengan password, isi file ini dengan isi ~/.ssh/iot-server.pub dari laptop
cp /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys
chown ubuntu:ubuntu /home/ubuntu/.ssh/authorized_keys && chmod 600 /home/ubuntu/.ssh/authorized_keys
```

Bila root masuk dengan password dan belum ada kunci, kirim kunci publik dari **laptop** lalu
ulangi baris `cp` di atas:

```bash
ssh-copy-id -i ~/.ssh/iot-server.pub root@203.0.113.10
```

Dari terminal **baru** di laptop, pastikan user `ubuntu` bisa masuk dan memakai `sudo`
sebelum lanjut. Login root akan dimatikan di 4.2:

```bash
ssh -i ~/.ssh/iot-server ubuntu@203.0.113.10
sudo whoami          # ✔ root
```

Bila Anda memakai nama user lain, ganti `ubuntu` di seluruh panduan dengan nama itu.

### Masuk sebagai `ubuntu`

Dari laptop:

```bash
chmod 600 ~/.ssh/iot-server          # wajib: SSH menolak kunci yang bisa dibaca orang lain
ssh -i ~/.ssh/iot-server ubuntu@203.0.113.10
```

Supaya lebih singkat, tambahkan ke `~/.ssh/config` di laptop:

```
Host iot-server
    HostName 203.0.113.10
    User ubuntu
    IdentityFile ~/.ssh/iot-server
```

lalu cukup `ssh iot-server`. Semua perintah di bawah dijalankan di server sebagai user
`ubuntu`.

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

Matikan login dengan password dan login root. Sebagian image sudah melakukannya, sebagian
tidak; pengaturan berikut membuatnya eksplisit di semua penyedia:

```bash
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
X11Forwarding no
MaxAuthTries 3
EOF
sudo sshd -t && sudo systemctl reload ssh
```

Berkas diberi awalan `00-` dengan sengaja. Untuk SSH, nilai **pertama** yang terbaca yang
berlaku, dan berkas di `/etc/ssh/sshd_config.d/` dibaca menurut abjad. Sebagian image
menaruh `PasswordAuthentication yes` di berkas lain (mis. `50-cloud-init.conf`) yang akan
mengalahkan berkas bernama `99-…`. Periksa hasil akhirnya:

```bash
sudo sshd -T | grep -E "^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication)"
# ✔ passwordauthentication no, permitrootlogin no, kbdinteractiveauthentication no
```

Server yang dulu dipasang dengan `99-hardening.conf` cukup mengganti namanya:
`sudo mv /etc/ssh/sshd_config.d/99-hardening.conf /etc/ssh/sshd_config.d/00-hardening.conf`.

**Jangan tutup** sesi SSH yang sedang terbuka. Buka terminal baru dan coba masuk sekali
lagi. Bila gagal, perbaiki dari sesi lama.

### 4.3 Firewall di dalam server (ufw)

Ini lapisan kedua di belakang firewall penyedia (2.4), atau satu-satunya firewall bila
penyedia tidak punya. Bila suatu saat aturan di panel terbuka tanpa sengaja, port internal
tetap tertutup.

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH          # IZINKAN SSH DULU, sebelum enable, agar tidak terkunci di luar
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 8883/tcp
sudo ufw enable
sudo ufw status verbose
```

Bila tidak ada firewall di panel penyedia, batasi SSH ke IP Anda di sini:
`sudo ufw allow from 198.51.100.7 to any port 22 proto tcp`, lalu `sudo ufw delete allow
OpenSSH`. Pastikan dulu konsol darurat penyedia bisa dipakai (2.4), karena IP rumah bisa
berubah.

### 4.4 fail2ban

`fail2ban` memblokir sementara alamat yang berulang kali gagal login SSH:

```bash
sudo apt install -y fail2ban
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

### 4.5 Swap (penting untuk server 1–2 GB)

Saat memori habis, sistem tidak langsung mematikan MySQL; swap memberi cadangan. Periksa
dulu, karena sebagian image sudah punya swap:

```bash
swapon --show                     # kosong = belum ada swap
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
      --exclude "Claude outputs" -e "ssh -i ~/.ssh/iot-server" \
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
| `MQTT_PUBLIC_WS_URL` | `wss://iot.contoh.my.id/mqtt` (broker untuk browser, tahap 21) |
| `OIDC_ISSUER` | `https://iot.contoh.my.id/o` (login untuk aplikasi lain, tahap 22) |

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
# kunci RSA penanda tangan id_token (login untuk aplikasi lain, tahap 22); sekali saja
sudo -u iot -H /srv/iot/venv/bin/python manage.py oidc_key
```

- Kunci OIDC tersimpan di `private_media/oidc/private.pem` (izin 600) dan ikut backup.
  Tanpa kunci ini, `check --deploy` gagal dengan `oauth.E001`.

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
curl -sI -H "Host: DOMAIN-ANDA" http://127.0.0.1:8001/accounts/login/ | head -1   # HTTP/1.1 200 OK
```

Pada tahap ini **hanya `iot-web`** yang dijalankan. `iot.target` (bridge MQTT, rule engine,
housekeeping) baru di-enable di langkah 10, setelah broker siap. Bila dijalankan lebih dulu,
`iot-mqtt` akan terus restart karena belum ada akun `iot-bridge` di broker.

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

Prasyarat, karena Let's Encrypt memeriksa domain dari internet lewat port 80:

```bash
dig +short DOMAIN-ANDA                       # harus IP server ini
curl -sI http://DOMAIN-ANDA/ | head -1       # Nginx menjawab (langkah 8.2)
```

Ganti `-d` dengan domain Anda dan `--email` dengan email aktif (Let's Encrypt mengirim
peringatan ke sini bila perpanjangan gagal). `-w /var/www/certbot` sudah sesuai konfigurasi
Nginx project. Uji dulu dengan `--dry-run`; batas kegagalan Let's Encrypt cukup ketat
(±5 per jam per domain):

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d iot.contoh.my.id \
     --email email-anda@contoh.com --agree-tos --no-eff-email --dry-run
# berhasil? jalankan lagi TANPA --dry-run
sudo certbot certonly --webroot -w /var/www/certbot -d iot.contoh.my.id \
     --email email-anda@contoh.com --agree-tos --no-eff-email
sudo ls /etc/letsencrypt/live/iot.contoh.my.id/   # ✔ fullchain.pem, privkey.pem
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
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://iot.contoh.my.id/api/v1/x/telemetry   # 301 (bukan 401)
curl -sI https://iot.contoh.my.id/accounts/login/ | grep -iE "HTTP/|strict-transport|x-frame"
```

Hasil:

- konfigurasi TLS bisa dinilai di [SSL Labs](https://www.ssllabs.com/ssltest/); targetnya
  A atau A+;
- permintaan yang memakai IP langsung (`http://203.0.113.10`) ditolak, karena pemindai
  otomatis biasanya datang lewat IP;
- `http://` hanya dipakai untuk validasi certbot. Semua alamat lain, termasuk API device
  `/api/`, dialihkan ke `https://`, sehingga access token dan password tidak pernah lewat
  jaringan tanpa enkripsi. Device harus memakai `https://` atau MQTT TLS (bagian 11).

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

Mosquitto dari langkah 5 masih berjalan dengan konfigurasi bawaan Ubuntu. Biarkan dulu.
Konfigurasi platform (`iot.conf`) menunjuk ke file akun dan sertifikat, jadi file-file itu
harus **sudah ada sebelum** `iot.conf` dipasang. Kerjakan 9.1 sampai 9.4 berurutan.

Prasyarat:

```bash
sudo grep -E '^MQTT_(USERNAME|PASSWORD)=' /srv/iot/app/.env   # terisi, bukan GANTI-...
ls /etc/systemd/system/iot-mqtt-*                              # disalin di langkah 8.1
sudo ls /etc/letsencrypt/live/                                 # sertifikat dari langkah 8.3
which mosquitto_passwd                                         # /usr/bin/mosquitto_passwd
```

### 9.1 Akun dan ACL

```bash
# folder kerja akun: hanya user iot. HARUS ada sebelum service dijalankan
# (ReadWritePaths di unit systemd menolak folder yang tidak ada).
sudo install -d -o iot -g iot -m 700 /srv/iot/mosquitto

# buat akun & ACL dari database
sudo systemctl start iot-mqtt-accounts.service
sudo ls -l /srv/iot/mosquitto            # ✔ passwd, acl, passwd.sha256

# salin ke /etc/mosquitto/iot (pemilik mosquitto, izin 600)
sudo systemctl start iot-mqtt-reload.service
sudo ls -l /etc/mosquitto/iot            # ✔ passwd, acl: mosquitto mosquitto -rw-------
```

Bila `passwd` tidak muncul, lihat alasannya dengan `sudo journalctl -u iot-mqtt-accounts -n 30
--no-pager`. Biasanya `MQTT_PASSWORD` belum diisi, MySQL belum siap, atau `mosquitto_passwd`
tidak ditemukan.

### 9.2 Sertifikat untuk Mosquitto (port 8883)

Mosquitto berjalan sebagai user `mosquitto`, yang tidak boleh membaca `/etc/letsencrypt`.
Hook berikut menyalin sertifikat ke `/etc/mosquitto/certs`. Hook ini juga berjalan otomatis
setiap kali certbot memperpanjang sertifikat.

```bash
sudo cp /srv/iot/app/deploy/mosquitto/certbot-deploy-hook.sh /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo sed -i 's/iot.contoh.my.id/DOMAIN-ANDA/' /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo grep DOMAIN= /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh   # ✔ domain Anda
sudo /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo ls -l /etc/mosquitto/certs          # ✔ fullchain.pem (644), privkey.pem (640), grup mosquitto
```

### 9.3 Izin AppArmor, lalu konfigurasi broker (paling akhir)

**Izin AppArmor.** Ubuntu versi baru (dan paket Mosquitto dari PPA) memasang profil AppArmor
`mosquitto`. Profil ini hanya mengizinkan Mosquitto membaca lokasi file tertentu, dan
`/etc/mosquitto/iot/` serta `/etc/mosquitto/certs/` tidak termasuk. Akibatnya Mosquitto
gagal (`status=13`, `Unable to open pwfile`) walaupun pemilik dan izin file sudah benar.
Ciri khasnya: `sudo -u mosquitto cat /etc/mosquitto/iot/passwd` berhasil, tetapi kernel
mencatat penolakan:

```bash
sudo dmesg | grep -i apparmor | grep -i mosquitto | tail -3
# apparmor="DENIED" operation="open" profile="mosquitto" name="/etc/mosquitto/iot/passwd"
```

Cek dulu apakah profil itu ada:

```bash
ls /etc/apparmor.d/ | grep -i mosquitto        # kosong = tidak ada profil, lewati bagian ini
grep -n "local" /etc/apparmor.d/mosquitto      # biasanya: include if exists <local/mosquitto>
```

Bila ada, tambahkan izin baca untuk kedua folder lewat **file lokal** profil. File lokal
tidak ditimpa saat paket diperbarui:

```bash
sudo tee /etc/apparmor.d/local/mosquitto >/dev/null <<'EOF'
# IoT platform: file akun/ACL (iot-mqtt-reload) dan sertifikat TLS (hook certbot)
/etc/mosquitto/iot/ r,
/etc/mosquitto/iot/** r,
/etc/mosquitto/certs/ r,
/etc/mosquitto/certs/** r,
EOF
```

Bila perintah `grep` tadi **tidak** menemukan baris include lokal, sisipkan sendiri tepat
sebelum kurung tutup `}` terakhir profil:

```bash
sudo sed -i '$ s/^}/  include if exists <local\/mosquitto>\n}/' /etc/apparmor.d/mosquitto
grep -n "local/mosquitto" /etc/apparmor.d/mosquitto     # ✔ satu baris
```

Muat ulang profil:

```bash
sudo apparmor_parser -r /etc/apparmor.d/mosquitto       # ✔ tanpa pesan error
```

Nama profil (`mosquitto`) diambil dari `ls` di atas. Bila namanya lain, mis.
`usr.sbin.mosquitto`, pakai nama itu untuk file lokal dan `apparmor_parser`.

**Konfigurasi broker.** Periksa sekali lagi bahwa keempat file yang dibutuhkan `iot.conf` sudah ada. Baris ini
menulis **SIAP** atau nama file yang masih kurang:

```bash
ok=1; for f in /etc/mosquitto/iot/passwd /etc/mosquitto/iot/acl \
               /etc/mosquitto/certs/fullchain.pem /etc/mosquitto/certs/privkey.pem; do
  sudo test -s "$f" || { echo "KURANG: $f"; ok=0; }; done; [ $ok = 1 ] && echo SIAP
```

Hanya bila **SIAP**:

```bash
sudo cp /srv/iot/app/deploy/mosquitto/iot.conf /etc/mosquitto/conf.d/iot.conf
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager   # ✔ active (running)
sudo ss -tlpn | grep mosquitto               # ✔ 127.0.0.1:1883, 127.0.0.1:9001 dan *:8883
```

Bila Mosquitto gagal start: pesan aslinya **tidak** ada di `journalctl`, karena konfigurasi
bawaan Ubuntu menulis log ke file. Lihat:

```bash
sudo tail -n 20 /var/log/mosquitto/mosquitto.log
```

Contohnya `Unable to open pwfile "/etc/mosquitto/iot/passwd"` berarti langkah 9.1 belum
berhasil, atau, bila file itu sudah ada, AppArmor menolaknya (cek `dmesg`, lihat izin
AppArmor di atas). Perbaiki penyebabnya, lalu `sudo systemctl reset-failed mosquitto && sudo
systemctl restart mosquitto` (setelah 5 kali gagal, systemd berhenti mencoba sampai di-reset).
Selama memperbaiki, broker bisa dijalankan dengan konfigurasi bawaan dulu:
`sudo mv /etc/mosquitto/conf.d/iot.conf /root/iot.conf.bak && sudo systemctl restart mosquitto`.

### 9.4 Sinkron akun otomatis

```bash
# akun dibuat ulang tiap 2 menit; broker dimuat ulang bila daftar akun berubah
sudo systemctl enable --now iot-mqtt-accounts.timer iot-mqtt-reload.path
systemctl list-timers iot-mqtt-accounts --no-pager   # ✔ terjadwal
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

Prasyarat: langkah 9.3 selesai (Mosquitto *active* dengan `iot.conf`). Setelah itu:

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

- Port **587** (STARTTLS). Banyak penyedia VPS memblokir port 25 keluar, jadi jangan memakai 25.
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

### 10.2 Telegram (opsional, tahap 17)

1. Di Telegram, buka **@BotFather** → `/newbot` → beri nama, mis. *IoT Reslab* dan username
   yang berakhiran `bot`, mis. `iot_reslab_bot`. BotFather memberi token
   `123456789:AA...`.
2. Tambahkan ke `/srv/iot/app/.env`:

   ```ini
   TELEGRAM_BOT_TOKEN=123456789:AA...
   TELEGRAM_BOT_USERNAME=iot_reslab_bot
   ```

3. `sudo systemctl restart iot.target` (web untuk halaman profil, rules untuk aksi).
4. Di web: **My profile → Telegram → Connect Telegram**, buka bot, tekan **START**, lalu
   **I pressed Start**, dan **Send test**.

Server hanya membutuhkan koneksi **keluar** HTTPS ke `api.telegram.org`. Tidak ada port
masuk atau webhook yang perlu dibuka. Token bot sama rahasianya dengan password: bila bocor,
cabut dan buat token baru lewat BotFather (`/revoke`). Penjelasan lengkap:
`docs/tahap-17-notifikasi-telegram.md`.

---

## 11. Menghubungkan device

Buat device di halaman web (login sebagai superuser), lalu salin access token-nya. Dalam
2 menit akun broker untuk device itu dibuat otomatis.

### 11.1 HTTP

```bash
curl -X POST https://iot.contoh.my.id/api/v1/<TOKEN>/telemetry \
     -H "Content-Type: application/json" -d '{"temperature": 25.1}'
```

Server hanya menerima `https://`. Permintaan ke `http://…/api/` dijawab `301` dan datanya
**tidak** tersimpan. Untuk ESP32 lewat HTTP (`firmware/esp32_telemetry/esp32_telemetry.ino`):
isi `SERVER = "https://iot.contoh.my.id"` dan tempel sertifikat **ISRG Root X1** ke
`ROOT_CA` (lihat 11.2). Sketch otomatis memakai TLS bila `SERVER` diawali `https://`.

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

Untuk ESP32 (`firmware/esp32_mqtt/esp32_mqtt.ino`, satu sketch untuk MQTT dan OTA):

1. Set `#define USE_TLS 1`, `MQTT_HOST = "iot.contoh.my.id"`, `MQTT_PORT = 8883`, dan
   `TOPIC_PREFIX = "iot"`.
2. Tempel sertifikat **ISRG Root X1** (PEM, dari letsencrypt.org/certificates) ke `ROOT_CA`.

Sketch itu sudah login dengan access token dan memakai CA yang sama untuk unduhan OTA lewat
`https://`. Dengan `USE_TLS 1`, sketch **menolak** unduhan OTA lewat `http://` (laporan
`FAILED: Refusing http:// download in TLS mode`); bila itu muncul, periksa `OTA_BASE_URL` di
`.env` server.

Untuk **browser** (aplikasi web, MQTTX Web, MQTT.js) alamatnya `wss://iot.contoh.my.id/mqtt`
(port 443, path `/mqtt`), dengan akun dan topik yang sama. Penjelasan:
`docs/tahap-21-mqtt-websocket.md`.

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

1. Buka UDP 5683 di firewall penyedia (IPv4 dan IPv6, bila ada; bagian 2.4), lalu
   `sudo ufw allow 5683/udp`.
2. Jalankan `sudo systemctl enable --now iot-coap`.

Setelah praktikum selesai, tutup lagi.

### 11.4 OTA

`OTA_BASE_URL=https://iot.contoh.my.id` membuat device mengunduh firmware lewat HTTPS.
File firmware disimpan di `/srv/iot/app/private_media` (tidak publik), dan hanya
`iot-web.service` yang boleh menulis ke sana.

### 11.5 Aplikasi lain yang login dengan akun platform (tahap 22)

Aplikasi seperti MQTT client web didaftarkan sekali oleh Admin:

```bash
cd /srv/iot/app
sudo -u iot -H /srv/iot/venv/bin/python manage.py oauth_client "MQTT Client" \
     --redirect-uri https://mqtt.contoh.my.id/oidc/callback/ --logout-uri https://mqtt.contoh.my.id/
```

`client_secret` hanya dicetak **sekali**. Salin langsung ke `.env` aplikasi itu, jangan ke
chat atau dokumen. Bila hilang atau bocor, jalankan perintah yang sama dengan
`--rotate-secret`.

Alamat yang dibutuhkan aplikasi klien ada di
`https://iot.contoh.my.id/o/.well-known/openid-configuration`. Pengguna melihat dan mencabut
izin aplikasi di **My profile → Connected apps**. Penjelasan:
`docs/tahap-22-login-untuk-aplikasi-lain.md`.

MQTT Client (`mqtt.contoh.my.id`) dipasang di VPS yang sama dengan panduan tersendiri:
`mqtt_client/docs/deploy.md` di repo `mqtt_client`.

---

## 12. Daftar periksa keamanan

Centang setelah selesai. Kolom "Cara cek" bisa dijalankan kapan saja untuk memastikan
pengaturan masih berlaku.

| # | Lapisan | Tindakan | Cara cek |
|---|---|---|---|
| 1 | Akun penyedia | MFA, peringatan biaya (bagian 2.2) | pengaturan keamanan akun penyedia |
| 2 | Jaringan | Firewall penyedia (bila ada): 22 hanya IP Anda; 80, 443, 8883; IPv6 sama | menu firewall di panel penyedia; dari luar: `nc -zv 203.0.113.10 3306` harus gagal |
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
| 16 | Backup | Snapshot penyedia (bila ada) + dump database harian + salinan di luar server | `sudo ls /srv/iot/backups`, menu snapshot di panel |
| 17 | Pemantauan | Alarm dari penyedia dan/atau pemantau uptime luar ke email | uji dengan mematikan `nginx` sebentar |

---

## 13. Backup dan pemulihan

Ada dua lapis backup:

1. **Snapshot penyedia** (langkah 2.5), bila ada. Isinya seluruh disk dan memulihkan server
   utuh. Pemulihan biasanya berupa membuat server baru dari snapshot, lalu memindahkan IP
   tetap (2.3) ke server baru; bila IP tidak bisa dipindah, perbarui record DNS (bagian 3)
   dan tunggu propagasinya. Contoh Lightsail: Lampiran A.5.
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
scp -i ~/.ssh/iot-server "ubuntu@203.0.113.10:/tmp/iot-backup/*" ~/Backups/iot/
```

Setelah selesai, hapus salinannya di server dengan `rm -r /tmp/iot-backup`.

Memulihkan database dari dump:

```bash
sudo systemctl stop iot.target
sudo sh -c 'gunzip < /srv/iot/backups/db-20261015-0230.sql.gz | mysql iot_platform'
sudo systemctl start iot.target
```

Uji pemulihan sekali, misalnya ke server percobaan dari snapshot. Backup yang belum pernah
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
besar, buat snapshot manual dulu di panel penyedia.

Bila kode disalin dengan rsync (cara 2 di langkah 7.2), jalankan rsync lagi, lalu baris
`pip install` sampai `restart` dari skrip secara manual.

### 14.1 Dari tahap 18 ke versi terbaru (tahap 19–20)

Tahap 19 (berbagi device) dan tahap 20 (state machine) hanya mengubah kode dan database.
Tidak ada layanan systemd baru atau isian `.env` baru. Konfigurasi Nginx terbaru (menutup
`http://` untuk API device) dipasang terpisah setelah device pindah ke TLS; lihat 14.2.

```bash
# 1. cadangan dulu: snapshot manual di panel penyedia, dan backup database
sudo systemctl start iot-backup.service
sudo ls -lt /srv/iot/backups | head -3          # ✔ file backup terbaru hari ini

# 2. di Mac: commit & push tahap 19–20; lalu di server:
sudo /srv/iot/app/deploy/scripts/update.sh
#    migrate menjalankan: sharing 0001–0002, activity 0002, rules 0006

# 3. periksa
sudo -u iot -H /srv/iot/venv/bin/python /srv/iot/app/manage.py showmigrations sharing rules activity | grep '\[ \]'   # ✔ tidak ada keluaran
systemctl is-active iot-web iot-mqtt iot-rules iot-housekeeping mosquitto
```

Setelah itu menu **Sharing groups** muncul di sidebar, dan **State machine** bisa dipilih di
*Rules → New rule*. `seed_demo` dan `seed_rules` tetap **jangan** dijalankan di server publik
(akun contoh berpassword `demo12345`).

Bila `update.sh` berhenti di `migrate`, layanan lama tetap berjalan. Kirim keluarannya untuk
diperiksa; jangan menjalankan `migrate --fake`.

Pembaruan paket Python tidak otomatis. Cek sebulan sekali:

```bash
sudo -u iot -H /srv/iot/venv/bin/pip list --outdated
```

Perhatikan terutama Django: setiap rilis patch (5.2.x) berisi perbaikan keamanan. Naikkan
versinya di `requirements.txt`, uji di laptop, lalu jalankan `update.sh`.

### 14.2 Menutup `http://` untuk API device

Versi terbaru `deploy/nginx/iot-platform.conf` tidak lagi meneruskan `http://…/api/` ke
aplikasi; semua permintaan port 80 (kecuali certbot) dialihkan ke `https://`. Server yang
dipasang dengan konfigurasi lama masih menerima API lewat `http://`. Urutan aman:

1. **Pindahkan device dulu.** Semua firmware memakai `https://` (HTTP) atau MQTT TLS port
   8883 (`firmware/esp32_mqtt` dengan `USE_TLS 1`). Cari device yang masih memakai
   `http://`. Log Nginx tidak mencatat http/https, jadi intip langsung lalu lintas port 80
   selama 10 menit (pasang dulu bila belum ada: `sudo apt install tcpdump`):

   ```bash
   sudo timeout 600 tcpdump -l -A -nn -i any 'tcp dst port 80' 2>/dev/null \
        | grep -o 'POST /api/v1/[^/ ]*' | sort | uniq -c
   ```

   Setiap baris berisi access token device yang masih mengirim lewat `http://` (cocokkan
   di halaman device). Keluaran kosong = aman untuk langkah 2. Perintah ini menampilkan
   token, jadi jangan disalin ke tempat umum.

2. **Ganti konfigurasi Nginx** (setelah `update.sh` menarik kode terbaru):

   ```bash
   sudo cp /etc/nginx/sites-available/iot-platform /root/iot-platform.nginx.bak
   sudo cp /srv/iot/app/deploy/nginx/iot-platform.conf /etc/nginx/sites-available/iot-platform
   sudo sed -i 's/iot.contoh.my.id/DOMAIN-ANDA/g' /etc/nginx/sites-available/iot-platform
   sudo nginx -t && sudo systemctl reload nginx
   ```

3. **Periksa:**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST http://DOMAIN-ANDA/api/v1/x/telemetry    # ✔ 301
   curl -s -o /dev/null -w '%{http_code}\n' -X POST https://DOMAIN-ANDA/api/v1/x/telemetry   # ✔ 401 (token salah)
   sudo certbot renew --dry-run                                                             # ✔ tetap berhasil
   ```

Bila ada device yang ternyata belum pindah, kembalikan sementara dengan
`sudo cp /root/iot-platform.nginx.bak /etc/nginx/sites-available/iot-platform` lalu
`sudo nginx -t && sudo systemctl reload nginx`.

### 14.3 Tahap 21–22: broker untuk browser dan login untuk aplikasi lain

Tahap 21 menambah listener WebSocket Mosquitto dan `location = /mqtt` di Nginx. Tahap 22
menambah paket `django-oauth-toolkit`, tabel baru, kunci RSA, dan isian `.env` baru. Urutan ini
penting: `update.sh` menjalankan `check --deploy`, yang **berhenti** bila kunci atau issuer
belum ada (`oauth.E001`, `oauth.E002`).

```bash
# 1. cadangan
sudo systemctl start iot-backup.service
sudo cp /etc/nginx/sites-available/iot-platform /root/iot-platform.nginx.bak
sudo cp /etc/mosquitto/conf.d/iot.conf /root/iot.conf.bak

# 2. ambil kode dan paket baru dulu (tanpa restart), supaya perintah oidc_key tersedia
cd /srv/iot/app
sudo -u iot -H git -C /srv/iot/app pull --ff-only
sudo -u iot -H /srv/iot/venv/bin/pip install --quiet -r requirements.txt

# 3. kunci RSA (sekali saja)
sudo -u iot -H /srv/iot/venv/bin/python manage.py oidc_key
sudo ls -l /srv/iot/app/private_media/oidc/      # ✔ private.pem, -rw------- iot

# 4. isian .env baru (ganti domain)
sudo -u iot tee -a /srv/iot/app/.env >/dev/null <<'EOF'
MQTT_PUBLIC_WS_URL=wss://DOMAIN-ANDA/mqtt
OIDC_ISSUER=https://DOMAIN-ANDA/o
EOF

# 5. pembaruan biasa: check --deploy, migrate (oauth2_provider 0001–0022, oauth 0001), restart
sudo /srv/iot/app/deploy/scripts/update.sh
```

Lalu broker dan Nginx:

```bash
# 6. Mosquitto: listener WebSocket di 127.0.0.1:9001. Device tersambung ulang sendiri.
sudo cp /srv/iot/app/deploy/mosquitto/iot.conf /etc/mosquitto/conf.d/iot.conf
sudo systemctl restart mosquitto
sudo ss -tlpn | grep mosquitto        # ✔ 127.0.0.1:9001 muncul

# 7. Nginx: /mqtt (WebSocket) dan /o/ (batas laju). Bila file di server pernah Anda ubah
#    sendiri, bandingkan dulu: diff /root/iot-platform.nginx.bak <file baru>
sudo cp /srv/iot/app/deploy/nginx/iot-platform.conf /etc/nginx/sites-available/iot-platform
sudo sed -i 's/iot.contoh.my.id/DOMAIN-ANDA/g' /etc/nginx/sites-available/iot-platform
sudo nginx -t && sudo systemctl reload nginx
```

Periksa:

```bash
# broker WebSocket: jabat tangan berhasil (-v menulis header ke layar; --max-time karena
# koneksi WebSocket yang berhasil memang tetap terbuka)
curl -sv --http1.1 -N --max-time 5 -o /dev/null https://DOMAIN-ANDA/mqtt \
     -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
     -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Protocol: mqtt" \
     2>&1 | grep -E "^< HTTP|Could not|Failed"                     # ✔ < HTTP/1.1 101 Switching Protocols
# OpenID Connect
curl -s https://DOMAIN-ANDA/o/.well-known/openid-configuration | head -c 200; echo   # ✔ "issuer":"https://DOMAIN-ANDA/o"
curl -s -o /dev/null -w '%{http_code}\n' https://DOMAIN-ANDA/api/v2/me             # ✔ 401 (tanpa token)
systemctl is-active iot-web iot-mqtt iot-rules iot-housekeeping mosquitto
```

Pemeriksaan WebSocket tidak menampilkan apa pun? Pastikan `DOMAIN-ANDA` sudah diganti; `-s`
menyembunyikan pesan *Could not resolve host*. Bila hasilnya bukan 101, periksa bagian demi
bagian dari dalam server:

```bash
# a. listener WebSocket Mosquitto aktif?
sudo ss -tlpn | grep 9001                       # ✔ 127.0.0.1:9001 ... mosquitto
# b. langsung ke Mosquitto, tanpa Nginx
curl -sv --http1.1 -N --max-time 3 -o /dev/null http://127.0.0.1:9001/mqtt \
     -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
     -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Protocol: mqtt" \
     2>&1 | grep -E "^< HTTP|Could not|Failed"  # ✔ < HTTP/1.1 101 Switching Protocols
# c. Nginx sudah memakai konfigurasi baru?
sudo grep -n "location = /mqtt" /etc/nginx/sites-available/iot-platform
```

| Hasil | Penyebab | Perbaikan |
|---|---|---|
| a kosong, atau b `Failed to connect` | listener 9001 belum aktif | salin ulang `iot.conf` (langkah 6), `sudo systemctl restart mosquitto`, lihat `sudo tail -n 20 /var/log/mosquitto/mosquitto.log` |
| b 101, lewat domain tidak | Nginx belum memakai blok `/mqtt` | c harus menemukan baris itu; salin ulang konfigurasi (langkah 7), `sudo nginx -t && sudo systemctl reload nginx` |
| lewat domain `404` | blok `/mqtt` ada di file lain atau di server block lain | pastikan file yang aktif: `ls -l /etc/nginx/sites-enabled/` |
| lewat domain `502` | Nginx tidak bisa menghubungi 127.0.0.1:9001 | kembali ke a dan b |

Di halaman device, tab **MQTT** kini memuat baris **WebSocket**, dan di **My profile** ada kartu
**Connected apps**. Aplikasi klien didaftarkan nanti, saat aplikasinya siap (langkah 11.5).

Bila Mosquitto gagal start dan `/var/log/mosquitto/mosquitto.log` menyebut *websockets* tidak
didukung, paket Mosquitto di server dibuat tanpa dukungan WebSocket. Kembalikan `/root/iot.conf.bak`, lalu pasang paket Mosquitto
dari PPA resmi (`ppa:mosquitto-dev/mosquitto-ppa`), yang selalu menyertakannya.

### 14.4 Tahap 23: akun MQTT sandbox

Tahap 23 hanya menambah kode, satu tabel (`mqtt_bridge 0001`), dan scope OAuth `mqtt:sandbox`.
Tidak ada paket, layanan systemd, atau konfigurasi Nginx/Mosquitto baru: akun sandbox ditulis
`iot-mqtt-accounts.timer` yang sudah berjalan sejak tahap 18.

```bash
sudo systemctl start iot-backup.service                 # cadangan dulu
sudo /srv/iot/app/deploy/scripts/update.sh              # pull, migrate (mqtt_bridge 0001), restart
```

Sandbox aktif otomatis karena `MQTT_DEVICE_LOGIN=True`. Untuk mematikannya, tambahkan
`MQTT_SANDBOX_ENABLED=False` di `.env` lalu `sudo systemctl restart iot.target`.

Periksa:

```bash
# 1. My profile -> MQTT sandbox -> Create sandbox account. Tunggu sampai timer berjalan (<= 2 menit):
sudo systemctl start iot-mqtt-accounts.service          # atau jalankan sekarang juga
journalctl -u iot-mqtt-accounts -n 3 --no-pager         # ✔ "... 1 sandbox account(s) ..."
# 2. dari laptop (ganti nilai dari kartu MQTT sandbox):
mosquitto_sub -h iot.contoh.my.id -p 8883 --capath /etc/ssl/certs -u sbx-5 -P 'PASSWORD' -t 'sandbox/#' -v -C 1 &
mosquitto_pub -h iot.contoh.my.id -p 8883 --capath /etc/ssl/certs -u sbx-5 -P 'PASSWORD' -t sandbox/USERNAME/hello -m hi
# ✔ sandbox/USERNAME/hello hi
```

Di macOS, `--capath /etc/ssl/certs` diganti `--cafile /etc/ssl/cert.pem`.

Aplikasi yang sudah login sebelum tahap ini (MQTT Client) belum memegang scope
`mqtt:sandbox`; pengguna cukup logout lalu login lagi dan menyetujui izin baru.

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
| `mosquitto` gagal start (`status=13`), journal hanya menulis *Loading config file* | file akun atau sertifikat yang disebut `iot.conf` belum ada / izinnya salah | baca `sudo tail /var/log/mosquitto/mosquitto.log`; ulangi 9.1 atau 9.2; lalu `reset-failed` + `restart mosquitto` (langkah 9.3) |
| `mosquitto` tetap `Unable to open pwfile` padahal file ada dan `sudo -u mosquitto cat` berhasil | profil AppArmor `mosquitto` menolak `/etc/mosquitto/iot/` | `sudo dmesg \| grep DENIED`; tambahkan izin di `/etc/apparmor.d/local/mosquitto`, lalu `apparmor_parser -r` (langkah 9.3) |
| `iot-mqtt-accounts` gagal, `Failed to set up mount namespacing` | folder `/srv/iot/mosquitto` belum dibuat | `sudo install -d -o iot -g iot -m 700 /srv/iot/mosquitto`, ulangi 9.1 |
| Device berhenti mengirim setelah konfigurasi Nginx baru, Serial Monitor `POST … -> 301` | firmware masih memakai `http://` | ganti ke `https://` + `ROOT_CA` (bagian 11.1) atau MQTT TLS; sementara bisa kembali ke cadangan (14.2) |
| ESP32 `POST … -> -1` / MQTT `state -2` setelah pindah ke TLS | `ROOT_CA` salah/kosong, atau jam belum sinkron | tempel ISRG Root X1 lengkap dengan baris BEGIN/END; pastikan Wi-Fi terhubung ke internet |
| `update.sh` berhenti di `check --deploy` dengan `oauth.E001` / `oauth.E002` | kunci OIDC atau `OIDC_ISSUER` belum ada | langkah 14.3 nomor 3 dan 4, lalu jalankan `update.sh` lagi |
| Aplikasi lain: `invalid_request ... redirect_uri` saat login | alamat callback tidak sama persis dengan yang didaftarkan | `manage.py oauth_client "<nama>" --redirect-uri <alamat persis>` |
| Browser gagal ke `wss://…/mqtt` (MQTT.js: *connection closed*) | Nginx belum punya `location = /mqtt` atau listener 9001 belum ada | `curl` jabat tangan di 14.3 harus `101`, ke Nginx dan langsung ke `http://127.0.0.1:9001/mqtt`; `ss -tlpn \| grep 9001` |
| `certbot` gagal *Timeout* / *Invalid response* | DNS belum mengarah ke server, port 80 tertutup, atau Nginx HTTP belum aktif | cek `dig +short DOMAIN`, firewall, `curl -sI http://DOMAIN` (langkah 8.3) |
| Device MQTT ditolak *not authorised* | akun belum dibuat (tunggu 2 menit) atau device nonaktif | `sudo systemctl start iot-mqtt-accounts iot-mqtt-reload`; cek `journalctl -u mosquitto` |
| `iot-mqtt` terus restart | `MQTT_USERNAME`/`MQTT_PASSWORD` di `.env` tidak sama dengan akun broker | jalankan ulang `iot-mqtt-accounts` setelah mengubah `.env` |
| ESP32 gagal TLS | `ROOT_CA` salah atau jam ESP32 belum sinkron | tempel ulang ISRG Root X1; tunggu NTP |
| SSH ditolak tiba-tiba | IP rumah berubah atau diblokir fail2ban | konsol darurat penyedia (2.4) → perbarui firewall / `fail2ban-client unban <ip>` |
| Server lambat, MySQL mati | memori habis | cek `free -h`; pastikan swap aktif; naikkan paket |

Aktifkan juga pemantauan dari luar server, karena server yang mati tidak bisa melaporkan
dirinya sendiri:

- **alarm penyedia** bila tersedia (CPU, status server) ke email; contoh Lightsail di
  Lampiran A.8;
- **pemantau uptime** gratis (mis. UptimeRobot, Better Stack) yang memeriksa
  `https://iot.contoh.my.id/accounts/login/` setiap beberapa menit dan mengirim email bila
  gagal.

---

## 16. Risiko yang masih ada

Server ini sudah layak dipakai untuk praktikum dan proyek kecil. Hal berikut sengaja belum
ditangani dan perlu diketahui:

| Risiko | Mengapa masih ada | Langkah berikutnya |
|---|---|---|
| CoAP tanpa enkripsi | DTLS belum diimplementasikan | biarkan port 5683 tertutup kecuali saat dipakai |
| Access token = password MQTT | satu rahasia per device | bocor satu token hanya membuka satu device; ganti tokennya (tombol *Generate new token*) |
| Satu server untuk semua | paling praktis dan murah | untuk ketersediaan tinggi: database terkelola, beberapa instance, load balancer |
| File OTA dilayani Django | cukup untuk ukuran kecil | `X-Accel-Redirect` ke Nginx bila device banyak |
| Firmware tidak ditandatangani | HTTPS + MD5 hanya melindungi perjalanan | tanda tangan firmware (Tahap 3 rencana OTA) |
| Halaman `/admin/` publik | tetap butuh login superuser | batasi IP dengan `location /admin/ { allow <IP>; deny all; … }` di Nginx |

---

## 17. Ukuran server dan biaya

Harga berubah-ubah dan berbeda antarpenyedia; cek halaman harga resmi sebelum membuat
server. Komponen yang perlu dihitung:

| Komponen | Catatan |
|---|---|
| VPS 2 GB RAM, IPv4 | biaya terbesar. Pilih paket dengan kuota transfer cukup; telemetri IoT jauh di bawah kuota paket terkecil sekalipun |
| IPv4 publik / IP tetap | di sebagian penyedia termasuk paket, di sebagian lain ditagih terpisah |
| Snapshot / backup otomatis | biasanya persentase dari harga server atau per GB per bulan |
| Domain | `.my.id` mulai sekitar Rp17 ribu per tahun; `.com` belasan dolar per tahun; subdomain kampus gratis |
| Let's Encrypt | gratis |

Contoh angka konkret untuk Lightsail ada di Lampiran A.

Hal yang sering terlupa: di banyak penyedia, server yang di-*stop* (dimatikan) **tetap
ditagih**. Untuk menghentikan biaya, buat snapshot lalu **hapus** server.

---

## Lampiran A: contoh Amazon Lightsail

Platform ini pertama kali dipasang di Lightsail. Bagian ini mencatat langkah per menu di
konsol Lightsail untuk bagian 1–3, 10.1, 13, dan 15 di atas.

### A.1 Akun AWS (bagian 2.2)

1. Aktifkan **MFA** untuk akun root AWS (*Security credentials → Assign MFA device*).
2. Untuk pekerjaan sehari-hari, buat user IAM dengan izin Lightsail, bukan memakai root.
3. Pasang batas biaya: **Billing → Budgets → Create budget**, misalnya US$20 per bulan dengan
   peringatan email.

### A.2 Membuat instance (bagian 2.1)

Di [konsol Lightsail](https://lightsail.aws.amazon.com/) pilih **Create instance**:

| Pilihan | Nilai | Catatan |
|---|---|---|
| Region | **Singapore** atau **Jakarta** | kuota transfer region Jakarta hanya setengah dari region lain, tetapi lalu lintas IoT kecil |
| Platform | Linux/Unix | |
| Blueprint | **OS Only → Ubuntu 24.04 LTS** | user `ubuntu` sudah ada, jadi langkah 4.0 dilewati |
| SSH key | unggah `~/.ssh/iot-server.pub`, atau buat key pair baru lalu **unduh** `.pem`-nya dan simpan sebagai `~/.ssh/iot-server` | |
| Network type | Dual-stack (IPv4 + IPv6) | |
| Plan | **2 GB RAM, 2 vCPU, 60 GB SSD** | |
| Nama | `iot-platform` | |

### A.3 Static IP (bagian 2.3)

Tanpa static IP, alamat instance Lightsail berubah setiap kali instance di-stop lalu
dinyalakan lagi.

1. Buka **Networking → Create static IP**, lalu sambungkan ke `iot-platform`.
2. Catat alamatnya. Static IP gratis selama tersambung ke instance yang berjalan.

### A.4 Firewall (bagian 2.4)

Buka instance → tab **Networking → IPv4 Firewall**. Hapus aturan bawaan yang tidak perlu,
lalu isi sesuai tabel 2.4. Untuk SSH, batasi ke IP Anda dan centang **Allow Lightsail
browser SSH**, supaya **Connect using SSH** di konsol tetap bisa dipakai sebagai konsol
darurat. Isi juga **IPv6 Firewall** dengan aturan yang sama.

### A.5 Snapshot otomatis (bagian 2.5 dan 13)

Buka instance → tab **Snapshots → Automatic snapshots → Enable**, pilih jam sepi (mis.
03:00). Lightsail menyimpan 7 snapshot harian terakhir.

- Pemulihan: tab Snapshots → snapshot → **Create new instance**, lalu pindahkan static IP ke
  instance baru.
- Snapshot manual sebelum pembaruan besar: tab Snapshots → **Create snapshot**.

### A.6 Domain dan DNS (bagian 1 dan 3)

Domain bisa didaftarkan langsung di **Domains & DNS → Register domain** (tarif Route 53;
sejak Juli 2026 `.com` sekitar US$16 per tahun). Domain `.id` tidak bisa didaftarkan dari
Lightsail. Lightsail otomatis membuat zona DNS gratis; record A dibuat di menu yang sama.

### A.7 Email (bagian 10.1)

Lightsail memblokir port 25 keluar. Pakai port 587 seperti di 10.1.

### A.8 Pemantauan (bagian 15)

Tab **Metrics** → *CPU utilization* dan *Status check failures* → **Add alarm** → kirim
email. Status check gagal berarti server tidak menjawab sama sekali.

### A.9 Biaya (per Oktober 2026)

| Komponen | Biaya |
|---|---|
| Instance 2 GB, IPv4 (Singapore) | US$12 per bulan (gratis 3 bulan pertama untuk pengguna Lightsail baru, untuk paket tertentu) |
| Static IP | gratis selama tersambung ke instance yang berjalan |
| Snapshot otomatis | US$0,05 per GB per bulan (inkremental; biasanya US$1–3) |
| Transfer data | termasuk 3 TB per bulan (setengahnya di Jakarta) |
| **Total** | **sekitar US$13–15 per bulan**, ditambah domain |

Instance Lightsail yang di-*stop* tetap ditagih penuh.

---

## Lampiran B: catatan untuk penyedia lain

Langkah bagian 2 dipetakan ke menu masing-masing penyedia. Nama menu bisa berubah; bila
berbeda, cari padanannya di dokumentasi penyedia.

| Kebutuhan (bagian) | Yang dicari di panel penyedia |
|---|---|
| Server Ubuntu 24.04, KVM (2.1) | *Create server / Droplet / Instance / VM*, image **Ubuntu 24.04 LTS** tanpa aplikasi tambahan |
| Kunci SSH (2.1, 4) | *SSH keys*: tempel `~/.ssh/iot-server.pub` sebelum server dibuat |
| IP tetap (2.3) | *Reserved IP*, *Floating IP*, *Primary IP*, *Static IP*, atau keterangan bahwa IP tetap selama server tidak dihapus |
| Firewall (2.4) | *Firewall*, *Cloud Firewall*, *Firewall group*, *Security group* |
| Konsol darurat (2.4) | *Console*, *VNC*, *Web console*, *Recovery* |
| Snapshot (2.5) | *Snapshots*, *Backups*, *Automatic backups* |

Hal yang sering berbeda dari Lightsail:

- **Login awal sebagai `root`**, kadang dengan password yang dikirim lewat email atau
  ditampilkan di panel. Kerjakan langkah 4.0, lalu 4.2 mematikan login root dan password.
  Ganti password root yang dikirim lewat email itu (`sudo passwd root`) atau kunci
  (`sudo passwd -l root`), karena email bukan tempat aman untuk password.
- **Tanpa firewall di panel.** `ufw` (4.3) menjadi satu-satunya firewall; batasi SSH ke IP
  Anda di sana.
- **Port 25 keluar** diblokir di banyak penyedia (kadang hanya untuk akun baru). Panduan ini
  memakai 587 (10.1), jadi tidak terpengaruh.
- **VPS lokal Indonesia** (mis. IDCloudHost, Biznet Gio, Dewaweb, Jagoan Hosting): pastikan
  paketnya **VPS/cloud server KVM** dengan akses root, bukan *shared hosting* atau paket
  OpenVZ. Region Jakarta memberi latensi kecil ke device di Indonesia.
- **Server kampus / VM di jaringan sendiri**: minta ke pengelola jaringan IP publik tetap,
  dan port 22 (dibatasi), 80, 443, 8883 yang diteruskan ke VM. Bila VM berada di belakang
  NAT, sertifikat Let's Encrypt tetap bisa diambil asalkan port 80 dari internet diteruskan
  ke VM itu.
- **Snapshot berbayar atau mingguan saja.** Backup database harian (bagian 13) tetap berjalan;
  salin hasilnya ke luar server secara rutin.
