# Tahap 25: Halaman bersih untuk pengguna umum dan dokumentasi dengan pencarian

Platform kini dipakai umum. Tahap ini memisahkan dua pembaca yang dulu bercampur di halaman web:

| Pembaca | Butuh | Tempatnya sekarang |
|---|---|---|
| **Pengguna** platform | cara memakai fitur, alamat dan contoh untuk device | halaman web dan **dokumentasi pengguna** `/docs/` |
| **Pengembang / pengelola server** | proses yang harus berjalan, isi `.env`, perintah `manage.py`, tahap pengembangan | folder `docs/` di repo (berkas-berkas tahap ini) |
| **Admin** platform | tahu bila konfigurasi server membuat fitur tidak jalan | catatan khusus Admin di halaman |

Tiga pekerjaan:

1. Keterangan pengembangan dihapus dari semua halaman dan dari komentar template firmware.
2. Keterangan itu dipindah ke dokumen tahap yang relevan; di halaman diganti keterangan untuk pengguna.
3. Halaman "How to connect real devices" (tahap 24) dikembangkan menjadi **dokumentasi umum**
   seluruh platform, dengan **pencarian**.

## 1. Apa yang dianggap "keterangan pengembangan"

| Jenis | Contoh | Perlakuan |
|---|---|---|
| Proses/worker yang harus berjalan | `python manage.py mqtt_bridge`, `coap_server`, `rule_engine`, `housekeeping`, `runserver`, `runall` | dihapus dari halaman, dicatat di dokumen tahapnya |
| Pengaturan server | `.env`, `OTA_BASE_URL`, `COAP_PUBLIC_HOST`, `TELEGRAM_BOT_TOKEN` | hanya Admin yang melihat namanya; pengguna melihat "belum tersedia, hubungi administrator" |
| Istilah tahap pengembangan | "Coming in a later stage", "tahap 9" di komentar | diganti ("Coming soon") atau dihapus |
| Istilah teknis yang tidak perlu | "Live (WebSocket)", "Polling every 5 s", `brew install libcoap` | diganti bahasa pengguna |
| Teknis tetapi **berguna** bagi pengguna | contoh `curl`, `mosquitto_pub`, `coap-client`, topik MQTT, kode status HTTP | **dipertahankan**: pengguna platform IoT memang memprogram device |

Komentar template (`{# #}`, `{% comment %}`) tidak terlihat di browser, jadi tetap berbahasa Indonesia.

### Catatan khusus Admin

`devices/templates/devices/includes/connect_reach.html` dipakai untuk semua alamat server yang
mungkin tidak terjangkau device (CoAP, unduhan OTA, alamat HTTP di dokumentasi):

```
reach == "loopback" dan user.is_admin  -> "Administrator: devices cannot reach … Set COAP_PUBLIC_HOST …
                                           Only administrators see this note."
reach == "loopback", pengguna biasa    -> "CoAP is not available from devices on this server yet.
                                           Please contact the administrator."
reach == "private"                     -> "… is a private network address …" (berguna bagi semua)
```

## 2. Peta pemindahan

| Dulu di | Bunyinya | Sekarang di halaman | Catatan pengembang dipindah ke |
|---|---|---|---|
| Halaman device, tab HTTP | Served by `runserver` | tautan ke *Device API reference* | `tahap-02-http-api.md` |
| Halaman device, tab MQTT | Data is exchanged only while `manage.py mqtt_bridge` is running | (dihapus) | `tahap-03-mqtt.md` |
| Halaman device, tab CoAP | … while `manage.py coap_server` is running; reach this computer; `COAP_PUBLIC_HOST` in `.env`; `brew install libcoap` | penjelasan UDP + catatan Admin | `tahap-06-coap.md` |
| Halaman device, Connection history | Checked by the `housekeeping` worker | The status is checked every few seconds | `tahap-16-…md` |
| Kartu Firmware | `OTA_BASE_URL` in `.env`, `runall 0.0.0.0:8000` | catatan Admin / pengguna | `tahap-09-ota.md` |
| Form jadwal aturan | The rule engine worker (`manage.py rule_engine`) must be running | run yang terlambat dilewati dan dicatat | `tahap-14-penjadwalan.md` |
| Detail aturan ECA | Rules are evaluated by the `rule_engine` worker | (dihapus) | `tahap-07-rule-engine-eca.md` |
| Pilih model aturan | Coming in a later stage | Coming soon | `tahap-07-rule-engine-eca.md` |
| Profil, kartu Telegram | the server has no Telegram bot configured | not available on this server | `tahap-17-notifikasi-telegram.md` |
| Status pembaruan langsung (JS) | Live (WebSocket), Polling every … | Live, Checking for new data every … | `tahap-04-websocket.md` |
| Panduan koneksi (tahap 24) | `runall`, `mqtt_bridge`, `coap_server`, `docs/deploy-vps.md`, certbot | bab dokumentasi tanpa catatan server | `tahap-24-…md`, dokumen ini |
| Komentar `firmware/esp32_telemetry` | mode lokal/produksi, tahap 9/24 | komentar Inggris untuk pengguna | `tahap-02-http-api.md` |
| Komentar `firmware/esp32_mqtt` | `.env`, `docs/deploy-vps.md`, `manage.py mqtt_accounts`, `runall`, tahap 3/9/16/24 | komentar Inggris untuk pengguna | `tahap-03-mqtt.md`, `tahap-09-ota.md` |
| Komentar `firmware/esp32_coap` | `scripts/fake_coap_device.py`, `docs/tahap-06`, "Mac: coap_server" | komentar Inggris untuk pengguna | `tahap-06-coap.md` |

Komentar sketch diganti dengan hati-hati: **kode** ketiga sketch tidak berubah. Ini diperiksa
dengan membandingkan isi sketch lama dan baru setelah semua komentar dibuang. Satu-satunya
perubahan di luar komentar adalah teks placeholder `"paste-MQTT_TOPIC_PREFIX-here"` menjadi
`"paste-the-topic-prefix-here"` (template unduhan tetap menggantinya dengan prefix server).
Baris pengaturan yang diisi otomatis oleh `devices/connect.py` (tahap 24) tidak diubah bentuknya.

## 3. Dokumentasi pengguna (app `userdocs`)

| URL | Isi |
|---|---|
| `/docs/` | daftar bab per bagian |
| `/docs/<slug>/` | satu bab, dengan daftar isi, bab sebelumnya/berikutnya |
| `/docs/search/?q=...` | hasil pencarian |

Terbuka **tanpa login**, supaya calon pengguna bisa membaca sebelum mendaftar (tautan
*Documentation* ada di bilah atas halaman login). Bagi yang login, menu samping punya bagian
**Help → Documentation**, dan halaman-halaman utama punya tombol **Help** ke bab yang sesuai
(`templates/includes/help_link.html`).

### Isi (22 bab, bahasa Inggris seperti antarmuka)

| Bagian | Bab |
|---|---|
| Basics | Getting started, Your account |
| Devices and data | Devices and locations, Variables, Dashboard/charts/history, Controlling devices |
| Connecting devices | Connect an ESP32, Device API reference, Security and certificates, Firmware updates and MAC address |
| Automation | Rules overview, ECA rules, Rule chains, State machines, Schedules, Alarms and notifications |
| Working together | Sharing devices, Activity log |
| Apps and integrations | MQTT sandbox, Connected apps and API |
| Help | Troubleshooting, For administrators |

### Struktur kode

| File | Isi |
|---|---|
| `userdocs/pages.py` | `PAGES`: slug, judul, bagian, ringkasan, dalam urutan menu |
| `userdocs/templates/userdocs/pages/<slug>.html` | isi satu bab (fragmen HTML, tanpa `extends`) |
| `userdocs/context.py` | `docs_context()`: fakta server dari settings (alamat web, broker, CoAP, batas waktu, ukuran firmware, …); `device_context()`: device terpilih di bab Connect an ESP32 |
| `userdocs/views.py` | `index`, `page`, `search_view` |
| `userdocs/search.py` | indeks dan pencarian |
| `userdocs/templates/userdocs/layout.html`, `index.html`, `page.html`, `search.html` | kerangka |
| `static/css/app.css` | gaya `.docs-*` |
| `userdocs/tests.py` | tes |

**Nilai server tidak ditulis mati.** Bab memakai `{{ mqtt.host }}`, `{{ http.base_url }}`,
`{{ limits.offline_seconds }}`, dst. dari `docs_context()`, jadi dokumentasi selalu cocok
dengan server tempat ia dibaca. Hanya bab *Connect an ESP32* yang memakai data pengguna:
`?device=<id>` mengisi token device itu, khusus pengguna login dengan peran Editor ke atas
(aturan yang sama dengan bagian "Connect a device", tahap 19).

### Menambah atau mengubah bab

1. Buat `userdocs/templates/userdocs/pages/<slug>.html`. Judul bagian: `<h2 id="...">`, sub-bagian
   `<h3 id="...">`. Id itulah tujuan tautan `…/<slug>/#id` dan hasil pencarian.
2. Tambahkan `Page("<slug>", "Judul", "Bagian", "Ringkasan satu kalimat.")` di `PAGES`.
3. Fragmen dirender juga **tanpa pengguna** untuk indeks pencarian: jangan bergantung pada
   `request`/`user` kecuali di dalam `{% if user.is_authenticated %}`.
4. Jalankan `python manage.py test userdocs`. Tes memeriksa semua bab bisa dibuka (dengan dan
   tanpa login) dan tidak memuat kata-kata pengembang (`manage.py`, `.env`, `runserver`, nama
   worker, `docs/…`, "tahap", …).

### Pencarian

Bab berjumlah puluhan, jadi tidak perlu mesin pencari terpisah (Elasticsearch, dll.):

1. Saat pencarian pertama, setiap bab dirender dengan `docs_context()`, lalu dipotong per
   judul `<h2>`/`<h3>` ber-id menjadi **bagian** (`split_sections`). Hasilnya disimpan di
   memori proses (`_index`), dengan kunci alamat web server.
2. Kueri dipecah menjadi kata (minimal 2 huruf, maksimal 8 kata). **Semua** kata harus ada di
   bagian itu (judul bab, judul bagian, atau isinya).
3. Skor = jumlah kemunculan di isi + 8 bila ada di judul bagian + 4 bila ada di judul bab (judul
   bab hanya dihitung untuk bagian pembukanya, supaya tidak semua bagian bab ikut cocok).
4. Hasil: maksimal 30, dengan cuplikan ±180 karakter di sekitar kecocokan pertama. Teks
   di-*escape* dulu, baru kata yang cocok dibungkus `<mark>`, jadi isi bab tidak bisa
   menyisipkan HTML ke hasil.

Indeks dibangun ulang saat proses web dimulai ulang (mis. setelah deploy), jadi bab yang diubah
langsung terlihat di pencarian setelah restart.

## 4. Yang diubah

| File | Perubahan |
|---|---|
| `userdocs/` (app baru), `config/settings.py`, `config/urls.py` | dokumentasi di `/docs/` |
| `templates/base.html` | menu *Help → Documentation*; tautan *Documentation* di bilah atas halaman publik |
| `templates/includes/help_link.html` | tombol Help; dipasang di Dashboard, Devices, Locations, Rules, Alarms, Firmware, Sharing groups, Activity log, My profile, form aturan, pilih model |
| `devices/templates/devices/device_detail.html` | tab HTTP/MQTT/CoAP dan Connection history tanpa keterangan pengembang; tombol ke bab Connect an ESP32 |
| `devices/templates/devices/includes/connect_reach.html` | catatan Admin vs pengguna |
| `devices/templates/devices/includes/connect_download.html` | tamu diminta login untuk mengunduh |
| `devices/views.py`, `devices/urls.py` | `ConnectGuideView` dihapus; `/devices/connect/` dialihkan ke `/docs/connect-esp32/`; `mqtt_info()` tanpa `show_bridge_hint` |
| `devices/templates/devices/connect_guide.html` | dihapus (isinya kini bab dokumentasi) |
| `ota/templates/ota/includes/device_firmware.html` | catatan `OTA_BASE_URL` khusus Admin; tautan MAC ke dokumentasi |
| `rules/templates/rules/includes/schedule_fields.html`, `rule_detail.html`, `model_picker.html`, `rule_form_*.html` | tanpa nama worker; "Coming soon"; tautan ke bab |
| `accounts/templates/accounts/profile.html` | teks Telegram; tombol Help |
| `static/js/auto_refresh.js` | teks status tanpa istilah teknis |
| `firmware/esp32_*/` | komentar Inggris untuk pengguna (kode tidak berubah) |
| `docs/tahap-02, 03, 04, 06, 07, 09, 14, 16, 17, 24` | bagian "Catatan" berisi keterangan yang dipindah |
| tes | `userdocs/tests.py` (baru); penyesuaian di `devices/tests_connect.py`, `ota/tests.py`, `rules/tests.py`, `notifications/tests.py` |

Tidak ada migrasi database.

## 5. Menerapkan di server

```bash
cd /srv/iot/app && git pull
python manage.py collectstatic --noinput     # app.css dan auto_refresh.js berubah
sudo systemctl restart iot.target            # proses web memuat app userdocs dan membangun ulang indeks
```

## 6. Tes

```bash
python manage.py test userdocs devices.tests_connect ota rules notifications
```

| Kelompok | Yang diuji |
|---|---|
| `DocsPageTests` | semua bab terbuka untuk tamu dan pengguna, tanpa kata-kata pengembang; daftar isi dan bab sebelumnya/berikutnya; nilai server tampil; token hanya untuk pemilik/Editor yang login; menu |
| `ServerNoticeTests` | pengguna biasa melihat "not available", Admin melihat nama pengaturan; halaman device tanpa kata-kata pengembang |
| `SearchTests` | bagian yang tepat di urutan pertama dan disorot, semua kata harus cocok, kueri kosong/pendek, cuplikan di-escape, indeks mencakup semua bab |

## 7. Menu bertingkat (pembaruan)

Menu kiri versi pertama hanya berupa daftar datar: judul bagian (huruf kecil kapital, abu-abu)
dan bab, sehingga sulit dibedakan mana yang bisa diklik. Kini menu mengikuti pola dokumentasi
seperti PyTorch:

```
Section Navigation
Automation                      <- bagian: tebal, bukan tautan (role="heading")
    Rules overview          ⌄   <- bab: tautan, menjorok; panah membuka sub-menu
    ECA rules               ⌃   <- bab yang sedang dibaca: disorot, sub-menu terbuka
      │ 1. When: the event      <- bagian di dalam bab (judul <h2>), menjorok lagi,
      │ 2. If: the condition       dengan garis pemandu; yang sedang dibaca disorot
      │ ...
    Rule chains             ⌄
```

- Sub-menu setiap bab diambil dari judul `<h2 id>` babnya. Daftar ini dihitung bersamaan dengan
  indeks pencarian (`search.build_index()` kini mengembalikan `sections` dan `tocs`), jadi tidak
  ada render tambahan per permintaan.
- Bab yang sedang dibaca: sub-menunya terbuka dan tautannya `#anchor` di halaman yang sama. Bab
  lain: sub-menu tertutup (komponen *collapse* Bootstrap), tautannya ke `/docs/<slug>/#anchor`.
- Di layar lebar (≥ 1200 px) ada kolom kanan **On this page** berisi judul `<h2>` dan `<h3>` bab
  itu; di layar lebih kecil daftar yang sama tampil di atas isi. Bagian yang sedang dibaca
  disorot di sub-menu kiri dan di kolom kanan (`IntersectionObserver` di `page.html`).
- Di layar sempit, menu tampil di atas isi dan dibuka dengan tombol **Chapters**.
- Contoh perintah di blok `<pre>` tidak lagi dipatahkan per huruf; baris panjang bisa digeser
  ke samping.

Berkas: `userdocs/templates/userdocs/layout.html`, `page.html`, `userdocs/views.py`
(`base_context()` -> `nav`), `userdocs/search.py`, `static/css/app.css` (`.docs-nav-*`,
`.docs-onpage`), tes `DocsPageTests.test_nested_navigation`.

## 8. Garis pembatas dan menyembunyikan menu utama (pembaruan)

- Menu dokumentasi dan isinya kini dipisah garis vertikal (`.docs-aside`, layar ≥ 992 px),
  seperti kolom kanan *On this page* yang sudah bergaris kiri.
- **Menu utama** platform bisa disembunyikan di layar lebar:
  - tombol di samping nama *IoT Platform* di atas menu menyembunyikannya;
  - tombol kecil di pojok kiri atas menampilkannya lagi;
  - pilihan disimpan di `localStorage` (`sidebar = "hidden"`), jadi berlaku di semua halaman
    dan setelah browser dibuka lagi. Ini fitur seluruh platform, bukan hanya dokumentasi.
- `static/js/sidebar.js` dimuat di `<head>` seperti `theme.js`: kelas `sidebar-collapsed`
  dipasang di `<html>` sebelum halaman digambar, sehingga menu tidak sempat muncul lalu hilang.
  Setelah berganti, skrip mengirim event `resize` supaya chart dan peta menyesuaikan lebarnya.
- CSS (`static/css/app.css`): `html.sidebar-collapsed .app-sidebar { display: none }` hanya
  mulai 992 px. Di layar kecil menu tetap *offcanvas* (tombol ☰), tidak terpengaruh.
- Tes: `DocsPageTests.test_menu_link` memeriksa kedua tombol, skripnya, dan garis pembatas.

## 9. Kolom tetap saat menu utama disembunyikan (pembaruan)

Sebelumnya kerangka dokumentasi memakai kolom Bootstrap berbasis persen (`col-lg-3`, `col-xl-7`,
`col-xl-2`) di dalam `.app-content` yang dibatasi 1440 px. Saat menu utama disembunyikan, seluruh
halaman bergeser ke kiri dan kotak pencarian serta *On this page* ikut berpindah, menyisakan ruang
kosong di kanan. Sekarang:

- `.docs-grid` memakai CSS grid: menu kiri **17rem**, isi `minmax(0, 1fr)`, *On this page*
  **13rem** (kelas `has-onpage`, mulai 1200 px). Hanya kolom isi yang melebar atau menyempit.
- Saat menu utama tersembunyi, `.app-content` tidak lagi dibatasi 1440 px, sehingga kotak
  pencarian dan *On this page* tetap menempel di kanan layar. Ini berlaku untuk semua halaman.
- Batas lebar teks `.docs-content` (52rem) dihapus agar isi benar-benar memakai ruang tambahan.
- Tes: `DocsPageTests.test_layout_has_fixed_side_columns`.
