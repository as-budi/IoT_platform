# Tahap 26 (tambahan) — Gaya tampilan FILKOM

Selain gaya **Standard** (biru), platform punya gaya **FILKOM** yang warnanya mengikuti
**FILKOM Apps** ([filkom.ub.ac.id/legacy/apps](https://filkom.ub.ac.id/legacy/apps)), sama dengan gaya
FILKOM di MQTT Client (`mqtt_client/docs/tahap-m12-gaya-filkom.md`). Gaya dan mode warna
(Light / Dark / System) dipilih terpisah, jadi keempat kombinasi bisa dipakai.

**Kedua gaya memakai tata letak, logo, identitas, dan bentuk yang sama.** Yang berbeda warna dan
huruf (Standard: Inter, FILKOM: Roboto seperti FILKOM Apps).

> Pembaruan: versi pertama gaya FILKOM memakai navy `#002647` / oranye `#EC831E` dari situs
> filkom.ub.ac.id. Sekarang diganti dengan palet FILKOM Apps (tabel di bawah), agar sama dengan MQTT Client.

## Kerangka bersama (kedua gaya)

| Bagian | Isi |
|---|---|
| Side menu | setinggi layar; di atasnya logo IoT, **IoT Platform** / *Robotic and Embedded System Laboratory* dalam kepala setinggi bilah atas (garis bawahnya sejajar); dapat disembunyikan |
| Bilah atas | dimulai setelah side menu; paling kiri tombol tiga garis untuk menyembunyikan/menampilkan side menu (di ponsel: membuka side menu, logo dan identitas ikut tampil); di halaman publik selebar halaman dengan logo dan identitas |
| Kanan atas (layar lebar) | pilihan Style dan Theme, profil (avatar, nama, peran), logout; di layar kecil di bagian bawah side menu |
| Kaki halaman | *© Computer Engineering Study Program - Faculty of Computer Science - Universitas Brawijaya* (kanan) |
| Huruf | Inter |
| Bentuk | membulat: kartu dan tabel 12 px, tombol dan input 8 px, pill bulat penuh |
| Tabel di kartu | sel paling kiri/kanan diberi jarak 1,25 rem agar teks tidak terpotong |

## Perbedaan warna

| Bagian | Standard | FILKOM (FILKOM Apps) |
|---|---|---|
| Warna utama | biru | slate gelap `#232E40`, slate `#2D3A50`, oranye `#FB9337` |
| Huruf | Inter | Roboto |
| Logo | kotak biru, gambar putih | kotak oranye, gelombang slate, node putih |
| Bilah atas dan kepala side menu | warna kartu, garis tipis abu-abu | `#232E40` dengan pita `#2D3A50` 6 px di bawahnya; nama aplikasi huruf besar |
| Side menu | warna kartu, menu aktif biru muda | putih (seperti FILKOM Apps), menu aktif abu-abu slate muda bergaris oranye |
| Menu akun | warna kartu | panel gelap `#202939` |
| Latar halaman | abu-abu sangat muda | `#D8DCE3` |
| Judul | biasa | `#32415A`; judul lokasi di dashboard bergaris oranye; label angka huruf besar |
| Tombol utama | biru, teks putih | slate `#2D3A50`, teks putih (seperti tombol *Sign In* FILKOM Apps); mode gelap: oranye, teks slate |
| Kepala tabel | abu-abu muda | slate `#2D3A50`, teks putih |
| Tautan | biru | `#0062CC` |
| Teks oranye (pill lokasi) | — | `#B85D0F` (oranye `#FB9337` di atas putih hanya ±2,3:1) |
| Kaki halaman | warna kartu | `#232E40` |
| Mode gelap | abu-abu gelap | slate gelap: latar `#141B26`, kartu `#1F2938`, garis `#314057` |

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
4. `filkom.css` **hanya mengganti warna dan huruf** (`base.html` memuat Roboto, bukan Inter, untuk gaya FILKOM): variabel `--app-*` dan `--ui-*` (terang dan gelap),
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
