# IoT Platform (Django)

Platform IoT yang dibangun bertahap dengan Django, dengan ThingsBoard sebagai acuan.
Project ini sekaligus sarana belajar Django: kodenya diberi komentar, dan setiap
tahap punya catatan penjelasan di folder `docs/`.

**Tahap 1 (saat ini): dashboard dasar** — manajemen pengguna (Admin & User),
daftar device per pemilik, card nilai terkini, dan tabel history.
Setiap device (alat fisik) bisa punya beberapa variabel, masing-masing dengan satuannya;
ada tampilan per device dan per variabel.
Komunikasi dengan device belum ada; datanya diisi dengan perintah `seed_demo`.

Penjelasan konsep:

- [docs/tahap-01-dashboard-dasar.md](docs/tahap-01-dashboard-dasar.md) — struktur project, pengguna, device, telemetri
- [docs/tahap-01b-device-kontrol.md](docs/tahap-01b-device-kontrol.md) — aktuator: desired/reported state dan command
- [docs/tahap-02-http-api.md](docs/tahap-02-http-api.md) — device sungguhan mengirim data lewat HTTP (access token, curl, ESP32)
- [docs/tahap-02b-auto-refresh-dan-periode-chart.md](docs/tahap-02b-auto-refresh-dan-periode-chart.md) — auto-refresh (polling) dan pilihan periode chart dengan agregasi
- [docs/tahap-03-mqtt.md](docs/tahap-03-mqtt.md) — komunikasi dua arah lewat MQTT (broker, topik, mqtt_bridge, ESP32)
- [docs/tahap-04-websocket.md](docs/tahap-04-websocket.md) — dashboard real-time lewat WebSocket (Django Channels, Daphne, Redis)
- [docs/tahap-04b-channels-daphne-redis.md](docs/tahap-04b-channels-daphne-redis.md) — peran Daphne, Channels, channels-redis, dan Redis
- [docs/tahap-05-dashboard.md](docs/tahap-05-dashboard.md) — dashboard per lokasi & device, pengaturan tampil/sembunyi, side menu
- [docs/tahap-06-coap.md](docs/tahap-06-coap.md) — komunikasi dua arah lewat CoAP (UDP, Observe, coap_server, ESP32)
- [docs/rule-engine-iot.md](docs/rule-engine-iot.md) — rule engine pada platform IoT: mekanisme dan enam model
- [docs/tahap-07-rule-engine-eca.md](docs/tahap-07-rule-engine-eca.md) — rule engine ECA: aturan otomatis, alarm, worker rule_engine
- [docs/contoh-data-dan-aturan.md](docs/contoh-data-dan-aturan.md) — pengguna, device, dan aturan ECA multi-variabel contoh, beserta skenario mencobanya
- [docs/eca-mekanisme.md](docs/eca-mekanisme.md) — ECA lebih dalam: kondisi AND/OR, logika tiga nilai, alur eksekusi, query database, contoh linimasa
- [docs/rencana-ota.md](docs/rencana-ota.md) — rencana pembaruan firmware over-the-air (OTA): komponen, risiko, tahapan
- [docs/menjalankan-semua-layanan.md](docs/menjalankan-semua-layanan.md) — `manage.py runall`: semua layanan dari satu terminal, dan pertimbangannya

## Prasyarat

- Miniconda (atau Python 3.10+ dengan `venv`)
- MySQL Community Server 8.0.11 atau lebih baru (disarankan 8.4 LTS)

Catatan: project ini semula disiapkan untuk MariaDB. Kode Django-nya sama untuk
keduanya (backend `django.db.backends.mysql`); yang berbeda hanya server dan
driver. Lihat bagian "Mengapa MySQL, bukan MariaDB" di bawah.

## Menjalankan pertama kali

Semua perintah dijalankan dari folder project (tempat `manage.py` berada).

**1. Pasang dan jalankan MySQL Community Server**

Unduh installer macOS (ARM64, DMG) dari <https://dev.mysql.com/downloads/mysql/>,
jalankan, dan catat password `root` yang Anda buat saat instalasi. Server
dinyalakan/dimatikan lewat System Settings → MySQL.

Bila XAMPP terpasang, matikan dulu "MySQL Database" di panel XAMPP: keduanya
memakai port 3306 dan tidak bisa berjalan bersamaan.

Installer menaruh program di `/usr/local/mysql/bin`, yang belum ada di `PATH`.
Tambahkan sekali saja:

```bash
echo 'export PATH="/usr/local/mysql/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
mysql --version
```

**2. Environment dan dependensi**

```bash
conda create -n iot_platform python=3.12
conda activate iot_platform
conda install -c conda-forge mysqlclient
pip install -r requirements.txt
```

Driver `mysqlclient` sengaja dipasang lewat conda-forge (sudah jadi, tanpa
kompilasi), sebelum `pip`. Jalankan `conda activate iot_platform` setiap membuka
terminal baru.

Bila sebelumnya Anda sempat mencoba membangun `mysqlclient` dengan `pip`,
bersihkan dulu: `pip uninstall -y mysqlclient`, lalu ulangi `conda install` di atas.

**3. Database dan penggunanya** — masuk ke MySQL (`mysql -u root -p`), lalu:

```sql
CREATE DATABASE iot_platform CHARACTER SET utf8mb4;
CREATE USER 'iot_user'@'localhost' IDENTIFIED BY 'ganti_password';
GRANT ALL PRIVILEGES ON iot_platform.* TO 'iot_user'@'localhost';
-- Dipakai `manage.py test`, yang membuat database sementara:
GRANT ALL PRIVILEGES ON test_iot_platform.* TO 'iot_user'@'localhost';
```

**4. Konfigurasi** — salin `.env.example` menjadi `.env`, lalu isi `DB_PASSWORD`
dengan password di atas.

```bash
cp .env.example .env
```

**5. Buat tabel**

```bash
python manage.py makemigrations accounts devices
python manage.py migrate
```

`makemigrations` membaca `models.py` dan menulis file migrasi (resep perubahan
tabel); `migrate` menjalankan resep itu ke database. File migrasi yang dihasilkan
adalah bagian dari kode — simpan, jangan dihapus. Bila file `0001_initial.py`
sudah ada, `makemigrations` hanya akan melaporkan "No changes detected".

**6. Data contoh dan menjalankan server**

```bash
python manage.py seed_demo
python manage.py runserver
```

Buka <http://127.0.0.1:8000/> dan masuk dengan salah satu akun contoh
(password semuanya `demo12345`):

| Username | Role  | Yang terlihat                                  |
|----------|-------|------------------------------------------------|
| `admin`  | Admin | semua device, menu Pengguna, situs `/admin/`   |
| `budi`   | User  | 3 device miliknya, termasuk Lab Relay Board    |
| `siti`   | User  | 1 device (sensor + pompa yang bisa dikontrol)  |

## Mensimulasikan device

Komunikasi dengan device sungguhan belum ada. Untuk melihat aktuator dan command
bekerja, jalankan simulator di terminal kedua:

```bash
python manage.py simulate_devices          # satu putaran
python manage.py simulate_devices --loop   # terus-menerus, tiap 5 detik
```

## Menjalankan tes

```bash
python manage.py test
```

## Bila ada kendala

- **`Authentication plugin 'mysql_native_password' cannot be loaded`** — Django
  masih tersambung ke server lama (MariaDB/XAMPP), atau `iot_user` dibuat dengan
  metode autentikasi lama. Pastikan MySQL XAMPP mati dan MySQL Community yang
  berjalan (`mysql -u root -p -e "SELECT VERSION();"` harus menampilkan 8.x/9.x
  tanpa kata "MariaDB").
- **`Access denied for user 'iot_user'`** — periksa `DB_PASSWORD` di `.env`.
  Bila masih gagal, ganti `DB_HOST=127.0.0.1` menjadi `DB_HOST=localhost`.
- **`Can't connect to server`** — MySQL belum berjalan (System Settings → MySQL
  → Start MySQL Server).
- **`mysql: command not found`** — langkah `PATH` di bagian 1 belum dijalankan.

## Mengapa MySQL, bukan MariaDB

Paket `mysqlclient` dari conda-forge dibangun di atas pustaka klien MySQL 9, yang
tidak lagi menyertakan metode autentikasi `mysql_native_password` — metode yang
dipakai MariaDB. Akibatnya driver itu tidak bisa masuk ke MariaDB, dan
alternatifnya (mengompilasi driver sendiri terhadap pustaka MariaDB) merepotkan
di macOS. MySQL Community memakai `caching_sha2_password`, yang didukung driver
conda-forge tanpa langkah tambahan. Selain itu, MariaDB bawaan XAMPP umumnya
versi 10.4, di bawah syarat minimum Django 5.2 (10.5).

Bila suatu saat ingin kembali ke MariaDB (10.5+): tidak ada kode yang perlu
diubah; cukup sediakan driver `mysqlclient` yang dibangun terhadap MariaDB
Connector/C.

## Komunikasi MQTT (tahap 3)

Pengaturan broker ada di `.env` (`MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC_PREFIX`, ...).
Bawaannya broker publik `broker.emqx.io`: hanya untuk belajar, karena siapa pun bisa
membaca pesannya.

```bash
pip install paho-mqtt                         # sekali saja
python manage.py mqtt_bridge                  # terminal terpisah dari runserver
python scripts/fake_mqtt_device.py TOKEN      # device tiruan dua arah
```

Topik setiap device ditampilkan di halaman device (bagian *Connect a device*).
Contoh firmware ESP32: `firmware/esp32_mqtt/esp32_mqtt.ino`.
Penjelasan lengkap: [docs/tahap-03-mqtt.md](docs/tahap-03-mqtt.md).

## Dashboard real-time (tahap 4)

Halaman device dan variabel menerima data baru lewat WebSocket tanpa memuat ulang.

```bash
pip install channels daphne channels-redis    # sekali saja
python manage.py runserver                    # harus tertulis "ASGI/Daphne"
```

Agar data dari `mqtt_bridge` juga tampil seketika, pasang Redis
(`brew install redis && brew services start redis`) lalu isi
`REDIS_URL=redis://127.0.0.1:6379/0` di `.env`.
Penjelasan lengkap: [docs/tahap-04-websocket.md](docs/tahap-04-websocket.md).

## Komunikasi CoAP (tahap 6)

CoAP berjalan di atas UDP (port 5683) tanpa broker: device harus bisa menjangkau
komputer ini secara langsung (satu jaringan, atau server dengan IP publik).

```bash
pip install aiocoap                           # sekali saja
python manage.py migrate                      # kolom last_protocol & last_seen_at
python manage.py coap_server                  # terminal terpisah dari runserver
python scripts/fake_coap_device.py TOKEN      # device tiruan dua arah (Observe)
```

Isi `COAP_PUBLIC_HOST` di `.env` dengan IP komputer ini di Wi-Fi agar alamat di tab
*CoAP* halaman device bisa dipakai ESP32. Contoh firmware: `firmware/esp32_coap/esp32_coap.ino`.
Penjelasan lengkap: [docs/tahap-06-coap.md](docs/tahap-06-coap.md).

## Menjalankan semua layanan sekaligus

Web, MQTT, CoAP, dan rule engine bisa dijalankan dari satu terminal:

```bash
python manage.py runall                  # web di http://127.0.0.1:8000/
python manage.py runall --skip coap      # tanpa CoAP
python manage.py runall --only web,rules # hanya sebagian
```

Setiap layanan tetap berjalan sebagai proses sendiri; `runall` menggabungkan log-nya,
menyalakan ulang yang crash, dan mematikan semuanya saat Ctrl+C. Hanya untuk
pengembangan; lihat [docs/menjalankan-semua-layanan.md](docs/menjalankan-semua-layanan.md).

## Rule engine (tahap 7)

Aturan otomatis model Event–Condition–Action, mis. "bila soil_moisture < 20 selama
60 detik, nyalakan pompa; pulih bila > 30". Dibuat di menu **Rules**; alarm tampil di
menu **Alarms**. Lima model rule engine lain sudah tersedia sebagai pilihan, belum
diimplementasikan.

```bash
python manage.py migrate                      # tabel rules_*
python manage.py seed_rules                   # 6 aturan contoh (setelah seed_demo)
python manage.py rule_engine                  # worker, terminal terpisah dari runserver
python manage.py send_telemetry "Fish Pond Node" dissolved_oxygen=3.5   # kirim data tiruan
```

Dengan `REDIS_URL` terisi, data diteruskan ke worker lewat antrean Redis. Tanpa Redis
aturan dinilai langsung saat data disimpan, tetapi worker tetap diperlukan untuk aturan
berdurasi dan "device diam". Email aksi aturan dicetak di terminal bila `EMAIL_HOST`
kosong. Penjelasan lengkap: [docs/tahap-07-rule-engine-eca.md](docs/tahap-07-rule-engine-eca.md).

## Struktur project

```
manage.py            pintu masuk semua perintah Django
config/              pengaturan project (settings, URL utama, WSGI/ASGI)
accounts/            app: model User + role, login, manajemen pengguna
devices/             app: model Device & Telemetry, halaman device
  management/commands/seed_demo.py   perintah pengisi data contoh
api/                 app: API HTTP untuk device (access token)
mqtt_bridge/         app: komunikasi device lewat MQTT (manage.py mqtt_bridge)
coap_gateway/        app: komunikasi device lewat CoAP (manage.py coap_server)
realtime/            app: WebSocket (Django Channels)
dashboard/           app: dashboard per lokasi & device
rules/               app: rule engine & alarm (manage.py rule_engine)
launcher/            app: manage.py runall (menjalankan semua layanan)
templates/           template lintas-app (base.html, login, 403)
static/              CSS
docs/                catatan belajar per tahap
```
