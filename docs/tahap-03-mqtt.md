# Tahap 3 — Komunikasi device lewat MQTT

Pada tahap 2, device berbicara dengan platform lewat HTTP: device bertanya, server
menjawab. Arah sebaliknya (platform → device) sulit, karena server tidak bisa
menghubungi ESP32 lebih dulu. MQTT menyelesaikan masalah itu.

```
                 ┌──────────────── broker MQTT (broker.emqx.io) ───────────────┐
                 │                                                             │
 ESP32 / skrip ──┤ publish  .../telemetry        ──►  subscribe ├── mqtt_bridge ──► database ──► dashboard
                 │ subscribe .../desired         ◄──  publish   │      (Django)  ◄── tombol ON / command
                 │ subscribe .../commands/+      ◄──  publish   │
                 │ publish  .../commands/<id>/response ──►      │
                 └─────────────────────────────────────────────────────────────┘
```

## 1. Konsep dasar MQTT

| Istilah | Arti |
|---|---|
| **Broker** | "kantor pos" pusat. Device dan platform sama-sama tersambung ke sini dan tidak pernah tersambung langsung satu sama lain. |
| **Topic** | alamat pesan berbentuk jalur, mis. `filkom-iot-7f3a9c/AbC123/telemetry` |
| **Publish** | mengirim pesan ke sebuah topik |
| **Subscribe** | berlangganan topik; broker meneruskan setiap pesan yang masuk ke topik itu |
| **Wildcard** | `+` = tepat satu level (`prefix/+/telemetry` = telemetri SEMUA device); `#` = semua level di bawahnya |
| **QoS** | 0 = paling banyak sekali (bisa hilang); 1 = paling sedikit sekali (dikonfirmasi, bisa dobel); 2 = tepat sekali (paling berat) |
| **Retain** | broker menyimpan pesan TERAKHIR di sebuah topik dan langsung memberikannya kepada pelanggan baru |
| **Keepalive** | "ping" berkala agar broker tahu klien masih hidup |
| **Clean session** | `false` = broker mengingat langganan klien dan menyimpan pesan QoS 1 selama klien terputus |

Karena device yang membuka koneksi ke broker (keluar), ESP32 di belakang router rumah
atau jaringan kampus tetap bisa menerima perintah. Itulah alasan utama MQTT dipakai
untuk arah platform → device.

## 2. Pengaturan broker di `.env`

```
MQTT_HOST=broker.emqx.io
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_TLS=False
MQTT_TOPIC_PREFIX=filkom-iot-7f3a9c
MQTT_CLIENT_ID=
MQTT_KEEPALIVE=60
```

`config/settings.py` membaca semuanya dengan `os.getenv`. Untuk pindah ke broker lain
(Mosquitto di server sendiri, EMQX Cloud, HiveMQ), cukup ubah `.env` lalu jalankan
ulang `mqtt_bridge`. Kode tidak perlu diubah.

**Mengapa `MQTT_TOPIC_PREFIX` harus unik?** Broker publik dipakai bersama oleh ribuan
orang. Bila dua orang memakai prefix yang sama, data mereka bercampur.

## 3. Daftar topik

Semua diawali `<prefix>/<access token>/` (dibuat di `mqtt_bridge/topics.py`; daftar
lengkap per device ditampilkan di halaman device, bagian **Connect a device**).

| Topik | Arah | QoS / retain | Isi |
|---|---|---|---|
| `telemetry` | device → platform | 1 | sama dengan HTTP API: `{"temperature": 25.1}`, `{"ts": ..., "values": {...}}`, atau daftar |
| `desired` | platform → device | 1, **retain** | desired state semua aktuator: `{"lamp": 1, "fan_speed": 40}` |
| `commands/<id>` | platform → device | 1 | `{"id": 12, "method": "blink", "params": {"times": 5}}` |
| `commands/<id>/response` | device → platform | 1 | `{"success": true, ...}` atau `{"success": false, "error": "..."}` |
| `errors` | platform → device | 0 | alasan sebuah pesan ditolak: `{"error": "...", "topic": "..."}` |

Pilihan desainnya:

- **Desired state di-retain, command tidak.** Desired state adalah *keadaan*: device
  yang baru menyala harus langsung tahu lampu diminta ON. Command adalah *aksi*:
  "reboot" tidak boleh terulang setiap kali device tersambung ulang.
- **Token di nama topik**, menggantikan token di URL pada HTTP. Broker publik tidak
  memeriksa siapa pengirimnya, jadi platform-lah yang memeriksa token setiap pesan.
- **Ada topik `errors`.** Pada HTTP, payload yang salah langsung dijawab 400. MQTT
  tidak punya jawaban otomatis, jadi bridge mengirim alasan penolakan ke topik ini.

## 4. Struktur kode: app `mqtt_bridge`

| File | Isi |
|---|---|
| `topics.py` | membuat dan membaca nama topik (teks murni, tanpa Django) |
| `handlers.py` | pesan MASUK: autentikasi token → validasi → simpan. Memakai ulang `api/payload.py` dan `api/services.py` dari tahap 2 |
| `outbox.py` | pesan KELUAR: desired state dan command yang belum terkirim |
| `management/commands/mqtt_bridge.py` | proses yang tersambung terus ke broker |
| `tests.py` | tes tanpa broker (koneksi diganti `FakeBroker`) |

**Mengapa proses terpisah?** `runserver` melayani request lalu selesai. Koneksi MQTT
harus hidup terus untuk mendengarkan pesan yang datang kapan saja. Karena itu ia
berjalan sebagai proses *worker* tersendiri: `python manage.py mqtt_bridge`.

**Pola outbox.** Saat Anda menekan ON atau mengirim command, view Django tetap hanya
menyimpan ke database (sama dengan tahap 1b). Bridge memeriksa database setiap detik
dan mengirim yang tertunda. Halaman web tidak pernah menunggu broker, dan tidak error
bila broker sedang mati: permintaan menunggu di database sampai koneksi pulih.

**Arti status command sekarang:**

| Status | Artinya |
|---|---|
| Queued | tersimpan, belum dikirim bridge (bridge mati, atau broker tidak terjangkau) |
| Sent | broker sudah menerima pesannya (QoS 1, sudah dikonfirmasi) |
| Succeeded / Failed | device sudah melaporkan hasilnya |
| Expired | tidak selesai sebelum batas waktu; jawaban yang terlambat diabaikan |

**Proses yang berjalan lama dan MySQL.** MySQL memutus koneksi yang lama diam
(`wait_timeout`). Bridge memanggil `close_old_connections()` sebelum menyentuh
database, supaya koneksi yang sudah basi diganti, bukan menghasilkan error
*"MySQL server has gone away"*.

## 5. Menjalankan

```bash
conda activate iot_platform
pip install paho-mqtt          # sekali saja (sudah tercantum di requirements.txt)

# terminal 1
python manage.py runserver
# terminal 2
python manage.py mqtt_bridge
# terminal 3: device tiruan (salin token dari halaman device)
python scripts/fake_mqtt_device.py TOKEN
```

Lalu di dashboard:

1. Tambahkan variabel `lamp` (kind *Actuator*, *On / off*) pada device itu.
2. Tekan **ON**. Dalam ±1 detik terminal 3 menampilkan `← desired state: {'lamp': 1}`,
   lalu melapor balik. Halaman berubah ke *In sync* sendiri (auto-refresh).
3. Kirim command `ping`, lalu `blink` dengan parameter `{"times": 5}`, lalu `open_valve`.
   Dua yang pertama *Succeeded*, yang terakhir *Failed* (method tidak dikenal device).
4. Matikan device tiruan (Ctrl+C), tekan OFF, nyalakan lagi. Device langsung menerima
   `retained desired state: {'lamp': 0}`.

**Mengamati lalu lintas pesan.** Perintah `mosquitto_sub` di halaman device
menampilkan semua pesan device itu (pasang dengan `brew install mosquitto`). Bisa juga
memakai aplikasi grafis **MQTTX** (gratis, dari EMQ): sambungkan ke broker yang sama,
lalu subscribe `<prefix>/#`.

**ESP32:** `firmware/esp32_mqtt/esp32_mqtt.ino`. Isi Wi-Fi, `TOPIC_PREFIX`, dan
`ACCESS_TOKEN`; pasang pustaka PubSubClient dan ArduinoJson 7. LED bawaan menjadi
`lamp`. Berbeda dengan HTTP, ESP32 tidak perlu tahu alamat IP Mac Anda: keduanya
bertemu di broker. Sketch yang sama juga dipakai di tahap berikutnya: status
online/offline (tahap 16), pembaruan firmware OTA (tahap 9), dan MQTT TLS ke server
produksi (`USE_TLS 1`, lihat `docs/deploy-vps.md` 11.2). Untuk tahap ini biarkan
`USE_TLS 0`; fitur lain tidak mengganggu.

**Jangan menjalankan `simulate_devices` bersamaan dengan `mqtt_bridge`** untuk device
yang sama: keduanya akan "mengambil" command yang sama. Simulator tetap berguna untuk
device yang tidak tersambung MQTT (`--device <id>`).

## 6. Keamanan: broker publik hanya untuk belajar

Broker publik **tidak memakai login, dan siapa pun bisa subscribe `#`**. Artinya:

- Semua data dan **access token** di nama topik bisa dibaca orang lain.
- Orang lain bisa mengirim telemetri palsu atau jawaban command atas nama device Anda.

Jangan mengirim data penting lewat broker publik. Untuk pemakaian sungguhan:

1. Pasang broker sendiri (Mosquitto atau EMQX) dengan username/password per device
   dan **ACL** (device hanya boleh publish/subscribe topiknya sendiri). Pola
   ThingsBoard: username MQTT = access token, topik tetap `v1/devices/me/...`.
2. Pakai TLS (`MQTT_TLS=True`, port 8883).
3. Setelah pindah broker, tekan **Generate new token** di setiap device, karena token
   lama sudah pernah terlihat di broker publik.

## Latihan

1. Subscribe `<prefix>/#` di MQTTX, lalu kirim telemetri dengan `"temperature": "panas"`.
   Amati pesan di topik `errors`.
2. Tambahkan aktuator `fan_speed` (angka, 0–100) dan tangani di `fake_mqtt_device.py`.
3. Buat command `set_interval` {"seconds": 5} yang mengubah interval kirim device tiruan.
4. Tambahkan *Last Will* pada device: broker mengirim `{"online": false}` ke topik
   `<prefix>/<token>/status` bila device putus tanpa pamit. Tampilkan status
   online/offline di dashboard.
