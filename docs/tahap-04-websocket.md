# Tahap 4 — Dashboard real-time dengan WebSocket

Pada tahap 2b, halaman bertanya ke server setiap 5 detik (*polling*): "ada yang baru?"
Hampir selalu jawabannya "belum", dan bila ada data baru, halaman dimuat ulang penuh.

Mulai tahap ini, **server yang memberi kabar**, begitu data tersimpan:

```
ESP32 ─HTTP/MQTT─► api/services.save_telemetry() ──► channel layer ──► DeviceConsumer ──► browser
                                                   group "device-3"     (satu per tab)     angka di card
                                                                                           dan chart bertambah
```

Tidak ada muat ulang halaman: angka di card berganti, titik baru muncul di ujung kanan
chart, dan baris baru masuk ke tabel history.

Penjelasan rinci tentang peran setiap paket ada di
[tahap-04b-channels-daphne-redis.md](tahap-04b-channels-daphne-redis.md).

## 1. HTTP vs WebSocket

| | HTTP (polling) | WebSocket |
|---|---|---|
| Koneksi | baru untuk setiap pertanyaan | satu, terus terbuka |
| Siapa memulai pesan | selalu browser | server **atau** browser |
| Jeda | sampai 5 detik | seketika (milidetik) |
| Beban | request terus-menerus walau tidak ada data | hanya saat ada data |
| Alamat | `http://...` | `ws://...` (`wss://` bila HTTPS) |

WebSocket dimulai sebagai request HTTP biasa dengan header `Upgrade: websocket`. Bila
server setuju (kode 101), koneksi TCP yang sama berubah menjadi saluran dua arah.

## 2. Bagian-bagian Django Channels

Django biasa (WSGI) hanya mengenal request → response. **Channels** menambahkan
dukungan ASGI, sehingga Django bisa memegang koneksi yang terbuka lama.

| Bagian | File | Peran |
|---|---|---|
| **Daphne** | `INSTALLED_APPS` paling atas | server ASGI; `runserver` kini melayani HTTP **dan** WebSocket |
| **ASGI application** | `config/asgi.py` | memilah koneksi: `http` → view Django, `websocket` → consumer |
| **Routing** | `realtime/routing.py` | "urls.py"-nya WebSocket: `ws/devices/<id>/` |
| **Consumer** | `realtime/consumers.py` | "view"-nya WebSocket: `connect`, `disconnect`, `device_event` |
| **Channel layer** | `CHANNEL_LAYERS` di settings | papan pengumuman bersama: kirim pesan ke sebuah *group* |
| **Events** | `realtime/events.py` | fungsi yang dipanggil siapa pun yang mengubah data |

**Group.** Setiap tab yang membuka device 3 bergabung ke group `device-3`. Pengirim
cukup mengirim satu pesan ke group itu; channel layer meneruskannya ke semua tab.
Pengirim tidak perlu tahu berapa tab yang sedang terbuka.

## 3. Siapa yang mengirim kabar

| Kejadian | Tempat | Pesan |
|---|---|---|
| telemetri tersimpan (HTTP, MQTT, simulator) | `api/services.save_telemetry()` | `{"type": "telemetry", "points": [...]}` |
| desired state diubah | `Variable.set_desired()` | `{"type": "desired", "variable": 9}` |
| command dibuat / dikirim / selesai | `CommandCreateView`, `Command.mark_sent()`, `Command.complete()` | `{"type": "command", "id": 12, "status": "SENT"}` |

Karena HTTP API, `mqtt_bridge`, dan `simulate_devices` sama-sama menyimpan lewat
`save_telemetry()`, cukup **satu** baris tambahan di sana untuk semua jalur.

**Dikirim setelah commit.** `events.emit()` memakai `transaction.on_commit()`. Bila
kabar dikirim sebelum transaksi selesai, browser bisa menerima kabar tentang data yang
ternyata batal disimpan.

**Kegagalan tidak menggagalkan penyimpanan.** Bila channel layer (Redis) mati, kabar
hanya dicatat di log sebagai peringatan; datanya tetap tersimpan.

## 4. Channel layer: memori atau Redis

| `REDIS_URL` di `.env` | Channel layer | Data HTTP & tombol dashboard | Data dari `mqtt_bridge` |
|---|---|---|---|
| kosong | `InMemoryChannelLayer` | seketika | paling lambat ±30 detik (pemeriksaan cadangan) |
| `redis://127.0.0.1:6379/0` | `RedisChannelLayer` | seketika | **seketika** |

Mengapa? `runserver` dan `mqtt_bridge` adalah **dua proses** terpisah dengan memori masing-masing.
Papan pengumuman di memori `mqtt_bridge` tidak terlihat oleh `runserver`. Redis adalah
papan pengumuman di luar keduanya, sehingga pesan bisa menyeberang proses. Saat Redis belum
dipakai, `mqtt_bridge` menampilkan peringatan ketika dijalankan.

Memasang Redis di macOS:

```bash
brew install redis
brew services start redis      # berjalan di latar belakang, port 6379
redis-cli ping                 # harus menjawab PONG
```

Lalu tambahkan ke `.env`: `REDIS_URL=redis://127.0.0.1:6379/0`, dan jalankan ulang
`runserver` dan `mqtt_bridge`.

**Masalah yang pernah terjadi: WebSocket putus setiap ±5 detik.** Log `runserver`
menampilkan `redis.exceptions.TimeoutError: Timeout reading from 127.0.0.1:6379`.
Penyebabnya, setiap consumer menunggu pesan dengan perintah Redis `BZPOPMIN` yang
memang menggantung sampai 5 detik bila tidak ada pesan (`brpop_timeout` di
channels-redis). Mulai redis-py 8, batas waktu membaca socket bawaannya juga 5 detik.
Keduanya berlomba, dan bila batas baca habis lebih dulu, consumer berhenti dengan error
sehingga Daphne menutup WebSocket. Perbaikannya ada di `config/settings.py`:
`socket_timeout` dinaikkan menjadi 15 detik (`REDIS_SOCKET_TIMEOUT`), lebih panjang dari
penantian BZPOPMIN. Tes `RedisSettingsTests` menjaga agar aturan ini tidak terlanggar.

## 5. Keamanan WebSocket

- **Login.** `AuthMiddlewareStack` membaca cookie sesi yang sama dengan halaman web, sehingga
  `scope["user"]` = pengguna yang login.
- **Kepemilikan.** Consumer memakai `visible_devices()`, aturan yang sama dengan halaman web.
  Pengguna lain ditolak dengan kode penutupan `4403`, pengunjung yang belum login `4401`.
- **Origin.** Proteksi CSRF Django **tidak berlaku** untuk WebSocket. `AllowedHostsOriginValidator`
  menolak koneksi yang dibuka dari situs lain (header `Origin` harus ada di `ALLOWED_HOSTS`).
  Tanpa ini, situs jahat bisa membuka WebSocket atas nama pengguna yang sedang login.
- **Satu arah.** Browser tidak mengirim perintah lewat WebSocket ini. Tombol ON dan form command
  tetap lewat form biasa yang dilindungi CSRF.
- Catatan: koneksi yang sudah terbuka tidak otomatis ditutup ketika pengguna logout di tab lain.

## 6. Di sisi browser (`static/js/auto_refresh.js`)

1. Membuka `ws://<host>/ws/devices/<id>/`. Status di atas halaman: **Live (WebSocket)**.
2. Setiap kabar diserahkan ke *handler* halaman (`AutoRefresh.on("telemetry", ...)`):
   - Halaman device: card **sensor** diperbarui di tempat dan disorot sebentar.
   - Halaman variabel: card nilai terkini, tabel history (halaman 1), dan chart. Chart bergeser
     mengikuti waktu, kecuali sedang di-zoom atau menampilkan rata-rata (periode panjang).
   - Kabar yang mengubah banyak bagian (aktuator, desired state, command, variabel baru) →
     halaman dimuat ulang, dengan aturan tunda yang sama seperti tahap 2b (tidak saat mengetik
     atau saat chart di-zoom).
3. Koneksi putus → sambung ulang dengan jeda 2, 4, 8, ... detik (maks. 30).
4. Server tidak melayani WebSocket sama sekali (mis. Daphne belum terpasang) → kembali ke
   polling tiap 5 detik. Halaman tetap berfungsi.
5. Selama tersambung pun, versi data diperiksa tiap 30 detik sebagai jaring pengaman.

## 7. Menjalankan

```bash
conda activate iot_platform
pip install channels daphne channels-redis   # sekali saja (ada di requirements.txt)
python manage.py runserver
```

Pastikan baris pertama keluaran `runserver` menyebut **"Starting ASGI/Daphne ... development
server"**. Bila masih "WSGI", periksa bahwa `"daphne"` ada di urutan pertama `INSTALLED_APPS`.

Mencoba: buka halaman sebuah variabel dengan periode **5 min**, lalu kirim data:

```bash
python scripts/fake_device.py TOKEN --interval 2          # lewat HTTP
python scripts/fake_mqtt_device.py TOKEN --interval 2     # lewat MQTT (+ mqtt_bridge)
```

Di tab *Network* developer tools browser, filter **WS**: terlihat satu koneksi
`ws/devices/<id>/`, dan di bagian *Messages* setiap kabar JSON yang masuk.

Di produksi, `runserver` diganti dengan `daphne config.asgi:application` (atau uvicorn),
biasanya di belakang Nginx yang meneruskan header `Upgrade`.

## Latihan

1. Buka halaman device yang sama di dua browser (mis. Chrome dan Safari, login sebagai pemilik
   dan Admin). Tekan ON di satu browser; amati browser lain berubah sendiri.
2. Matikan Redis (`brew services stop redis`) saat semuanya berjalan. Apa yang terjadi pada data
   dari MQTT? Apa yang tertulis di log `runserver`?
3. Tambahkan kabar `{"type": "status", "online": false}` dari MQTT *Last Will* (latihan tahap 3),
   lalu tampilkan lencana Online/Offline di halaman device tanpa muat ulang.
4. Buat halaman daftar device ikut diperbarui langsung: consumer baru yang bergabung ke group
   semua device milik pengguna.
