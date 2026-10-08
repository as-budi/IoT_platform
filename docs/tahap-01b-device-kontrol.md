# Tahap 1b — Device yang bisa dikontrol

Sampai tahap 1, semua variabel adalah sensor: device mengirim nilai, platform
menampilkannya. Tahap ini menambahkan **aktuator** — sesuatu yang bisa diubah dari
dashboard (relay, lampu, kipas, pompa, servo) — dengan dua pola interaksi yang
umum di platform IoT. Komunikasi dengan device sungguhan belum dibuat; perannya
digantikan oleh perintah `simulate_devices`.

## Prinsip utama

**Mengirim perintah tidak sama dengan perintah itu terlaksana.** Relay bisa macet,
device bisa sedang mati, perintah bisa terlambat. Karena itu platform selalu
memisahkan apa yang *diminta* dari apa yang *benar-benar terjadi*.

## Pola 1 — Desired state / reported state

Untuk aktuator yang **punya keadaan** (ON/OFF, kecepatan 0–100 %, sudut servo).

| | Disimpan di | Diubah oleh |
|---|---|---|
| **Desired** (diminta) | `Variable.desired_value`, `desired_at`, `desired_by`, `desired_version` | pengguna, lewat dashboard |
| **Reported** (terjadi) | telemetri terbaru variabel itu | device |

Status di dashboard (`Variable.sync_status`):

| Status | Arti |
|---|---|
| *Waiting for device* | device belum melapor apa pun **setelah** permintaan terakhir |
| *In sync* | laporan setelah permintaan, nilainya sama dengan yang diminta |
| *Mismatch* | device sudah melapor, tetapi nilainya lain (macet / ditolak) |

Perhatikan aturan "**setelah** permintaan": laporan lama yang kebetulan bernilai sama
tidak dianggap bukti. Tes `test_old_report_does_not_count_as_sync` menjaga aturan ini.

Hal yang perlu dicermati di kode:

- `Variable.set_desired()` (`devices/models.py`) — `transaction.atomic()` dan
  `select_for_update()` memastikan perubahan nilai, kenaikan versi, dan catatan log
  terjadi bersama-sama, dan dua klik yang hampir bersamaan tidak bertabrakan.
- `DesiredStateLog` — jejak audit: siapa meminta nilai berapa, kapan.
  Tampil sebagai *Request history* di halaman variabel.
- `SetDesiredView` (`devices/views.py`) **tidak** menulis telemetri. Kalau ia
  melakukannya, status akan langsung "In sync" walaupun device tidak pernah
  menyalakan apa pun — dashboard berbohong.
- `SetDesiredForm` memeriksa batas min/max **di server**. Atribut `min`/`max` pada
  `<input>` hanya kenyamanan; isian browser mudah diakali.
- `desired_version` naik setiap ada permintaan baru. Di tahap komunikasi nanti,
  device memakainya untuk tahu apakah ada permintaan yang belum ia jalankan.

## Pola 2 — Command (setara "RPC" di ThingsBoard)

Untuk **aksi sekali jalan** yang tidak meninggalkan keadaan: `reboot`,
`open_valve` selama 5 detik, `calibrate`.

```
QUEUED ──► SENT ──► SUCCEEDED
   │         └────► FAILED
   └──────────────► EXPIRED   (lewat batas waktu sebelum selesai)
```

- `Command` (`devices/models.py`) menyimpan `method`, `params` (JSON), status,
  `response`, pembuat, dan `expires_at`.
- **Command yang kedaluwarsa tidak pernah dijalankan belakangan.** Katup yang tiba-tiba
  terbuka tiga jam kemudian, karena device baru menyala, justru berbahaya.
  `Command.objects.expire_due()` (custom QuerySet) menandainya, dan
  `Command.complete()` menolak mengubah command yang sudah final.
- Formulir di halaman device (`CommandForm`) memeriksa bahwa `method` hanya huruf,
  angka, dan garis bawah, dan bahwa `params` adalah objek JSON.

## Kapan memakai yang mana

| | Desired / reported | Command |
|---|---|---|
| Contoh | relay, kipas, servo, setpoint | reboot, buka katup N detik |
| Device sedang mati | permintaan menunggu, dijalankan saat menyala | kedaluwarsa |
| Klik berkali-kali | hanya nilai terakhir yang berlaku | setiap klik = satu command |
| Bukti terlaksana | reported = desired | status + response |

## Mencoba di dashboard

```bash
python manage.py flush            # opsional: mulai dari data kosong
python manage.py seed_demo
python manage.py runserver
```

Masuk sebagai `budi`, buka **Lab Relay Board**:

1. Klik **ON** pada *Lamp* → status *Waiting for device*.
2. Di terminal lain jalankan simulator:
   ```bash
   python manage.py simulate_devices
   ```
   Muat ulang halaman → status *In sync*, nilai besar di card berubah.
3. Kirim command `reboot` dari bagian **Commands**, jalankan simulator lagi →
   status *Succeeded* beserta response-nya.
4. Coba kegagalan: `python manage.py simulate_devices --fail-rate 1` →
   aktuator *Mismatch*, command *Failed*.
5. Kirim command dengan batas waktu 1 menit, tunggu lebih dari 1 menit, lalu muat
   ulang halaman → *Expired*; simulator tidak akan menjalankannya.

Simulator juga bisa berjalan terus: `python manage.py simulate_devices --loop`.

## Tampilan

- **Halaman device**: bagian *Controls* (satu card per aktuator: nilai reported,
  nilai yang diminta, status, tombol ON/OFF atau isian angka), bagian *Sensors*,
  dan bagian *Commands* (formulir + 20 command terakhir).
- **Halaman variabel aktuator**: card *Reported by device* dan *Requested by user*
  berdampingan, chart (untuk ON/OFF digambar bertingkat), tabel history, dan
  *Request history*.
- Filter template buatan sendiri di `devices/templatetags/device_tags.py`:
  `show_value` (ON/OFF atau 2 desimal), `as_json`, dan `plain_number`.

## Yang belum (bahan tahap komunikasi)

- Device sungguhan belum bisa mengambil desired state atau command; simulator yang
  menggantikannya.
- Halaman tidak memperbarui diri; perlu dimuat ulang untuk melihat perubahan status.
- Belum ada pembedaan "boleh melihat" dan "boleh mengontrol"; pemilik device dan
  Admin bisa keduanya.
- Pengaman keselamatan (mis. pompa mati sendiri bila tangki kosong) nantinya wajib
  ada di **firmware**, bukan bergantung pada server.

## Latihan

1. Tambahkan tipe nilai **pilihan** (mis. mode AC: `cool`, `dry`, `fan`). Petunjuk:
   tambahkan pilihan di `Variable.ValueType`, simpan daftar opsinya, lalu tampilkan
   sebagai dropdown di `control_form.html`.
2. Tambahkan tombol **Cancel** untuk command yang masih *Queued*.
3. Tampilkan jumlah aktuator yang berstatus *Mismatch* di daftar device, supaya
   masalah langsung terlihat tanpa membuka setiap device.
