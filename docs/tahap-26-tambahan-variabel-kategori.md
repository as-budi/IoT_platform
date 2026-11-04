# Tambahan sebelum tahap 27: value type "Categories"

Variabel kini punya value type ketiga, **Categories**, di samping Number dan On / off. Pengguna
menulis daftar kategori (satu per baris); kategori ke-*i* disimpan sebagai angka *i*
(mulai 0) di semua tempat yang sudah ada: telemetri, desired state, kondisi dan aksi aturan,
micro rule. Tidak ada tabel baru; struktur penyimpanan angka tetap.

## 1. Data

| Perubahan | Isi |
|---|---|
| `Variable.ValueType.CATEGORY` | nilai `"CATEGORY"`, label "Categories" |
| `Variable.categories` | JSONField, daftar label berurutan (migrasi `devices 0022_variable_categories`) |
| `Variable.is_categorical`, `is_discrete` | `is_discrete` = On / off atau kategori: tanpa satuan, tanpa rata-rata |
| `Variable.value_labels` | `["OFF", "ON"]`, daftar kategori, atau `[]` |
| `Variable.encode(raw)` | nama (huruf besar/kecil sama) atau kode → angka; `ValueError` bila tidak sah |
| `Variable.format_value(v)` | ON/OFF, nama kategori (`#5` bila kode tak dikenal), atau angka |
| `Variable.value_error(v)` | satu tempat validasi nilai aktuator (ON/OFF, kategori, min/max), dipakai form kontrol, form aturan (ECA dan rule chain), dan aksi Set actuator |
| `devices.models.category_label(v, daftar)` | kode → label untuk data berbentuk dict (snapshot evaluasi) |

## 2. Form variabel

`VariableForm.categories_text`: textarea, satu label per baris. Validasi: minimal 2, maksimal 32,
panjang ≤ 40, unik tanpa membedakan huruf besar/kecil, bukan angka murni (supaya kode dan nama tidak
tertukar saat device mengirim teks). Bila variabel sudah punya data, daftar tidak boleh lebih
pendek dari kode terbesar yang sudah dipakai (telemetri atau desired value). Ganti ke Number atau
On / off mengosongkan daftar; min/max dikosongkan untuk On / off dan Categories. JavaScript halaman
menampilkan isian kategori hanya untuk Categories, dan satuan/min/max hanya untuk Number.

## 3. Data masuk (HTTP, MQTT, CoAP)

- `api/payload.to_number` meneruskan teks (dipangkas, maks. 100 karakter) apa adanya.
- `api/services.save_telemetry` mengubah setiap nilai lewat `encode_value()` → `Variable.encode()`,
  **di dalam transaksi**: satu nilai salah membatalkan seluruh payload, termasuk variabel yang
  baru dibuat otomatis. Pesan kesalahan menyebut daftar kategori atau rentang kodenya; teks untuk
  variabel Number memberi petunjuk untuk mengubah value type.
- HTTP API kini menangkap `PayloadError` dari `save_telemetry` (MQTT dan CoAP sudah menangkapnya).
- Variabel baru yang dibuat otomatis tetap Number; nama kategori untuk key baru ditolak.

## 4. Tampilan

| Tempat | Perubahan |
|---|---|
| Filter `show_value` | label kategori |
| Filter baru `show_threshold`, `category_label`, `json_attr` | ambang/nilai aksi sebagai label; label dari daftar; JSON untuk atribut `data-*` |
| Kartu device dan dashboard, halaman variabel | `data-labels` (JSON); `LiveFormat.value(nilai, isBoolean, labels)` di `auto_refresh.js` untuk pembaruan langsung |
| Chart | tangga (`stepped`), sumbu Y berlabel kategori, min 0 dan maks = jumlah kategori − 1; ember ringkasan memakai nilai maksimum (seperti ON/OFF), karena rata-rata kode tidak bermakna |
| Kontrol aktuator | satu tombol per kategori (≤ 4), selain itu `<select>`; yang dikirim kode |
| Unduhan CSV/XLSX | kolom tambahan `category` |
| API v2 `/me/devices` | `categories` per variabel |
| Event WebSocket telemetri | `categories` per titik |

## 5. Aturan

- `VariableSelect` (widget `VariableChoiceField`): opsi variabel ON/OFF dan kategori membawa
  `data-labels`. Select variabel kondisi diberi `data-category-target="threshold"`, select aktuator
  aksi (ECA, rule chain, state machine) `data-category-target="value"`.
- `static/js/category_inputs.js`: bila variabel seperti itu dipilih, isian angka pasangannya
  disembunyikan dan diganti pilihan label; kode tetap dikirim lewat isian angka. Baris yang
  ditambahkan halaman ikut ditangani (MutationObserver). Variabel ON/OFF ikut mendapat pilihan
  ON/OFF.
- Teks aturan: `expression.html` dan ringkasan (`eca.threshold_text`) menampilkan nama kategori;
  log eksekusi menampilkan nama (`eca.value_text`); kotak "Last evaluation" memakai `categories`
  di snapshot kondisi.
- Pesan alarm/email/Telegram: `{value}` = nama kategori atau ON/OFF (`value_label` di konteks
  aturan); `Alarm.value` tetap angka. `{threshold}` = nama kategori.
- Micro rule tidak berubah: kategori sudah berupa angka bulat, sehingga `==` aman di float32.

## 6. Untuk device

Device boleh mengirim nama (`{"door": "open"}`) atau kode (`{"door": 1}`). Desired state yang
diterima aktuator selalu kode (`{"mode": 2}`); firmware memetakan kode ke keadaannya sendiri.
Template firmware tidak berubah.

## 7. Tes

`devices/tests_categories.py`: model (encode, format, validasi), form (daftar sah/tidak sah, data
yang sudah tersimpan, ganti tipe), data masuk HTTP dan MQTT (nama, kode, penolakan utuh), halaman
device/variabel/unduhan, aturan (mesin ECA dengan kondisi kategori, aksi kategori, pesan alarm,
form aturan).

## 8. Update server

Migrasi `devices 0022` (kolom baru dengan nilai bawaan `[]`, aman untuk data lama), dan file
statis baru `js/category_inputs.js`. Cukup `update.sh` seperti biasa.
