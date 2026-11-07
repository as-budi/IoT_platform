# Tahap 26 (tambahan) — Template firmware ESP32-C3 dengan OLED

`firmware/esp32c3_oled_mqtt/esp32c3_oled_mqtt.ino`: template MQTT untuk board ESP32-C3 dengan OLED
0,42" 72x40 (SSD1306, I2C SDA = GPIO5, SCL = GPIO6), tombol BOOT (GPIO9) dan LED biru (GPIO8, aktif LOW).

## Isi

- **Semua fitur template `esp32_mqtt`**: telemetri berkala, aktuator `lamp` (desired state, termasuk pesan
  yang hanya berisi aktuator yang berubah), command `blink` / `ping` dengan pemeriksaan kedaluwarsa (NTP),
  status online/offline + heartbeat, laporan versi/board/MAC, OTA, tanda terima JSON aturan, TLS opsional.
- **Hanya bila `#define OLED true`** (ubah ke `false` untuk board tanpa OLED; library U8g2 tidak diperlukan):
  - Command `oled`: `{"x": 0, "y": 0, "msg": "Hello"}`. x, y = pojok kiri atas teks (piksel), `\n` = baris
    baru, `ms` opsional = lama tampil (bawaan `OLED_MESSAGE_MS` 15 s; 0 = sampai tombol ditekan). Tanpa OLED
    command ini dijawab `success: false`.
  - Menu dengan tombol BOOT: **tekan 1x** = item berikutnya, **tekan lama** (≥ 0,7 s) = masuk/pilih,
    **tekan 2x** = kembali.

    | Menu | Isi |
    |---|---|
    | Display | nilai aktuator (Lamp ON/OFF), status online dan mode; saat data datang tampil "received: …" 2 detik lalu kembali |
    | Firmware | versi, board, 3 byte terakhir MAC |
    | Mode | Auto = telemetri setiap `SEND_INTERVAL_MS`; Manual = hanya lewat menu Telemetry. Tekan 1x pindah pilihan, tekan lama simpan (disimpan di flash/Preferences, tetap setelah restart) |
    | Telemetry | kirim telemetri 1x, lalu kembali ke Display ("telemetry: sent") |

  - Layar status saat menyambung Wi-Fi/MQTT dan progres OTA ("Updating 40%").

## Pengaturan Arduino IDE

Board *ESP32C3 Dev Module*, *USB CDC On Boot: Enabled* (Serial Monitor lewat USB), Partition Scheme dengan
OTA (mis. *Default 4MB with spiffs*). Library: PubSubClient, ArduinoJson 7, U8g2 (bila OLED true).
Untuk OTA unggah file `.ino.bin` (bukan `.ino.merged.bin`) dengan board **ESP32-C3 OLED**.

## Board dan unduhan dari platform

- Board baru **ESP32-C3 OLED** (`esp32c3_oled`, migrasi devices 0025 dan ota 0002: kolom board 10 → 16
  karakter). Firmware ini melaporkan `"board": "esp32c3_oled"`, jadi board device ikut terisi otomatis dan
  paket OTA harus diunggah dengan board yang sama (tidak tertukar dengan template ESP32-C3 biasa).
- Dokumentasi → *Connect an ESP32* → MQTT: bila device yang dipilih ber-board ESP32-C3 OLED, tombol unduh
  memberi template ini (`esp32c3-oled-mqtt-<device>.zip`, alamat broker, prefix dan token sudah terisi).
  Tanpa device (atau device board lain) tersedia tombol *ESP32-C3 OLED variant*
  (`/devices/connect/firmware/mqtt.zip?board=esp32c3_oled`).
- Template khusus board didaftarkan di `devices/connect.py` (`BOARD_TEMPLATES`).

## Model FSM

Antarmuka tombol + OLED dimodelkan sebagai dua FSM bertingkat (pengenal kejadian tombol = Mealy berwaktu,
antarmuka = statechart/EFSM hierarkis). Gambar (TikZ/PDF/SVG/PNG), definisi formal, dan tabel transisi:
[docs/fsm-esp32c3-oled/README.md](fsm-esp32c3-oled/README.md).
