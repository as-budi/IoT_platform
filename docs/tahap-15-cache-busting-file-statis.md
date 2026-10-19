# Tahap 15: Cache busting file statis

## Masalahnya

Setelah update ke server, kotak JSON di halaman aturan tampil berantakan, padahal di laptop
normal. Penyebabnya bukan kode, tetapi **cache browser**:

```nginx
location /static/ {
    alias /srv/iot/app/staticfiles/;
    expires 7d;            # "browser boleh memakai salinan ini selama 7 hari"
}
```

- `json_viewer.js` adalah file **baru**, jadi browser langsung mengunduhnya.
- `app.css` sudah pernah diunduh, dan namanya tidak berubah. Browser memakai salinan
  lama dari cache tanpa bertanya ke server, sehingga gaya `.jv-*` yang baru tidak ada.

Setiap pengguna bisa melihat campuran JS baru + CSS lama sampai 7 hari. Menyuruh semua
orang menekan Cmd+Shift+R bukan solusi.

Menurunkan `expires` juga bukan solusi yang baik: browser jadi lebih sering bertanya ke server,
halaman lebih lambat, dan masalahnya tetap ada selama durasi cache itu.

## Solusinya: nama file berisi hash isinya

Ide dasarnya: **jangan pernah mengubah isi file di bawah nama yang sama.** Bila isinya
berubah, ganti namanya.

`collectstatic` di produksi kini menyalin setiap file dua kali:

```
staticfiles/css/app.css                  nama asli
staticfiles/css/app.3f9a1c0b7d2e.css     nama + 12 karakter MD5 isinya
staticfiles/staticfiles.json             peta: "css/app.css" -> "css/app.3f9a1c0b7d2e.css"
```

Template tidak perlu diubah. `{% static 'css/app.css' %}` membaca peta itu dan menghasilkan:

```html
<link rel="stylesheet" href="/static/css/app.3f9a1c0b7d2e.css">
```

Ketika `app.css` diubah lalu `update.sh` dijalankan, hash-nya berubah, misalnya menjadi
`app.81c4e0aa92f1.css`. HTML yang baru menunjuk URL baru yang belum pernah ada di cache
browser mana pun, jadi **pasti diunduh**. Tidak ada lagi CSS lama bercampur JS baru.

Karena file ber-hash tidak pernah berubah isinya, browser boleh menyimpannya **setahun** tanpa
bertanya lagi. Hasilnya justru lebih cepat daripada sebelumnya.

Bonus: `collectstatic` juga mengganti rujukan di dalam CSS (`url(...)`, mis. gambar dan font
admin Django) dengan nama ber-hash.

## Yang diubah

| File | Isi |
|---|---|
| `config/storage.py` | `HashedStaticStorage`, turunan `ManifestStaticFilesStorage` bawaan Django |
| `config/settings.py` | `STORAGES["staticfiles"]` memakai `HashedStaticStorage` bila `DEBUG=False` |
| `deploy/nginx/iot-platform.conf` | cache setahun untuk file ber-hash, 1 jam untuk nama asli |
| `config/tests.py` | 5 tes |

### Kapan aktif

```python
STATIC_HASHED = DJANGO_STATIC_HASHED=True  atau  (DEBUG=False dan bukan "manage.py test")
```

| Lingkungan | Penyimpanan | Alasan |
|---|---|---|
| Laptop (`DEBUG=True`, `runserver`) | biasa | `runserver` melayani file langsung dari `static/`, tanpa cache panjang, tanpa perlu `collectstatic` |
| Tes | biasa | tes tidak menjalankan `collectstatic` |
| Server (`DEBUG=False`) | **ber-hash** | `update.sh` selalu menjalankan `collectstatic` |

`DJANGO_STATIC_HASHED=True/False` di `.env` bisa memaksa salah satunya.

### Versi yang "pemaaf"

Bawaan Django: bila template memakai `{% static 'js/baru.js' %}` tetapi `collectstatic`
belum dijalankan setelah file itu ditambahkan, **seluruh halaman error 500** (`ValueError:
Missing staticfiles manifest entry`). Satu kelalaian bisa menjatuhkan seluruh situs.

`HashedStaticStorage` memakai nama asli sebagai cadangan: halaman tetap tampil, file itu saja
yang tanpa cache busting (atau 404 bila memang tidak ada).

```python
class HashedStaticStorage(ManifestStaticFilesStorage):
    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            return name
```

### Nginx

```nginx
# File ber-hash: isinya tidak pernah berubah -> simpan setahun.
location ~* "^/static/(.+\.[0-9a-f]{12}\.[a-z0-9]+)$" {
    alias /srv/iot/app/staticfiles/$1;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    add_header X-Content-Type-Options nosniff always;
}

# Nama asli: hanya 1 jam.
location /static/ {
    alias /srv/iot/app/staticfiles/;
    expires 1h;
    add_header X-Content-Type-Options nosniff always;
}
```

- Location regex (`~*`) diperiksa sebelum location prefix biasa dipakai, jadi URL ber-hash
  masuk ke blok pertama.
- `immutable` memberi tahu browser bahwa isi file tidak akan berubah. Browser bahkan tidak
  memeriksa ulang saat halaman di-reload.
- Nama asli masih bisa diminta, misalnya oleh halaman HTML lama yang masih terbuka di tab.
  Untuk itu cache-nya hanya 1 jam.

## Menerapkan di server

1. Commit dan push dari Mac, lalu jalankan update seperti biasa:

   ```bash
   sudo /srv/iot/app/deploy/scripts/update.sh
   ```

   `collectstatic` di dalam skrip itu kini juga membuat file ber-hash dan `staticfiles.json`.

2. Perbarui konfigurasi Nginx (satu kali saja). Cara termudah: salin ulang file dari
   repository seperti di panduan deploy langkah 8.3, setelah membuat cadangan:

   ```bash
   sudo cp /etc/nginx/sites-available/iot-platform ~/iot-platform.nginx.bak
   sudo cp /srv/iot/app/deploy/nginx/iot-platform.conf /etc/nginx/sites-available/iot-platform
   sudo sed -i 's/iot.contoh.my.id/iot.reslab.cloud/g' /etc/nginx/sites-available/iot-platform
   sudo nginx -t && sudo systemctl reload nginx
   ```

   Bila Anda pernah mengubah file itu langsung di server, ganti hanya blok
   `location /static/ { ... }` dengan dua blok di atas.

3. Periksa:

   ```bash
   # Nama file di HTML harus ber-hash:
   curl -s https://iot.reslab.cloud/accounts/login/ | grep -o '/static/css/app[^"]*'
   # -> /static/css/app.3f9a1c0b7d2e.css

   # File ber-hash: cache setahun
   curl -sI https://iot.reslab.cloud/static/css/app.3f9a1c0b7d2e.css | grep -i cache-control
   # -> Cache-Control: public, max-age=31536000, immutable
   ```

   Ganti `3f9a1c0b7d2e` dengan hash yang muncul di perintah pertama.

Pengguna yang browsernya masih menyimpan `app.css` lama tidak perlu melakukan apa pun: HTML baru
sudah menunjuk nama ber-hash yang belum pernah mereka unduh.

## Catatan

- Tambahkan file statis selalu lewat `{% static '...' %}`, jangan menulis `/static/...`
  langsung di template atau JavaScript, supaya nama ber-hash dipakai.
- File di `static/` dengan `url(...)` ke file yang tidak ada membuat `collectstatic` gagal
  (`update.sh` berhenti sebelum restart, situs lama tetap berjalan). Pesannya menyebut file
  yang dicari.
- `staticfiles/` berisi nama asli dan nama ber-hash dari semua versi yang pernah
  di-collectstatic. Ukurannya kecil, tetapi bila ingin dibersihkan:
  `collectstatic --clear --noinput`.

```bash
python manage.py test config
```
