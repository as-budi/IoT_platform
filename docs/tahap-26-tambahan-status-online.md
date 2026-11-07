# Tahap 26 (tambahan) — Status online yang tidak macet

## Gejala

Device *Node F5.3* tampil **Online** padahal data terakhirnya 23 jam yang lalu dan device
sudah mati.

## Cara platform menentukan online (sebelum perbaikan)

`devices/presence.py` (tahap 16):

1. **Data masuk** → online. Worker `housekeeping` menjadikan device offline setelah
   `DEVICE_OFFLINE_SECONDS` (300 s) tanpa data.
2. **Sesi MQTT**: device mengirim `online` (retained) ke `<prefix>/<token>/status` dan memasang
   Last Will `offline`. Selama sesi terbuka (`mqtt_connected`), batas 300 s **tidak dipakai sama
   sekali**; device baru offline bila `offline` datang dari Last Will.

Jadi device yang pernah mengirim `online` tetap online **selamanya** bila `offline` tidak pernah
sampai. Penyebab yang mungkin:

- firmware mengirim `online` tetapi tidak memasang Last Will;
- device disconnect secara rapi (`mqtt.disconnect()`): broker tidak mengirim Last Will untuk
  disconnect yang rapi;
- broker di-restart ketika device sudah mati: Last Will tidak dikirim, sedangkan `online`
  retained tetap tersimpan;
- **setiap restart `mqtt_bridge`** (mis. saat `update.sh`) bridge menerima lagi `online` retained
  yang tersimpan di broker dan menjadikan device online lagi, walaupun device sudah lama mati.

Riwayat di halaman device (*Connection history*) menunjukkan penyebabnya: baris *Online · MQTT
session started* tanpa *Offline* sesudahnya.

## Perbaikan

1. **`online` retained diabaikan.** Bridge meneruskan flag RETAIN (`handle_message(...,
   retained=...)`). Broker hanya memberi flag itu pada salinan tersimpan yang dikirim karena
   bridge baru (ulang) subscribe, bukan pada pesan yang baru dikirim device. `online` lama tidak
   membuktikan device sedang tersambung, jadi diabaikan. `offline` retained tetap dipakai (Last
   Will yang terlewat saat bridge mati).
2. **Batas diam untuk sesi MQTT.** Kolom baru `Device.last_heard_at` (migrasi devices 0023)
   dicatat untuk setiap pesan MQTT baru dari device: data, status, laporan firmware, jawaban
   command, tanda terima aturan (paling sering tiap 30 s). Device dengan sesi terbuka yang tidak
   mengirim pesan apa pun selama `DEVICE_MQTT_SILENT_SECONDS` (bawaan **900 s**) menjadi offline
   dengan alasan *No MQTT message for 900 s (connection lost without Last Will)*, dan
   `mqtt_connected` dikosongkan. `0` = perilaku lama (hanya Last Will). Sebelum ada
   `last_heard_at`, dipakai `last_seen_at` lalu `status_changed_at`.
3. **Detak di firmware template MQTT**: `online` diulang setiap 5 menit (`STATUS_EVERY_MS`),
   sehingga device yang jarang mengirim data tetap online selama sesinya hidup.

Batas 900 s tetap lebih longgar daripada 300 s, jadi device yang jarang mengirim data (tetapi
mengirim detak) tidak berkedip offline-online seperti tanpa sesi MQTT.

## File

| Bagian | File |
|---|---|
| Logika status | `devices/presence.py` (`heard`, `check_timeouts`, `mqtt_status(retained=...)`) |
| Model + migrasi | `devices/models.py` `last_heard_at`, `devices/migrations/0023_device_last_heard_at.py` |
| Bridge | `mqtt_bridge/handlers.py`, `mqtt_bridge/management/commands/mqtt_bridge.py` |
| Pengaturan | `config/settings.py` `DEVICE_MQTT_SILENT_SECONDS`, `.env.example`, `deploy/env.production.example` |
| Firmware | `firmware/esp32_mqtt/esp32_mqtt.ino` (detak 5 menit) |
| Tampilan / dokumentasi | `device_detail.html` (keterangan *Connection history*), `userdocs` *Devices* dan *Device API* |
| Tes | `devices/tests_housekeeping.py` |

## Update server

```bash
sudo /srv/iot/app/deploy/scripts/update.sh      # migrasi devices 0023, restart housekeeping dan bridge
```

Setelah update, `housekeeping` dalam beberapa detik menjadikan offline semua device yang sesi
MQTT-nya terbuka tetapi tidak mengirim apa pun selama 15 menit (termasuk Node F5.3). Firmware
buatan sendiri sebaiknya ikut mengirim detak `online`, atau data, minimal setiap ±5 menit.
