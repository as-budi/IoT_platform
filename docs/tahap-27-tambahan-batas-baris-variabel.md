# Tambahan tahap 27 — Batas baris telemetri per variabel

## Aturan
- Satu variabel menyimpan paling banyak **N baris telemetri** (bawaan `TELEMETRY_MAX_ROWS_PER_VARIABLE=1000`).
- **Admin bisa memberi angka lain per akun**: *Users → Edit → Telemetry rows per variable*
  (`User.telemetry_max_rows`). Kosong = bawaan server, `0` = tanpa batas. Batasnya milik akun **pemilik device**.
- Pesan yang membawa nilai untuk variabel yang penuh **ditolak utuh**, termasuk nilai variabel lain di pesan yang sama
  (aturan lama: payload disimpan utuh atau ditolak utuh). Variabel baru yang ikut dibuat pesan itu juga batal.
- **Aktuator ikut dibatasi.** Setelah penuh, laporan keadaan aktuator juga ditolak sampai datanya dihapus.
- Variabel yang sudah lebih dari N baris saat fitur ini dipasang **langsung penuh**.

| Protokol | Variabel penuh |
|---|---|
| HTTP | `409 Conflict`, `{"error": "Variable temperature is full (1,000 rows, ...)", "full": ["temperature"], "limit": 1000}` |
| CoAP | `4.09 Conflict`, isi sama |
| MQTT | tidak disimpan; pesan di `<prefix>/<token>/errors` |

## Tanpa COUNT(*): penghitung per variabel
Menghitung `COUNT(*)` setiap kali data masuk membaca semua baris variabel itu (pola yang membuat dashboard 504, lihat
[tahap-27-tambahan-versi-data-ringan.md](tahap-27-tambahan-versi-data-ringan.md)). Jadi `Variable.telemetry_rows`
menyimpan jumlahnya (`devices/row_limit.py`):

| Kapan | Apa |
|---|---|
| Migrasi `devices/0029` | diisi **sekali** dari tabel telemetri (`COUNT ... GROUP BY variable_id`) |
| `save_telemetry()` → `reserve()` | naik sebanyak baris baru; pemeriksaan batas di sini |
| *Delete data* → `release()` | turun sebanyak baris yang dihapus (tidak pernah di bawah 0) |
| `manage.py recount_telemetry` | hitung ulang, **hanya** setelah data diubah di luar aplikasi (SQL manual, mis. `TRUNCATE`) |
| `seed_demo` | memanggil `recount()` di akhir (datanya dibuat dengan `bulk_create`) |

Pemeriksaan saat data masuk, di dalam transaksi `save_telemetry()`:
1. **Tanpa query tambahan**: `telemetry_rows` sudah ikut terbaca bersama variabelnya (`get_or_create` per key). Bila
   `telemetry_rows + baris baru > batas`, `VariableFull` langsung dilempar dan tidak ada yang ditulis.
2. **Pengaman pesan bersamaan** (web/HTTP, `mqtt_bridge`, `coap_server`): satu UPDATE bersyarat per variabel,
   ```sql
   UPDATE devices_variable SET telemetry_rows = telemetry_rows + 2 WHERE id = 11020 AND telemetry_rows <= 1000 - 2
   ```
   0 baris berubah berarti pesan lain lebih dulu mengisinya, `VariableFull` dilempar, dan seluruh transaksi batal (termasuk
   catatan interval 10 detik).
3. Batas akun dibaca dengan satu query kecil (`limit_for`: `telemetry_max_rows` pemilik device).

Tidak ada pekerjaan berkala dan tidak ada pemeriksaan di luar saat data masuk.

`VariableFull` adalah turunan `PayloadError`, jadi `mqtt_bridge` tidak perlu diubah. `api/views.py` dan
`coap_gateway/handlers.py` menangkapnya untuk 409 / 4.09.

**`Variable.save()` tidak menulis `telemetry_rows`** (untuk variabel yang sudah ada). Form *Edit*, desired state, dan
kode lain yang memanggil `variable.save()` memegang objek yang dibaca beberapa detik sebelumnya. Tanpa ini, penghitungnya
akan ditimpa angka lama, padahal telemetri terus masuk di proses lain. Penghitung hanya berubah lewat UPDATE atomik.

**Log aktivitas** `variable.full` (Warning) ditulis **sekali**, saat pesan yang diterima membuat variabel tepat
mencapai batasnya, bukan pada setiap penolakan.

## *Delete data*: unduh CSV dulu, lalu hapus
Tombol **Delete data** di halaman variabel (Editor ke atas, sama dengan menghapus variabel) membuka halaman konfirmasi
(`VariableDataDeleteView`, `devices/variable_data_confirm_delete.html`). Tombol **Download CSV and delete**
(`static/js/delete_data.js`):
1. mengunduh CSV seluruh data **sampai baris terakhir saat halaman dibuka**: `export/csv/?range=all&up_to=<id>`
   (format sama dengan *Download → All data*, `devices/export.py`);
2. menyimpannya sebagai file (tautan blob sementara);
3. **baru** mengirim POST `up_to=<id>`, yang menghapus baris `id <= up_to`, ringkasan chart (rollup) sampai waktu baris
   itu, menurunkan penghitung, dan menulis log `variable.data_deleted`. Lalu kembali ke halaman variabel.

- Unduhan gagal: POST tidak dikirim, dan tidak ada yang terhapus tanpa salinannya.
- Data yang datang selama halaman terbuka (`id > up_to`) tidak ada di CSV, jadi juga tidak dihapus.
- Tanpa JavaScript, form terkirim biasa, dan halaman memberi tautan CSV untuk diunduh lebih dulu.
- Variabel, pengaturannya, dan desired state tetap ada. Variabel langsung menerima data lagi.

## Tampilan
Dua tingkat, dihitung dari penghitung saja (`row_limit.usage()`, tanpa query): **hampir penuh** mulai 90%
(`WARN_FRACTION`, kuning, *92% full*, dibulatkan ke bawah sehingga 999/1000 = 99%) dan **penuh** (merah, **Full**).
- Halaman variabel: *523 / 1,000 rows stored*. Hampir penuh: lencana kuning + "At 1,000 rows new data for this
  variable is refused". Penuh: lencana **Full** + "New data for this variable is refused".
- Halaman device: **banner** merah (variabel penuh, dengan tautannya, dan penjelasan bahwa seluruh pesan ditolak) dan
  banner kuning (hampir penuh, dengan persentasenya). Lencana juga ada di kartu sensor dan aktuator.
- Dashboard: lencana yang sama di kartu variabel (`device.row_limit` diisi `device_tree()`; owner sudah ikut lewat
  `select_related`, jadi tidak ada query tambahan).
- Lencana dibuat oleh `devices/includes/row_badge.html` (filter `row_state` / `row_percent` di `device_tags`). Sengaja
  diletakkan di luar elemen `data-live="meta"`, karena JavaScript halaman menulis ulang elemen itu setiap ada data baru.
- *Users*: kolom **Row limit** (abu-abu = bawaan server), dan field *Telemetry rows per variable* di *Add*/*Edit*.
- Dokumentasi pengguna: *Variables → Row limit and deleting data*, *Device API* (409 / 4.09), *Troubleshooting*,
  *Administration*.

## Pemasangan
`update.sh` menjalankan migrasi. `devices/0029` menghitung baris setiap variabel sekali, satu `COUNT ... GROUP BY`
atas tabel telemetri, yang bisa butuh beberapa detik untuk tabel besar. Setelah itu variabel yang sudah ≥ batas langsung penuh.

## Tes
`devices/tests_row_limit.py`: penghitung, 409 + seluruh pesan ditolak, pesan yang melewati batas, aktuator, variabel baru
batal, batas per akun dan `0`, log sekali, MQTT `errors`, CoAP 4.09, `save()` tidak menimpa penghitung,
`recount_telemetry`, *Delete data* (CSV = baris yang dihapus, data baru tetap, rollup, log, Viewer 403, tanpa `up_to`),
form Admin, tingkat 90%/penuh, banner dan lencana di halaman variabel, halaman device, dan dashboard (`WarningTests`).
