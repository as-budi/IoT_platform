# Tahap 27a — Snapshot JPEG dari ESP32-CAM

Bagian pertama rencana [rencana-tahap-27-kamera.md](rencana-tahap-27-kamera.md), untuk ESP32-CAM.
Live view (27b) dan perekaman (27c) belum dikerjakan.

Bagian kedua (lanjutan 27a) menambahkan: thumbnail, kartu kamera di dashboard, aksi aturan
*Capture snapshot* (gambar ditautkan ke alarm dan bisa dikirim ke Telegram), kuota penyimpanan, dan
log aktivitas. Lihat [Lanjutan](#lanjutan).

## Alur

```
ESP32-CAM ──HTTP POST image/jpeg──▶ /api/v1/<token>/snapshot ──▶ private_media/camera/<device>/<yyyy/mm/dd>/<HHMMSS>.jpg
    ▲                                        │
    │ command "capture" (MQTT/HTTP)          └─▶ WebSocket {"type": "snapshot"} → kartu Camera diperbarui
    └──── tombol Capture now / aturan SEND_COMMAND
```

Gambar dikirim lewat HTTP, bukan MQTT, karena broker membatasi ukuran paket (`max_packet_size 65536`).

## Server (app `cameras`)

- **Model**: `Camera` (device, name — bawaan `camera`, retention_days: kosong = `CAMERA_RETENTION_DAYS`,
  0 = simpan selamanya) dan `Snapshot` (camera, ts, file, size, width, height, source
  `PERIODIC`/`COMMAND`/`DEVICE`, command). Kamera dibuat otomatis saat snapshot pertama tiba.
- **Board baru** `esp32cam` (*ESP32-CAM*): migrasi devices 0026, ota 0004; cameras 0001.
- **Unggah** `POST /api/v1/<token>/snapshot`, body JPEG mentah, header opsional `X-Source`
  (periodic/device), `X-Command-Id`, `X-Camera`. Jawaban 201 `{id, size, width, height}`; galat 400
  (kosong), 401/403 (token), 413 (> `CAMERA_MAX_SNAPSHOT_KB`), 415 (bukan JPEG), 429 (lebih cepat dari
  `CAMERA_MIN_INTERVAL_S`; snapshot jawaban command tidak dibatasi). Unggahan juga memperbarui
  *last seen* / status online device.
- **Ukuran gambar** dibaca dari marker SOF JPEG (tanpa Pillow).
- **Penyimpanan privat** di `CAMERA_STORAGE_DIR`, disajikan lewat `/cameras/snapshots/<id>.jpg` setelah
  pemeriksaan akses (minimal VIEWER). Di produksi file dikirim nginx dengan `X-Accel-Redirect`
  (`CAMERA_X_ACCEL_PREFIX=/protected/camera/`).
- **Halaman device**: kartu *Camera* (muncul bila board ESP32-CAM atau sudah ada snapshot) berisi
  snapshot terbaru, 6 snapshot terakhir, tombol *Capture now* (OPERATOR) dan tautan galeri. Snapshot baru
  langsung tampil lewat WebSocket.
- **Galeri** `/cameras/devices/<id>/`: 24 per halaman, filter tanggal, unduh, hapus (EDITOR), pengaturan
  retensi (EDITOR).
- **Capture now** membuat command `capture` (TTL 60 s) dengan mekanisme command yang sudah ada; aturan
  juga bisa memotret lewat aksi *Send command* `capture`.
- **Retensi**: `python manage.py housekeeping` menghapus snapshot yang lebih tua dari batas kamera
  (file ikut terhapus).
- **Dokumentasi pengguna**: halaman *Cameras (ESP32-CAM)* dan entri di *Connect an ESP32*.

## Firmware `firmware/esp32cam_mqtt/esp32cam_mqtt.ino`

Diunduh dari *Connect an ESP32* → MQTT bila device ber-board ESP32-CAM (`SERVER`, broker, prefix, token
sudah terisi).

- Semua fitur template MQTT (telemetri, desired, command, status/heartbeat, OTA, laporan MAC, TLS opsional).
- Kamera AI-Thinker (PSRAM, 2 frame buffer, `CAMERA_GRAB_LATEST`).
- Desired: `lamp` (LED merah GPIO33, aktif LOW), `flash` (LED flash GPIO4), `snapshot_interval` (detik,
  bawaan 300, 0 = mati), `framesize` (mis. `VGA`, `SVGA`), `quality` (10–63); semuanya dilaporkan balik.
- Command `capture` → foto diunggah dengan `X-Command-Id`, jawaban `{"success", "snapshot", "size"}`.
- Arduino IDE: board *AI Thinker ESP32-CAM*, PSRAM Enabled, Partition *Minimal SPIFFS (1.9MB APP with OTA)*.
  Library: PubSubClient, ArduinoJson 7 (driver kamera bawaan core ESP32).

## Pengaturan `.env`

| Nama | Bawaan | Arti |
|---|---|---|
| `CAMERA_STORAGE_DIR` | `private_media/camera` | folder snapshot (tidak ikut `backup.sh`) |
| `CAMERA_MAX_SNAPSHOT_KB` | 512 | ukuran maksimum satu JPEG |
| `CAMERA_MIN_INTERVAL_S` | 2 | jarak minimum antar unggahan per kamera |
| `CAMERA_RETENTION_DAYS` | 30 | retensi bawaan (0 = selamanya) |
| `CAMERA_STORAGE_QUOTA_MB` | 0 | kuota gambar per pemilik; penuh = gambar tertua dihapus (0 = tanpa batas) |
| `CAMERA_X_ACCEL_PREFIX` | kosong | `/protected/camera/` di produksi (nginx) |

## Deploy

1. `deploy/scripts/update.sh` (migrasi devices 0026, ota 0004, cameras 0001; collectstatic).
2. Pasang ulang `deploy/nginx/iot-platform.conf`: zona `iot_snapshot` (2 r/s), lokasi
   `/api/v1/<token>/snapshot` (`client_max_body_size 1m`) dan lokasi internal `/protected/camera/`; lalu
   `nginx -t && systemctl reload nginx`.
3. Tambahkan `CAMERA_X_ACCEL_PREFIX=/protected/camera/` di `.env` produksi, restart layanan.

## Pengujian

`cameras/tests.py` (13 tes): parsing JPEG, unggahan sukses/ditolak/rate limit/pengecualian command,
kartu halaman device, streaming gambar dan X-Accel, akses berbagi, capture, galeri + hapus file, retensi,
unduhan template ESP32-CAM. Firmware baru diperiksa sintaksnya dengan header tiruan; belum dikompilasi
dengan toolchain ESP32 asli dan belum diuji di perangkat.

## Lanjutan

### Thumbnail

- `cameras/thumbs.py`: saat gambar diterima dibuat salinan lebar 320 px (JPEG kualitas 70) dengan
  **Pillow** (baru di `requirements.txt`), disimpan di sebelah gambarnya (`<HHMMSS>-t.jpg`, kolom
  `Snapshot.thumb`, migrasi cameras 0002). Gambar yang sudah kecil, atau tanpa Pillow: tanpa thumbnail.
- `/cameras/snapshots/<id>.jpg?thumb=1` menyajikan thumbnail (atau gambar asli bila tidak ada). Dipakai
  deretan "Recent", galeri, dashboard, dan tabel alarm; filter template `{{ snapshot|thumb_url }}`.
- `python manage.py camera_thumbnails [--limit N]` membuat thumbnail untuk gambar lama.

### Dashboard

Device yang punya snapshot mendapat kartu **Camera** (gambar terbaru, "x ago", klik → galeri) di
depan kartu variabelnya. Kabar WebSocket `snapshot` kini membawa `device` dan `thumb`, jadi kartu
diganti di tempat; versi polling dashboard ikut memperhitungkan snapshot terakhir.

### Aksi aturan *Capture snapshot*

- Jenis aksi baru `CAPTURE` di ECA, rule chain (node *Action*), dan state machine (migrasi rules 0011:
  pilihan `CAPTURE` + kolom `send_photo`). Field: device kamera, *Send the photo to Telegram*,
  recipients (id chat; kosong = chat pemilik aturan), message (keterangan foto, dengan placeholder).
- `rules/actions.capture_snapshot()` → `cameras.services.rule_capture()`: command `capture` (berlaku
  120 s) + `CaptureRequest` (command, aturan, chat Telegram, keterangan). Pemilik aturan perlu peran
  Operator pada device kamera. Di edge (micro rule) aksi ini termasuk aksi cloud.
- Saat gambar jawaban tiba (`X-Command-Id`): sumber `Rule`, ditautkan ke aturan dan ke **alarm aktif
  aturan itu** (yang terbaru), lalu bila diminta dikirim ke Telegram (`notifications.telegram.send_photo`,
  multipart `sendPhoto`) di thread terpisah setelah commit, supaya ESP32-CAM tidak menunggu Telegram.
- Tabel alarm (daftar alarm, halaman aturan, halaman device) menampilkan sampai 4 thumbnail; galeri
  menampilkan nama aturan dan nomor alarm.
- Ekspor/impor JSON aturan mendukung aksi/node `CAPTURE` (`device`, `send_photo`, `message`, `recipients`).

### Kuota

`CAMERA_STORAGE_QUOTA_MB` (bawaan 0 = tanpa batas; contoh produksi 1024): total ukuran gambar semua
device milik satu pengguna. Bila gambar baru tidak muat, gambar **tertua** pemilik itu dihapus lebih dulu
(seperti CCTV yang menimpa rekaman lama). Galeri menampilkan pemakaian kepada pemilik.

### Log aktivitas

`camera.capture` (Capture now, kategori Control), `camera.snapshot_deleted` dan `camera.settings`
(kategori Device). Gambar berkala dan gambar dari aturan tidak dicatat satu per satu (log aturan sudah
mencatat aksinya), supaya log tidak penuh.

### API v2 untuk MQTT client

`/api/v2/me/devices` menambah `snapshot_url` per device (alamat `POST /api/v1/<token>/snapshot`, hanya
untuk Editor ke atas, sama seperti `access_token`). Dipakai simulasi kamera di repo `mqtt_client`
(tahap M11), yang meneruskan gambar dari browser ke alamat ini.

### Deploy tambahan

`update.sh` memasang Pillow dan menjalankan migrasi cameras 0002 dan rules 0011. Opsional: isi
`CAMERA_STORAGE_QUOTA_MB` di `.env`, lalu `manage.py camera_thumbnails` untuk gambar lama.

### Tes tambahan

10 tes di `cameras/tests.py`: thumbnail (tersimpan, disajikan, ikut terhapus; Pillow sungguhan), kuota,
aksi aturan (tautan alarm, Telegram, tanpa akses, tanpa Telegram), multipart `sendPhoto`, form aksi,
kartu dashboard, log aktivitas.

## Belum dikerjakan

Live view (27b), perekaman/timelapse (27c). Firmware ESP32-CAM belum diuji pada perangkat.
