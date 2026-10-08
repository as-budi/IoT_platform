# Tahap 2b — Auto-refresh dan periode chart

Dua fitur untuk mempermudah pengujian device sungguhan:

1. **Auto-refresh.** Halaman device dan halaman variabel dimuat ulang sendiri
   ketika ada data baru, jadi tidak perlu menekan F5 setiap kali ESP32 atau
   `fake_device.py` mengirim data.
2. **Periode chart.** Chart di halaman variabel bisa menampilkan 5 menit, 10 menit,
   30 menit, 1 jam, 1 hari, 1 minggu, 1 bulan, 1 tahun, atau rentang custom.

## 1. Auto-refresh dengan polling

Browser tidak bisa "ditelepon" oleh server lewat HTTP biasa: server hanya bisa
menjawab bila ditanya. Karena itu halaman **bertanya berkala** (*polling*):

```
browser ── GET /devices/3/updates/ ───────► Django
        ◄── {"version": "1532-4-17-0-0"} ──
        (5 detik kemudian, bertanya lagi)
        ◄── {"version": "1533-4-17-0-0"} ──   berbeda -> muat ulang halaman
```

**Versi data** dibuat oleh `data_version()` di `devices/views.py`. Isinya bukan
datanya, hanya "sidik jari" pendek yang pasti berubah bila ada sesuatu yang baru:

| Bagian | Berubah ketika |
|---|---|
| id telemetri terbesar | ada telemetri baru (dari API, simulator, atau seed) |
| jumlah `desired_version` | ada yang mengubah desired state aktuator |
| id command terakhir, waktu dikirim/selesai | command dibuat atau statusnya berubah (device page saja) |

Halaman variabel memanggil `/devices/3/updates/?variable=7`, sehingga hanya data
variabel itu yang dihitung.

Mengapa tidak mengirim seluruh data setiap 5 detik? Balasan JSON ini hanya
beberapa puluh byte dan query-nya memakai index, jadi ringan walaupun tabel
telemetri berisi jutaan baris.

**Kapan halaman tidak dimuat ulang.** Logikanya di `static/js/auto_refresh.js`:

- pengguna sedang mengetik atau sudah mengisi sebuah form (mis. form command),
- chart sedang di-zoom (halaman variabel mendaftarkan alasan ini lewat
  `AutoRefresh.hold(...)`),
- tab browser tidak terlihat (tidak bertanya ke server sama sekali).

Dalam keadaan itu muncul tombol **Reload now**. Saklar *Auto-refresh* bisa dimatikan;
pilihannya diingat oleh browser (`localStorage`). Posisi scroll dipertahankan
setelah halaman dimuat ulang.

**Mengapa endpoint membalas 401, bukan redirect ke login.** Bila sesi habis,
`LoginRequiredMixin` biasa akan mengirim halaman login (HTML). Pemanggilnya
JavaScript, bukan manusia, jadi lebih jelas bila balasannya JSON 401; skrip lalu
berhenti dan menampilkan "Session expired".

**Polling vs. WebSocket.** Polling sederhana dan cukup untuk satu orang yang sedang
menguji. Untuk banyak pengguna dan pembaruan seketika, platform seperti ThingsBoard
memakai WebSocket: server yang mengirim kabar tanpa ditanya. Itu memerlukan Django
Channels dan dibahas di tahap berikutnya.

## 2. Periode chart

Periode dibaca dari URL, misalnya:

```
/devices/3/variables/7/?period=5m
/devices/3/variables/7/?period=custom&start=2026-10-07T08:00&end=2026-10-07T12:00
```

- Form `ChartPeriodForm` (`devices/forms.py`) memvalidasinya. Nilai yang salah
  tidak membuat error 500: chart kembali ke **1 day** dan pesannya ditampilkan.
- Rentang berakhir di **sekarang**, sehingga data yang baru masuk muncul di ujung
  kanan. Sumbu x selalu selebar periode yang dipilih.
- **Custom:** isi *Start* dan *End* (waktu Asia/Jakarta). *End* yang dikosongkan
  berarti "sampai sekarang", cocok untuk menguji sambil auto-refresh menyala.
- Form memakai `method="get"` karena hanya membaca data. Alamatnya bisa
  disimpan sebagai bookmark.
- Tombol periode dan tautan halaman tabel memakai tag `{% querystring %}`
  (Django 5.1+), yang menyalin parameter URL lain. Berpindah ke halaman 2
  tabel tidak menghilangkan periode chart.

## 3. Meringkas data panjang (aggregation)

Device yang mengirim tiap 10 detik menghasilkan ~260.000 baris per bulan. Browser
tidak perlu semuanya, karena layar hanya selebar ~1000 piksel. `chart_series()` di
`devices/charts.py` bekerja begini:

1. Hitung jumlah baris dalam rentang (`count()`).
2. Bila ≤ `CHART_MAX_POINTS` (1000), kirim data asli.
3. Bila lebih, bagi rentang menjadi 1000 **ember** sama lebar, lalu database
   menghitung rata-rata tiap ember:

```sql
SELECT (TIMESTAMPDIFF(SECOND, '2026-09-07 05:00:00', ts) DIV 2592) AS bucket,
       AVG(value)
FROM devices_telemetry
WHERE variable_id = 7 AND ts BETWEEN ... AND ...
GROUP BY bucket ORDER BY bucket
```

Untuk periode 1 bulan, lebar ember = 30 hari / 1000 ≈ 43 menit. Keterangan di bawah
judul chart menunjukkannya, mis. *"259,200 readings · average per 43 min 12 s"*.

- Perhitungan dilakukan **di database**, bukan di Python, agar jutaan baris tidak
  perlu dipindahkan ke Django.
- Variabel ON/OFF memakai nilai **maksimum**, bukan rata-rata: rata-rata 0,4 tidak
  bermakna, sedangkan "pernah ON dalam rentang ini" bermakna.
- `TIMESTAMPDIFF` dan `DIV` adalah fungsi MySQL/MariaDB. Django tidak menyediakannya,
  jadi dibuat kelas `BucketIndex(Func)` sendiri. Bila suatu saat pindah ke
  PostgreSQL, hanya kelas ini yang perlu diganti.

## Mencoba

```bash
python manage.py test devices
python manage.py runserver
# terminal lain:
python scripts/fake_device.py TOKEN --interval 5
```

Buka halaman variabel, pilih **5 min**, lalu amati titik baru muncul setiap 5 detik
tanpa menekan F5. Coba zoom chart: auto-refresh menunggu sampai *Reset* ditekan.

## Latihan

1. Ubah `data-interval="5"` di `includes/auto_refresh.html` menjadi 2, lalu amati
   permintaan `updates/` di tab *Network* developer tools browser.
2. Tambahkan auto-refresh ke halaman daftar device (perlu versi data untuk semua
   device milik pengguna).
3. Ganti muat ulang halaman penuh dengan memperbarui chart saja: endpoint mengembalikan
   titik baru, lalu JavaScript memanggil `chart.data.datasets[0].data.push(...)`
   dan `chart.update()`.
