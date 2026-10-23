# Tahap 2 — Komunikasi device lewat HTTP

Mulai tahap ini, data tidak lagi hanya berasal dari `seed_demo` dan simulator:
device sungguhan (ESP32, Raspberry Pi, atau skrip apa pun) bisa mengirim telemetri
ke platform lewat HTTP.

```
ESP32 ──POST /api/v1/<access token>/telemetry──► Django ──► tabel Telemetry
        {"temperature": 25.1, "humidity": 60}            ◄── 200 {"saved": 2, ...}
```

## 1. Access token: identitas device

- Setiap device punya `access_token` acak sepanjang 20 karakter
  (`Device.access_token`, dibuat oleh `generate_access_token()` dengan modul
  `secrets`). Lihat dan salin token di halaman device, bagian **Connect a device**.
- Token ini menggantikan username + password. Siapa pun yang mengetahuinya bisa
  mengirim data atas nama device, jadi perlakukan seperti password.
- Bila bocor, tekan **Generate new token**. Token lama langsung ditolak (401), dan
  device fisik harus diisi token baru.

**Migrasi `0014` dalam tiga langkah.** Kolom unik dengan nilai default acak tidak
bisa ditambahkan sekaligus: Django akan mengisi semua device lama dengan *satu*
nilai yang sama, dan aturan unik langsung dilanggar. Karena itu: tambah kolom yang
boleh kosong → isi token tiap device (`RunPython`) → jadikan wajib dan unik.

## 2. Mengapa API tidak memakai login dan CSRF

| | Halaman dashboard | API device |
|---|---|---|
| Siapa pemanggilnya | manusia di browser | mesin (ESP32, skrip) |
| Bukti identitas | cookie sesi setelah login | access token di URL |
| Perlindungan CSRF | wajib (`{% csrf_token %}`) | tidak perlu (`@csrf_exempt`) |
| Balasan | halaman HTML | JSON + kode status HTTP |

CSRF melindungi dari situs lain yang "menumpang" cookie login di browser pengguna.
Device tidak memakai cookie; ia membuktikan identitasnya dengan token di **setiap**
request, sehingga serangan semacam itu tidak berlaku. Tes
`TelemetryApiTests` memakai `Client(enforce_csrf_checks=True)` untuk membuktikan
endpoint tetap bisa dipanggil tanpa token CSRF.

## 3. Struktur kode: app baru `api`

| File | Isi |
|---|---|
| `api/urls.py` | `/api/v1/<token>/telemetry` (dengan atau tanpa `/` di akhir) |
| `api/views.py` | `telemetry()`: autentikasi → baca JSON → validasi → simpan |
| `api/payload.py` | aturan format payload; fungsi murni tanpa database, mudah dites |
| `api/services.py` | `save_telemetry()`: menyimpan dalam satu transaksi |
| `api/tests.py` | tes endpoint |

Pemisahan ini disengaja: view hanya mengurus HTTP, `payload.py` hanya mengurus
aturan data, `services.py` hanya mengurus database.

## 4. Format payload (kompatibel dengan ThingsBoard)

```json
{"temperature": 25.1, "humidity": 60}
```
Waktu = saat server menerima.

```json
{"ts": 1791436800000, "values": {"temperature": 25.1}}
```
Waktu dari device, dalam **milidetik** sejak 1 Januari 1970 UTC.

```json
[{"ts": 1791436800000, "values": {"temperature": 25.1}},
 {"ts": 1791436860000, "values": {"temperature": 25.3}}]
```
Beberapa bacaan sekaligus, mis. data yang tertahan saat Wi-Fi putus.

Aturannya:

- Nilai harus angka; `true`/`false` disimpan sebagai `1`/`0`. Teks ditolak.
- Key hanya huruf, angka, dan garis bawah, diawali huruf (sama dengan `Variable.key`).
- **Key baru otomatis menjadi variabel sensor baru.** Device baru bisa langsung
  mengirim tanpa mendaftarkan variabel satu per satu; label dan satuan bisa
  dilengkapi belakangan di dashboard.
- **Ditolak utuh atau diterima utuh.** Bila satu nilai saja salah, tidak ada yang
  disimpan, supaya tidak ada data setengah jadi.
- `ts` yang terlalu kecil hampir pasti dikirim dalam detik; balasannya menjelaskan
  hal itu. `ts` lebih dari 5 menit di masa depan ditolak (jam device salah).
- Paling banyak 1000 nilai per request.

**Laporan aktuator.** Bila key-nya adalah variabel aktuator (mis. `lamp`), nilai
yang dikirim adalah *reported state*. Status *Waiting for device* di dashboard
otomatis berubah menjadi *In sync* atau *Mismatch*.

## 5. Kode status balasan

| Kode | Arti | Contoh isi |
|---|---|---|
| 200 | tersimpan | `{"saved": 2, "new_variables": ["humidity"]}` |
| 400 | body bukan JSON, atau isinya melanggar aturan | `{"error": "Value of 'status' must be a number ..."}` |
| 401 | token tidak dikenal | `{"error": "Invalid access token."}` |
| 403 | device dinonaktifkan | `{"error": "Device is inactive."}` |
| 405 | bukan POST | — |

## 6. Mencoba dengan curl

Salin perintah dari halaman device, atau ganti `TOKEN` di bawah:

```bash
# berhasil
curl -X POST http://127.0.0.1:8000/api/v1/TOKEN/telemetry \
     -H "Content-Type: application/json" -d '{"temperature": 25.1, "humidity": 60}'

# token salah -> 401
curl -i -X POST http://127.0.0.1:8000/api/v1/salah/telemetry -d '{"t": 1}'

# nilai teks -> 400 beserta alasannya
curl -X POST http://127.0.0.1:8000/api/v1/TOKEN/telemetry -d '{"temperature": "panas"}'
```

`-i` menampilkan kode status HTTP di baris pertama balasan.

## 7. Device tiruan tanpa perangkat keras

```bash
python scripts/fake_device.py TOKEN --interval 5
```

Skrip ini hanya memakai pustaka bawaan Python, tidak memakai Django: ia "klien
luar" yang hanya tahu alamat server dan token, sama seperti ESP32. Biarkan berjalan,
lalu muat ulang halaman device untuk melihat nilai dan chart bertambah.

## 8. ESP32 sungguhan

Contoh firmware: `firmware/esp32_telemetry/esp32_telemetry.ino` (Arduino IDE, board
ESP32, tanpa pustaka tambahan). Isi nama/password Wi-Fi, `SERVER`, dan `ACCESS_TOKEN`.
Di laptop `SERVER` diawali `http://`. Untuk server produksi isi `https://<domain>` dan
`ROOT_CA`: server produksi hanya menerima HTTPS (lihat `docs/deploy-vps.md` 11.1).

ESP32 berada di jaringan Wi-Fi, bukan di Mac Anda, sehingga tiga hal perlu disiapkan:

1. **Jalankan Django agar menerima koneksi dari jaringan**, bukan hanya dari Mac itu sendiri:
   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```
2. **Cari alamat IP Mac** di jaringan yang sama dengan ESP32:
   ```bash
   ipconfig getifaddr en0
   ```
   Pakai alamat itu di `SERVER`, mis. `http://192.168.1.10:8000`. Jangan memakai
   `127.0.0.1`: bagi ESP32, alamat itu adalah dirinya sendiri.
3. **Izinkan alamat itu di Django.** Tambahkan ke `.env`:
   ```
   DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.10
   ```
   Tanpa ini Django menolak request dengan *400 Bad Request (Invalid HTTP_HOST header)*.
   Bila macOS menanyakan izin koneksi masuk untuk Python, pilih *Allow*.

Buka Serial Monitor (115200 baud) untuk melihat balasan server pada setiap kiriman.

## 9. Catatan keamanan

- Token berada di URL, sehingga bisa tercatat di log server. Pola ini sama dengan
  ThingsBoard; di produksi, wajib memakai **HTTPS** agar token tidak terbaca di jaringan.
- Belum ada pembatasan jumlah request (*rate limiting*). Satu device yang salah
  program bisa membanjiri server; ini perlu ditambahkan sebelum dipakai luas.

## 10. Yang belum (langkah berikutnya)

- Device belum bisa **mengambil desired state dan command**. Untuk itu dibutuhkan
  endpoint kedua (mis. `GET /api/v1/<token>/control`) yang mengembalikan permintaan
  yang belum dijalankan, dan endpoint untuk melaporkan hasil command.
- MQTT, supaya perintah sampai ke device seketika tanpa device harus bertanya.

## Latihan

1. Kirim payload berisi `lamp: true` ke device yang punya aktuator `lamp`, lalu amati
   status *Waiting for device* berubah di dashboard.
2. Ubah `fake_device.py` agar mengirim 10 bacaan sekaligus dalam format daftar (`[...]`)
   dengan `ts` masing-masing.
3. Tambahkan kolom `last_seen_ip` pada `Device` dan isi dengan alamat pengirim
   (`request.META["REMOTE_ADDR"]`) setiap kali telemetri diterima.
