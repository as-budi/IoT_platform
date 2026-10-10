# Tahap 5 — Dashboard, pengaturan tampilan, dan side menu

## 1. Halaman Dashboard (`/dashboard/`)

Satu halaman berisi card SEMUA variabel milik pengguna, dikelompokkan dua tingkat:

```
Lokasi (Greenhouse A)
  └─ Device (Greenhouse Node)   ● Online
       └─ [Water pump] [Air humidity] [Soil moisture] [Air temperature]
Lokasi (Server room)
  └─ ...
No location
  └─ ...
```

- Di atasnya ada empat ringkasan: jumlah lokasi, device, device online (mengirim data
  dalam 5 menit terakhir), dan variabel yang ditampilkan.
- Card sensor bisa diklik menuju halaman variabel. Card aktuator membawa tombol
  kontrol (desired state) yang sama dengan halaman device.
- Halaman depan (`/`) dan tujuan setelah login sekarang adalah Dashboard.

**Satu query untuk semua variabel.** `device_tree()` di `dashboard/views.py` mengambil
semua device yang boleh dilihat (lewat `visible_devices()`, aturan kepemilikan yang sama),
lalu semua variabelnya sekaligus dengan `with_latest_values()`. Pengelompokan dilakukan
di Python dengan `defaultdict`, bukan dengan query per lokasi atau per device.

## 2. Pengaturan tampil/sembunyi (`/dashboard/customize/`)

Model `DashboardPreference` (app `dashboard`), satu baris per pengguna:

| Field | Isi |
|---|---|
| `hidden_locations` | ManyToMany ke `Location` |
| `hidden_devices` | ManyToMany ke `Device` |
| `hidden_variables` | ManyToMany ke `Variable` |
| `hide_unlocated` | sembunyikan kelompok "No location" |

Dua keputusan desain:

- **Yang disimpan adalah yang DISEMBUNYIKAN.** Device atau variabel baru otomatis tampil
  tanpa pengguna harus mengatur ulang.
- **Bertingkat.** Variabel tampil hanya bila variabel itu, device-nya, dan lokasinya
  tidak disembunyikan. Mematikan lokasi tidak menghapus pilihan di dalamnya; begitu
  dinyalakan lagi, pilihan lama kembali.

Di form, centang = tampil. View menghitung `semua id milik pengguna − id yang dicentang`
lalu menyimpannya dengan `.set(...)`. Id milik pengguna lain yang dikirim lewat form
diabaikan, karena perhitungan hanya memakai id dari `visible_devices()`.

Pengaturan ini per pengguna: Budi menyembunyikan sesuatu tidak memengaruhi dashboard Siti.

Migrasi: `dashboard/migrations/0001_initial.py`. Jalankan `python manage.py migrate`.

## 3. Pembaruan langsung di dashboard

Halaman device memakai satu WebSocket per device (`ws/devices/<id>/`). Dashboard memuat
banyak device sekaligus, jadi ada consumer kedua, `DashboardConsumer` di
`ws/dashboard/`, yang bergabung ke group **semua** device milik pengguna.
Bentuk pesannya sama; JavaScript dashboard mencari card lewat `data-live-variable`.

- Telemetri sensor dan aktuator diterapkan langsung ke card. Status sync aktuator
  (*In sync*, *Waiting for device*, *Mismatch*) dihitung ulang di browser dengan aturan
  yang sama seperti `Variable.sync_status`.
- Kabar untuk variabel yang disembunyikan diabaikan. Variabel yang belum ada di halaman
  (baru dibuat otomatis oleh API) membuat halaman dimuat ulang.
- Penanda device berubah menjadi *Online* saat datanya masuk.
- Cadangan polling memakai `/dashboard/updates/`.

## 4. Side menu

`templates/base.html` kini memakai kerangka dua kolom: side menu di kiri, isi di kanan.

- Komponen Bootstrap `offcanvas-lg`: pada layar ≥ 992 px tampil tetap di kiri; pada
  layar kecil tersembunyi dan muncul lewat tombol ☰ di bilah atas.
- Isi: Dashboard, Devices, Locations; untuk Admin juga Users dan Admin site; di bawahnya
  saklar tema dan pengguna yang sedang login beserta tombol Log out.
- Halaman publik (login, daftar) tidak memakai side menu.
- Django melarang `{% block content %}` muncul dua kali dalam satu template, jadi kedua
  tata letak berbagi satu block dan hanya pembungkusnya yang dibedakan dengan `{% if %}`.

## Latihan

1. Tambahkan tombol "Hide" kecil di header setiap device pada dashboard yang langsung
   menyembunyikan device itu (POST ke view baru), tanpa membuka halaman Customize.
2. Simpan juga urutan lokasi pilihan pengguna (field `PositiveIntegerField` pada model
   perantara ManyToMany).
3. Perbarui angka "Online now" secara langsung saat device mulai/berhenti mengirim data.
