# Tahap 9 — Pembaruan firmware over-the-air (OTA)

Tahap ini mengerjakan **Tahap 1 (OTA dasar)** dari [rencana-ota.md](rencana-ota.md). Firmware
device kini bisa diperbarui dari platform tanpa kabel USB:

- unggah file `.bin` sekali di halaman **Firmware**,
- pilih versi di halaman device, lalu klik **Update firmware**,
- ikuti progresnya secara langsung.

```
 Browser                      Django                                    Device (ESP32)
───────                      ──────                                    ──────────────
"Update firmware" ──► start_update()
                       OtaUpdate QUEUED
                       Command "ota" {update, version, url, md5, size}
                                   ──── MQTT / CoAP (seperti command lain) ────►
                                                                     jawab {"success": true}
                                   ◄──── GET /api/v1/ota/<token-unduhan>.bin ── HTTPUpdate mengunduh
                       token dipakai SEKALI;  QUEUED → DOWNLOADING       menulis ke partisi cadangan,
                                   ──── file + header x-MD5 ─────────►   memeriksa MD5
                                   ◄──── firmware: DOWNLOADING 40 % … DOWNLOADED, VERIFIED, UPDATING
                                                                     restart
                                   ◄──── firmware: {"version": "1.1.0"} ── firmware BARU menyala
                       versi cocok → UPDATED
progres tampil langsung ◄── WebSocket (kabar "ota")
```

## 1. Gagasan utama

1. **Perintah dan file lewat jalur berbeda.** Perintahnya adalah command `ota` biasa, sehingga
   sampai ke device lewat MQTT atau CoAP seperti command lain. File firmware (ratusan KB)
   selalu diunduh lewat **HTTP**: mengirim file sebesar itu lewat MQTT atau CoAP jauh lebih
   repot.
2. **Bukti keberhasilan datang dari device yang sudah restart.** Server baru menandai
   `UPDATED` saat device menyala dan **melaporkan versi barunya**. Laporan "UPDATING" belum
   cukup, karena firmware baru bisa saja gagal menyala.
3. **URL unduhan memakai token tersendiri.** Token ini sekali pakai dan kedaluwarsa, dan
   **bukan** access token device. Command `ota` dikirim lewat broker publik yang bisa dibaca
   siapa saja (bagian 6).
4. **Firmware lama tidak pernah dihapus lebih dulu.** ESP32 punya dua partisi aplikasi.
   Firmware baru ditulis ke partisi cadangan dan baru dipakai setelah MD5-nya cocok. Bila
   listrik padam atau Wi-Fi putus di tengah jalan, device tetap menyala dengan firmware lama.

## 2. Yang ditambahkan

| File | Isi |
|---|---|
| `ota/models.py` | `FirmwarePackage`, `OtaUpdate` (status, progres, token unduhan, command), `OtaEvent` (riwayat) |
| `ota/services.py` | `start_update`, `cancel`, `claim_download`, `handle_report` (`report_state`, `report_version`), `command_finished`, `expire_due` |
| `ota/forms.py` | upload (checksum otomatis, cek ukuran dan magic byte 0xE9), pilih versi |
| `ota/views.py`, `urls.py`, `templates/ota/` | halaman Firmware, upload, detail paket, detail pembaruan, kartu Firmware di halaman device, endpoint unduhan |
| `devices/models.py` | `Board` (esp32, esp32s2, esp32s3, esp32c3, esp8266, sim) dan `Device.board`, `firmware_version`, `firmware_reported_at` |
| `api/urls.py`, `api/views.py` | `POST /api/v1/<token>/firmware` (laporan), `GET /api/v1/ota/<token-unduhan>.bin` (unduhan) |
| `mqtt_bridge/topics.py`, `handlers.py` | topik `<prefix>/<token>/firmware` |
| `coap_gateway/handlers.py` | `POST /api/v1/<token>/firmware` |
| `devices/control.py` | jawaban command `ota` diteruskan ke `ota.services.command_finished` |
| `scripts/fake_mqtt_device.py` | device tiruan kini bisa OTA, termasuk skenario gagal (`--ota-fail`) |
| `firmware/esp32_mqtt_ota/` | contoh firmware ESP32 yang bisa memperbarui dirinya |
| `ota/tests.py` | 28 tes: paket, mulai/batal, unduhan, laporan, tiga protokol, batas waktu, halaman, alur penuh |

Migrasi: `devices/0016_device_firmware` dan `ota/0001_initial`.

## 3. Tabel database

```
FirmwarePackage   title, version, board, file, size, md5, sha256, notes, uploaded_by
                  unik: (uploaded_by, board, version)
OtaUpdate         device, package, status, progress, error, previous_version,
                  command (1-1 ke Command "ota"),
                  download_token, token_expires_at, token_used_at, downloaded_by,
                  created_by, created_at, updated_at, finished_at
  OtaEvent        update, status, message, source (web/MQTT/HTTP/COAP/device/server), created_at
Device            + board, firmware_version, firmware_reported_at
```

- Satu `OtaUpdate` adalah **satu percobaan**. Percobaan yang gagal tidak diulang; pengguna
  membuat percobaan baru. Riwayat semua percobaan tetap tersimpan.
- Paket yang pernah dipakai tidak bisa dihapus (`on_delete=PROTECT`), supaya riwayat tetap
  menunjuk ke file yang benar.

## 4. Status dan siapa yang mengubahnya

| Status | Diubah oleh | Kapan |
|---|---|---|
| `QUEUED` | server (`start_update`) | pengguna menekan *Update firmware* |
| `DOWNLOADING` | server (`claim_download`) atau laporan device | file mulai diunduh. Laporan berikutnya memperbarui % saja |
| `DOWNLOADED` | laporan device | file lengkap diterima |
| `VERIFIED` | laporan device | MD5 cocok, image ditandai untuk boot |
| `UPDATING` | laporan device | device akan restart |
| `UPDATED` | **server** (`report_version`) | device menyala dan melaporkan versi yang sama dengan paket |
| `FAILED` | device atau server | lihat bagian 7 |
| `CANCELED` | pengguna | tombol *Cancel* |

Aturan perubahan status:

- **Status hanya boleh maju.** Laporan `DOWNLOADING` setelah `VERIFIED` ditolak dengan pesan
  "cannot go back".
- **`UPDATED` tidak bisa dilaporkan device.** Hanya laporan versi yang bisa membuatnya.
- Status akhir (`UPDATED`, `FAILED`, `CANCELED`) tidak bisa berubah lagi.

Penulisan status memakai baris terkunci (`select_for_update`), karena laporan MQTT dan
permintaan unduhan HTTP bisa datang hampir bersamaan.

## 5. Protokol untuk device

### 5.1 Command `ota` (platform → device)

Dikirim lewat jalur command yang sudah ada: MQTT `<prefix>/<token>/commands/<id>`, atau CoAP
`GET .../commands`.

```json
{"id": 12, "method": "ota",
 "params": {"update": 7, "title": "Greenhouse firmware", "version": "1.1.0",
            "url": "http://192.168.1.10:8000/api/v1/ota/Qhyn…5C.bin",
            "md5": "314881da450d0512abbae7aa1399dbe5", "size": 912345}}
```

Device menjawab seperti command lain:

- `{"success": true}` berarti "diterima". Ini **belum** berarti berhasil memperbarui.
- `{"success": false, "error": "Low battery"}` membuat pembaruan langsung `FAILED`.

### 5.2 Unduhan (device → platform, HTTP)

```
GET /api/v1/ota/<token-unduhan>.bin
200  isi file, dengan header Content-Length dan x-MD5
404  token tidak dikenal
410  sudah dipakai, kedaluwarsa, atau pembaruan sudah selesai/dibatalkan
403  device nonaktif
```

`HTTPUpdate` milik Arduino-ESP32 otomatis membaca `x-MD5` dan menolak image yang tidak cocok.

### 5.3 Laporan firmware (device → platform)

Payload sama untuk ketiga protokol:

| Protokol | Alamat |
|---|---|
| HTTP | `POST /api/v1/<token>/firmware` |
| MQTT | publish ke `<prefix>/<token>/firmware` |
| CoAP | `POST coap://<server>/api/v1/<token>/firmware` |

```json
{"version": "1.1.0", "board": "esp32"}                    // setiap kali menyala/tersambung
{"update": 7, "state": "DOWNLOADING", "progress": 40}     // selama pembaruan
{"update": 7, "state": "FAILED", "error": "MD5 mismatch"}
```

Laporan versi memperbarui `Device.firmware_version` dan `Device.board`. Bila ada pembaruan
yang sedang berjalan:

- versi **sama** dengan paket → `UPDATED`;
- versi berbeda **dan** status `UPDATING` → `FAILED`. Device restart tetapi firmware lama yang
  menyala (rollback);
- versi berbeda sebelum `UPDATING` → diabaikan. Contohnya device yang sekadar tersambung
  ulang ke broker.

## 6. Keamanan

| Risiko | Penanganan di tahap ini |
|---|---|
| Command `ota` terbaca di broker publik | URL memuat **token unduhan**, bukan access token. Token hanya membuka satu file, **sekali**, selama `OTA_DOWNLOAD_TTL` (30 menit), dan hanya selama pembaruan belum selesai |
| File firmware diunduh orang lain lewat alamat yang bisa ditebak | file disimpan di `private_media/firmware/` (`OTA_STORAGE_DIR`), **bukan** di static/media. Satu-satunya jalan mengambilnya adalah URL bertoken |
| Dua permintaan memakai token yang sama bersamaan | `UPDATE … SET token_used_at = … WHERE token_used_at IS NULL` bersifat atomik: hanya satu yang berhasil |
| Firmware untuk board lain | paket punya board; server menolak bila tidak sama dengan `Device.board` |
| File salah atau rusak | upload: ekstensi `.bin`, ukuran ≤ `OTA_MAX_FIRMWARE_SIZE`, byte pertama 0xE9 (image ESP). Device: MD5 dari header `x-MD5` |
| Pengguna lain memakai paket/device kita | paket hanya terlihat oleh pengunggahnya (Admin melihat semua); device lewat `visible_devices` |
| Firmware palsu disisipkan di perjalanan (HTTP) | **belum** ditangani: butuh HTTPS dan tanda tangan firmware (Tahap 3 rencana) |

Satu catatan jujur: topik MQTT itu sendiri sudah memuat access token device, dan broker
publik bisa dibaca siapa saja. Ini kelemahan yang sudah ada sejak tahap 3, dan tahap ini tidak
menambahnya. Solusinya adalah broker sendiri dengan login dan ACL per device.

## 7. Kegagalan dan batas waktu

| Penyebab | Siapa yang tahu | Pesan |
|---|---|---|
| Device offline, command tidak pernah diambil | server, setelah command kedaluwarsa (`OTA_COMMAND_TTL`, 10 menit) | *The device did not pick up the update command in time* |
| Device menolak command | jawaban command | *The device refused the update: …* |
| Unduhan putus, MD5 tidak cocok, partisi terlalu kecil | device melapor `FAILED` | *Device: …* (dari `httpUpdate.getLastErrorString()`) |
| Firmware baru tidak menyala, device kembali ke versi lama | laporan versi setelah `UPDATING` | *The device restarted with version 1.0.0, not 1.1.0* |
| Tidak ada kabar apa pun | server, setelah `OTA_UPDATE_TIMEOUT` (15 menit) | *No progress after downloading within 15 minutes* |
| Pengguna membatalkan | tombol *Cancel* | status `CANCELED`; command dan link unduhan tidak berlaku lagi |

Batas waktu diperiksa secara **lazy**: setiap kali data OTA dibaca (halaman device, laporan,
unduhan, mulai pembaruan), `expire_due()` menandai yang sudah lewat waktu. Pola ini sama
dengan `Command.expire_due()`, sehingga tidak perlu proses latar belakang tambahan.

Pembatalan **tidak bisa menghentikan** device yang sudah mulai menulis firmware. Pembatalan
hanya membuat server berhenti menunggu dan link unduhan mati. Bila device tetap restart
dengan versi baru, laporan versinya tetap tercatat di `Device.firmware_version`.

## 8. Halaman

- **Firmware** (menu *Maintenance*): daftar paket, tombol *Upload firmware*, dan pembaruan
  terbaru. Detail paket menampilkan MD5, SHA-256, catatan rilis, dan device yang memakainya.
- **Halaman device → Firmware**:
  - versi yang berjalan dan board;
  - pilihan versi, hanya untuk board yang sama dan bukan versi yang sedang berjalan;
  - tombol *Update firmware*;
  - selama berjalan: langkah-langkah, bilah progres yang bergerak langsung lewat WebSocket,
    dan tombol *Cancel*;
  - lima percobaan terakhir.

  Ada peringatan bila alamat unduhan berupa `127.0.0.1`.
- **Detail pembaruan**: riwayat lengkap (waktu, status, pesan, sumber: web/MQTT/HTTP/device),
  status command, dan kapan serta dari IP mana file diunduh.

## 9. Praktikum A: device tiruan (tanpa ESP32)

1. Persiapan:

   ```bash
   python manage.py migrate
   python manage.py runall
   ```

2. Login sebagai `budi`, lalu buka device **Lab Relay Board** → *Edit*. Isi *Board* =
   **Simulated device**.
3. Buat dua file "firmware" tiruan. Board *Simulated device* menerima file apa saja:

   ```bash
   printf 'firmware 1.1.0 %.0s' {1..500} > relay-1.1.0.bin
   printf 'firmware 1.2.0 %.0s' {1..800} > relay-1.2.0.bin
   ```

4. Menu **Firmware → Upload firmware**: unggah keduanya dengan versi `1.1.0` dan `1.2.0`, board
   *Simulated device*.
5. Jalankan device tiruan dengan access token Lab Relay Board:

   ```bash
   python scripts/fake_mqtt_device.py <ACCESS_TOKEN> --firmware-version 1.0.0 --board sim
   ```

   Halaman device kini menampilkan *Running version 1.0.0*.
6. Pilih `1.1.0` → **Update firmware**. Amati:
   - bilah progres naik,
   - langkah *Downloading → Downloaded → Verified → Updating*,
   - device tiruan "restart",
   - status menjadi **Updated**.

   Di terminal device tiruan terlihat setiap pesan yang dikirim.
7. Coba kegagalan. Hentikan device tiruan, lalu jalankan lagi dengan versi yang sekarang
   (`1.1.0`) dan salah satu opsi:

   ```bash
   python scripts/fake_mqtt_device.py <TOKEN> --firmware-version 1.1.0 --board sim --ota-fail md5
   python scripts/fake_mqtt_device.py <TOKEN> --firmware-version 1.1.0 --board sim --ota-fail download
   python scripts/fake_mqtt_device.py <TOKEN> --firmware-version 1.1.0 --board sim --ota-fail boot
   ```

   Lalu perbarui ke `1.2.0`. Bandingkan pesan gagalnya di halaman detail pembaruan.
   `boot` meniru rollback: semua langkah berhasil, tetapi device menyala lagi dengan versi
   lama.
8. Hentikan device tiruan, lalu mulai pembaruan. Status bertahan *Queued*; setelah 10 menit
   menjadi *Failed* ("did not pick up the update command").

## 10. Praktikum B: ESP32 sungguhan

1. **Jaringan.** ESP32 harus bisa menjangkau komputer Anda lewat HTTP. Cari IP Wi-Fi komputer
   (`ipconfig getifaddr en0` di macOS), misalnya `192.168.1.10`. Isi `.env`:

   ```
   OTA_BASE_URL=http://192.168.1.10:8000
   DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.10
   ```

   Lalu jalankan `python manage.py runall 0.0.0.0:8000`. Tanpa `ALLOWED_HOSTS`, Django menolak
   permintaan ESP32 dengan kode 400.
2. **Firmware pertama lewat kabel.** Buka `firmware/esp32_mqtt_ota/esp32_mqtt_ota.ino`, isi
   Wi-Fi, prefix MQTT, dan access token, biarkan `FW_VERSION "1.0.0"`. Pilih **Tools →
   Partition Scheme** yang punya dua partisi aplikasi (mis. *Default 4MB with spiffs*), lalu
   upload lewat USB.
3. **Firmware kedua.** Ubah `FW_VERSION` menjadi `"1.1.0"` dan ubah sesuatu yang terlihat (mis.
   `SEND_INTERVAL_MS`). Pilih **Sketch → Export Compiled Binary**. Arduino IDE membuat
   `esp32_mqtt_ota.ino.bin` di folder sketch (atau folder `build/`). **Jangan** upload lewat
   USB.
4. Unggah `.bin` itu di halaman Firmware: versi `1.1.0`, board ESP32.
5. Di halaman device (board terisi otomatis dari laporan ESP32), pilih `1.1.0` → **Update
   firmware**. Di Serial Monitor terlihat urutan ini:

   ```
   <- …/commands/31  {"id":31,"method":"ota","params":{…}}
   OTA: downloading http://192.168.1.10:8000/api/v1/ota/….bin
   -> …/firmware  {"update":9,"state":"DOWNLOADING","progress":10}
   …
   -> …/firmware  {"update":9,"state":"UPDATING"}
   OTA: restarting into the new firmware
   Firmware 1.1.0 (esp32)
   -> …/firmware  {"version":"1.1.0","board":"esp32"}
   ```

6. Coba kegagalan:
   - unggah `.bin` untuk board lain;
   - matikan server saat unduhan berjalan;
   - unggah paket berversi `1.2.0` padahal isinya masih firmware `1.1.0`. Device akan menyala
     dan melaporkan `1.1.0`, sehingga hasilnya `FAILED`. Ini menunjukkan mengapa versi di
     platform harus sama dengan `FW_VERSION` di kode.

Kebutuhan: paket board **esp32 by Espressif versi 2.0 atau lebih baru** (untuk
`httpUpdate.onProgress`), PubSubClient, dan ArduinoJson 7.

## 11. Pengaturan (.env)

| Nama | Bawaan | Arti |
|---|---|---|
| `OTA_BASE_URL` | kosong (alamat di address bar) | alamat server untuk device, mis. `http://192.168.1.10:8000` |
| `OTA_STORAGE_DIR` | `private_media/firmware` | folder file firmware (sudah di `.gitignore`) |
| `OTA_MAX_FIRMWARE_SIZE` | 4194304 (4 MB) | batas ukuran upload |
| `OTA_COMMAND_TTL` | 600 | detik bagi device untuk mengambil command `ota` |
| `OTA_DOWNLOAD_TTL` | 1800 | masa berlaku link unduhan |
| `OTA_UPDATE_TIMEOUT` | 900 | batas seluruh pembaruan, dari mulai sampai `UPDATED` |

## 12. Batasan dan tahap berikutnya

Belum dikerjakan (Tahap 2 dan 3 di [rencana-ota.md](rencana-ota.md)):

- pembaruan banyak device sekaligus dan rollout bertahap;
- alarm otomatis bila pembaruan gagal (bisa juga lewat rule engine);
- rollback otomatis yang dipicu firmware (ESP-IDF app rollback);
- HTTPS wajib dan tanda tangan firmware;
- syarat minimum level baterai.

File dilayani oleh Django (`FileResponse`). Ini cukup untuk praktikum; di produksi file
sebaiknya dilayani web server seperti Nginx (`X-Accel-Redirect`).

## Latihan

1. Mulai pembaruan, lalu buka URL unduhan dari command (lihat tabel Commands) di browser dua
   kali. Apa jawaban kedua, dan mengapa?
2. Tunjukkan di kode tempat server memutuskan `UPDATED`. Mengapa laporan `"state": "UPDATED"`
   dari device ditolak?
3. Ubah `--ota-delay` device tiruan menjadi 30 detik, dan `OTA_UPDATE_TIMEOUT` menjadi 60.
   Apa yang terjadi, dan pesan apa yang muncul?
4. Buat aturan ECA atau rule chain yang membuat alarm bila sebuah device **berhenti melapor**
   setelah pembaruan (petunjuk: event *Device sends no data*).
5. Tambahkan pemeriksaan "versi baru harus lebih tinggi daripada versi sekarang"
   (*downgrade* ditolak). Di fungsi mana sebaiknya diletakkan, dan bagaimana membandingkan
   `1.10.0` dengan `1.9.0` dengan benar?
