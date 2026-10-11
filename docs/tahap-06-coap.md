# Tahap 6 — Komunikasi device lewat CoAP

CoAP (*Constrained Application Protocol*, RFC 7252) adalah protokol ketiga di platform ini,
di samping HTTP dan MQTT. Rencana awalnya ada di [rencana-tahap-06-coap.md](rencana-tahap-06-coap.md).

```
ESP32 ──HTTP POST──► runserver ────────┐
ESP32 ──MQTT──► broker ──► mqtt_bridge ─┼──► api/payload + api/services ──► MySQL ──► WebSocket ──► dashboard
ESP32 ──CoAP (UDP 5683)──► coap_server ─┘             (logika yang sama)
```

## 1. CoAP dalam satu halaman

| | HTTP | CoAP |
|---|---|---|
| Transport | TCP | **UDP** |
| Ukuran header | ratusan byte (teks) | **4 byte** (biner) |
| Metode | GET, POST, PUT, DELETE | sama |
| Alamat | `http://host/path` | `coap://host:5683/path` |
| Kode respons | 200, 400, 404 | 2.05, 4.00, 4.04 (kelas.detail) |
| Server mengirim tanpa diminta | tidak | **ya, dengan Observe** |

**Jenis pesan.** Karena UDP tidak menjamin paket sampai, CoAP punya mekanismenya sendiri:

- **CON** (*confirmable*): harus dibalas **ACK**; bila tidak, pengirim mengulang dengan jeda
  yang makin panjang.
- **NON** (*non-confirmable*): kirim lalu lupakan, untuk data yang boleh hilang.
- **ACK**: tanda terima. Balasan biasanya "menumpang" di ACK (*piggybacked*).
- **RST**: penolakan, misalnya penerima tidak lagi mengenali pesan itu.

**Message ID** mencocokkan CON dengan ACK-nya; **token** mencocokkan permintaan dengan
jawabannya. Firmware ESP32 di tahap ini memakai message ID untuk mengenali balasan GET.

**Observe (RFC 7641).** Klien mengirim `GET` dengan opsi *Observe*. Server membalas isi
sekarang dan mengingat klien itu. Setiap kali isinya berubah, server mengirim jawaban baru
tanpa diminta. Inilah cara CoAP "mendorong" data ke device, peran yang di MQTT dipegang
oleh subscribe.

## 2. Resource di platform ini

Semua alamat diawali `coap://<server>:5683/api/v1/<access token>/`:

| Metode | Resource | Fungsi | Balasan |
|---|---|---|---|
| POST | `telemetry` | telemetri; JSON sama persis dengan HTTP/MQTT | `2.04 Changed` `{"saved": 2, "new_variables": [...]}` |
| GET (+Observe) | `desired` | desired state semua aktuator | `2.05 Content` `{"pump": 1}` |
| GET (+Observe) | `commands` | command yang masih berjalan | `2.05 Content` `{"commands": [{"id", "method", "params"}]}` |
| POST | `commands/<id>/response` | hasil command | `2.04 Changed` `{"id": 12, "status": "SUCCEEDED"}` |

Kesalahan: `4.00 Bad Request` (payload salah, disertai alasannya), `4.01 Unauthorized`
(token salah), `4.03 Forbidden` (device nonaktif), `4.04 Not Found`, `4.05 Method Not Allowed`.
Berbeda dengan MQTT, device **langsung** menerima alasan penolakan di balasan.

**Command.** Daftar `commands` berisi command berstatus QUEUED **dan** SENT. Saat dikirim,
QUEUED menjadi SENT; command tetap ikut dikirim sampai device melaporkan hasilnya. Jadi
device yang tersambung ulang tidak kehilangan command, tetapi device **wajib mengingat id**
yang sudah dijalankan supaya tidak menjalankannya dua kali (lihat `fake_coap_device.py`).
Command kedaluwarsa tidak pernah dikirim.

## 3. Struktur kode

| File | Isi |
|---|---|
| `coap_gateway/handlers.py` | aturan setiap permintaan, **tanpa** aiocoap: `handle(metode, path, payload) -> Reply` |
| `coap_gateway/resources.py` | penerjemah ke aiocoap; satu `ApiResource` untuk semua alamat `/api/v1/...`; pengamat Observe |
| `coap_gateway/management/commands/coap_server.py` | perintah `python manage.py coap_server` |
| `devices/control.py` | logika desired state & command yang **dipakai bersama** MQTT dan CoAP |
| `scripts/fake_coap_device.py` | device tiruan dengan Observe penuh |
| `firmware/esp32_coap/esp32_coap.ino` | contoh ESP32 (GET berkala) |

**Mengapa proses terpisah?** Daphne (`runserver`) melayani HTTP dan WebSocket di atas TCP.
CoAP berjalan di atas UDP, jadi dijalankan sebagai proses tersendiri, seperti `mqtt_bridge`.

**asyncio dan ORM Django.** aiocoap berbasis asyncio, sedangkan ORM Django sinkron. Setiap
akses database dibungkus `sync_to_async` (fungsi `run_db()` di `resources.py`), ditambah
`close_old_connections()` karena server berjalan lama.

**Bagaimana server tahu harus mengirim kabar Observe?** Tugas latar `ApiResource.watch()`
memeriksa database setiap `COAP_OBSERVE_INTERVAL` detik (bawaan 1), **hanya** untuk device
yang sedang diamati. Ringkasan isi (`observe_signature()`) dibandingkan dengan yang terakhir:
untuk desired state berupa versi tiap aktuator, untuk command berupa daftar id yang masih
berjalan. Bila berbeda, pengamat dipicu dan aiocoap mengirim isi terbaru ke device.

**Protokol terakhir.** `Device.last_protocol` dan `Device.last_seen_at` (migrasi `0015`)
diisi oleh `save_telemetry()`. Halaman device, daftar device, dan dashboard menampilkan
lencana "via HTTP / MQTT / CoAP", dan lencana itu ikut diperbarui lewat WebSocket.

## 4. Menjalankan

```bash
conda activate iot_platform
pip install aiocoap               # sekali saja (ada di requirements.txt)
python manage.py migrate          # kolom last_protocol & last_seen_at
python manage.py coap_server      # terminal terpisah
python scripts/fake_coap_device.py TOKEN     # device tiruan
```

Pengaturan di `.env`:

```
COAP_BIND=                 # kosong = otomatis (lihat di bawah)
COAP_PORT=5683
COAP_PUBLIC_HOST=192.168.1.10   # IP Mac di Wi-Fi; dipakai di halaman device & oleh ESP32
COAP_OBSERVE_INTERVAL=1
```

**Alamat yang didengarkan.** aiocoap memakai *transport* UDP yang berbeda per sistem operasi.
Di Linux (`udp6`) server bisa mendengarkan semua alamat sekaligus. Di macOS dan Windows
(`simplesocketserver`) server harus diberi IP yang jelas, jadi `coap_server` membuka satu
server untuk `127.0.0.1` dan satu untuk IP Mac di jaringan (diambil dari `COAP_PUBLIC_HOST`,
atau dideteksi otomatis). Baris pertama keluaran `coap_server` menunjukkan alamat mana saja
yang didengarkan; bila IP Mac berganti (pindah Wi-Fi), jalankan ulang `coap_server`.

Halaman device kini punya tab **HTTP | MQTT | CoAP**. Tab CoAP berisi alamat lengkap dan
contoh perintah `coap-client` (dari libcoap, `brew install libcoap`):

```bash
coap-client -m post -t json -e '{"temperature": 25.1}' coap://192.168.1.10:5683/api/v1/TOKEN/telemetry
coap-client -m get -s 60 coap://192.168.1.10:5683/api/v1/TOKEN/desired
```

Saat `coap-client -s 60` berjalan, tekan ON/OFF di dashboard: keadaan baru muncul di
terminal dalam ±1 detik. Itulah Observe.

## 5. Jaringan: kapan CoAP bisa dipakai

CoAP **tidak memakai broker**. Device harus bisa menjangkau server secara langsung:

- **Satu jaringan (lab/rumah):** cukup isi `COAP_PUBLIC_HOST` dengan IP Mac di Wi-Fi dan
  izinkan koneksi masuk untuk Python di firewall macOS.
- **Beda jaringan:** server harus punya alamat yang bisa dijangkau dari internet (VPS/cloud
  dengan port UDP 5683 terbuka, atau *port forwarding* di router). Tunnel seperti ngrok
  umumnya tidak mendukung UDP.
- **NAT dan UDP:** router melupakan "jalur" UDP setelah puluhan detik sampai beberapa menit
  tanpa lalu lintas. Kabar Observe dari server lalu dibuang diam-diam. Karena itu device
  harus mengirim sesuatu secara berkala (di sini: telemetri tiap 10 detik) dan mendaftar
  ulang Observe bila notifikasi berhenti datang. `fake_coap_device.py` melakukan keduanya.

Untuk device di lokasi lain tanpa server publik, MQTT lewat broker tetap jalur paling praktis.

## 6. Keamanan

Tahap ini memakai `coap://` tanpa enkripsi, setara HTTP tanpa HTTPS: token dan data bisa
dibaca siapa pun di jaringan yang sama. Versi aman `coaps://` memakai **DTLS** (port 5684,
biasanya *pre-shared key* per device). Ini wajib sebelum CoAP dipakai lewat internet, dan
menjadi langkah lanjutan.

## Latihan

1. Jalankan `fake_device.py` (HTTP), `fake_mqtt_device.py`, dan `fake_coap_device.py` untuk
   tiga device berbeda sekaligus. Amati lencana "via ..." di dashboard.
2. Hentikan `coap_server` saat `fake_coap_device.py` berjalan, lalu jalankan lagi. Berapa
   lama sampai Observe tersambung ulang?
3. Ubah `COAP_OBSERVE_INTERVAL` menjadi 5, lalu ukur jeda antara menekan ON dan reaksi device.
4. Tambahkan resource `GET <token>/time` yang mengembalikan waktu server dalam milidetik,
   supaya ESP32 tanpa NTP bisa mengisi `ts` telemetri dengan benar.
