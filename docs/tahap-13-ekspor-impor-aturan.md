# Tahap 13: Ekspor dan impor aturan ECA sebagai JSON

Aturan ECA kini bisa dilihat, diunduh, dan dimuat ulang sebagai JSON. Kegunaannya:

- menyimpan aturan di luar database (cadangan, Git, lampiran tugas);
- menyalin aturan ke pengguna lain atau ke server lain;
- mengubah aturan yang panjang dengan editor teks, lalu memuatnya kembali;
- bahan ajar: struktur aturan ECA terlihat utuh dalam satu objek.

## Di mana tombolnya

| Tempat | Tombol | Hasil |
|---|---|---|
| Halaman detail aturan ECA | **Export** | mengunduh `rule-<nama>.json` |
| | **Import** | mengganti isi aturan ini dari JSON (nama, event, kondisi, aksi) |
| | kartu **JSON** (klik untuk membuka) | JSON aturan, tombol *Copy*, *Open raw*, *Download* |
| Halaman daftar aturan | **Import** | membuat aturan BARU dari JSON |

| URL | Nama url | Fungsi |
|---|---|---|
| `/rules/<pk>/json/` | `rules:json` | JSON aturan (`?download=1` = sebagai file) |
| `/rules/<pk>/import/` | `rules:import_into` | ganti isi aturan dari JSON |
| `/rules/import/` | `rules:import` | aturan baru dari JSON |

Rule chain (Flow) belum didukung; ketiga URL menjawab 404 untuk aturan non-ECA.

## Bentuk JSON (versi 1)

```json
{
  "format": "iot-platform.rule",
  "version": 1,
  "model": "ECA",
  "exported_at": "2026-10-16T15:20:00+07:00",
  "name": "Irrigation",
  "description": "Water the greenhouse",
  "enabled": true,
  "eca": {
    "event": "TELEMETRY",
    "device": null,
    "silence_seconds": null,
    "duration_seconds": 0,
    "cooldown_seconds": 0,
    "max_age_seconds": 300,
    "when": [
      [
        {"device": "Greenhouse Node", "variable": "soil_moisture", "operator": "<", "threshold": 20.0},
        {"device": "Weather station", "variable": "rain", "operator": "<", "threshold": 1.0}
      ],
      [
        {"device": "Greenhouse Node", "variable": "temperature", "operator": ">", "threshold": 35.0}
      ]
    ],
    "clear": [
      [{"device": "Greenhouse Node", "variable": "soil_moisture", "operator": ">", "threshold": 30.0}]
    ]
  },
  "actions": [
    {"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "Greenhouse Node", "variable": "pump", "value": 1.0},
    {"kind": "SEND_COMMAND", "run_on": "ACTIVATE", "device": "Greenhouse Node", "method": "blink",
     "params": {"times": 3}, "ttl_seconds": 60},
    {"kind": "ALARM", "run_on": "ACTIVATE", "severity": "WARNING", "message": "Dry: {values}"},
    {"kind": "SET_ACTUATOR", "run_on": "CLEAR", "device": "Greenhouse Node", "variable": "pump", "value": 0.0}
  ]
}
```

Cara membaca `when`: daftar **grup**. Kondisi di dalam satu grup digabung **AND**, grup-grup
digabung **OR**. Contoh di atas berarti:

```
(soil_moisture < 20 AND rain < 1) OR temperature > 35
```

Ini persis bentuk penyimpanan aturan di database (tahap 7: OR berisi AND, kolom `group`),
jadi tidak ada terjemahan logika yang bisa salah.

| Field | Isi |
|---|---|
| `format`, `version`, `model` | penanda file; impor menolak file lain |
| `eca.event` | `TELEMETRY` (data baru) atau `INACTIVITY` (device diam, pakai `device` + `silence_seconds`) |
| `operator` | `>`, `>=`, `<`, `<=`, `==`, `!=` |
| `actions[].kind` | `SET_ACTUATOR` (device, variable, value), `SEND_COMMAND` (device, method, params, ttl_seconds), `ALARM` (severity, message), `EMAIL` (message, recipients) |
| `actions[].run_on` | `ACTIVATE` atau `CLEAR` |

## Konsep 1: nama, bukan id

Di database, kondisi menyimpan `variable_id = 17`. Angka itu hanya bermakna di satu
database: di server lain, id 17 bisa variabel milik orang lain atau tidak ada sama sekali.
Karena itu JSON menyebut **nama device + key variabel**. Saat impor, nama dicari di antara
device yang boleh dipakai pemilik aturan (`visible_devices(owner)`, aturan yang sama dengan
dropdown di form).

Akibatnya:

- File bisa dibaca dan disunting manusia.
- Aturan yang diekspor budi bisa diimpor siti, asalkan siti punya device dengan nama dan
  variabel yang sama (misalnya praktikum dengan perangkat seragam).
- Bila nama tidak ditemukan, atau ada dua device bernama sama, impor ditolak dengan pesan
  yang menyebut baris mana yang bermasalah.

Pemilik aturan, keadaan (Idle/Active), log eksekusi, dan alarm **tidak** ikut diekspor:
file ini hanya definisi aturan.

## Konsep 2: impor lewat form yang sama

Cara termudah membuat impor yang tidak aman adalah menulis langsung ke tabel dari JSON.
Akibatnya semua pemeriksaan di form terlewati: aktuator di luar batas min/max, target aksi
berupa sensor, histeresis yang tumpang tindih, device milik orang lain.

`rules/portable.py` tidak menyimpan apa pun. Fungsi `eca_form_data()` hanya menerjemahkan
JSON menjadi data **seolah-olah form ECA dikirim dari browser**:

```
JSON  ──eca_form_data()──►  {"rule-name": "...", "when-0-variable": "17", ...}
                                   │
                    build_eca_forms() + validate_eca_forms()   ← sama dengan halaman Edit
                                   │
                           save_eca_forms()                     ← sama dengan halaman Edit
```

Ketiga fungsi itu dipindahkan dari `EcaRuleFormView` di `rules/views.py` supaya dipakai
bersama oleh form biasa dan impor. Setiap aturan baru di form otomatis berlaku juga untuk
impor. Tes `test_form_validation_still_applies` membuktikannya: nilai 150 untuk aktuator
0–100, histeresis yang salah, dan sensor sebagai target aksi semuanya ditolak, dengan pesan
yang sama seperti di form.

## Konsep 3: mengganti isi aturan yang ada

Import di halaman detail **mengganti** aturan itu: nama, event, semua kondisi, dan semua aksi
diganti isi JSON. Id aturan tetap sama, sehingga log eksekusi dan alarm lama tetap
tersambung. Aturan kembali ke *Idle*, seperti setelah disunting.

Teknisnya, `build_eca_forms(..., replace=True)` membuat kumpulan kondisi dan aksi yang
kosong (tidak memuat baris lama), lalu `save_eca_forms(..., replace=True)` menghapus baris
lama dan menyimpan yang baru. Semuanya dalam satu `transaction.atomic()`: bila satu bagian
gagal, aturan lama tetap utuh.

Bila impor ditolak, **tidak ada yang disimpan**, dan teks JSON tetap ada di kotak supaya bisa
diperbaiki.

## Batas

- Ukuran file/teks maksimal 100 KB.
- File harus UTF-8.
- Satu file = satu aturan.

## Mencoba

1. Buka sebuah aturan ECA, klik kartu **JSON**, lalu **Copy**.
2. Kembali ke daftar aturan → **Import** → tempel → **Import rule**. Muncul aturan kedua
   yang isinya sama.
3. Di aturan baru itu, klik **Import**, ubah `threshold` di JSON, lalu **Replace rule**.
4. Coba ubah nama device menjadi nama yang tidak ada: impor ditolak dan pesannya menyebut
   grup kondisi yang salah.

```bash
python manage.py test rules.tests_portable
```
