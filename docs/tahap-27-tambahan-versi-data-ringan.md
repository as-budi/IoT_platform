# Tambahan tahap 27 — Versi data dan nilai terakhir tanpa membaca seluruh telemetri

## Masalah
Di server, saat banyak pengguna membuka dashboard bersamaan (satu kelas), halaman mendapat **504 Gateway Time-out**.
Log Nginx berisi `upstream timed out (110) ... GET /dashboard/`, dan `top` menunjukkan `mysqld` memakai lebih dari
satu core penuh sementara Daphne hampir diam: Daphne menunggu MySQL.

Dua query di dashboard membaca **semua** baris telemetri setiap variabel yang terlihat:

1. **Versi data** (`dashboard_version`, juga `data_version` di halaman device). Setiap tab dashboard
   menanyakannya setiap 30 detik (`/dashboard/updates/`, cadangan WebSocket di `static/js/auto_refresh.js`):
   ```python
   Telemetry.objects.filter(variable__device__in=devices).aggregate(m=Max("pk"))
   ```
   Index `(variable, ts)` tidak urut menurut `id`, jadi MySQL membaca seluruh baris variabel-variabel itu. Untuk
   admin artinya seluruh tabel.
2. **Nilai terakhir di kartu dashboard** (`with_latest_values`): dua subquery "baris terbaru" ditambah
   `COUNT(*)` per variabel. Jumlah baris itu bahkan tidak ditampilkan di dashboard.

Setelah Daphne restart, semua tab menyambung ulang hampir bersamaan dan masing-masing langsung menanyakan versi,
sehingga antrean di MySQL bertambah dan pengguna yang menekan refresh menambahnya lagi.

## Kenapa subquery "baris terbaru" juga lambat
Dengan id variabel yang **tetap**, MySQL melompat ke ujung index dan membaca satu baris:
```sql
SELECT id FROM devices_telemetry WHERE variable_id = 20 ORDER BY ts DESC, id DESC LIMIT 1;
-- EXPLAIN: Backward index scan; Using index      Handler_read: 1
```
Dalam subquery yang **bergantung pada query luar** (`variable_id = devices_variable.id`), MySQL 8.4 mengambil semua
baris variabel lewat index lalu mengurutkannya (`EXPLAIN ANALYZE`: *Covering index lookup ... rows=101 loops=66*,
lalu *Sort*). Bentuk lain (`MAX(ts)`, `LATERAL`, `FORCE INDEX`) sama saja.

## Perbaikan (`devices/views.py`, `dashboard/views.py`)
- **`latest_readings(variable_ids)`**: setiap variabel mendapat query kecilnya sendiri dengan id tetap, digabung
  menjadi satu query `UNION ALL` (per 200 variabel, `LATEST_CHUNK`):
  ```sql
  (SELECT variable_id, id, value, ts FROM devices_telemetry WHERE variable_id = 1 ORDER BY ts DESC, id DESC LIMIT 1)
  UNION ALL (SELECT ... WHERE variable_id = 2 ...) ...
  ```
  Hasilnya `{variable_id: (id, value, ts)}`. Baris yang dibaca = jumlah variabel, berapa pun besar tabelnya.
- **`latest_telemetry_id(variables)`**: id terbesar di antara baris terbaru itu. Dipakai `dashboard_version` dan
  `data_version` (device dan satu variabel).
- **`device_tree`** (dashboard) memakai `latest_readings` untuk `last_value`/`last_seen` dan tidak lagi menghitung
  `telemetry_count`. `with_latest_values` tetap dipakai halaman yang menampilkan jumlah baris (daftar dan detail device).

Hasil ukur di data lokal (±6.650 baris telemetri, 66 variabel; angka `Handler_read*`, termasuk ±259 pembacaan oleh
pengukuran itu sendiri):

| Bagian | Sebelum | Sesudah |
|---|---|---|
| `dashboard_version` (bagian telemetri lama / seluruh fungsi baru) | ±7.089 | ±558 |
| Nilai terakhir kartu dashboard (`with_latest_values` / `device_tree`) | ±273.331 | ±490 |

Di server, angka "sebelum" tumbuh bersama tabel telemetri; angka "sesudah" hanya bergantung pada jumlah variabel.

## Akibat kecil
Versi data sekarang mengikuti **baris terbaru menurut waktu** setiap variabel. Baris dengan `ts` lebih lama dari
data terakhir variabelnya (waktu dikirim mundur, mis. data yang tertunda) tidak mengubah versi. Halaman tetap
menerimanya lewat WebSocket. Halaman yang sedang polling melihatnya pada pembaruan berikutnya.

## Tes
- `devices.tests.AutoRefreshTests.test_latest_telemetry_id_takes_newest_reading_per_variable`
- `dashboard.tests.DashboardQueryTests.test_dashboard_does_not_count_telemetry_rows`
- `dashboard.tests.DashboardUpdatesTests.test_version_query_count_does_not_grow_with_variables`

## Memeriksa di server
Saat dashboard terasa lambat:
```bash
sudo mysql -e "SHOW FULL PROCESSLIST;" | grep -v Sleep
```
Query `SELECT MAX(...) ... devices_telemetry` atau subquery `COUNT(...)` yang berjalan puluhan detik adalah pola lama.
Setelah pembaruan ini, query dashboard berbentuk `(SELECT ... LIMIT 1) UNION ALL (...)` dan selesai dalam milidetik.
