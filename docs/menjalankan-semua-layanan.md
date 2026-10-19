# Menjalankan semua layanan sekaligus: `manage.py runall`

Sejak tahap 7, platform terdiri dari empat proses yang harus menyala bersamaan:

| Nama | Perintah | Tugas |
|---|---|---|
| `web` | `runserver` | halaman web, API HTTP, WebSocket (Daphne) |
| `mqtt` | `mqtt_bridge` | device lewat MQTT |
| `coap` | `coap_server` | device lewat CoAP |
| `rules` | `rule_engine` | worker rule engine |
| `housekeeping` | `housekeeping` | status online/offline device dan tabel ringkasan telemetri (tahap 16) |

Daripada membuka empat terminal, cukup jalankan:

```bash
python manage.py runall                      # semuanya; web di 127.0.0.1:8000
python manage.py runall --skip coap          # tanpa CoAP
python manage.py runall --only web,rules     # hanya web dan rule engine
python manage.py runall --no-restart         # layanan yang mati tidak dinyalakan ulang
```

Hasilnya satu terminal dengan log yang diberi awalan nama layanan:

```
web   | Starting ASGI/Daphne version 4.2.3 development server at http://127.0.0.1:8000/
mqtt  | 10:02:11  Connected to broker.emqx.io:1883
coap  | 10:02:11  CoAP server listening on udp://127.0.0.1:5683
rules | 10:02:12  Rule "Irrigation" activated (value 18) -> Set actuator: Water pump = ON
```

Tekan **Ctrl+C** sekali untuk menghentikan semuanya.

## 1. Cara kerjanya: supervisor, bukan program gabungan

`runall` **tidak** menggabungkan keempat layanan menjadi satu program. Ia hanya
menjadi *supervisor* ("pengasuh"): setiap layanan tetap dijalankan sebagai proses anak
tersendiri, persis seperti saat dijalankan di terminal masing-masing.

```
runall (supervisor)
  ├── python manage.py runserver      ──► punya anak sendiri untuk auto-reload
  ├── python manage.py mqtt_bridge
  ├── python manage.py coap_server
  ├── python manage.py rule_engine
  └── python manage.py housekeeping
```

Memisahkan proses adalah keputusan desain yang disengaja:

- **Isolasi kegagalan.** Bila `coap_server` crash, web dan MQTT tetap berjalan.
- **Model kerja berbeda.** Daphne dan aiocoap memakai asyncio, paho-mqtt memakai
  thread, dan rule engine berupa loop. Digabung dalam satu proses, satu bagian yang
  lambat (misalnya email yang menunggu SMTP) bisa menahan bagian lain. Python juga
  hanya menjalankan satu thread pada satu waktu (GIL).
- **Sama dengan produksi.** Di server sungguhan pun layanan dijalankan terpisah. Bedanya
  hanya pada pengasuhnya (lihat bagian 3).

Tugas supervisor (`launcher/supervisor.py`):

1. Menjalankan setiap layanan dengan `subprocess.Popen` dalam **grup proses** sendiri.
2. Membaca keluaran setiap anak (stdout dan stderr) dengan satu thread per anak, lalu
   mencetaknya dengan awalan nama.
3. Bila sebuah layanan berhenti, menyalakannya ulang dengan jeda yang makin panjang:
   2, 4, 8, … hingga 60 detik (*exponential backoff*). Setelah 5 kali crash cepat
   berturut-turut, supervisor menyerah untuk layanan itu, supaya log tidak dibanjiri error
   yang sama. Layanan yang sempat sehat selama 30 detik dihitung dari nol lagi.
4. Saat Ctrl+C, mengirim sinyal ke **seluruh grup** setiap layanan secara bertahap:
   - SIGINT (sama dengan Ctrl+C, sehingga setiap layanan berhenti dengan rapi),
   - SIGTERM bila setelah 10 detik masih hidup,
   - SIGKILL sebagai langkah terakhir.

   Grup proses penting karena `runserver` punya proses anak untuk auto-reload. Tanpa
   grup, anak itu bisa tertinggal dan terus memakai port 8000.

Sebelum mulai, `runall` juga memeriksa beberapa hal:

| Pemeriksaan | Akibat |
|---|---|
| Port web sudah dipakai | berhenti dengan pesan jelas |
| `paho-mqtt` atau `aiocoap` belum terpasang | layanan itu dilewati dengan peringatan |
| `RULE_ENGINE_MODE=off` | `rules` dilewati |
| Redis tidak bisa dihubungi | peringatan; rule engine akan terus mencoba |
| `runall` lain sedang berjalan untuk project ini | berhenti (lihat bagian 2) |

## 2. Pertimbangan keamanan dan hal lain

**Untuk pengembangan dan praktikum: aman dipakai.** `runall` tidak mengubah apa yang
dijalankan, hanya cara memulainya. Risiko keamanannya sama dengan menjalankan keempat
perintah secara manual. Ada beberapa hal yang perlu diperhatikan:

1. **Satu perintah menyalakan semua pintu masuk.** CoAP ikut membuka UDP 5683 tanpa enkripsi
   (`coap://`), dan MQTT tersambung ke broker publik. Bila sebuah layanan tidak
   diperlukan, matikan dengan `--skip`. Jangan teruskan port 5683 dari router ke
   internet (lihat docs/tahap-06-coap.md).
2. **Alamat web.** Bawaannya `127.0.0.1:8000`, artinya hanya bisa dibuka dari Mac itu sendiri.
   `runall 0.0.0.0:8000` membuka akses dari seluruh jaringan (misalnya untuk demo di
   kelas). Lakukan hanya di jaringan tepercaya; `runall` menampilkan peringatan.
3. **Jangan jalankan dua kali.** Dua `mqtt_bridge` dengan `MQTT_CLIENT_ID` yang sama akan
   saling memutus koneksi di broker, dan port web/CoAP akan bentrok. `runall` memakai
   kunci file (`flock`) di folder sementara sistem agar hanya satu yang bisa berjalan.
   Kuncinya terlepas otomatis saat proses berhenti, termasuk bila crash.
4. **Auto-reload hanya untuk web.** Saat kode diubah, `runserver` memuat ulang dirinya,
   tetapi `mqtt_bridge`, `coap_server`, dan `rule_engine` tidak. Setelah mengubah kode
   worker, hentikan `runall` (Ctrl+C) lalu jalankan lagi.
5. **Log bercampur.** Untuk menelusuri satu masalah, layanan itu kadang lebih mudah
   dijalankan sendiri, misalnya `python manage.py rule_engine` di terminal terpisah
   bersama `runall --skip rules`.
6. **Rahasia tetap di `.env`.** Semua proses anak membaca `.env` yang sama. Jangan
   menuliskan password di perintah `runall`, karena perintah terlihat di daftar proses
   (`ps`).

## 3. Di server produksi: pakai pengasuh milik sistem

`runall` sengaja **tidak** dirancang untuk produksi:

- `runserver` adalah server pengembangan: tidak diaudit keamanannya dan tidak dirancang
  untuk beban.
- Supervisor kecil ini tidak punya rotasi log, tidak memulai otomatis saat komputer
  menyala, dan tidak membatasi hak akses proses.

Di server, setiap layanan didaftarkan ke pengelola proses milik sistem:

| Alat | Contoh |
|---|---|
| **systemd** (Linux) | satu file `.service` per layanan, `Restart=on-failure`, `User=iot` (bukan root) |
| **supervisord** | satu blok `[program:...]` per layanan |
| **Docker Compose** | satu *service* per proses, `restart: unless-stopped` |

Web dijalankan dengan `daphne config.asgi:application` (atau uvicorn) di belakang Nginx
yang melayani HTTPS, dengan `DEBUG=False`. Prinsipnya sama dengan `runall`: proses
terpisah dan pengasuh yang menyalakan ulang. Hanya alatnya yang lebih tangguh.

## 4. Struktur kode

| File | Isi |
|---|---|
| `launcher/supervisor.py` | class `Service` dan `Supervisor` (tanpa Django, mudah diuji) |
| `launcher/management/commands/runall.py` | perintah `runall`: pilihan layanan, pemeriksaan awal, kunci satu-instans |
| `launcher/tests.py` | uji restart dan backoff, berhenti beserta "cucu" proses, port terpakai |

## Latihan

1. Matikan Redis (`brew services stop redis`), lalu jalankan `runall`. `rules` gagal saat
   mulai karena tidak bisa menghubungi Redis. Berapa jeda antar-percobaan ulangnya? Nyalakan
   Redis sebelum percobaan kelima dan amati `rules` pulih sendiri.
2. Kali ini matikan Redis **saat** `runall` sudah berjalan. Mengapa `rules` tidak
   di-restart oleh supervisor, melainkan menulis "Redis error … retrying in 5 s"?
   Petunjuk: bandingkan kegagalan saat mulai dengan kegagalan di dalam loop
   (`rules/management/commands/rule_engine.py`). `mqtt_bridge` berperilaku serupa:
   paho-mqtt menyambung ulang ke broker sendiri.
3. Buka terminal kedua dan jalankan `runall` lagi. Pesan apa yang muncul, dan mengapa ini
   penting untuk MQTT?
