# Rencana: Pembaruan Firmware Over-the-Air (OTA)

Status: **Tahap 1 (OTA dasar) sudah dikerjakan** pada tahap 9 project, lihat
[tahap-09-ota.md](tahap-09-ota.md). Tahap 2 (rollout) dan Tahap 3 (keamanan lanjutan) masih
rencana. Dokumen ini merangkum diskusi awal tentang penambahan OTA ke platform: kelayakan,
komponen yang dibutuhkan, risiko, dan tahapan pengerjaannya.

## 1. Ringkasan

OTA memungkinkan firmware device (misalnya ESP32) diperbarui dari platform, tanpa mencabut
device dan menyambungkannya ke laptop.

- **Bagian server tidak rumit**, karena sebagian besar fondasinya sudah ada di project. Ukuran
  pekerjaannya kira-kira setara tahap CoAP.
- **Bagian yang menuntut kehati-hatian ada di device.** Firmware yang salah bisa membuat
  device tidak bisa dihubungi lagi dari jauh, jadi mekanisme pengamannya perlu dirancang sejak
  awal.

## 2. Fondasi yang sudah ada

| Yang sudah ada | Peran dalam OTA |
|---|---|
| **Command** (QUEUED → SENT → SUCCEEDED/FAILED), tersampaikan lewat HTTP, MQTT, dan CoAP | perintah OTA cukup satu command baru, misalnya `ota {"url": …, "version": "1.2.0", "md5": …}` |
| **Access token per device** | otentikasi device |
| **WebSocket** (`realtime`) | progres update tampil langsung di halaman device |
| **Rule engine dan alarm** | alarm "update gagal" atau "device tidak kembali online setelah update" |
| **Desired state** | device mengambil ulang keadaan aktuator setelah restart |

## 3. Komponen yang perlu ditambahkan di server

1. **Paket firmware**: upload file `.bin`, nomor versi, jenis board (mis. `esp32`,
   `esp32-s3`), checksum MD5/SHA-256 yang dihitung otomatis, ukuran, catatan rilis, dan
   pengunggah.
2. **Versi firmware setiap device**: device melaporkan versinya setiap kali menyala, disimpan
   di `Device.firmware_version`. Dengan begitu terlihat device mana yang sudah atau belum
   diperbarui.
3. **Endpoint unduhan**: dikerjakan sebagai `GET /api/v1/ota/<token-unduhan>.bin`, memakai token
   unduhan sekali pakai yang kedaluwarsa (lihat bagian 5).
4. **Status update per device**, mengikuti pola ThingsBoard:

   ```
   QUEUED → DOWNLOADING → DOWNLOADED → VERIFIED → UPDATING → UPDATED
                                                            ↘ FAILED (beserta alasannya)
   ```

   Riwayat setiap percobaan update disimpan.
5. **Halaman Firmware**: daftar dan upload paket, tombol *Update firmware* di halaman device,
   progres langsung, lalu update ke banyak device sekaligus per jenis board (tahap 2).

Alur satu update:

```
Pengguna klik "Update firmware"
   → server membuat command "ota" (URL unduhan + versi + checksum)
   → command sampai ke device lewat HTTP / MQTT / CoAP (seperti command lain)
   → device mengunduh .bin lewat HTTP(S), memeriksa checksum, menulis ke partisi cadangan
   → device melaporkan status per langkah, lalu restart
   → firmware baru menyala, tersambung, dan melaporkan versi barunya
   → server menandai UPDATED; bila tidak kembali dalam batas waktu → FAILED + alarm
```

## 4. Sisi device (ESP32)

- Arduino-ESP32 sudah punya pustaka **`HTTPUpdate`**. Pustaka ini mengunduh firmware,
  menulisnya ke partisi cadangan, lalu me-restart device.
- ESP32 memakai **dua partisi aplikasi** (ota_0 dan ota_1). Bila listrik padam di tengah
  unduhan, firmware lama tetap utuh. Pastikan skema partisi di Arduino IDE mendukung OTA
  (misalnya "Default 4MB with spiffs").
- **File firmware selalu diunduh lewat HTTP(S)**, apa pun protokol perintahnya. Mengirim file
  sekitar 1 MB lewat MQTT atau CoAP jauh lebih repot; untuk CoAP butuh *blockwise transfer*.
- Firmware contoh perlu:
  - melaporkan versinya saat menyala,
  - menerima command `ota`,
  - melaporkan status per langkah,
  - menjawab command dengan hasilnya.

## 5. Risiko dan penanganannya

| Risiko | Penanganan |
|---|---|
| Firmware rusak atau terpotong saat diunduh | checksum MD5 dicek sebelum firmware dipakai; `HTTPUpdate` bisa memeriksa header `x-MD5` |
| Firmware menyala tetapi tidak bisa tersambung ke platform | *rollback*: firmware baru baru dianggap sah setelah berhasil melapor ke platform; bila tidak, boot kembali ke versi lama. Dukungannya perlu dicek pada versi Arduino-ESP32 yang dipakai; di ESP-IDF tersedia lewat fitur app rollback |
| Salah kirim firmware (mis. untuk board lain) | setiap paket diberi jenis board, dan server menolak bila tidak cocok dengan device |
| Firmware palsu disusupkan | unduhan lewat HTTPS; untuk produksi, *secure boot* atau tanda tangan digital firmware |
| **Token bocor lewat broker publik** | command di `broker.emqx.io` bisa dibaca siapa saja. URL unduhan memakai **token sekali pakai yang kedaluwarsa**, bukan access token device |
| Aktuator ikut ter-reset saat device restart | device mengambil ulang desired state setelah menyala (pola yang sudah ada) |
| Update massal gagal bersamaan | *staged rollout*: satu device dulu, lalu sebagian kecil, lalu sisanya; berhenti otomatis bila gagal |
| Device bertenaga baterai mati di tengah update | syarat minimum level baterai sebelum update dimulai (bila device melaporkannya) |
| File besar di server | batas ukuran upload; file disimpan di `MEDIA_ROOT`. `runserver` hanya melayani media untuk pengembangan; di produksi file dilayani oleh Nginx |

## 6. Tahapan pengerjaan

### Tahap 1: OTA dasar (prioritas) — selesai, lihat [tahap-09-ota.md](tahap-09-ota.md)

- model paket firmware, upload, dan checksum otomatis;
- `Device.firmware_version`, dilaporkan device saat menyala;
- update **satu device** dari halaman device, memakai command `ota`;
- endpoint unduhan dengan token sekali pakai;
- status update per langkah dan riwayatnya, tampil langsung lewat WebSocket;
- contoh firmware ESP32 yang bisa memperbarui dirinya sendiri;
- device tiruan (skrip Python) untuk menguji alur tanpa perangkat;
- dokumentasi konsep dan langkah praktikum.

Hasilnya cukup untuk praktikum dan sudah memperlihatkan seluruh alur OTA.

### Tahap 2: Rollout

- update ke banyak device per jenis board;
- rollout bertahap, berhenti otomatis bila ada yang gagal;
- alarm bila update gagal atau device tidak kembali online.

### Tahap 3: Keamanan lanjutan

- rollback otomatis bila firmware baru tidak berhasil melapor;
- penandatanganan firmware dan verifikasi tanda tangan di device;
- HTTPS wajib untuk unduhan.

## 7. Catatan pengujian

- Sisi server dan device tiruan bisa diuji otomatis seperti tahap-tahap sebelumnya.
- Firmware ESP32 harus diuji di **perangkat sungguhan**: proses unduh, restart, laporan versi
  baru, dan skenario gagal (file rusak, Wi-Fi putus di tengah unduhan).

## 8. Referensi pola

- ThingsBoard: paket OTA dan status `fw_state` (DOWNLOADING, DOWNLOADED, VERIFIED, UPDATING,
  UPDATED, FAILED).
- Arduino-ESP32: pustaka `HTTPUpdate` dan `Update`.
- ESP-IDF: `esp_https_ota` dan app rollback.
- Untuk device berbasis Linux (bukan mikrokontroler): Mender atau Eclipse hawkBit.
