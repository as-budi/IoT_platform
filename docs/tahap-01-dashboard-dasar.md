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
   daftar variabel device itu beserta nilai terkininya.
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
- Aturan "pengguna hanya melihat device miliknya" ditulis **satu kali**, di fungsi
  `visible_devices()` (`devices/views.py`), dan dipakai semua view device maupun
  variabel. Menyembunyikan tombol di template saja tidak cukup — penyaringan harus
  terjadi di view/queryset.

## 5. Device, variabel, dan telemetri

Rancangan data project ini mengikuti platform IoT pada umumnya:

```
Device      alat fisik (mis. ESP32 + DHT22): nama, lokasi, pemilik, status
  Variable  besaran yang diukur alat itu: key, label, satuan
    Telemetry  deret waktu (nilai, waktu) untuk satu variabel
```

- `Variable.key` adalah nama yang nanti dipakai device di payload, mis.
  `{"temperature": 25.1, "humidity": 60}`. Karena itu dibatasi ke huruf, angka, dan
  garis bawah (`RegexValidator`), dan tidak boleh kembar dalam satu device
  (`UniqueConstraint`). `label` adalah nama tampilan; bila kosong, key yang tampil.
- Dua tampilan:
  - **Per device** (`/devices/<id>/`, `DeviceDetailView`) — satu card nilai terkini
    untuk setiap variabel. Nilai terbaru, waktu, dan jumlah baris tiap variabel
    diambil sekaligus dengan `Subquery` (fungsi `with_latest_values()`).
  - **Per variabel** (`/devices/<id>/variables/<id>/`, `VariableDetailView`) —
    card nilai terkini, chart 24 jam terakhir, dan tabel history.
- **URL bertingkat.** View variabel mengambil device dari `<device_pk>` di URL lewat
  `visible_devices()`, lalu mencari variabel hanya di dalam device itu
  (`OwnedVariableMixin`). Akibatnya variabel device orang lain, atau variabel yang
  dipasangkan dengan device yang salah di URL, selalu 404.
- **Chart** — view menyiapkan `chart_points` berupa list `{x: milidetik, y: nilai}`;
  template mengirimnya ke browser dengan filter `json_script`, lalu Chart.js
  (dimuat dari CDN) menggambarnya. Inilah pola umum "data dari Django, gambar oleh
  JavaScript". Script khusus halaman ini diletakkan di `{% block extra_js %}`.
- **Tabel history** — `Paginator` memotong data per 25 baris; `?page=` memilih halaman.
- **Daftar device** — variabel tiap device diambil dengan `prefetch_related`
  (satu query tambahan untuk semua device, bukan satu per device), sedangkan jumlah
  baris dan waktu data terakhir dengan `Subquery`.
- Waktu disimpan dalam UTC (`USE_TZ = True`) dan ditampilkan dalam zona waktu `TIME_ZONE`.

### Catatan: riwayat perubahan model dan pola migrasi

Model ini sudah berganti dua kali, dan keduanya memakai pola yang sama:
**tambah → pindahkan data → hapus**, supaya tidak ada data yang hilang.

| Migrasi | Perubahan |
|---|---|
| `0001` | awal: banyak *key* per device, tanpa satuan |
| `0002`–`0004` | ke "satu device = satu variabel" (device ber-banyak-key dipecah) |
| `0005`–`0006` | label bahasa Inggris, kolom `location` |
| `0007`–`0009` | kembali ke "satu device = banyak variabel", kini dengan model `Variable` |

Pada `0007`–`0009`: `0007` membuat tabel `Variable` dan kolom `telemetry.variable`
(sementara boleh kosong), `0008` (*migrasi data*, `RunPython`) membuat satu
variabel untuk setiap device lama dan memindahkan telemetrinya, `0009` menghapus
kolom lama (`telemetry.device`, `device.variable`, `device.unit`).

Alasan kembali ke banyak variabel per device: satu alat fisik memiliki satu
koneksi, satu token, satu firmware, dan satu status online — semua itu milik alat,
bukan milik tiap sensornya. Device hasil pemecahan lama tidak digabung otomatis,
karena Django tidak bisa tahu mana yang sebenarnya satu alat.

## 6. Latihan

Cara terbaik memahami alurnya adalah mengubahnya sendiri:

1. ~~Tambahkan kolom `location` pada `Device`~~ — **sudah dikerjakan**. Lacak
   jejaknya sebagai contoh satu perubahan yang melewati semua lapisan:
   `models.py` (field) → `makemigrations`/`migrate` (tabel) → `forms.py`
   (daftar `fields`) → `device_list.html` dan `device_detail.html` (tampilan)
   → `admin.py`. Lupa satu langkah, kolomnya tidak muncul di tempat itu.
2. ~~Tampilkan jumlah baris telemetri tiap device~~ — **sudah dikerjakan**
   (kolom "Rows"). Perhatikan `DeviceListView`: jumlahnya dihitung dengan
   `Subquery` + `Coalesce`, bukan `annotate(Count("telemetry"))` seperti di
   `UserListView`. Komentar di kode menjelaskan alasannya. Latihan lanjutan:
   ganti sementara dengan `Count("telemetry")`, lalu bandingkan SQL keduanya
   lewat `print(queryset.query)` di `manage.py shell`.
3. Buka `/admin/` sebagai `admin`, tambahkan satu baris telemetri secara manual
   untuk salah satu variabel, lalu lihat card-nya berubah di halaman device.
4. Tambahkan sparkline (chart mini tanpa sumbu) di setiap card halaman device.
   Petunjuk: siapkan titik-titiknya di `DeviceDetailView`, lalu pakai ulang pola
   `json_script` + Chart.js dari halaman variabel.

## 7. Batasan tahap ini (bahan tahap berikutnya)

- Belum ada jalur masuk data dari device (HTTP/MQTT) maupun token device.
- Nilai telemetri hanya angka; belum ada atribut atau tipe lain.
- Halaman tidak memperbarui diri; perlu muat ulang untuk melihat data baru.
- Belum ada alarm maupun rule engine.
- Pendaftaran terbuka tanpa verifikasi email, CAPTCHA, atau persetujuan Admin;
  siapa pun yang bisa membuka situs bisa membuat akun.
- Belum ada fitur lupa/ganti password oleh pengguna sendiri.
- Admin belum bisa mengganti password pengguna lain dari dashboard
  (untuk sementara lewat `/admin/`).
