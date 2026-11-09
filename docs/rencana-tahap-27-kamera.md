# Rencana: Data visual dari ESP32-CAM (tahap 27)

Status: **27a selesai untuk ESP32-CAM** (lihat [tahap-27a-snapshot-esp32cam.md](tahap-27a-snapshot-esp32cam.md)); 27b dan 27c belum dikerjakan.

## 1. Ringkasan

Platform saat ini hanya mengenal data angka (telemetri), status aktuator, command, dan file
firmware. Kamera menambah dua jenis data yang sangat berbeda:

| Jenis | Contoh | Ukuran / laju | Cocok untuk |
|---|---|---|---|
| **Snapshot** (gambar diam, JPEG) | foto tiap 5 menit, foto saat alarm, foto atas perintah | 20–200 KB per gambar | riwayat, bukti alarm, timelapse, notifikasi Telegram |
| **Live view** (gambar beruntun) | ESP32-CAM JPEG 1–2 fps | 0,3–2 Mbit/s terus-menerus per kamera | memantau langsung dari halaman device / dashboard |

Rekomendasi: kerjakan **snapshot dulu** (tahap 27a), karena memakai jalur yang sudah ada
(token device, HTTP API, penyimpanan file privat, hak akses berbagi, aturan, Telegram) dan
langsung berguna. **Live view** (tahap 27b) menyusul.

## 2. Fondasi yang sudah ada

| Fondasi | Dipakai untuk |
|---|---|
| Device + access token, HTTP API `/api/v1/<token>/...` | ESP32-CAM mengunggah snapshot |
| Penyimpanan file privat (`private_media`, seperti file firmware OTA) dan URL unduhan bertoken | menyimpan dan menyajikan gambar tanpa membuka folder media ke publik |
| Command (MQTT/HTTP/CoAP) | `capture`, `live_start`, `live_stop` |
| Desired state | resolusi, kualitas JPEG, interval snapshot, lampu flash |
| Peran berbagi (Viewer/Operator/Editor) | siapa boleh melihat gambar / live view, siapa boleh memotret |
| Aturan ECA/Flow/State machine + aksi Telegram | "bila pintu terbuka → foto → kirim ke Telegram" |
| Housekeeping + timer cleanup | menghapus gambar lama (retensi) |
| Board di device (tahap 26) | board baru `ESP32-CAM` untuk template dan OTA |

## 3. Yang perlu ditambahkan di server

### 3.1 Model data

| Model | Kolom utama | Catatan |
|---|---|---|
| `Camera` | device (FK, satu device boleh punya beberapa kamera), name, retention_days | status online, lokasi, berbagi dan log aktivitas mengikuti device |
| `Snapshot` | camera, ts, file, width, height, size, source (`PERIODIC` / `COMMAND` / `RULE` / `MANUAL`), rule (FK opsional), alarm (FK opsional) | file di `private_media/camera/<device>/<yyyy>/<mm>/`; indeks (camera, ts) |

### 3.2 Unggah snapshot dari device

- `POST /api/v1/<token>/snapshot` dengan badan `image/jpeg` (bukan multipart, supaya firmware
  sederhana). Header opsional: `X-Camera` (nama kamera), `X-Source`, `X-Command-Id`.
- Validasi: magic byte JPEG `FF D8 FF`, ukuran ≤ `CAMERA_MAX_SNAPSHOT_KB` (mis. 512 KB),
  dimensi dibaca dengan Pillow, laju per device dibatasi (mis. 1 gambar / 2 detik).
- **nginx**: saat ini `/api/` dibatasi `client_max_body_size 64k`. Perlu `location` khusus
  `~ ^/api/v1/[^/]+/snapshot$` dengan batas ±1 MB dan zona `limit_req` sendiri.
- **Bukan lewat MQTT**: broker dibatasi `max_packet_size 65536` (deploy/mosquitto/iot.conf), dan
  gambar di broker memperbesar memori. MQTT tetap dipakai untuk *perintah* memotret dan
  *kabar* bahwa gambar baru tersedia.
- Thumbnail (mis. 320 px) dibuat saat diterima, untuk daftar dan dashboard.

### 3.3 Menampilkan

- Halaman device: kartu **Camera** berisi gambar terakhir, waktu, tombol *Capture now*
  (command `capture`), dan galeri/linimasa snapshot (paginasi per hari).
- Dashboard: widget kamera (gambar terakhir, diperbarui lewat WebSocket `{"type": "snapshot"}`).
- Unduh: satu gambar, atau ZIP satu hari; timelapse (MP4) bisa ditunda.
- Gambar disajikan lewat view Django yang memeriksa hak akses, lalu `X-Accel-Redirect` ke nginx
  (file besar tidak lewat Daphne). Pola yang sama dengan unduhan firmware.

### 3.4 Aturan dan notifikasi

- Aksi baru **Capture snapshot** (kamera tertentu) untuk ECA / Flow / State machine.
- Alarm menyimpan snapshot terkait; halaman alarm menampilkannya.
- Telegram: `sendPhoto` dengan snapshot terakhir (atau yang baru diambil) dan teks aturan.
- Placeholder baru di teks notifikasi: `{snapshot_url}` (link bertoken, kedaluwarsa).

### 3.5 Live view (tahap 27b)

- ESP32-CAM mengirim JPEG 1–2 fps lewat WebSocket device (atau POST beruntun) hanya selama ada
  penonton; server meneruskan gambar terakhir ke penonton halaman lewat Channels tanpa menyimpannya.
- Mulai/berhenti otomatis: command `live_start` ketika halaman dibuka, `live_stop` saat ditutup atau
  tidak ada penonton selama N detik.
- Batas: satu sesi live per kamera, resolusi bawaan rendah (QVGA/VGA), laju dibatasi di server.

### 3.6 Perekaman dan analitik (tahap 27c, opsional)

Timelapse dari snapshot, deteksi gerak sederhana sebagai sumber kejadian untuk aturan. Ditunda sampai
kebutuhan nyata, karena biaya disk dan bandwidth VPS paling besar di bagian ini.

### 3.7 Pengaturan `.env`

`CAMERA_ENABLED`, `CAMERA_MAX_SNAPSHOT_KB`, `CAMERA_RETENTION_DAYS` (bawaan per kamera),
`CAMERA_MIN_INTERVAL_S`, `CAMERA_STORAGE_QUOTA_MB` per pengguna, `CAMERA_LIVE_MAX_FPS`.

## 4. Sisi device

### 4.1 Template firmware ESP32-CAM (AI-Thinker)

- Library `esp32-camera` (bawaan paket board "esp32"); board *AI Thinker ESP32-CAM*, PSRAM aktif.
- Fitur template MQTT yang sudah ada (telemetri, desired, command, status, OTA, laporan MAC) +:
  - command `capture` → ambil JPEG → `POST /snapshot` → jawab `{"success": true, "size": ...}`;
  - desired `snapshot_interval`, `framesize` (QVGA … UXGA), `quality` (10–63), `flash` (LED GPIO4);
  - command `live_start` / `live_stop` (tahap 27b);
  - telemetri: `rssi`, `free_psram`, `snapshot_count`.
- Board baru di platform: **ESP32-CAM** (`esp32cam`), template unduhan sendiri seperti ESP32-C3 OLED.
- Catatan perangkat keras: ESP32-CAM tidak punya USB (perlu adaptor USB-TTL), arus puncak saat
  Wi-Fi + kamera + flash besar (catu daya 5 V ≥ 1 A), dan flash 4 MB tetap cukup untuk OTA.

## 5. Risiko dan penanganannya

| Risiko | Penanganan |
|---|---|
| Disk VPS penuh oleh gambar | retensi per kamera, kuota per pengguna, cleanup harian (timer yang ada), ukuran maks per gambar |
| Bandwidth VPS habis oleh live view | live hanya saat ditonton, satu sesi per kamera, resolusi dan fps bawaan rendah |
| Privasi (kamera merekam orang) | gambar hanya untuk pemilik dan yang diberi akses; URL bertoken kedaluwarsa; log aktivitas setiap lihat/unduh; pemberitahuan di dokumentasi |
| Daphne terbebani file besar | file disajikan nginx (`X-Accel-Redirect`), unggahan dibatasi ukuran dan laju |
| Token device bocor → orang lain mengunggah gambar | validasi JPEG, laju terbatas, regenerate token (fitur yang ada) |
| ESP32-CAM tidak stabil (brownout, reset) | catu daya cukup, watchdog, laporan `reset_reason` di telemetri |

## 6. Tahapan pengerjaan

### Tahap 27a: Snapshot (prioritas)

Selesai seluruhnya untuk ESP32-CAM, termasuk thumbnail, kartu dashboard, aksi aturan *Capture snapshot*
(tautan ke alarm, Telegram `sendPhoto`), kuota, dan log aktivitas. Catatan: unduh ZIP satu hari dan
placeholder `{snapshot_url}` tidak dibuat; foto langsung dikirim ke Telegram sebagai gantinya.

1. Model `Camera`, `Snapshot`, migrasi; board `esp32cam`.
2. Endpoint `POST /api/v1/<token>/snapshot`, validasi, thumbnail, penyimpanan privat; lokasi nginx khusus.
3. Kartu Camera di halaman device + galeri, tombol *Capture now*; widget dashboard; kabar WebSocket.
4. Template firmware ESP32-CAM (unduh dari halaman Connect) dengan `capture` dan snapshot berkala.
5. Retensi + cleanup + kuota; log aktivitas (snapshot diterima, dihapus).
6. Aksi aturan *Capture snapshot*, snapshot di alarm, Telegram `sendPhoto`.
7. Tes (unit + unggahan nyata dari ESP32-CAM), dokumentasi pengguna "Cameras".

### Tahap 27b: Live view ESP32-CAM

1. Jalur JPEG beruntun dari device (WebSocket device / POST), diteruskan ke penonton lewat Channels.
2. Command `live_start` / `live_stop` otomatis berdasarkan penonton; batas fps dan satu sesi per kamera.
3. Tampilan live di kartu Camera dan widget dashboard.
4. Template firmware: mode live.

### Tahap 27c: Perekaman dan analitik (opsional)

Timelapse MP4 dari snapshot; deteksi gerak sebagai sumber kejadian untuk aturan.

## 7. Catatan pengujian

- Unggahan: JPEG valid/tidak valid, terlalu besar, terlalu sering, token salah, device nonaktif.
- Hak akses: Viewer melihat, Operator memotret, pengguna lain 404; URL bertoken kedaluwarsa.
- Retensi: gambar lebih tua dari batas terhapus beserta file-nya.
- Beban: 10 kamera × 1 gambar/menit selama 1 hari (ukuran disk, waktu respons).
- Live view: latensi, fps nyata, penonton bersamaan, putus-sambung Wi-Fi.

## 8. Keputusan yang perlu diambil

1. Apakah live view (27b) dibutuhkan, atau cukup snapshot berkala + *Capture now*.
2. Retensi bawaan dan kuota disk per pengguna (dipengaruhi ukuran disk VPS).
3. Apakah perekaman/timelapse memang dibutuhkan.

## 9. Referensi

- esp32-camera (driver kamera ESP32, OV2640 dll.): https://components.espressif.com/components/espressif/esp32-camera
