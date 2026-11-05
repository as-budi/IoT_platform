# Tahap 26 (tambahan) — Command kedaluwarsa sampai ke device

## Masalah

Sebelumnya pesan command hanya `{"id", "method", "params"}`. Saat command kedaluwarsa,
platform hanya mengubah statusnya menjadi EXPIRED di database; device tidak pernah tahu batas
waktunya. Dengan firmware template (clean session, command tidak retained) device yang offline
memang tidak pernah menerima command itu, tetapi:

1. status *Sent* tidak berarti diterima: bridge mengirim walaupun device offline, sehingga
   command hilang di broker padahal device mungkin online lagi sebelum batas waktunya;
2. device dengan persistent session (`clean_session=false`) menerima command dari antrean broker
   setelah tersambung lagi — bisa berjam-jam kemudian — tanpa tahu command itu sudah basi;
3. command yang tiba terlambat atau diproses belakangan tetap dijalankan.

## Empat perbaikan

### 1. Batas waktu di pesan command (`devices/control.py`)

```json
{"id": 12, "method": "blink", "params": {"times": 5}, "expires_at": 1762412345, "ttl": 297}
```

- `expires_at` — `Command.expires_at` sebagai Unix time (detik, UTC, dibulatkan ke bawah).
- `ttl` — sisa detik saat pesan dibuat (`command_ttl()`), untuk device tanpa jam.

Berlaku untuk MQTT (`mqtt_bridge/outbox.py`) dan CoAP (`GET .../commands`), karena keduanya
memakai `command_payload()`.

### 2. MQTT 5 Message Expiry Interval (`mqtt_bridge`)

`mqtt_bridge` kini tersambung ke broker dengan **MQTT 5** (`.env MQTT_PROTOCOL=5`, bawaan) dan
mengirim setiap command dengan properti *Message Expiry Interval* = `ttl`. Menurut spesifikasi
MQTT 5, broker wajib membuang pesan yang batas umurnya habis sebelum terkirim ke pelanggan,
termasuk pelanggan MQTT 3.1.1 dan pesan di antrean persistent session. Device tidak perlu
diubah. Untuk broker yang hanya mendukung MQTT 3.1.1: `MQTT_PROTOCOL=3.1.1` (tanpa batas umur
pesan; perbaikan 1, 3, dan 4 tetap berlaku).

`publish(topic, payload, retain, expiry=None)`: argumen `expiry` baru, hanya diisi untuk command.

### 3. Kirim hanya saat device online (`Outbox.flush_commands`)

Command QUEUED hanya dikirim ke device dengan `is_online=True`, dan baru setelah device online
selama `ONLINE_GRACE` (2 detik) supaya langganannya sudah terpasang. Selama device offline,
command tetap **Queued** dan dikirim begitu device online lagi — kecuali sudah kedaluwarsa
(`expire_due()` dijalankan lebih dulu). Command dengan sisa waktu < 1 detik tidak dikirim.

`is_online` diisi oleh `devices/presence.py`: pesan `online`/Last Will di topik status (MQTT),
atau data yang masuk (semua protokol). Device MQTT yang hanya mendengarkan tanpa pernah
mengirim apa pun harus mengumumkan `online` di topik status agar menerima command.
Device CoAP tidak terpengaruh: ia mengambil command QUEUED/SENT sendiri saat bertanya.

### 4. Firmware template memeriksa jam (`firmware/esp32_mqtt`, `firmware/esp32_coap`)

- `configTime(0, 0, "pool.ntp.org", "time.google.com")` setelah Wi-Fi tersambung.
- `secondsExpired(command)`: bila jam sudah diset (lebih baru dari November 2023) dan
  `time(nullptr) > expires_at + 2`, command **tidak dijalankan dan tidak dijawab** (platform sudah
  menandainya Expired dan menolak jawaban terlambat). Selama jam belum diset, command dijalankan
  seperti sebelumnya.
- Template MQTT kini subscribe **sebelum** mengirim `online`, karena platform langsung mengirim
  command yang menunggu begitu device online.

## MQTT client

`mqtt_client` tahap M10: auto-responder dan simulasi OTA tidak menjalankan command yang sudah
kedaluwarsa (`mqtt_client/docs/tahap-m10-command-expiry.md`).

## File

| Bagian | File |
|---|---|
| Pesan command | `devices/control.py` (`command_ttl`, `command_payload`) |
| Outbox | `mqtt_bridge/outbox.py` (`ONLINE_GRACE`, `flush_commands`) |
| Bridge | `mqtt_bridge/management/commands/mqtt_bridge.py` (MQTT 5, `expiry`) |
| Pengaturan | `config/settings.py` `MQTT_PROTOCOL`, `.env.example`, `deploy/env.production.example` |
| Firmware | `firmware/esp32_mqtt/esp32_mqtt.ino`, `firmware/esp32_coap/esp32_coap.ino` |
| Dokumentasi pengguna | `userdocs/.../control.html`, `device-api.html` (`#command-expiry`) |
| Tes | `mqtt_bridge/tests.py`, `coap_gateway/tests.py` |

## Update server

```bash
sudo /srv/iot/app/deploy/scripts/update.sh      # tanpa migrasi; me-restart mqtt_bridge
```

`MQTT_PROTOCOL` tidak perlu ditambahkan ke `.env` (bawaannya 5). Mosquitto ≥ 1.6 mendukung
MQTT 5. Log bridge kini menyebut versinya (`Connecting to ... with MQTT 5`) dan menulis
`(expires in N s)` di setiap command yang dikirim. Flash ulang device dengan template baru agar
pemeriksaan jam (perbaikan 4) aktif.

## Uji manual batas umur pesan (perbaikan 2)

Dengan Mosquitto dan device (atau `mosquitto_sub`) yang memakai persistent session:

```bash
# 1. Daftarkan sesi persisten lalu putuskan (Ctrl+C):
mosquitto_sub -h <broker> -u <token> -P <token> -i test-dev -c -q 1 -t '<prefix>/<token>/commands/+' -v
# 2. Kirim "online" ke topik status (mosquitto_sub tidak punya Last Will, jadi platform tetap
#    menganggap device online), lalu di platform kirim command dengan Expires after = 1 minute.
#    Bridge mengirimnya; broker menyimpannya di antrean sesi test-dev.
mosquitto_pub -h <broker> -u <token> -P <token> -r -t '<prefix>/<token>/status' -m online
# 3. Tunggu lebih dari 1 menit, lalu sambung lagi dengan perintah no. 1:
#    command TIDAK muncul. Ulangi dengan menyambung sebelum 1 menit: command muncul.
```
