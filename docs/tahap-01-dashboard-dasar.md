# Tahap 1 — Dashboard dasar

Catatan ini menjelaskan *mengapa* kodenya berbentuk seperti sekarang.
Disarankan dibaca sambil membuka file yang disebut.

## 1. Project vs app

Django memisahkan **project** (satu instalasi: pengaturan + URL utama, di sini
folder `config/`) dari **app** (satu modul fitur). Kita punya dua app:

| App        | Tanggung jawab                                   |
|------------|--------------------------------------------------|
| `accounts` | siapa penggunanya dan apa role-nya               |
| `devices`  | device, telemetri, dan halaman untuk melihatnya  |

Setiap app punya susunan file yang sama. Inilah "struktur baku" Django:

| File            | Isi                                                         |
|-----------------|-------------------------------------------------------------|
| `models.py`     | struktur tabel database, ditulis sebagai class Python       |
| `views.py`      | logika tiap halaman: ambil data, pilih template             |
| `urls.py`       | pemetaan alamat URL ke view                                 |
| `forms.py`      | definisi dan validasi form input                            |
| `templates/`    | HTML                                                        |
| `admin.py`      | pendaftaran model ke situs admin bawaan                     |
| `tests.py`      | tes otomatis                                                |
| `migrations/`   | riwayat perubahan struktur tabel (dihasilkan Django)        |

## 2. Perjalanan satu request

Contoh: Budi membuka `/devices/3/`.

1. `config/urls.py` — awalan `devices/` cocok, sisanya diserahkan ke `devices/urls.py`.
2. `devices/urls.py` — `<int:pk>/` cocok, `pk=3`, view-nya `DeviceDetailView`.
3. `devices/views.py` — `OwnedDeviceMixin` memastikan Budi sudah login dan
   menyaring queryset ke device miliknya; `DetailView` mengambil device ber-`pk` 3
   dari queryset itu (bila bukan miliknya: 404). `get_context_data()` menambahkan
   nilai terkini dan halaman history.
4. `devices/templates/devices/device_detail.html` — data tadi dirangkai menjadi HTML,
   di dalam kerangka `templates/base.html`.

Pola ini disebut **MTV**: Model (data), Template (tampilan), View (logika).

## 3. Pengguna dan role

- `accounts/models.py` — `User` mewarisi `AbstractUser` milik Django dan menambah
  kolom `role` (`ADMIN` / `USER`). Model user kustom harus dibuat **sebelum
  migrasi pertama**, sebab menggantinya di tengah jalan rumit. Itulah alasan kita
  membuatnya sekarang walau baru menambah satu kolom.
- `settings.py` → `AUTH_USER_MODEL = "accounts.User"` memberi tahu Django untuk
  memakai model itu.
- Login/logout memakai view bawaan (`accounts/urls.py`); kita hanya menyediakan
  template `templates/registration/login.html`.
- `accounts/mixins.py` → `AdminRequiredMixin` menjaga halaman manajemen pengguna.
- Pendaftaran mandiri: `/accounts/register/` → `RegisterView`. Form-nya
  (`RegisterForm`) tidak memuat field `role`, sehingga akun baru selalu ber-role
  User; setelah tersimpan, `login()` langsung membuatkan sesi.

Jadi ada dua jalan membuat akun: pengunjung mendaftar sendiri (selalu User), atau
Admin membuatkannya lewat menu Pengguna (boleh memilih role).

Dua hal yang mudah tertukar:

- **`role`** = hak di aplikasi kita (menu Pengguna, melihat semua device).
- **`is_staff` / `is_superuser`** = hak masuk ke situs `/admin/` bawaan Django.

Perbandingan dengan ThingsBoard: di sana ada tiga tingkat (System Admin → Tenant
Admin → Customer User). Kita mulai dari dua role; kolom `role` berupa pilihan,
sehingga menambah tingkat nanti tidak mengubah rancangan.

## 4. Device dan kepemilikan

- `Device.owner` adalah `ForeignKey` ke user: satu user, banyak device.
- Aturan "pengguna hanya melihat device miliknya" ditulis **satu kali**, di
  `OwnedDeviceMixin.get_queryset()`, dan dipakai semua view device. Menyembunyikan
  tombol di template saja tidak cukup — penyaringan harus terjadi di view/queryset.

## 5. Telemetri: card dan tabel

Aturan rancangan project ini: **satu device = satu variabel**.

- `Device` mencatat variabel yang diukurnya (`variable`, mis. `temperature`) dan
  satuannya (`unit`, mis. `°C`). Alat fisik yang mengukur beberapa besaran
  didaftarkan sebagai beberapa device.
- `Telemetry` hanya menyimpan deret waktu `(device, value, ts)`.
- **Card** — `Device.latest` mengambil baris terbaru device itu.
- **Tabel history** — `Paginator` memotong data per 25 baris; `?page=` memilih halaman.
- **Daftar device** — nilai terkini tiap device diambil dengan `Subquery`
  (lihat `DeviceListView`), sehingga tetap satu query untuk seluruh daftar.
- Waktu disimpan dalam UTC (`USE_TZ = True`) dan ditampilkan dalam `Asia/Jakarta`.

Ini sengaja berbeda dari ThingsBoard, yang mengizinkan banyak *key* per device.
Konsekuensinya perlu diingat saat membuat jalur masuk data nanti: satu pesan dari
alat multi-sensor harus dipecah ke beberapa device.

### Catatan: migrasi skema vs migrasi data

Rancangan awal memakai banyak key per device. Perubahannya dikerjakan dalam tiga
migrasi di `devices/migrations/`, dan ini pola yang umum dipakai:

1. `0002` menambah kolom baru (`variable`, `unit`) — struktur berubah, data lama utuh.
2. `0003` memindahkan data: device ber-banyak-key dipecah menjadi satu device per
   key (`RunPython`). Ini disebut *migrasi data*.
3. `0004` baru menghapus kolom lama (`key`).

Urutan "tambah → pindahkan → hapus" menjaga agar tidak ada data yang hilang.

## 6. Latihan

Cara terbaik memahami alurnya adalah mengubahnya sendiri:

1. Tambahkan kolom `location` pada `Device` (model → `makemigrations` → `migrate`
   → form → template). Ini menyentuh hampir semua lapisan.
2. Tampilkan jumlah baris telemetri tiap device di daftar device
   (petunjuk: `annotate(Count(...))`, lihat `UserListView`).
3. Buka `/admin/` sebagai `admin`, tambahkan satu baris telemetri secara manual,
   lalu lihat card-nya berubah.

## 7. Batasan tahap ini (bahan tahap berikutnya)

- Belum ada jalur masuk data dari device (HTTP/MQTT) maupun token device.
- Nilai telemetri hanya angka; belum ada atribut atau tipe lain.
- Halaman tidak memperbarui diri; perlu muat ulang untuk melihat data baru.
- Belum ada grafik, alarm, maupun rule engine.
- Pendaftaran terbuka tanpa verifikasi email, CAPTCHA, atau persetujuan Admin;
  siapa pun yang bisa membuka situs bisa membuat akun.
- Belum ada fitur lupa/ganti password oleh pengguna sendiri.
- Admin belum bisa mengganti password pengguna lain dari dashboard
  (untuk sementara lewat `/admin/`).
