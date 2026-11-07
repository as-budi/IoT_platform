# Tahap 26 (tambahan) — Gaya tampilan FILKOM

Selain gaya **Standard** (biru), platform punya gaya **FILKOM** yang warnanya selaras dengan
[filkom.ub.ac.id](https://filkom.ub.ac.id/). Gaya dan mode warna (Light / Dark / System) dipilih
terpisah, jadi keempat kombinasi bisa dipakai.

**Kedua gaya memakai tata letak, logo, identitas, bentuk, dan huruf (Inter) yang sama.** Yang
berbeda hanya warna.

## Kerangka bersama (kedua gaya)

| Bagian | Isi |
|---|---|
| Side menu | setinggi layar; di atasnya logo IoT, **IoT Platform** / *Robotic and Embedded System Laboratory*; dapat disembunyikan |
| Bilah atas | dimulai setelah side menu; paling kiri tombol tiga garis untuk menyembunyikan/menampilkan side menu (di ponsel: membuka side menu, logo dan identitas ikut tampil); di halaman publik selebar halaman dengan logo dan identitas |
| Kanan atas (layar lebar) | pilihan Style dan Theme, profil (avatar, nama, peran), logout; di layar kecil di bagian bawah side menu |
| Kaki halaman | *Faculty of Computer Science - Universitas Brawijaya* (kiri), *Computer Engineering Program* (kanan) |
| Huruf | Inter |
| Bentuk | membulat: kartu dan tabel 12 px, tombol dan input 8 px, pill bulat penuh |
| Tabel di kartu | sel paling kiri/kanan diberi jarak 1,25 rem agar teks tidak terpotong |

## Perbedaan warna

| Bagian | Standard | FILKOM |
|---|---|---|
| Warna utama | biru | navy `#002647`, navy gelap `#000D18`, oranye `#EC831E` |
| Logo | kotak biru, gambar putih | kotak navy, gelombang oranye, node putih |
| Side menu | warna kartu, menu aktif biru muda | navy, menu aktif oranye transparan bergaris oranye |
| Bilah atas | warna kartu, garis tipis abu-abu | navy gelap, garis oranye 3 px |
| Kaki halaman | warna kartu | navy, garis oranye |
| Judul | biasa | navy; judul halaman bergaris oranye pendek, judul lokasi di dashboard bergaris oranye penuh |
| Tombol utama | biru, teks putih | oranye, teks navy (putih di atas oranye hanya ±2,7:1, navy ±6:1) |
| Kepala tabel | abu-abu muda | navy |
| Mode gelap | abu-abu gelap | navy sangat gelap (`#07131F`), kartu `#0E2033` |

Logo (`templates/includes/ui_logo.html`) adalah gambar SVG sendiri (bukan logo FILKOM/UB):
kotak membulat, gelombang sinyal di atas node dan jalur sirkuitnya. Warnanya diambil dari
variabel `--ui-logo-bg`, `--ui-logo-frame`, `--ui-logo-wave`, `--ui-logo-node`.

## Memilih gaya

- **Per akun**: kanan atas (layar kecil: bawah side menu) → *Style* → **Standard** / **FILKOM**.
  Disimpan di `User.ui_style` (migrasi accounts 0003), jadi sama di semua perangkat.
- **Bawaan situs** (`.env`): `UI_STYLE=standard|filkom`, untuk pengunjung (halaman login,
  daftar, dokumentasi) dan akun yang belum memilih.
- **Kunci**: `UI_STYLE_SELECTABLE=False` menyembunyikan pilihan; semua akun memakai `UI_STYLE`.

## Cara kerja

1. `accounts/context_processors.py` menentukan `ui_style` untuk setiap halaman.
2. `templates/base.html` memasang `<html data-ui="...">`, lalu untuk kedua gaya memakai
   `includes/ui_topbar.html`, `includes/brand.html` (logo + identitas) dan
   `includes/ui_footer.html`. Untuk gaya FILKOM, `static/css/filkom.css` dimuat sesudah `app.css`.
3. `app.css` berisi kerangka bersama dengan warna dari variabel `--ui-*` (bilah atas, kaki
   halaman, avatar, menu aktif, logo) dan `--app-*`; nilai bawaannya adalah warna Standard.
4. `filkom.css` **hanya mengganti warna**: variabel `--app-*` dan `--ui-*` (terang dan gelap),
   ditambah warna beberapa komponen (side menu, tombol utama, kepala tabel, chip device, pill
   lokasi). Semua aturannya diawali `html[data-ui="filkom"]`.
5. Tombol pilihan: `includes/ui_style_switch.html` → `POST /accounts/ui-style/`
   (`accounts.views.UiStyleView`), lalu kembali ke halaman asal.

Catatan: Bootstrap 5.3 memberi `.offcanvas-lg` di layar lebar
`background-color: transparent !important`; `app.css` dan `filkom.css` menimpanya dengan
`!important` agar warna side menu tetap terpasang.

Komponen baru cukup memakai variabel `--app-*` / `--ui-*` agar otomatis ikut kedua gaya; periksa
tetap di keempat kombinasi.

## File

| File | Isi |
|---|---|
| `accounts/models.py`, `accounts/migrations/0003_user_ui_style.py` | `User.ui_style` |
| `accounts/context_processors.py`, `accounts/views.py`, `accounts/urls.py` | `ui_style`, `UiStyleView` |
| `config/settings.py`, `.env.example`, `deploy/env.production.example` | `UI_STYLE`, `UI_STYLE_SELECTABLE` |
| `templates/base.html`, `templates/includes/ui_*.html`, `brand.html` | kerangka, logo, identitas, pilihan |
| `static/css/app.css` | kerangka bersama dan warna Standard |
| `static/css/filkom.css` | warna FILKOM |
| `accounts/tests_ui_style.py` | tes |

## Update server

```bash
sudo /srv/iot/app/deploy/scripts/update.sh   # migrasi accounts 0003, collectstatic
```

Tambahkan `UI_STYLE=filkom` ke `.env` bila gaya FILKOM ingin menjadi bawaan, lalu restart
layanan web.
