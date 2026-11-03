# Tahap 24: MAC address device dan panduan "How to connect real devices"

Dua tambahan yang saling terkait:

1. Selain versi firmware, ESP32 kini melaporkan **MAC address** Wi-Fi-nya. Platform menyimpannya
   di device dan menampilkannya di kartu **Firmware** halaman device.
2. Menu baru **Guides → How to connect real devices** (`/devices/connect/`): panduan langkah demi
   langkah untuk ESP32 lewat HTTP, MQTT, atau CoAP, penjelasan sertifikat, dan tombol unduh
   **template firmware** yang sudah berisi alamat server dan access token device yang dipilih.

> **Diperbarui di tahap 25.** Halaman panduan ini kini menjadi bab **"Connect an ESP32"** di
> dokumentasi pengguna (`/docs/connect-esp32/`, app `userdocs`); bagian sertifikatnya menjadi bab
> **"Security and certificates"**. `/devices/connect/` dialihkan ke sana, `ConnectGuideView` dan
> `devices/connect_guide.html` dihapus. Unduhan template (`ConnectFirmwareView`, `RootCaView`,
> `devices/connect.py`) tetap di app `devices`. Komentar ketiga sketch kini berbahasa Inggris untuk
> pengguna; catatan pengembangnya dipindah ke tahap 02, 03, 06, dan 09. Lihat
> [tahap-25-dokumentasi-pengguna.md](tahap-25-dokumentasi-pengguna.md).

## 1. MAC address

### Payload

MAC ikut di laporan firmware yang sudah ada sejak tahap 9, lewat ketiga protokol:

```json
{"version": "1.0.0", "board": "esp32", "mac": "24:6F:28:AA:BB:CC"}
```

| Protokol | Tujuan |
|---|---|
| HTTP | `POST /api/v1/<token>/firmware` |
| MQTT | topik `<prefix>/<token>/firmware` |
| CoAP | `POST coap://<host>:5683/api/v1/<token>/firmware` |

Di ESP32 nilainya dari `WiFi.macAddress()`, dipanggil setelah Wi-Fi menyala.

### Penyimpanan

`devices/models.py`, migrasi `0021_device_mac_address`:

```python
mac_address = models.CharField("MAC address", max_length=17, blank=True, editable=False)
mac_reported_at = models.DateTimeField("MAC reported", null=True, blank=True, editable=False)
```

`editable=False`: sama seperti `firmware_version`, nilainya **dilaporkan device**, bukan diketik
pengguna. Hanya alat fisiknya yang tahu MAC-nya sendiri.

### Aturan (ota/services.py)

- `normalize_mac()` menerima `24:6F:28:AA:BB:CC`, `24-6f-28-aa-bb-cc`, `246f.28aa.bbcc`, atau
  `246F28AABBCC`, dan menyimpannya seragam sebagai `24:6F:28:AA:BB:CC`. Bentuk seragam membuat
  MAC mudah dicari dan dibandingkan.
- MAC yang salah (bukan 12 digit heksadesimal, atau semuanya nol) membuat **seluruh laporan
  ditolak** (HTTP 400, pesan di topik `errors` untuk MQTT, 4.00 untuk CoAP). MAC diperiksa sebelum
  apa pun disimpan, supaya laporan tidak tersimpan setengah.
- Laporan **tanpa** `mac` tetap diterima. Firmware lama, dan device yang sedang di tengah
  pembaruan OTA, tidak boleh tiba-tiba ditolak: laporan versi itulah yang menandai pembaruan
  `UPDATED`. Device seperti itu tampil "unknown" sampai firmware-nya diganti.
- `{"mac": "..."}` saja juga boleh.

Untuk apa MAC? Untuk mengenali **alat fisik mana** yang sedang memakai token ini, mis. saat
mencari board di daftar DHCP router, atau saat curiga sebuah token disalin ke board lain (MAC
di halaman tiba-tiba berubah).

Yang ikut diperbarui:

| File | Perubahan |
|---|---|
| `firmware/esp32_mqtt` | `reportFirmwareVersion()` menambah `doc["mac"] = WiFi.macAddress()` |
| `firmware/esp32_telemetry` (HTTP) | **baru**: melapor versi, board, dan MAC ke `/firmware` saat menyala, diulang sampai server menjawab 200 |
| `firmware/esp32_coap` | **baru**: melapor versi, board, dan MAC saat menyala dan setiap 10 menit (UDP bisa hilang); `SERVER_HOST` kini teks (IP atau nama domain) |
| `scripts/fake_mqtt_device.py` | MAC tiruan yang tetap untuk satu token, berawalan `02:` (*locally administered*) |
| `api/v2.py` | field `mac_address` di `/api/v2/me/devices` |
| kartu Firmware, admin | kolom MAC address |

## 2. Halaman "How to connect real devices"

`devices/views.py`: `ConnectFirmwareView`, `RootCaView` (dan, sampai tahap 24, `ConnectGuideView`).
`devices/connect.py`: logika template firmware. Panduannya sejak tahap 25:
`userdocs/templates/userdocs/pages/connect-esp32.html`.

| URL | Isi |
|---|---|
| `/devices/connect/[?device=<id>]` | sejak tahap 25 dialihkan ke `/docs/connect-esp32/[?device=<id>]`; dengan `device`, semua alamat dan topik memuat token device itu |
| `/devices/connect/firmware/<http\|mqtt\|coap>.zip[?device=<id>]` | template firmware siap buka di Arduino IDE |
| `/devices/connect/isrg-root-x1.pem` | sertifikat root CA untuk TLS |

Isi halaman: ringkasan langkah, persiapan Arduino IDE (paket board, partition scheme untuk OTA,
driver USB, pustaka per protokol), perbandingan protokol, rincian per protokol beserta tombol
unduh, sertifikat, MAC address, dan tabel masalah umum.

### Template firmware dibuat dari sketch yang sama

Template **tidak** ditulis ulang di Python. Sumbernya tetap `firmware/<sketch>/<sketch>.ino`.
Saat tombol unduh ditekan, `render_sketch()` membaca sketch itu dan mengganti baris pengaturan:

| Protokol | Baris yang diisi | Dari |
|---|---|---|
| HTTP | `SERVER` | `OTA_BASE_URL` (.env), atau alamat di address bar |
| MQTT | `MQTT_HOST`, `MQTT_PORT`, `TOPIC_PREFIX`, `USE_TLS` | `MQTT_PUBLIC_HOST/PORT/TLS`, `MQTT_TOPIC_PREFIX` |
| CoAP | `SERVER_HOST`, `SERVER_PORT` | `COAP_PUBLIC_HOST` (atau address bar), `COAP_PORT` |
| semua | `ACCESS_TOKEN` | device yang dipilih |
| HTTP, MQTT | `ROOT_CA` | `firmware/certs/isrg-root-x1.pem` |

Setiap baris harus cocok **tepat satu kali**. Bila sketch diubah sampai barisnya tidak dikenali,
unduhan gagal dengan pesan jelas (`TemplateMismatch`) dan tesnya ikut gagal, bukan diam-diam
menghasilkan sketch yang keliru. Jadi bila Anda menyunting sketch, pertahankan bentuk baris
pengaturan di atas.

Isi file .zip:

```
esp32-mqtt-greenhouse-node/
├── esp32_mqtt/esp32_mqtt.ino     <- nama folder = nama .ino (syarat Arduino IDE)
├── README.txt                    <- nilai yang sudah diisi dan langkah berikutnya
└── isrg-root-x1.pem              <- salinan root CA (sudah tertanam di sketch)
```

Yang tetap diisi sendiri: nama dan password Wi-Fi. Keduanya sengaja tidak diminta di halaman,
supaya password Wi-Fi tidak pernah terkirim ke server.

### Hak akses

Access token hanya untuk **Editor ke atas** (aturan tahap 19, sama dengan bagian "Connect a
device"). Daftar pilihan device memakai `visible_devices(user, access.EDITOR)`. Unduhan dengan
`?device=` milik orang lain, atau device yang hanya dibagikan sebagai Viewer/Operator, dijawab
404 supaya token tidak bocor.

## 3. Sertifikat

ESP32 **tidak** butuh sertifikat miliknya sendiri. Ia butuh **root CA** yang menandatangani
sertifikat server, untuk memastikan ia berbicara dengan server yang benar. Setelah koneksi
terenkripsi terbentuk, device login dengan access token.

| Keadaan | Sertifikat yang dibutuhkan device |
|---|---|
| Belajar di jaringan lokal (`http://`, MQTT 1883) | tidak ada |
| Server produksi dengan Let's Encrypt (`docs/deploy-vps.md`) | ISRG Root X1 (berlaku sampai 4 Juni 2035), sudah tertanam di template |
| Server lain | root CA penerbit sertifikat server: cek dengan `openssl s_client -connect host:8883 -showcerts` |

Sidik jari SHA-256 ISRG Root X1:
`96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6`.

Sertifikat **server** (untuk administrator) diambil dengan `certbot` dan diperbarui otomatis;
lihat `docs/deploy-vps.md`. Device tidak terpengaruh perpanjangan selama root CA-nya sama.

Belum didukung: sertifikat klien per device (X.509 / mutual TLS) dan CoAP lewat DTLS (`coaps://`).

## 4. Tes

```bash
python manage.py test devices.tests_connect ota userdocs
```

- `devices/tests_connect.py`: pengalihan alamat lama, isi .zip per protokol, token dan
  placeholder, hak akses (pemilik, Viewer, Editor, belum login), root CA. Tes halaman panduan dan
  peringatan alamat 127.0.0.1 pindah ke `userdocs/tests.py` (tahap 25).
- `ota/tests.py`: normalisasi MAC, MAC salah menolak seluruh laporan, laporan tanpa MAC tetap
  diterima, MAC lewat HTTP, MQTT, dan CoAP, MAC tampil di halaman device.
