# Tahap 4b — Mengenal Daphne, Channels, channels-redis, dan Redis

Catatan pendamping [tahap-04-websocket.md](tahap-04-websocket.md). Keempat komponen ini
bekerja bertingkat. Tiga di antaranya pustaka Python yang dipasang dengan `pip`, sedangkan
Redis adalah program server terpisah.

```
Browser ──ws://──► Daphne ──► Channels (consumer, routing, auth) ──► kode Django kita
                                     │
                                     └── channel layer ◄── channels-redis ◄──► Redis ◄── mqtt_bridge
```

## Mengapa butuh semua ini?

Django biasa memakai **WSGI**. Satu request masuk, view menjawab, koneksi selesai. Model ini
tidak bisa memegang koneksi WebSocket yang terbuka berjam-jam dan harus siap mengirim kabar
kapan saja.

**ASGI** adalah versi asinkron dari WSGI. Ia mendukung koneksi yang hidup lama dan banyak
protokol (HTTP, WebSocket). Keempat komponen di bawah adalah perlengkapan untuk menjalankan
Django dengan ASGI dan saling berkirim kabar.

## 1. `daphne`: server ASGI

**Perannya:** program yang benar-benar membuka port 8000, menerima koneksi dari browser, dan
mengurus detail protokol. Untuk WebSocket, itu berarti *handshake* `Upgrade: websocket`,
memecah dan menyusun *frame*, serta ping/pong. Setara dengan Gunicorn untuk WSGI.

**Di project kita:**

- `"daphne"` diletakkan paling atas di `INSTALLED_APPS`. Akibatnya perintah
  `python manage.py runserver` diganti versi Daphne. Tandanya, keluaran awal menyebut
  *"Starting ASGI/Daphne … development server"*.
- Daphne membaca `ASGI_APPLICATION = "config.asgi.application"` di settings.
- Di produksi, `runserver` diganti dengan `daphne config.asgi:application`. Alternatif lain
  yang setara adalah `uvicorn` atau `hypercorn`.

**Tanpa Daphne:** `runserver` kembali ke WSGI. Request ke `ws://…/ws/devices/3/` dijawab 404,
dan `static/js/auto_refresh.js` jatuh ke polling.

## 2. `channels`: kerangka WebSocket untuk Django

Daphne hanya mengurus koneksi. Channels menyediakan cara menulis logikanya dengan gaya Django.

| Bagian Channels | Padanannya di Django biasa | Di project kita |
|---|---|---|
| `ProtocolTypeRouter` | — (memilah HTTP vs WebSocket) | `config/asgi.py` |
| `URLRouter` | `urls.py` | `realtime/routing.py`: `ws/devices/<pk>/` |
| **Consumer** | view | `realtime/consumers.py`: `DeviceConsumer` |
| `AuthMiddlewareStack` | `AuthenticationMiddleware` → `request.user` | mengisi `scope["user"]` dari cookie sesi |
| `AllowedHostsOriginValidator` | (pengganti proteksi CSRF untuk WebSocket) | menolak koneksi dari situs lain |
| `database_sync_to_async` | — | memanggil ORM (sinkron) dari consumer (async) |
| **Channel layer API**: `group_add`, `group_send` | — | `consumers.py` dan `realtime/events.py` |
| `WebsocketCommunicator` | test `Client` | `realtime/tests.py` |

Konsep terpentingnya adalah **channel layer**: papan pengumuman tempat bagian-bagian program
berkirim pesan.

- Setiap koneksi WebSocket punya alamat sendiri (`channel_name`).
- Koneksi bisa bergabung ke **group**. Di sini setiap tab yang membuka device 3 masuk group
  `device-3`.
- Siapa pun cukup memanggil `group_send("device-3", pesan)`, dan Channels meneruskannya ke
  semua anggota group. Pengirim tidak perlu tahu berapa tab yang sedang terbuka.

Channels hanya mendefinisikan **cara** memakai papan pengumuman itu. **Tempat** papannya
disediakan oleh *backend* channel layer, dan Channels membawa satu backend bawaan:
`InMemoryChannelLayer`.

## 3. `channels-redis`: backend channel layer berbasis Redis

**Perannya:** menerjemahkan `group_add` dan `group_send` menjadi perintah ke server Redis,
sehingga papan pengumumannya berada **di luar** proses Python.

**Masalah yang diselesaikannya:** project kita terdiri dari dua proses terpisah.

| Proses | Isi |
|---|---|
| A: `runserver` (Daphne) | semua `DeviceConsumer`, yaitu tab-tab browser yang tersambung |
| B: `mqtt_bridge` | menerima telemetri dari broker MQTT, lalu menyimpannya |

Dengan `InMemoryChannelLayer`, papan pengumumannya hanya ada di memori masing-masing proses.
Pengumuman yang ditempel proses B tidak pernah terlihat oleh proses A. Dengan
`RedisChannelLayer`, kedua proses menempel dan membaca dari papan yang sama, yaitu Redis.

**Di project kita**, `config/settings.py` memilih backend berdasarkan `.env`:

- `REDIS_URL` kosong → `InMemoryChannelLayer`. Data MQTT baru tampil lewat pemeriksaan
  cadangan setiap 30 detik.
- `REDIS_URL=redis://127.0.0.1:6379/0` → `RedisChannelLayer`. Semuanya tampil seketika.

## 4. Redis: server penyimpan data di memori

**Perannya:** program server tersendiri (bukan pustaka Python) yang menyimpan data di RAM dan
mendengarkan di port **6379**. Karena di RAM, operasinya sangat cepat, dalam orde mikrodetik.

**Di project kita, Redis hanya dipakai sebagai "kantor pos" antarproses.** Isinya pesan-pesan
pendek yang kedaluwarsa sendiri (bawaan channels-redis sekitar 60 detik). Data telemetri,
device, dan pengguna tetap disimpan di **MySQL**. Kalau Redis dimatikan, tidak ada data yang
hilang; yang hilang hanya kabar seketika ke browser.

Di luar project ini, Redis juga sering dipakai sebagai *cache*, penyimpan sesi, atau antrean
tugas (mis. Celery).

```bash
brew install redis
brew services start redis    # jalan di latar belakang
redis-cli ping               # → PONG
redis-cli monitor            # lihat setiap perintah yang masuk ke Redis
```

`redis-cli monitor` bagus untuk mengajar. Biarkan berjalan saat device mengirim data, maka
terlihat channels-redis menulis pesan ke kunci berawalan `asgi:` setiap kali ada telemetri.

## Satu perjalanan pesan dari ESP32 ke browser

1. ESP32 mem-*publish* `{"temperature": 25.1}` ke broker MQTT.
2. **Proses B** (`mqtt_bridge`) menerimanya, lalu memanggil `save_telemetry()`. Data tersimpan
   di MySQL.
3. Setelah transaksi selesai (`on_commit`), `events.deliver()` memanggil
   `group_send("device-3", …)`.
4. **channels-redis** di proses B menulis pesan itu ke **Redis**.
5. **channels-redis** di proses A, yang sedang menunggu pesan untuk anggota group `device-3`,
   mengambilnya dari Redis.
6. **Channels** memanggil `DeviceConsumer.device_event()` di setiap tab yang tergabung, lalu
   `send_json()`.
7. **Daphne** membungkusnya menjadi *frame* WebSocket dan mengirimnya ke browser.
8. `auto_refresh.js` menerima JSON-nya, dan angka di card serta chart berubah.

Bila data datang lewat HTTP API, langkah 2–6 terjadi di dalam proses A sendiri. Karena itu
jalur HTTP tetap seketika walau tanpa Redis.

## Ringkasan

| Komponen | Jenis | Satu kalimat | Tanpanya |
|---|---|---|---|
| `daphne` | server ASGI (pip) | membuka port dan berbicara protokol WebSocket | tidak ada WebSocket, browser memakai polling |
| `channels` | pustaka Django (pip) | consumer, routing, auth, dan API channel layer | tidak bisa menulis logika WebSocket |
| `channels-redis` | backend channel layer (pip) | menghubungkan channel layer ke Redis | hanya InMemory: tidak bisa lintas proses |
| Redis | server (brew) | papan pengumuman di luar proses Python | data dari `mqtt_bridge` terlambat hingga 30 detik |

Analogi singkatnya:

- **Daphne** adalah operator telepon yang memegang sambungan.
- **Channels** adalah aturan kantor tentang siapa melayani sambungan mana.
- **Channel layer** adalah papan pengumuman.
- **Redis** adalah papan pengumuman yang dipasang di lobi, sehingga kantor sebelah
  (`mqtt_bridge`) juga bisa menempel pengumuman.
