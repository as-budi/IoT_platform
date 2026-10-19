# Tahap 16: Tabel ringkasan telemetri dan status online/offline device

Dua pekerjaan berkala yang tidak dipicu pesan, dikerjakan satu worker baru:

```bash
python manage.py housekeeping        # runall: layanan "housekeeping"; server: iot-housekeeping.service
```

| Setiap | Pekerjaan | Kode |
|---|---|---|
| 10 detik | device tanpa data > `DEVICE_OFFLINE_SECONDS` → **offline** | `devices/presence.py` |
| 60 detik | **tabel ringkasan** telemetri per menit dan per jam | `devices/rollups.py` |

Mengapa worker sendiri, bukan di `rule_engine`? Rule engine boleh dimatikan
(`RULE_ENGINE_MODE=off`), sedangkan status device dan chart tetap dibutuhkan. Cukup **satu**
proses housekeeping.

---

## Bagian A — Tabel ringkasan telemetri

### Masalahnya

Device yang mengirim data tiap 10 detik menghasilkan sekitar 8.640 baris per hari per
variabel, atau ±3 juta baris per tahun. Sejak tahap 2b, chart periode panjang sudah diringkas
**di database** (rata-rata per "ember" waktu). Namun setiap kali halaman "1 year" dibuka,
database tetap harus membaca **seluruh** 3 juta baris itu. Makin lama platform berjalan,
makin lambat chart-nya.

### Solusinya: hitung sekali, simpan, pakai berkali-kali

```
Telemetry (data asli)                 8.640 baris/hari per variabel
  └─► ringkasan per MENIT             1.440 baris/hari
        └─► ringkasan per JAM            24 baris/hari  (dihitung dari baris menit)
```

Teknik ini disebut **rollup** atau **downsampling**. Database time-series (TimescaleDB
*continuous aggregates*, InfluxDB *downsampling tasks*) dan ThingsBoard menyediakan hal yang
sama.

Tabel `devices_telemetryrollup` (model `TelemetryRollup`):

| Kolom | Isi |
|---|---|
| `variable` | variabel |
| `resolution` | `60` (per menit) atau `3600` (per jam) |
| `bucket` | awal ember, mis. 10:07:00 |
| `count` | banyaknya data dalam ember |
| `total` | jumlah nilai |
| `min_value`, `max_value` | nilai terkecil dan terbesar |

**Mengapa menyimpan `total` + `count`, bukan rata-rata?** Rata-rata beberapa ember tidak bisa
dirata-rata begitu saja bila jumlah datanya berbeda:

```
ember A: 2 data, rata-rata 10      ember B: 8 data, rata-rata 20
rata-rata dari rata-rata = 15      ✗
(2·10 + 8·20) / (2 + 8) = 18       ✓   = total gabungan / count gabungan
```

Dengan `total` dan `count`, baris jam bisa dihitung dari baris menit, dan chart bisa
menggabungkan ringkasan dengan data asli, semuanya dengan hasil yang benar. `min`/`max`
juga bisa digabung (min dari min, max dari max). Data asli di tabel `Telemetry` **tidak
diubah atau dihapus**.

### Cara worker menghitung (`rollups.update`)

```
setiap menit:
  1. MENIT  hitung ulang ember menit pada [done_until − 10 menit, menit sekarang)
            langsung dari Telemetry, untuk semua variabel sekaligus
  2. JAM    hitung ulang jam-jam yang tersentuh langkah 1, dari baris MENIT
  3. catat done_until di tabel RollupProgress
```

SQL langkah 1 (MySQL):

```sql
SELECT variable_id,
       TIMESTAMPDIFF(SECOND, '1970-01-01 00:00:00', ts) DIV 60 AS idx,
       COUNT(*), SUM(value), MIN(value), MAX(value)
FROM devices_telemetry
WHERE ts >= '2026-10-19 03:00:00' AND ts < '2026-10-19 03:11:00'
GROUP BY variable_id, idx;
```

Beberapa keputusan penting:

- **Ember yang masih berjalan tidak dihitung.** Pukul 10:07:40, ember 10:07 belum selesai,
  jadi yang dihitung hanya sampai 10:07:00. Ringkasan per jam juga hanya untuk jam yang sudah
  lewat.
- **Mundur 10 menit (`TELEMETRY_ROLLUP_LATE_SECONDS=600`).** Device boleh mengirim data
  dengan waktu lampau, misalnya data yang ditampung selama Wi-Fi putus (`"ts"` di payload,
  tahap 2). Setiap putaran menghitung ulang 10 menit terakhir, sehingga data yang terlambat
  kurang dari 10 menit tetap masuk ringkasan.
- **"Hitung ulang" = hapus lalu tulis ulang** ember dalam rentang itu, dalam satu transaksi.
  Cara ini sama untuk MySQL dan SQLite, dan **idempoten**: dijalankan dua kali hasilnya sama.
- **Pertama kali** (tabel kosong), worker meringkas seluruh data lama, dipotong per 6 jam
  supaya transaksi dan memori tetap kecil. Di server dengan data berbulan-bulan, putaran
  pertama bisa butuh beberapa menit.
- **Index baru `telemetry_ts`.** Query di atas mencari berdasarkan waktu saja, untuk semua
  variabel. Index lama `(variable, ts)` tidak bisa dipakai untuk itu, sehingga tanpa index
  baru MySQL akan membaca seluruh tabel setiap menit.

Data yang datang **lebih** terlambat dari 10 menit, atau data lama yang diimpor, baru masuk
ringkasan setelah dihitung ulang:

```bash
python manage.py rollup_telemetry --rebuild --since 2026-10-01
python manage.py rollup_telemetry --rebuild          # semua, dari data paling lama
```

### Chart memakai ringkasan

`devices/charts.py` memilih sumber berdasarkan panjang periode:

| Periode | Sumber | Baris dibaca per variabel (data tiap 10 s) |
|---|---|---|
| ≤ 1 hari (5 min … 1 day, custom pendek) | data asli | ≤ 8.640 |
| ≤ 7 hari (1 week) | ringkasan **menit** | ≤ 10.080 (dulu ±60.000) |
| > 7 hari (1 month, 1 year) | ringkasan **jam** | ≤ 8.760 (dulu ±3 juta) |

Bagian terbaru, setelah `RollupProgress.done_until` (beberapa menit terakhir untuk ringkasan
menit, sampai ±1 jam untuk ringkasan jam), diambil dari data asli. Keduanya digabung
per ember chart:

```
|←────────── ringkasan (cepat) ──────────→|←─ data asli ─→|
start                                  done_until        now
```

Supaya satu baris ringkasan tidak terbelah ke dua ember chart, lebar ember chart dibuat
kelipatan resolusi ringkasan (mis. 11 menit untuk 1 minggu), dihitung dari titik nol 1970.

Keterangan di bawah judul chart: `… · average per 11 min · from 1 min summaries`.

Bila worker belum pernah berjalan (tabel `RollupProgress` kosong), chart memakai data asli
seperti sebelumnya. Tidak ada yang rusak, hanya lebih lambat.

---

## Bagian B — Status online/offline device

### Sebelumnya

Dashboard menghitung "online" saat halaman dibuka: ada data dalam 5 menit terakhir.
Kelemahannya:

- tidak ada **kejadian** "device X offline pukul 14:02", sehingga tidak bisa dicatat,
  dikabarkan ke browser, atau dipakai fitur lain;
- tampilan baru berubah bila halaman dimuat ulang;
- device MQTT yang jarang mengirim data (mis. tiap 30 menit) selalu tampak offline,
  padahal koneksinya hidup.

### Sekarang: status disimpan, perubahan dicatat

Kolom baru di `Device`:

| Kolom | Isi |
|---|---|
| `is_online` | status saat ini |
| `status_changed_at` | kapan terakhir berubah |
| `mqtt_connected` | sesi MQTT sedang terbuka (lihat Last Will di bawah) |

Tabel `DeviceStatusEvent` mencatat setiap perubahan (waktu, online/offline, alasan). Isinya
tampil di halaman device, kartu **Connection history**.

Perubahan status:

| Kejadian | Hasil | Alasan yang dicatat |
|---|---|---|
| telemetri masuk (HTTP, MQTT, CoAP) dan device offline | online | `Data received` |
| tanpa data > `DEVICE_OFFLINE_SECONDS` (bawaan 300) dan tidak ada sesi MQTT | offline | `No data for 300 s` |
| device MQTT publish `online` ke topik status | online | `MQTT session started` |
| `offline` di topik status (Last Will dari broker, atau device pamit) | offline | `MQTT session ended (disconnect or Last Will)` |

Setiap perubahan memakai **UPDATE bersyarat**:

```sql
UPDATE devices_device SET is_online = 0, status_changed_at = ... WHERE id = 3 AND is_online = 1;
```

Bila dua proses melihat perubahan yang sama bersamaan (mis. bridge MQTT dan worker), hanya
satu yang mendapat "1 baris berubah", sehingga kejadian tidak tercatat dua kali. Telemetri
dari device yang **sudah** online tidak menambah query apa pun.

Perubahan juga dikirim ke browser lewat WebSocket:

```json
{"type": "status", "device": 3, "online": false, "reason": "No data for 300 s", "since": 1791436800000}
```

Dashboard langsung mengganti label **Online/Offline** dan angka **Online now**. Halaman
device dimuat ulang otomatis.

### MQTT Last Will and Testament

Batas waktu tanpa data punya dua kelemahan: lambat (baru tahu setelah 5 menit) dan salah
untuk device yang memang jarang mengirim data. MQTT punya mekanisme yang lebih tepat:

1. Saat **CONNECT**, device menitipkan pesan wasiat ke broker:
   topik `<prefix>/<token>/status`, isi `offline`, retained.
2. Setelah tersambung, device publish `online` (retained) ke topik yang sama.
3. Bila koneksi **putus tanpa pamit** (listrik mati, Wi-Fi hilang, crash), broker tidak
   menerima apa pun selama ±1,5 × *keepalive* (60 s → ±90 s), lalu **broker** yang mengirim
   wasiat `offline` atas nama device.
4. Bila device berhenti dengan rapi, wasiat tidak dikirim. Karena itu device sebaiknya
   publish `offline` sendiri sebelum disconnect.

```
device ──CONNECT (will: status=offline, retained)──► broker
device ──PUBLISH status=online (retained)──────────► broker ──► mqtt_bridge ──► Online
        ✕ listrik mati
                                ... 90 s tanpa keepalive ...
                                broker ──PUBLISH status=offline──► mqtt_bridge ──► Offline
```

Karena kedua pesan **retained**, bridge yang baru dinyalakan ulang langsung menerima status
terakhir setiap device.

Selama sesi MQTT terbuka (`mqtt_connected`), batas waktu tanpa data **tidak dipakai**: broker
yang menjaga koneksi. Topik status bersifat opsional. Device tanpa Last Will (HTTP, CoAP,
firmware lama) tetap memakai batas waktu tanpa data.

Yang sudah disesuaikan:

| File | Perubahan |
|---|---|
| `mqtt_bridge/topics.py`, `handlers.py` | bridge berlangganan `<prefix>/+/status`; isi `online`/`offline` (teks) atau `{"online": false}` |
| `mqtt_bridge/.../mqtt_accounts.py` | ACL Mosquitto: `pattern write <prefix>/%u/status` (broker memeriksa izin topik wasiat saat CONNECT) |
| `scripts/fake_mqtt_device.py` | Last Will + `online` saat tersambung + `offline` saat Ctrl+C; `--no-will` untuk membandingkan |
| `firmware/esp32_mqtt_ota/esp32_mqtt_ota.ino` | `mqtt.connect(id, user, pass, topicStatus, 1, true, "offline")` lalu publish `online` |
| halaman device, tab MQTT | baris topik status |

Mencoba dengan device tiruan:

```bash
python scripts/fake_mqtt_device.py <TOKEN> --interval 600   # data tiap 10 menit, tetap Online
# Ctrl+C              -> "offline" dikirim device sendiri: langsung Offline
# kill -9 <pid>       -> Offline ±90 s kemudian, dikirim broker (Last Will)
python scripts/fake_mqtt_device.py <TOKEN> --interval 600 --no-will
#                     -> Offline setelah 300 s tanpa data, lalu Online lagi saat data datang
```

---

## Pengaturan (`.env`)

```bash
DEVICE_OFFLINE_SECONDS=300           # offline setelah N detik tanpa data (tanpa sesi MQTT)
TELEMETRY_ROLLUP_LATE_SECONDS=600    # ringkasan menit dihitung ulang mundur N detik
```

Pilih `DEVICE_OFFLINE_SECONDS` lebih besar dari interval kirim device yang paling jarang,
misalnya 3 × interval.

## Menerapkan

**Laptop:** `python manage.py migrate`, lalu `python manage.py runall`. Layanan
`housekeeping` ikut berjalan.

**Server** (unit systemd baru harus disalin sekali):

```bash
sudo /srv/iot/app/deploy/scripts/update.sh                     # ambil kode + migrate + restart
sudo cp /srv/iot/app/deploy/systemd/iot-housekeeping.service /srv/iot/app/deploy/systemd/iot.target /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart iot.target                             # iot.target kini memuat iot-housekeeping
sudo journalctl -u iot-housekeeping -f                        # putaran pertama meringkas semua data lama
```

Migrasi `devices 0020` menambah index `telemetry_ts` pada tabel telemetri. Pada tabel yang
sangat besar, langkah ini bisa butuh beberapa menit, tetapi MySQL tetap melayani baca-tulis
selama index dibuat.

ACL Mosquitto diperbarui otomatis oleh `iot-mqtt-accounts.timer` (paling lama 2 menit
setelah update), sehingga device boleh publish ke topik status.

## Tes

```bash
python manage.py test devices.tests_housekeeping
```

| Kelompok | Yang diuji |
|---|---|
| `RollupTests` | count/total/min/max menit dan jam, ember berjalan tidak dihitung, idempoten, data terlambat dalam/di luar jendela, `--rebuild` |
| `RollupChartTests` | 1 minggu dari ringkasan menit, periode panjang dari ringkasan jam, rata-rata sama dengan data asli, ON/OFF memakai maksimum, bagian terbaru dari data asli |
| `PresenceTests` | online saat data masuk, offline setelah batas waktu (sekali saja), sesi MQTT + Last Will, isi topik status, ACL, halaman device dan daftar device, `housekeeping --once` |

## Ide lanjutan

- Event "device offline" untuk rule engine (alarm/email saat device mati), selain event
  "device diam" ECA yang sudah ada.
- Pita min–max di chart (datanya sudah ada di tabel ringkasan).
- Retensi: menghapus data asli lebih tua dari N hari, sementara ringkasan jam disimpan lebih
  lama (nomor 2 bagian kedua di daftar saran).
