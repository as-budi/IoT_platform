"""
Penyimpanan file statis dengan nama ber-hash (cache busting), dipakai di produksi.

Masalah yang diselesaikan: Nginx menyuruh browser menyimpan file statis lama
(header Expires/Cache-Control). Bila nama file tetap `css/app.css`, browser
terus memakai salinan lama walaupun isinya di server sudah berubah.

Solusinya: `collectstatic` menyalin setiap file dua kali, dengan nama asli DAN
dengan nama yang memuat 12 karakter hash MD5 isinya:

    staticfiles/css/app.css
    staticfiles/css/app.3f9a1c0b7d2e.css
    staticfiles/staticfiles.json        <- peta nama asli -> nama ber-hash

Tag `{% static 'css/app.css' %}` lalu menghasilkan `/static/css/app.3f9a1c0b7d2e.css`.
Isi berubah -> hash berubah -> URL berubah -> browser PASTI mengunduh versi baru.
File ber-hash tidak pernah berubah isinya, jadi Nginx boleh menyuruh browser
menyimpannya selama setahun (lihat deploy/nginx/iot-platform.conf).

Penjelasan lengkap: docs/tahap-15-cache-busting-file-statis.md.
"""
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class HashedStaticStorage(ManifestStaticFilesStorage):
    """Seperti ManifestStaticFilesStorage, tetapi lebih pemaaf.

    Bawaan Django: `{% static 'x.js' %}` untuk file yang tidak tercatat di
    staticfiles.json (mis. lupa menjalankan collectstatic setelah menambah file)
    melempar ValueError, dan SELURUH halaman menjadi error 500. Di sini nama asli
    dipakai sebagai cadangan: file itu tampil tanpa cache busting (atau 404 bila
    memang tidak ada), halaman lainnya tetap berjalan.
    """

    manifest_strict = False   # tidak ada di manifest -> hitung hash dari file di STATIC_ROOT

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:     # file juga tidak ada di STATIC_ROOT
            return name
