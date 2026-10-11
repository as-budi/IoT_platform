# Rencana Tahap 6 — Penambahan Protokol CoAP

Dokumen rencana sebelum implementasi. Belum ada kode yang diubah.

## 1. Gambaran besar

CoAP menjadi **jalur protokol ketiga**, setara dengan HTTP dan MQTT. Inti platform
(validasi payload, penyimpanan, desired state, command, WebSocket) tidak berubah; yang
ditambah hanya "adapter" CoAP.

```
ESP32 ──HTTP POST──► runserver ────────┐
ESP32 ──MQTT──► broker ──► mqtt_bridge ─┼──► api/payload + api/services ──► MySQL ──► WebSocket ──► dashboard
ESP32 ──CoAP (UDP 5683)──► coap_server ─┘             (logika yang sama)
```

Perbedaan penting dengan MQTT: **CoAP tidak memakai broker**. Device berbicara langsung
ke server kita lewat UDP, sama seperti HTTP. Konsekuensinya, ESP32 harus bisa menjangkau
Mac, yaitu satu jaringan Wi-Fi dan port UDP 5683 diizinkan firewall macOS.

## 2. Perubahan mekanisme

### a. Proses baru: `python manage.py coap_server`

- Memakai pustaka **aiocoap**, pustaka CoAP murni Python berbasis asyncio.
- Berjalan sebagai proses terpisah, seperti `mqtt_bridge`, karena `runserver` (Daphne)
  hanya melayani HTTP dan WebSocket, bukan UDP.
- Pengaturan di `.env`:
  - `COAP_BIND=0.0.0.0` dan `COAP_PORT=5683`: alamat yang didengarkan.
  - `COAP_PUBLIC_HOST`: alamat yang ditampilkan di halaman device, misalnya IP Mac di
    jaringan Wi-Fi.
- ORM Django bersifat sinkron, sedangkan aiocoap asinkron. Setiap akses database
  dibungkus `sync_to_async`, dengan `close_old_connections()` seperti di bridge MQTT.

### b. Resource (alamat) CoAP

Strukturnya meniru API HTTP (dan pola ThingsBoard), supaya mudah dibandingkan:

| Metode | URI | Fungsi | Balasan |
|---|---|---|---|
| POST | `/api/v1/<token>/telemetry` | kirim telemetri; payload JSON sama persis dengan HTTP/MQTT | `2.04 Changed` |
| GET + **Observe** | `/api/v1/<token>/desired` | desired state semua aktuator; server **mendorong** perubahan | `2.05 Content` |
| GET + **Observe** | `/api/v1/<token>/commands` | command yang menunggu; command baru didorong ke device | `2.05 Content` |
| POST | `/api/v1/<token>/commands/<id>/response` | hasil command `{"success": true, ...}` | `2.04 Changed` |

Kode kesalahan dipetakan dari HTTP: `4.00 Bad Request` (payload salah),
`4.01 Unauthorized` (token salah), `4.03 Forbidden` (device nonaktif), `4.04 Not Found`,
dan `4.05 Method Not Allowed`. Berbeda dengan MQTT, device **langsung menerima alasan
penolakan** di balasan, jadi tidak perlu topik `errors`.

### c. Observe: cara CoAP "mendorong" data ke device

Fitur Observe (RFC 7641) adalah pembeda utama CoAP dari HTTP biasa:

1. ESP32 mengirim satu kali `GET /desired` dengan opsi *Observe*.
2. Server membalas keadaan sekarang, lalu **mengingat** device itu sebagai pengamat.
3. Setiap kali tombol ON di dashboard ditekan, server mengirim notifikasi baru ke ESP32
   tanpa diminta.

Supaya `coap_server` tahu ada perubahan, ia bergabung ke group `device-<id>` di **Redis**,
sama seperti consumer WebSocket. Kejadian `desired` dan `command` sudah dipancarkan sejak
tahap 4, jadi tidak perlu mekanisme baru. Sebagai cadangan, server juga memeriksa database
setiap beberapa detik, seperti pola outbox MQTT.

Untuk command, aturannya tetap sama:

- status menjadi **SENT** setelah command dikirim ke device,
- menjadi **SUCCEEDED/FAILED** setelah device mengirim hasil,
- command yang kedaluwarsa tidak pernah dikirim.

### d. Logika bersama dirapikan

Logika "ambil desired state device", "ambil command yang menunggu", dan "catat hasil
command" saat ini ada di dalam `mqtt_bridge`. Logika itu akan dipindah ke satu modul
bersama (`devices/control.py`). Dengan begitu MQTT dan CoAP memakai kode yang sama, dan
perilakunya pasti identik. Tes MQTT yang sudah ada menjaga agar pemindahan ini tidak
mengubah apa pun.

### e. Keamanan

Tahap ini memakai **CoAP tanpa enkripsi** (`coap://`), setara dengan HTTP tanpa HTTPS.
Ini cukup untuk jaringan lokal saat belajar. Versi aman `coaps://` memakai **DTLS**
(port 5684, biasanya dengan *pre-shared key* per device). Dukungan DTLS di sisi server
aiocoap masih terbatas, jadi dicatat sebagai langkah lanjutan di dokumentasi, bukan
target tahap ini.

## 3. Perubahan tampilan (UI)

**Halaman device, kartu "Connect a device"** sudah panjang (HTTP + MQTT). Kartu ini akan
diubah menjadi **tab: HTTP | MQTT | CoAP**. Tab CoAP berisi:

- alamat server `coap://<COAP_PUBLIC_HOST>:5683`,
- tabel resource seperti di atas, lengkap dengan token device itu,
- contoh perintah siap salin, misalnya:

  ```bash
  coap-client -m post -t json -e '{"temperature": 25.1}' coap://192.168.1.10/api/v1/TOKEN/telemetry
  coap-client -s 60 coap://192.168.1.10/api/v1/TOKEN/desired     # observe 60 detik
  ```

  (`coap-client` dari libcoap, `brew install libcoap`; atau `aiocoap-client` yang ikut
  terpasang bersama aiocoap.)

**Penanda protokol (usulan, butuh migrasi kecil):**

- Device mendapat dua kolom baru, `last_seen_at` dan `last_protocol` (HTTP/MQTT/CoAP).
- Hasilnya tampil sebagai lencana kecil, misalnya **"via CoAP · 3 s ago"**, di daftar
  device, halaman device, dan header device di dashboard.
- Ini berguna saat menguji tiga protokol bergantian, dan menjadi dasar status
  Online/Offline yang lebih akurat untuk rule engine nanti.

Selain dua hal itu, dashboard dan halaman lain tidak berubah. Data dari CoAP otomatis
tampil seketika karena tetap lewat `save_telemetry()`, sehingga WebSocket ikut bekerja.

## 4. File yang akan ditambah atau diubah

| Baru | Isi |
|---|---|
| app `coap_gateway/` | `resources.py` (resource CoAP), `management/commands/coap_server.py`, `tests.py` |
| `devices/control.py` | logika desired/command bersama MQTT & CoAP |
| `scripts/fake_coap_device.py` | device tiruan: kirim telemetri, observe desired & command, kirim hasil |
| `firmware/esp32_coap/esp32_coap.ino` | contoh ESP32 (pustaka *CoAP simple library*) |
| `docs/tahap-06-coap.md` | konsep: UDP, CON/NON/ACK, kode respons, Observe, perbandingan dengan HTTP/MQTT |

| Diubah | Perubahan |
|---|---|
| `config/settings.py`, `.env`, `.env.example`, `requirements.txt` | pengaturan `COAP_*`, paket `aiocoap` |
| `mqtt_bridge/handlers.py`, `outbox.py` | memakai `devices/control.py` |
| `devices/models.py` + migrasi | `last_seen_at`, `last_protocol` (bila disetujui) |
| `api/services.py` | mencatat protokol pengirim |
| `device_detail.html` | kartu Connect menjadi tab, tab CoAP baru |
| daftar device, dashboard | lencana protokol |

Cara menjalankan nanti cukup satu terminal tambahan, `python manage.py coap_server`, di
samping `runserver` dan `mqtt_bridge`.

## 5. Catatan tentang ESP32

Pustaka CoAP yang paling umum untuk Arduino mendukung GET/POST, tetapi dukungan
Observe-nya terbatas. Karena itu contoh firmware akan memakai GET berkala untuk desired
state dan command. Observe penuh tetap bisa dicoba dan didemokan dengan
`fake_coap_device.py` dan `coap-client`.

## 6. Keputusan yang perlu dikonfirmasi

- Penambahan penanda protokol (`last_seen_at`, `last_protocol`) yang memerlukan migrasi.
- Urutan tahap: CoAP menjadi tahap 6, rule engine menjadi tahap 7.
