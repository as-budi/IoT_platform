# Tambahan tahap 27 — Firmware dikenali dari nama + versi

## Masalah
Sebelumnya versi firmware unik per (pengunggah, board, versi). Padahal device dengan board yang sama bisa
menjalankan **program berbeda**, misalnya "Greenhouse node 1.0.0" dan "Pump controller 1.0.0" untuk ESP32. Upload
kedua ditolak. Selain itu, platform hanya membandingkan **versi** yang dilaporkan device, sehingga:

1. daftar *Update to* menyembunyikan paket yang versinya sama dengan versi device;
2. *Update firmware* menolak "already runs version 1.0.0" walaupun programnya lain;
3. rollback ke program lama dengan versi yang sama tercatat **UPDATED** (gagal terlihat berhasil).

## Aturan baru (seperti `fw_title` + `fw_version` di ThingsBoard)
- **Upload:** unik per (pengunggah, nama, board, versi). Nama dibandingkan tanpa beda huruf besar-kecil dan spasi
  berlebih (`Lower("title")` di constraint `firmware_unique_version_per_title`; form merapikan spasi). Versi yang sama
  dengan nama lain boleh diunggah. Label field `title` di form menjadi **Name**.
- **Laporan device:** `{"title": "Greenhouse node", "version": "1.0.0", "board": "esp32", "mac": "..."}`. `title`
  opsional (maks. 80 karakter), disimpan di `Device.firmware_title`. Laporan tanpa `title` mengosongkan nama
  (firmware lama), dan platform kembali membandingkan versi saja.
- **Daftar *Update to*** (`compatible_packages`): semua paket untuk board device, kecuali nama + versi yang sedang
  berjalan. Device tanpa nama: tidak ada yang disembunyikan.
- **Mulai pembaruan** (`start_update`):
  - nama + versi sama dengan yang berjalan → ditolak ("already runs …");
  - **nama berbeda** (program lain) → boleh, dengan **halaman konfirmasi** ("This device runs Greenhouse node 1.0.0.
    Replace it with Pump controller 1.0.0?") — `ota/update_confirm.html`, tombol *Replace with …*;
  - device belum melaporkan nama dan versinya sama → boleh, dengan konfirmasi yang menjelaskan bahwa hasilnya
    tidak bisa dipastikan.
  - `services.confirmation_needed()` memberi alasannya; `start_update(..., confirmed=True)` melewatinya.
- **Berhasil/gagal** (`report_version`): UPDATED bila versi sama dengan paket **dan** nama sama (tanpa beda huruf
  besar-kecil) atau tidak dilaporkan. Restart setelah UPDATING dengan firmware lain → FAILED; bila versinya cocok tetapi
  namanya tidak, pesannya menyebut kemungkinan `FW_TITLE` di sketch berbeda dengan nama di platform.
- **Riwayat:** `OtaUpdate.previous_title` baru; tabel dan detail pembaruan menampilkan "Greenhouse node 1.0.0 →
  Pump controller 1.0.0" (`previous_label`, `target_label`).
- **API v2:** device memuat `firmware_title`.

## Firmware dan simulator
- Template ESP32 (`esp32_mqtt`, `esp32c3_oled_mqtt`, `esp32cam_mqtt`, `esp32_coap`, `esp32_telemetry`) punya
  `#define FW_TITLE "…"` dan mengirim `title` dalam laporan firmware. Template MQTT menolak command `ota` hanya bila
  nama (`strcasecmp`) **dan** versi sama dengan yang berjalan. Command `ota` sudah membawa `params.title`.
- `scripts/fake_mqtt_device.py`: opsi `--firmware-title` (default "Fake node").
- MQTT Client: laporan firmware simulator membawa `title` (nama dari platform, atau "MQTT Client simulator"), dan
  simulasi OTA menolak nama + versi yang sama.

## Migrasi
`devices/0027_firmware_title` (kolom `Device.firmware_title`) dan `ota/0005_firmware_title` (constraint baru,
`OtaUpdate.previous_title`). Data lama aman: kunci lama lebih ketat dari kunci baru. Device lama mendapat nama
setelah firmware-nya diperbarui ke template yang mengirim `title`.

## Tes
`ota/tests.py`: `FirmwareNameTests` (simpan/hapus nama, tolak nama+versi sama, konfirmasi program lain, ganti program
dengan versi sama, rollback ke program lama → FAILED, nama tanpa beda huruf besar-kecil), upload versi sama dengan nama
lain, constraint database, halaman konfirmasi (`test_other_program_asks_for_confirmation`).
