# Tahap 26 (tambahan) — Publish JSON aturan ke device

Percobaan sebelum firmware micro rule: apakah **semua device yang dipakai sebuah aturan mampu
menerima JSON aturan utuh** lewat MQTT. Tidak ada fragmentasi atau dekomposisi — setiap device
menerima JSON yang sama dengan kartu JSON / Export, ditambah kunci `publish` (lihat bagian 3).

## 1. Perubahan JSON aturan

`rules/portable.py` — `export_rule()` kini menambahkan daftar device beserta MAC address-nya
tepat setelah kunci header:

```json
{
  "format": "iot-platform.rule", "version": 1, "model": "ECA", "name": "Irrigation",
  "description": "", "enabled": true,
  "devices": [
    {"name": "Soil probe", "mac": "AA:BB:CC:00:00:01"},
    {"name": "Pump node",  "mac": "AA:BB:CC:00:00:03"}
  ],
  "eca": { ... }
}
```

- `rule_devices(rule)` mengumpulkan device dari kondisi, aksi, node flow, dan state machine
  (urutan pertama muncul, tanpa duplikat). Device tanpa MAC → `"mac": null`.
- Daftar ini **informasi saja**: impor mengabaikannya (nama device tetap dicocokkan di blok
  `eca`/`flow`/`state_machine`), sehingga JSON lama dan baru sama-sama bisa diimpor.
- Ini bukan bagian "Run on devices" (micro rule); fragmen micro rule tidak berubah.

## 2. Topik MQTT

| Topik | Arah | QoS | Retained | Isi |
|---|---|---|---|---|
| `<prefix>/<token>/rules/<rule id>` | platform → device | 1 | ya | JSON aturan + `publish` (Full/Compact); pesan kosong = dicabut |
| `<prefix>/<token>/rules/<rule id>/ack` | device → platform | 1 | tidak | tanda terima |

`<token>` = access token device (sama dengan topik telemetri/command). Karena retained, device
yang sedang mati tetap menerima JSON terakhir begitu tersambung dan subscribe `.../rules/+`.

ACL Mosquitto (`mqtt_accounts`) mendapat dua baris baru:

```
pattern read  <prefix>/%u/rules/+
pattern write <prefix>/%u/rules/+/ack
```

`mqtt_bridge` subscribe `<prefix>/+/rules/+/ack`. API v2 (`/api/v2/devices/<id>/` → `_topics`)
dan halaman dokumentasi pengguna *Device API* juga menampilkan `rules` dan `rule_ack`.

## 3. Isi pesan

Semua device aturan menerima **pesan yang sama**: JSON aturan dengan satu kunci `publish` di
depan. Tidak ada amplop lain — nama dan MAC penerima tidak ditulis dua kali, dan topik ack tidak
ikut dikirim (device cukup menambahkan `/ack` ke topik pesan).

Ada dua bentuk, dipilih di sebelah tombol Publish (`RulePublication.compact`):

**Full** — JSON di tab JSON halaman aturan, tanpa `exported_at`:

```json
{"publish": {"id": 3, "rev": "9f3a1c07"},
 "format": "iot-platform.rule", "version": 1, "model": "ECA", "name": "Irrigation",
 "description": "", "enabled": true,
 "devices": [{"name": "Pump", "mac": "AA:BB:CC:00:00:02"}, {"name": "Soil probe", "mac": "AA:BB:CC:00:00:01"}],
 "eca": {"event": "TELEMETRY", "device": null, "silence_seconds": null, ...,
         "when": [[{"device": "Soil probe", "variable": "soil", "operator": "<", "threshold": 20.0}]]},
 "actions": [{"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "Pump", "variable": "pump", "value": 1.0}]}
```

**Compact** — Full tanpa `format`, `description`, dan nilai kosong (`null`, `""`, `[]`, `{}`),
dan `"device"` / `"target_device"` di kondisi, aksi, node, dan state machine ditulis sebagai
indeks di `devices` (0 = device pertama):

```json
{"publish": {"id": 4, "rev": "9f3a1c07"}, "version": 1, "model": "ECA", "name": "Irrigation", "enabled": true,
 "devices": [{"name": "Pump", "mac": "AA:BB:CC:00:00:02"}, {"name": "Soil probe", "mac": "AA:BB:CC:00:00:01"}],
 "eca": {"event": "TELEMETRY", ..., "when": [[{"device": 1, "variable": "soil", "operator": "<", "threshold": 20.0}]]},
 "actions": [{"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": 0, "variable": "pump", "value": 1.0}]}
```

Bila ada dua device bernama sama (mis. device milik pengguna lain), nama tidak diganti indeks.

- `publish.id` — nomor publish per aturan (1, 2, 3, ...), dipakai untuk mencocokkan ack.
- `publish.rev` — hash isi aturan (bentuk Full); sama untuk Full dan Compact selama aturan tidak
  diubah.
- Device mengenali dirinya lewat MAC-nya di `devices`. Template ESP32 dan MQTT client membalas
  `ok: false` bila ada MAC di `devices` tetapi MAC-nya sendiri tidak termasuk.

Ukuran pada 19 aturan contoh `seed_edge_rules` (total, satu pesan per aturan):

| Bentuk | Byte | Hemat |
|---|---|---|
| amplop lama (`type`, `device`, `ack`, `rule: {...}`) | 23.305 | – |
| Full | 19.877 | 15% |
| Compact | 14.085 | 40% |

`size_bytes` dihitung dengan `mqtt_bridge.outbox.encode()` — bentuk yang benar-benar dikirim
(karakter non-ASCII ditulis `\uXXXX`) — sehingga sama dengan `size` yang dilaporkan device.

Device → platform (ack), di topik pesan + `/ack`:

```json
{"publish": 3, "ok": true, "size": 1432}
{"publish": 3, "ok": false, "error": "invalid JSON"}
```

`publish` boleh dihilangkan (dipakai publish terakhir). `size` (opsional) = byte yang dibaca
device, ditampilkan di kolom keterangan.

Tab Devices di kartu JSON menampilkan pesan yang dikirim pada publish terakhir (*Message sent in
publish #n*), sehingga mudah dibandingkan dengan log device.

## 4. Alur dan status

```
Publish ─► RulePublication (#n) ─► RuleDelivery per device (QUEUED / SKIPPED)
            mqtt_bridge outbox ─► SENT ─► ack ok ─► RECEIVED
                                         ack ok=false ─► REJECTED
Withdraw / hapus aturan ─► pesan kosong retained ─► CLEARED
Publish baru sebelum terkirim ─► kiriman lama SUPERSEDED
```

- **SKIPPED**: device nonaktif, atau pemilik aturan tidak punya akses Operator ke device.
- Status SENT tanpa ack berarti broker sudah menerima pesan tetapi device belum menjawab
  (mati, belum subscribe, atau firmware belum mendukung).
- Publish menyatu dengan **kartu JSON** di halaman aturan (satu kartu):
  - judul kartu memuat badge ringkasan publish terakhir (*Not published*, *2/3 received ·
    1 waiting*, *Withdrawn*, *changed*), tetap terlihat walau kartu tertutup;
  - baris tombol: Copy / Open raw / Download di kiri, Publish / Withdraw di kanan;
  - dua tab: **JSON** (viewer) dan **Devices** (tabel MAC, koneksi terakhir, status, waktu kirim,
    waktu balas, ukuran pesan terhadap buffer 8192 byte, peringatan bila aturan sudah diubah);
  - selama ada kiriman yang menunggu (dan setelah redirect ke `#publish`), kartu terbuka dengan
    tab Devices aktif; tabel dan badge dimuat ulang setiap 3 detik.
- Tombol, tab Devices, dan badge hanya tampil untuk aturan ECA bila `MICRO_RULES` mengizinkan
  pengguna itu (`all` / `admin` / `off`, sama dengan "Run on devices"). Selain itu kartu hanya
  berisi JSON seperti sebelumnya (juga untuk aturan Flow dan State machine).
- Riwayat ada di Django admin → *Rule publications*.

## 5. File yang berubah

| Bagian | File |
|---|---|
| Model + migrasi | `rules/models.py` (`RulePublication`, `RuleDelivery`), `rules/migrations/0008_rule_publication.py`, `0009_publication_compact.py` |
| Logika | `rules/publish.py`, `rules/portable.py`, `rules/apps.py` |
| Halaman | `rules/views.py`, `rules/urls.py`, `rules/templates/rules/includes/json_card.html`, `publish_card.html`, `publish_badge.html`, `publish_status.html`, `rule_detail.html`, `static/css/app.css`, `rules/admin.py` |
| MQTT | `mqtt_bridge/topics.py`, `outbox.py`, `handlers.py`, `management/commands/mqtt_bridge.py`, `mqtt_accounts.py` |
| Lainnya | `api/v2.py`, `userdocs/context.py`, `userdocs/templates/userdocs/pages/device-api.html` |
| Firmware | `firmware/esp32_mqtt/esp32_mqtt.ino` — subscribe `rules/+`, buffer 8192, balas ack |
| Tes | `rules/tests_publish.py`, `mqtt_bridge/tests.py`, `rules/tests_schedule.py` |

## 6. Firmware ESP32 (template)

`esp32_mqtt.ino` kini:

1. `mqtt.setBufferSize(8192)` — PubSubClient diam-diam membuang pesan yang lebih besar dari
   buffer, jadi ini batas ukuran JSON aturan.
2. Subscribe `<base>/rules/+` QoS 1.
3. Pesan masuk: kosong → aturan dicabut; selain itu di-parse dengan ArduinoJson, mencari
   dirinya di `devices` lewat `WiFi.macAddress()`, lalu membalas ke `.../rules/<id>/ack`
   `{"publish", "size", "ok", "rule"}` (atau `ok:false` + `error`, mis. JSON rusak atau MAC
   tidak ada di aturan).

Firmware belum menjalankan aturan — tahap ini hanya membuktikan penerimaan.

## 7. MQTT client

Aplikasi `mqtt_client` mendukung topik baru (lihat `mqtt_client/docs/tahap-m7-rule-json.md`):
profil platform device subscribe `rules/+`, dan sesi bisa membalas ack otomatis sehingga
publish dapat diuji tanpa perangkat keras.

## 8. Update server

```bash
sudo /srv/iot/app/deploy/scripts/update.sh            # pull, pip, migrate (rules 0008, 0009), static, restart
sudo systemctl start iot-mqtt-accounts.service         # tulis ulang ACL sekarang (atau tunggu timer 2 menit)
```

`iot.target` me-restart `mqtt_bridge`, yang kemudian subscribe topik ack. Firmware device perlu
di-flash ulang (atau OTA) dengan template baru agar bisa menerima dan membalas.

## 9. Pengujian

```bash
python manage.py test rules.tests_publish mqtt_bridge
```

Uji manual: buka aturan → **Publish to devices** → jalankan MQTT client dengan profil device
yang dipakai aturan dan centang *Acknowledge rule JSON automatically* → status berubah menjadi
*Received*.
