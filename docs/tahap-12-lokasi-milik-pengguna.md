# Tahap 12: Lokasi milik pengguna

Sampai tahap 11, lokasi adalah data bersama: nama unik untuk seluruh platform, hanya Admin
yang boleh menambah, dan halaman **Locations** menampilkan semua lokasi kepada semua
pengguna (termasuk lokasi tempat ia tidak punya device). Untuk platform yang dipakai banyak
orang, itu membocorkan nama dan koordinat tempat milik orang lain.

Sejak tahap ini lokasi diperlakukan seperti device: **setiap lokasi punya pemilik**.

| | Sebelum | Sesudah |
|---|---|---|
| Siapa yang membuat lokasi | Admin | setiap pengguna, untuk dirinya sendiri |
| Halaman Locations | semua lokasi | lokasi milik sendiri (Admin: semua, dengan kolom Owner) |
| Lokasi orang lain | terlihat | 404, seperti device orang lain |
| Nama lokasi | unik untuk seluruh platform | unik per pemilik (budi dan siti boleh sama-sama punya "Lab") |
| Dropdown lokasi di form device | semua lokasi | lokasi milik sendiri (Admin: semua, ditulis "Lab (budi)") |
| `Device.location` saat lokasi dihapus | `PROTECT` | `RESTRICT` (lihat konsep 3) |

## File yang berubah

| File | Perubahan |
|---|---|
| `devices/models.py` | `Location.owner`, `UniqueConstraint(owner, name)`, `Device.location` → `RESTRICT` |
| `devices/migrations/0017`–`0019` | tambah kolom → isi data → wajibkan (konsep 4) |
| `devices/views.py` | `visible_locations(user)`, `OwnedLocationMixin`; view lokasi tidak lagi khusus Admin |
| `devices/forms.py` | `DeviceForm`: pilihan lokasi milik pemilik + pemeriksaan pemilik; `LocationForm`: pemilik dan nama unik |
| `devices/templates/devices/location_*.html`, `device_list.html` | tombol Add/Edit/Delete untuk semua pengguna; kolom Owner untuk Admin |
| `dashboard/templates/dashboard/index.html` | Admin melihat nama pemilik lokasi orang lain |
| `devices/admin.py` | kolom dan filter owner |
| `devices/management/commands/seed_demo.py` | lokasi contoh dibuat untuk pemilik device yang memakainya |
| `devices/tests.py`, `devices/tests_migrations.py`, `dashboard/tests.py` | tes baru dan penyesuaian |

## Konsep 1: satu aturan kepemilikan, dua fungsi

Tahap 1 menaruh aturan kepemilikan device di satu fungsi, `visible_devices(user)`. Lokasi
mendapat pasangannya:

```python
def visible_locations(user):
    queryset = Location.objects.select_related("owner")
    if user.is_admin:
        return queryset
    return queryset.filter(owner=user)
```

Semua view lokasi (daftar, detail, tambah, ubah, hapus) mengambil data lewat
`OwnedLocationMixin`, yang memanggil fungsi ini. Lokasi orang lain tidak ada di queryset,
sehingga Django menjawab **404**, bukan 403: pengguna bahkan tidak tahu lokasi itu ada.
Karena itu tombol *Edit* dan *Delete* tidak perlu `{% if user.is_admin %}` lagi: siapa pun
yang bisa membuka halaman sebuah lokasi memang berhak mengubahnya.

## Konsep 2: lokasi device harus milik pemilik device

Dropdown lokasi di `DeviceForm` hanya berisi lokasi milik pengguna. Django menolak id lain
yang dikirim (misalnya diketik manual lewat DevTools), karena `ModelChoiceField` hanya
menerima pilihan dari queryset-nya.

Admin melihat semua lokasi dan boleh memilih pemilik device, sehingga kombinasi yang salah
masih mungkin: device milik budi di lokasi milik siti. `DeviceForm.clean()` menolaknya:

```
This location belongs to siti, not to budi.
```

Aturan yang sama berlaku ke arah sebaliknya: Admin tidak bisa memindahkan sebuah lokasi ke
pemilik lain selama masih ada device pemilik lama di sana (`LocationForm.clean()`).

Hasilnya: device di sebuah lokasi selalu milik pemilik lokasi itu. Karena itu jumlah device
di halaman Locations cukup `Count("devices")`, tanpa filter per pengguna seperti di tahap 1.

## Konsep 3: `PROTECT` vs `RESTRICT`

Lokasi yang masih dipakai device tetap tidak boleh dihapus. Sampai tahap 11 ini dijaga
`on_delete=PROTECT`. Begitu lokasi punya pemilik, muncul satu kasus baru: **menghapus
pengguna**.

```
User (dihapus)
 ├─ CASCADE ─► Device   (ikut terhapus)
 └─ CASCADE ─► Location (ikut terhapus)
                 ▲
Device.location ─┘  PROTECT: "Location masih dipakai Device" -> seluruh penghapusan ditolak
                    RESTRICT: device itu juga sedang dihapus  -> boleh
```

`PROTECT` menolak tanpa melihat konteks, sehingga pengguna yang punya device di lokasinya
sendiri tidak bisa dihapus sama sekali. `RESTRICT` (Django 3.1+) menolak penghapusan
langsung sebuah lokasi yang masih dipakai, **kecuali** device yang memakainya juga sedang
dihapus dalam operasi yang sama lewat `CASCADE`. Itulah yang dibutuhkan di sini.
Tes `test_deleting_user_removes_their_devices_and_locations` dan
`test_location_in_use_cannot_be_deleted` memeriksa keduanya.

## Konsep 4: migrasi tiga langkah untuk data yang sudah ada

Kolom `owner` wajib diisi, tetapi database yang sudah berjalan (laptop dan server) berisi
lokasi tanpa pemilik. Polanya sama dengan migrasi lokasi di tahap 1: tambah → isi → wajibkan.

| Migrasi | Isi |
|---|---|
| `0017_location_owner` | kolom `owner` boleh kosong; nama tidak lagi unik global; `Device.location` → `RESTRICT` |
| `0018_location_owner_data` | `RunPython`: memberi pemilik pada setiap lokasi |
| `0019_location_owner_required` | `owner` wajib; constraint unik (`owner`, `name`) |

Aturan di `0018` untuk satu lokasi lama:

1. Pemiliknya = pengguna yang **paling banyak** punya device di lokasi itu (bila seri: id
   terkecil).
2. Pengguna lain yang juga punya device di sana mendapat **salinan** lokasi (nama,
   koordinat, deskripsi sama), dan device-nya dipindah ke salinan itu. Pengaturan dashboard
   "sembunyikan lokasi ini" milik pengguna tersebut ikut dipindah ke salinannya.
3. Lokasi tanpa device diberikan ke Admin pertama (atau pengguna pertama).

Contoh: "Lab" dipakai 2 device siti dan 1 device budi → "Lab" tetap milik siti, budi
mendapat "Lab" sendiri. Skenario ini diuji di `devices/tests_migrations.py` dengan
memundurkan database ke `0017`, mengisi data lama, lalu menjalankan migrasi sungguhan.

Migrasi ini **tidak bisa dibatalkan dengan bersih**: setelah salinan dibuat, mundur ke
`0016` gagal karena nama lokasi kembar melanggar aturan unik lama. Buat backup dulu
(langkah di bawah).

## Mencoba

1. `python manage.py migrate` (devices `0017`–`0019`).
2. Login sebagai budi → **Locations**: hanya lokasi budi. Klik **Add location**.
3. Tambah device: dropdown lokasi hanya berisi lokasi budi.
4. Coba buka `/devices/locations/<id lokasi milik siti>/`: 404.
5. Login sebagai Admin → **Locations**: semua lokasi dengan kolom **Owner**. Tambah device
   untuk budi dan pilih lokasi milik siti: ditolak.

```bash
python manage.py test devices dashboard
```

## Menerapkan di server

```bash
sudo systemctl start iot-backup              # backup database dulu (hasil di /srv/iot/backups)
sudo /srv/iot/app/deploy/scripts/update.sh   # pull + migrate + restart
```

Setelah itu periksa halaman **Locations** sebagai Admin: lokasi yang dulu dipakai bersama
kini muncul sekali per pemilik. Ubah nama salinannya bila perlu.
