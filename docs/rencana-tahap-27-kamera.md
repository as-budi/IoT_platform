# Rencana: Data visual dari ESP32-CAM dan IP camera (tahap 27)

Status: **rencana**, belum dikerjakan.

## 1. Ringkasan

Platform saat ini hanya mengenal data angka (telemetri), status aktuator, command, dan file
firmware. Kamera menambah dua jenis data yang sangat berbeda:

| Jenis | Contoh | Ukuran / laju | Cocok untuk |
|---|---|---|---|
| **Snapshot** (gambar diam, JPEG) | foto tiap 5 menit, foto saat alarm, foto atas perintah | 20–200 KB per gambar | riwayat, bukti alarm, timelapse, notifikasi Telegram |
| **Live stream** (video) | ESP32-CAM MJPEG, IP camera RTSP H.264/H.265 | 0,3–4 Mbit/s terus-menerus per penonton/kamera | memantau langsung dari dashboard |

Rekomendasi: kerjakan **snapshot dulu** (tahap 27a), karena memakai jalur yang sudah ada
(token device, HTTP API, penyimpanan file privat, hak akses berbagi, aturan, Telegram) dan
berguna untuk kedua jenis kamera. **Live stream** (tahap 27b) menyusul dengan server media terpisah,
karena Django/Daphne tidak dirancang meneruskan video.

## 2. Fondasi yang sudah ada

| Fondasi | Dipakai untuk |
|---|---|
| Device + access token, HTTP API `/api/v1/<token>/...` | ESP32-CAM mengunggah snapshot |
| Penyimpanan file privat (`private_media`, seperti file firmware OTA) dan URL unduhan bertoken | menyimpan dan menyajikan gambar tanpa membuka folder media ke publik |
| Command (MQTT/HTTP/CoAP) | `capture`, `stream_start`, `stream_stop`, `flash` |
| Desired state | resolusi, kualitas JPEG, interval snapshot, lampu flash |
| Peran berbagi (Viewer/Operator/Editor) | siapa boleh melihat gambar / live, siapa boleh memotret |
| Aturan ECA/Flow/State machine + aksi Telegram | "bila pintu terbuka → foto → kirim ke Telegram" |
| Housekeeping + timer cleanup | menghapus gambar lama (retensi) |
| Board di device (tahap 26) | board baru `ESP32-CAM` untuk template dan OTA |

## 3. Yang perlu ditambahkan di server

### 3.1 Model data

| Model | Kolom utama | Catatan |
|---|---|---|
| `Camera` | device (FK, satu device boleh punya beberapa kamera), name, kind (`ESP32CAM` / `IPCAM`), snapshot_interval, retention_days, stream_url (IP camera, terenkripsi), stream_path (nama jalur di server media), is_enabled | IP camera tanpa ESP32 tetap dicatat sebagai device bertipe "camera", supaya status online, lokasi, berbagi dan log aktivitas langsung berlaku |
| `Snapshot` | camera, ts, file, width, height, size, source (`PERIODIC` / `COMMAND` / `RULE` / `MANUAL`), rule (FK opsional), alarm (FK opsional) | file di `private_media/camera/<device>/<yyyy>/<mm>/`; indeks (camera, ts) |
| (opsional) `Recording` | camera, start, end, file | hanya bila perekaman video diperlukan (tahap 27c) |

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

### 3.5 Live stream (tahap 27b)

Browser tidak bisa memutar RTSP, dan kamera umumnya berada di jaringan lokal (di balik NAT),
sehingga server tidak bisa menariknya langsung. Rancangan:

```
IP camera ──RTSP──► [gateway lokal: MediaMTX / go2rtc] ──RTSP/SRT/RTMP (keluar)──┐
ESP32-CAM ──MJPEG/JPEG frames──────────────────────────────────────────────────┤
                                                                                ▼
                                   VPS: MediaMTX (server media) ──WebRTC / HLS──► browser
                                        │  auth HTTP ke Django: boleh publish? boleh read?
                                        ▼
                                   Django (/api/v1/media-auth)
```

- **Server media**: MediaMTX (satu biner, menerima RTSP/RTMP/SRT/WebRTC dan menyajikannya sebagai
  WebRTC (latensi < 1 s) atau HLS (lebih kompatibel, latensi beberapa detik)). Dijalankan sebagai
  layanan systemd `iot-media`, di belakang nginx untuk HLS/WebRTC-HTTP; port UDP WebRTC dibuka di firewall.
- **Otorisasi**: MediaMTX memanggil endpoint Django untuk setiap publish/read. Publish diizinkan bila
  token device cocok dengan jalurnya; read diizinkan bila pengguna (cookie sesi / token sekali
  pakai di URL player) berhak melihat device itu.
- **IP camera**:
  - di jaringan yang sama dengan server: MediaMTX menarik RTSP langsung (`source: rtsp://...`);
  - di balik NAT (umumnya): **gateway lokal** (mini PC / Raspberry Pi / PC lab) menjalankan
    MediaMTX atau go2rtc yang mendorong stream keluar ke server. Platform menyediakan file
    konfigurasi gateway yang bisa diunduh (seperti template firmware).
- **ESP32-CAM**: MJPEG tidak efisien untuk diteruskan. Pilihan bertahap:
  1. *mode foto cepat*: ESP32-CAM mengirim JPEG 1–2 fps lewat WebSocket device yang sama
     dengan snapshot; server meneruskannya ke penonton halaman (cukup untuk pemantauan);
  2. bila butuh video sungguhan: ESP32-CAM di LAN di-*pull* oleh gateway lokal (go2rtc bisa
     mengubah MJPEG menjadi stream yang dapat diteruskan).
- **Hemat**: stream hanya berjalan saat ada yang menonton (command `stream_start` ketika
  halaman dibuka, `stream_stop` saat ditutup atau tidak ada penonton N detik).

### 3.6 Perekaman (tahap 27c, opsional)

MediaMTX dapat merekam per segmen; perlu kuota disk, retensi, dan halaman pemutaran. Ditunda
sampai kebutuhan nyata, karena biaya disk dan bandwidth VPS paling besar di bagian ini.

### 3.7 Pengaturan `.env`

`CAMERA_ENABLED`, `CAMERA_MAX_SNAPSHOT_KB`, `CAMERA_RETENTION_DAYS` (bawaan per kamera),
`CAMERA_MIN_INTERVAL_S`, `MEDIA_SERVER_URL` / `MEDIA_SERVER_PUBLIC_URL`, `MEDIA_SERVER_AUTH_SECRET`,
`CAMERA_STORAGE_QUOTA_MB` per pengguna.

## 4. Sisi device

### 4.1 Template firmware ESP32-CAM (AI-Thinker)

- Library `esp32-camera` (bawaan paket board "esp32"); board *AI Thinker ESP32-CAM*, PSRAM aktif.
- Fitur template MQTT yang sudah ada (telemetri, desired, command, status, OTA, laporan MAC) +:
  - command `capture` → ambil JPEG → `POST /snapshot` → jawab `{"success": true, "size": ...}`;
  - desired `snapshot_interval`, `framesize` (QVGA … UXGA), `quality` (10–63), `flash` (LED GPIO4);
  - command `stream_start` / `stream_stop` (tahap 27b);
  - telemetri: `rssi`, `free_psram`, `snapshot_count`.
- Board baru di platform: **ESP32-CAM** (`esp32cam`), template unduhan sendiri seperti ESP32-C3 OLED.
- Catatan perangkat keras: ESP32-CAM tidak punya USB (perlu adaptor USB-TTL), arus puncak saat
  Wi-Fi + kamera + flash besar (catu daya 5 V ≥ 1 A), dan flash 4 MB tetap cukup untuk OTA.

### 4.2 IP camera

- Tidak ada firmware: cukup **URL RTSP** (atau URL snapshot JPEG / ONVIF) dan kredensialnya.
- Snapshot berkala untuk IP camera diambil oleh gateway lokal atau MediaMTX (bila terjangkau),
  lalu disimpan sebagai `Snapshot` biasa.
- Kredensial kamera disimpan terenkripsi (kunci di `.env`) dan tidak pernah dikirim ke browser.

## 5. Risiko dan penanganannya

| Risiko | Penanganan |
|---|---|
| Disk VPS penuh oleh gambar | retensi per kamera, kuota per pengguna, cleanup harian (timer yang ada), ukuran maks per gambar |
| Bandwidth VPS habis oleh stream | stream hanya saat ditonton, batas penonton/kamera, resolusi bawaan rendah, HLS untuk banyak penonton |
| Privasi (kamera merekam orang) | gambar hanya untuk pemilik dan yang diberi akses; URL bertoken kedaluwarsa; log aktivitas setiap lihat/unduh live; pemberitahuan di dokumentasi |
| Kamera di balik NAT | gateway lokal yang mendorong keluar (tidak perlu port forwarding) |
| Daphne terbebani file besar | file disajikan nginx (`X-Accel-Redirect`), unggahan dibatasi ukuran dan laju |
| Token device bocor → orang lain mengunggah gambar | validasi JPEG, laju terbatas, regenerate token (fitur yang ada) |
| ESP32-CAM tidak stabil (brownout, reset) | catu daya cukup, watchdog, laporan `reset_reason` di telemetri |

## 6. Tahapan pengerjaan

### Tahap 27a: Snapshot (prioritas)

1. Model `Camera`, `Snapshot`, migrasi; board `esp32cam`.
2. Endpoint `POST /api/v1/<token>/snapshot`, validasi, thumbnail, penyimpanan privat; lokasi nginx khusus.
3. Kartu Camera di halaman device + galeri, tombol *Capture now*; widget dashboard; kabar WebSocket.
4. Template firmware ESP32-CAM (unduh dari halaman Connect) dengan `capture` dan snapshot berkala.
5. Retensi + cleanup + kuota; log aktivitas (snapshot diterima, dihapus).
6. Aksi aturan *Capture snapshot*, snapshot di alarm, Telegram `sendPhoto`.
7. Tes (unit + unggahan nyata dari ESP32-CAM), dokumentasi pengguna "Cameras".

### Tahap 27b: Live stream

1. MediaMTX di VPS (`iot-media.service`), konfigurasi nginx, firewall UDP WebRTC.
2. Endpoint otorisasi Django untuk publish/read; jalur per kamera.
3. Player di halaman device/dashboard (WebRTC, cadangan HLS); mulai/berhenti otomatis.
4. IP camera: form URL RTSP; template konfigurasi gateway lokal (MediaMTX/go2rtc) yang bisa diunduh.
5. ESP32-CAM: mode foto cepat (JPEG 1–2 fps lewat WebSocket device).

### Tahap 27c: Perekaman dan analitik (opsional)

Perekaman per segmen, pemutaran, timelapse MP4; deteksi gerak / objek di gateway atau server
sebagai sumber kejadian untuk aturan.

## 7. Catatan pengujian

- Unggahan: JPEG valid/tidak valid, terlalu besar, terlalu sering, token salah, device nonaktif.
- Hak akses: Viewer melihat, Operator memotret, pengguna lain 404; URL bertoken kedaluwarsa.
- Retensi: gambar lebih tua dari batas terhapus beserta file-nya.
- Beban: 10 kamera × 1 gambar/menit selama 1 hari (ukuran disk, waktu respons).
- Stream: latensi WebRTC vs HLS, jumlah penonton bersamaan, putus-sambung gateway.

## 8. Keputusan yang perlu diambil

1. Cakupan awal: snapshot saja (27a) atau langsung live stream (27b)?
2. Jenis IP camera yang dipakai (merek, RTSP/ONVIF) dan apakah ada perangkat di lab yang bisa
   menjadi gateway lokal.
3. Retensi bawaan dan kuota disk per pengguna (dipengaruhi ukuran disk VPS).
4. Apakah perekaman video memang dibutuhkan, atau cukup snapshot + live.

## 9. Referensi

- MediaMTX (server media RTSP/RTMP/SRT/WebRTC/HLS, autentikasi HTTP eksternal): https://github.com/bluenviron/mediamtx
- esp32-camera (driver kamera ESP32, OV2640 dll.): https://components.espressif.com/components/espressif/esp32-camera
