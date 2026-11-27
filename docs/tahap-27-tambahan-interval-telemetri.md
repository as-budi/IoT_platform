# Tambahan tahap 27 — Batas interval pengiriman telemetri (minimal 10 detik)

## Masalah
Device praktikum sering mengirim telemetri setiap 1 detik, atau setiap nilai dalam pesan sendiri. Dengan satu kelas,
itu berarti ratusan baris per detik: tabel telemetri cepat membengkak, MySQL sibuk, dan dashboard ikut lambat
(lihat juga [tahap-27-tambahan-versi-data-ringan.md](tahap-27-tambahan-versi-data-ringan.md)).

## Aturan
- **Satu pesan telemetri setiap `TELEMETRY_MIN_INTERVAL_SECONDS` detik per device** (bawaan 10), dihitung untuk
  semua protokol sekaligus (HTTP, MQTT, CoAP). `0` = tanpa batas.
- Satu pesan boleh membawa banyak nilai dan banyak waktu (`[{"ts": ..., "values": {...}}, ...]`). Device yang membaca
  sensor lebih sering dapat mengirim bacaannya sekaligus.
- **Kelonggaran 1 detik** (`INTERVAL_SLACK_SECONDS`): device yang mengirim tepat setiap 10 detik bisa tiba 9,8 detik
  setelah pesan sebelumnya. Tanpa kelonggaran, setiap pesan kedua ditolak dan device itu efektif mengirim setiap 20 detik.
- **Laporan yang hanya berisi variabel aktuator tidak dibatasi**, dan tidak menggeser waktu pesan terakhir. Itu adalah
  *reported state* setelah device menjalankan desired state (template firmware: `reportLamp()` segera setelah `desired`).
  Kalau laporan itu ditolak, dashboard tidak tahu bahwa aktuatornya sudah berubah. Campuran aktuator + sensor tetap dibatasi.
- Telemetri dari platform sendiri (`simulate_devices`, `send_telemetry`, tanpa `protocol`) tidak dibatasi.

| Protokol | Pesan terlalu cepat |
|---|---|
| HTTP | `429 Too Many Requests`, header `Retry-After: <detik>`, body `{"error": "...", "retry_after": 4}` |
| CoAP | `4.29 Too Many Requests` (RFC 8516), body `{"error": "...", "retry_after": 4}` |
| MQTT | tidak disimpan; pesan `{"error": "Telemetry is limited to one message every 10 s per device; try again in 4 s.", "topic": ...}` di `<prefix>/<token>/errors` |

## Cara kerjanya (`api/services.py`)
`save_telemetry()` memanggil `claim_interval()` di **dalam** transaksinya, sebelum apa pun disimpan:
```sql
UPDATE devices_device SET last_telemetry_at = <sekarang>
WHERE id = 7 AND (last_telemetry_at IS NULL OR last_telemetry_at <= <sekarang - 9 s>)
```
- 1 baris berubah: diterima. 0 baris: pesan sebelumnya terlalu baru, dan `TelemetryTooFrequent` dilempar.
- Satu UPDATE bersyarat sudah aman walaupun pesan dari satu device tiba bersamaan di proses yang berbeda (web untuk
  HTTP, `mqtt_bridge`, `coap_server`): MySQL mengunci baris device, jadi pesan kedua melihat waktu yang baru.
- Karena di dalam transaksi, pesan yang **gagal disimpan** (mis. nama kategori salah, `400`) ikut membatalkan catatan
  waktunya, jadi device bisa langsung mengirim ulang pesan yang sudah diperbaiki.
- `TelemetryTooFrequent` adalah turunan `PayloadError`. `mqtt_bridge/handlers.py` tidak perlu diubah: kesalahan
  payload sudah dikirim ke topik `errors`. `api/views.py` dan `coap_gateway/handlers.py` menangkapnya lebih dulu
  untuk kode 429 / 4.29.
- Kolom baru `Device.last_telemetry_at` (migrasi `devices/0028`), terpisah dari `last_seen_at`, karena snapshot kamera
  juga mengisi `last_seen_at`. Kalau keduanya dipakai bersama, unggahan snapshot akan memblokir telemetri.

## Firmware dan contoh
- Template di `firmware/` sudah mengirim setiap 10 detik (`SEND_INTERVAL_MS = 10000`, CoAP setiap 10 detik, HTTP
  `delay(10000)` ditambah waktu request).
- Template ESP32-C3 OLED punya menu *Telemetry* (kirim sekarang, mode Manual). Kalau ditekan kurang dari 10 detik
  setelah pengiriman sebelumnya, pesannya ditolak, dan alasannya muncul di topik `errors`.
- MCU Simulator: contoh `esp8266_nodemcu_mqtt.ino` diubah dari 5 menjadi 10 detik. Contoh lain sudah 10 detik.
- MQTT Client: fitur *Repeat* (bawaan 5 detik) adalah alat umum untuk broker mana pun, jadi tidak dibatasi. Ke broker
  platform, pesan telemetri yang terlalu cepat dijawab di topik `errors`.

## Pengaturan
`.env`: `TELEMETRY_MIN_INTERVAL_SECONDS=10` (lihat `.env.example`, `deploy/env.production.example`). Dokumentasi pengguna
(`/docs/device-api/#interval`, *Troubleshooting*) menampilkan nilai dari pengaturan ini, dan bagian itu hilang bila `0`.

## Tes
- `api.tests.TelemetryIntervalTests`: 429 + `Retry-After`, diterima lagi setelah interval, kelonggaran, per device,
  laporan aktuator, payload gagal tidak memakai jatah, `0` mematikan, simulator platform tidak dibatasi.
- `mqtt_bridge.tests.TelemetryIntervalTests`: ditolak dengan pesan untuk topik `errors`, laporan aktuator tetap diterima.
- `coap_gateway.tests.TelemetryIntervalTests`: `4.29` dengan `retry_after`.
