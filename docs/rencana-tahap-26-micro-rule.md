# Rencana Tahap 26 — Micro Rule: Model dan Compiler

Dokumen rencana. Sudah diimplementasikan; hasil dan perbedaannya ada di
`docs/tahap-26-micro-rule-model-dan-compiler.md`.

Tahap 26 adalah tahap pertama dari rangkaian micro rule (26–30). Tahap ini **hanya
mengubah platform**. Firmware, distribusi fragmen ke device, dan ESP-NOW yang sebenarnya
baru dikerjakan di tahap 27–28. Hasil akhir tahap 26: dari sebuah aturan ECA, platform
bisa menunjukkan device mana saja yang terlibat, apa isi fragmen untuk tiap device, apakah
fragmen muat di batas ESP32/ESP-NOW, dan bagaimana fragmen itu akan berperilaku (lewat
evaluator referensi Python yang semantiknya identik dengan mesin ECA di cloud).

| Tahap | Isi | Menyentuh firmware? |
|---|---|---|
| **26** | Model, analisis edge-capable, compiler aturan → fragmen, halaman rencana, evaluator referensi, test vector | Tidak |
| 27 | Distribusi fragmen (topik `rules` retained + ack), runtime micro rule di ESP32 untuk aturan satu device | Ya |
| 28 | Aturan multi-device lewat ESP-NOW broadcast, HMAC + counter (wajib) | Ya |
| 29 | Deteksi platform tidak terjangkau, log ring buffer, rekonsiliasi | Ya |
| 30 | Evaluasi eksperimen untuk paper | — |

## 1. Gambaran besar

```
                     ┌────────────── platform (tahap 26) ──────────────┐
 Rule ECA (DB) ──►  ir.py ──► analyze.py ──► compile.py ──► EdgePlan + EdgeFragment
                     │            │               │                 │
                     │            ▼               ▼                 ▼
                     │     laporan edge-     placement +      halaman "Edge plan",
                     │     capable           cek ukuran       unduh JSON
                     │                                              │
                     └── evaluator.py (referensi) ◄── test vector ──┘──► firmware/microrule/vectors/
                                                                         (dipakai uji C++ tahap 27)
```

Prinsip:

1. **Mesin cloud tidak berubah.** `rules/engines/eca.py` tetap menjalankan aturan seperti
   sekarang. Tahap 26 hanya membaca aturan dan menghasilkan rencana.
2. **Satu sumber semantik.** Aturan diterjemahkan ke representasi antara (IR) yang netral
   dari Django. IR inilah yang dikompilasi, di-hash, dan dijalankan oleh evaluator referensi.
3. **Deterministik.** Aturan yang sama menghasilkan IR, hash, dan fragmen yang byte-per-byte
   sama. Hash dipakai di tahap 27 untuk ack dan deteksi fragmen usang.
4. **Fitur disembunyikan di produksi.** Diaktifkan dengan `MICRO_RULES["ENABLED"]`
   (default `False`) sampai tahap 28 selesai, supaya pengguna umum tidak melihat fitur
   yang device-nya belum bisa jalankan.

## 2. Kendala ESP-NOW dan ESP32 yang menentukan desain

| Kendala | Nilai | Akibat pada desain |
|---|---|---|
| Payload maksimum per frame | 250 byte (ESP-NOW v1) | Format frame ringkas (bagian 6); compiler memeriksa ukuran frame terburuk |
| Jumlah peer | maks. 20; peer terenkripsi default 6 (maks. 17) | Tidak membatasi: produsen memakai **broadcast** (cukup 1 peer `FF:FF:FF:FF:FF:FF`), penerima tidak perlu mendaftarkan pengirim (bagian 5) |
| Broadcast | tanpa ACK/retransmisi di lapisan MAC, tanpa enkripsi ESP-NOW | Heartbeat + `max_age` menoleransi frame hilang; HMAC + counter di lapisan aplikasi wajib (tahap 28) |
| Pengalamatan | MAC sumber tersedia di callback penerimaan | Evaluator menyaring frame berdasarkan MAC sumber; butuh MAC dari tahap 24; device tanpa MAC → aturan diblokir |
| Kanal Wi-Fi | Semua device yang berkomunikasi harus di kanal yang sama | Device dalam satu AP otomatis sekanal. Penguncian kanal saat AP mati = urusan firmware tahap 28; tahap 26 menyiapkan kolom `channel` (null) |
| Tanpa NTP saat platform/Internet mati | — | Waktu di frame berupa **umur sampel** (relatif), bukan timestamp absolut |
| Float di ESP32 | `float` 32-bit | IR dan evaluator memakai pembulatan float32; peringatan untuk `==`/`!=` dengan ambang pecahan |
| Penyimpanan | NVS / LittleFS | Fragmen JSON ≤ 1 KB per aturan per device, maks. 8 aturan per device |

## 3. Analisis edge-capable (`rules/edge/analyze.py`)

Output: `Report(verdict, items)`. `verdict` salah satu dari:

- **FULL** — semua aksi kendali bisa jalan di device; tidak ada aksi cloud.
- **PARTIAL** — aksi kendali jalan di device; notifikasi (alarm, email, Telegram) tetap
  di platform.
- **NONE** — tidak bisa dijalankan di device; `items` menjelaskan alasannya.

Setiap item punya kode, tingkat (`block` / `warn` / `info`), dan teks untuk pengguna:

| Kode | Tingkat | Aturan |
|---|---|---|
| `MODEL` | block | Hanya model ECA. Flow dan state machine menyusul setelah tahap 30 |
| `EVENT_SCHEDULE` | block | Event Schedule butuh jam yang akurat; tetap di cloud |
| `EVENT_INACTIVITY` | block | Diam-nya device sulit dibedakan dari putusnya link ESP-NOW; tetap di cloud (v1) |
| `NO_CONTROL` | block | Tidak ada aksi Set actuator / Send command; tidak ada yang perlu dijalankan di device |
| `NO_MAC` | block | Device yang terlibat belum melaporkan MAC address |
| `ACCESS` | block | Pemilik aturan tidak punya hak OPERATOR atas device aktuator (pakai `sharing/access.py`) |
| `LIMIT_*` | block | Melebihi batas: sinyal > 8, kondisi > 16, grup > 4, aksi > 4 per jenis, sumber per evaluator > 16, sinyal dipancarkan per device > 8, aturan per device > 8, fragmen > 1 KB, frame > 250 byte |
| `MAX_AGE_ZERO` | warn | `max_age = 0` di device berarti nilai terakhir dipercaya selamanya walau link putus; disarankan diisi |
| `MAX_AGE_RANGE` | block | `max_age` > 6500 detik tidak muat di kolom umur frame |
| `FLOAT_EQ` | warn | `==`/`!=` dengan ambang pecahan bisa berbeda hasil di float32 |
| `CLOUD_ACTION` | info | Alarm / email / Telegram tetap dijalankan platform |
| `COMMAND_HANDLER` | info | Send command di device membutuhkan handler `method` di firmware |
| `KEY_HASH` | block | Dua variabel di satu device menghasilkan hash key (CRC-16) yang sama; sangat jarang, ganti salah satu key |
| `DEVICE_BUDGET` | warn | Batas per device (sumber, sinyal dipancarkan, jumlah aturan) dihitung lintas semua aturan edge dan mendekati/melebihi batas |
| `NO_CAPS` | info | Firmware device belum melaporkan dukungan `microrule/1` (opsional, bagian 9) |

Hak akses dicek dengan fungsi yang sama dengan mesin cloud (`can_watch`) supaya aturan yang
di cloud tidak boleh membaca suatu device, juga tidak boleh di edge.

## 4. Representasi antara (IR) — `rules/edge/ir.py`

IR adalah dataclass Python, diserialisasi ke JSON kanonik (kunci terurut, tanpa spasi).
Contoh untuk aturan irigasi dari `docs/eca-mekanisme.md`:

```
WHEN soil_moisture (node S) atau temperature (node T)
IF   (soil_moisture < 20 AND temperature > 30) OR soil_moisture < 10, selama 60 detik
THEN pump (node P) = 1, alarm, email
CLEAR bila soil_moisture > 30 → pump = 0
```

```json
{
  "fmt": "microrule-ir/1",
  "rule": 12,
  "devices": [
    {"p": 1, "device": 41, "mac": "AA:BB:CC:00:00:01"},
    {"p": 2, "device": 42, "mac": "AA:BB:CC:00:00:02"},
    {"p": 3, "device": 43, "mac": "AA:BB:CC:00:00:03"}
  ],
  "signals": [
    {"s": 1, "p": 1, "key": "soil_moisture"},
    {"s": 2, "p": 2, "key": "temperature"}
  ],
  "when":  [[[1, "<", 20.0], [2, ">", 30.0]], [[1, "<", 10.0]]],
  "clear": [[[1, ">", 30.0]]],
  "timing": {"duration": 60, "cooldown": 0, "max_age": 300},
  "actions": [
    {"on": "activate", "do": "set", "p": 3, "key": "pump", "value": 1.0},
    {"on": "clear",    "do": "set", "p": 3, "key": "pump", "value": 0.0}
  ],
  "cloud": [{"on": "activate", "kind": "ALARM"}, {"on": "activate", "kind": "EMAIL"}]
}
```

- `p` (nomor device dalam aturan) dan `s` (signal id) diberi nomor berurutan menurut `device.pk` lalu
  `variable.key`, supaya deterministik dan muat 1 byte.
- Ambang dibulatkan ke float32 saat IR dibuat; nilai asli tetap ada di laporan.
- `rev` = 8 hex pertama SHA-256 dari JSON kanonik IR. MAC ikut di-hash: mengganti board
  (MAC berubah) membuat rencana usang dan perlu dikompilasi ulang.
- `when` kosong + `clear` kosong tidak mungkin terjadi untuk event Telemetry (form sudah
  mewajibkan kondisi), tetapi IR tetap memvalidasinya.

## 5. Compiler dan penempatan — `rules/edge/compile.py`

### a. Algoritme penempatan

1. **A** = device aktuator, yaitu device milik `target_variable` (Set actuator) dan
   `target_device` (Send command).
2. **Setiap device di A menjadi evaluator.** Ia menerima seluruh ekspresi WHEN/CLEAR dan
   timing, tetapi hanya **aksinya sendiri**.
3. Untuk setiap sinyal yang sumbernya device *d* dan dipakai evaluator selain *d*: *d*
   menjadi **produsen** yang **memancarkan (broadcast)** sinyal itu. Produsen tidak perlu
   tahu siapa penerimanya. Sinyal yang sumber dan evaluatornya sama dibaca lokal, tanpa
   ESP-NOW.
4. Device yang sekaligus produsen dan evaluator mendapat satu fragmen dengan dua bagian
   (`role: "src+eval"`).
5. Device yang tidak menjadi produsen maupun evaluator tidak mendapat fragmen.

Pilihan desain: **evaluasi tereplikasi**. Bila aksi aturan menyasar dua aktuator (pompa di
P dan katup di V), P dan V masing-masing mengevaluasi aturan dengan input yang sama, bukan
satu evaluator yang mengirim perintah ke yang lain. Keuntungannya: tidak ada hop tambahan
dan tidak ada titik kegagalan tunggal. Risikonya: frame yang hilang ke salah satu evaluator
bisa membuat keduanya sementara berbeda keadaan. Divergensi ini diukur di tahap 30 sebagai
metrik konsistensi.

Pilihan desain: **broadcast, bukan unicast**. Produsen memancarkan satu frame berisi semua
sinyal yang ia publikasikan; setiap evaluator menyaring frame dengan pasangan (MAC sumber,
hash key). Akibatnya:

- jumlah peer ESP-NOW tidak lagi membatasi: produsen memakai 1 peer (alamat broadcast),
  evaluator 0 peer;
- satu frame melayani semua aturan dan semua evaluator yang memakai sinyal itu;
- isi yang dipancarkan produsen tidak bergantung pada aturan tertentu: cukup daftar variabel
  (tabel publikasi), sehingga mengubah aturan sering cukup memperbarui fragmen evaluator;
- konsekuensinya: tidak ada ACK/retransmisi (diatasi heartbeat dan `max_age`); tidak ada
  enkripsi ESP-NOW dan MAC sumber bisa dipalsukan, sehingga HMAC + counter wajib di tahap
  28; nilai terbaca oleh device lain di jangkauan, termasuk milik pengguna lain (opsi
  enkripsi AES-CCM dibahas di tahap 28).

Strategi pembanding, yaitu evaluator di device sensor dan aktuator hanya menerima keputusan,
dirancang terpisah di `docs/rencana-tahap-26b-evaluator-di-sensor.md`.

Unicast dicadangkan sebagai opsi per aturan untuk kendali yang membutuhkan ACK, di luar
cakupan tahap 26–28. Perbandingan tingkat kehilangan frame broadcast vs unicast menjadi
salah satu eksperimen tahap 30.

Predicate pushdown (produsen mengirim bit hasil kondisi, bukan nilai) **tidak** dipakai di
v1: mengirim nilai float32 hanya 4 byte, mendukung beberapa kondisi pada satu sinyal, dan
membuat produsen tidak perlu tahu ekspresi. Pushdown dicatat sebagai varian untuk
dibandingkan di tahap 30.

### b. Fragmen

Fragmen evaluator untuk P:

```json
{
  "fmt": "microrule/1", "rule": 12, "rev": "9f3a1c07", "role": "eval",
  "self": 3,
  "sources": [{"p": 1, "mac": "AA:BB:CC:00:00:01"}, {"p": 2, "mac": "AA:BB:CC:00:00:02"}],
  "signals": [{"s": 1, "from": 1, "kh": "0x6511"}, {"s": 2, "from": 2, "kh": "0xFF0F"}],
  "when":  [[[1, "<", 20.0], [2, ">", 30.0]], [[1, "<", 10.0]]],
  "clear": [[[1, ">", 30.0]]],
  "timing": {"duration": 60, "cooldown": 0, "max_age": 300, "tick": 1},
  "on_activate": [{"do": "set", "key": "pump", "value": 1.0}],
  "on_clear":    [{"do": "set", "key": "pump", "value": 0.0}]
}
```

`kh` adalah hash key (CRC-16/CCITT dari nama variabel; nilai di atas hanya contoh). `from`
merujuk ke `sources`; `from: 0` berarti sinyal lokal milik device itu sendiri.

Kontribusi aturan ini ke **tabel publikasi** S (fragmen SRC):

```json
{
  "fmt": "microrule-pub/1", "self": 1,
  "publish": [{"key": "soil_moisture", "kh": "0x6511", "heartbeat": 100, "min_interval": 1}]
}
```

Fragmen SRC tidak memuat daftar penerima maupun rule id. Tabel publikasi sebuah device
adalah **gabungan** fragmen SRC dari semua aturan edge yang memakai device itu sebagai
sumber: satu baris per variabel, dengan `heartbeat` dan `min_interval` diambil yang
terkecil. Tabel ini punya `rev` sendiri (hash isinya) dan di tahap 27 dikirim ke device
terpisah dari fragmen aturan.

- `heartbeat` = `max(5, max_age // 3)` detik: nilai terakhir dikirim ulang walau tidak
  berubah, supaya evaluator tidak menganggapnya usang selama link sehat (tiga frame boleh
  hilang sebelum nilai menjadi "tidak diketahui"). Bila `max_age = 0`, heartbeat 30 detik.
- `min_interval` membatasi laju kirim saat nilai sering berubah.
- Fragmen tidak memuat token akses, nama pengguna, atau teks pesan notifikasi.
- Kunci HMAC **belum** ada di tahap 26. Rancangannya: satu kunci per device produsen, disalin
  ke `sources` di fragmen evaluator yang memakai sinyalnya. Skema menyisakan kolom `key_id`
  (null) di tabel publikasi dan di `sources`, diisi tahap 28.

### c. Batas (`rules/edge/limits.py`, dapat diubah lewat `settings.MICRO_RULES`)

```python
MICRO_RULES = {
    "ENABLED": False,          # True di lingkungan pengembangan
    "MAX_SIGNALS": 8,
    "MAX_GROUPS": 4,
    "MAX_CONDITIONS": 16,
    "MAX_ACTIONS": 4,          # per on_activate / on_clear
    "MAX_SOURCES": 16,         # device sumber yang dilacak satu evaluator, lintas aturan
    "MAX_PUBLISHED_SIGNALS": 8,  # variabel yang dipancarkan satu device, lintas aturan
    "MAX_RULES_PER_DEVICE": 8,
    "MAX_FRAGMENT_BYTES": 1024,
    "MAX_FRAME_BYTES": 250,
    "TICK_SECONDS": 1,
}
```

## 6. Format frame ESP-NOW v0 (`rules/edge/wire.py`)

Tahap 26 belum mengirim apa pun, tetapi format frame perlu ditetapkan sekarang karena
compiler harus menjamin frame terburuk muat 250 byte, dan test vector byte-level dibutuhkan
untuk firmware tahap 27–28.

Frame dikirim **per device produsen** (bukan per aturan) ke alamat broadcast.

| Offset | Ukuran | Kolom |
|---|---|---|
| 0 | 2 | magic `"MR"` |
| 2 | 1 | versi = 1 |
| 3 | 1 | tipe: 1 = DATA (2 = HELLO, 3 = EVENT, dicadangkan) |
| 4 | 4 | counter, uint32 little-endian, naik setiap frame (anti-replay, tahap 28) |
| 8 | 1 | jumlah sinyal *n* |
| 9 | 8·*n* | per sinyal: hash key CRC-16 u16, nilai float32 LE, umur u16 (satuan 100 ms) |
| 9+8*n* | 8 | tag HMAC-SHA256 terpotong (nol di tahap 26) |

Ukuran = 17 + 8*n* ≤ 250 → maksimum 29 sinyal per frame, di atas batas 8 sinyal yang
dipancarkan per device.

- Rule id tidak ada di frame. Evaluator memetakan (MAC sumber, hash key) ke sinyal
  aturannya, sehingga satu frame bisa mengisi sinyal beberapa aturan sekaligus.
- Sinyal diidentifikasi dengan hash key, bukan nomor urut, supaya tidak bergeser saat tabel
  publikasi berubah. Compiler memblokir tabrakan hash di satu device (`KEY_HASH`).
- Urutan penyaringan di evaluator: magic dan versi → MAC sumber ada di `sources` → (tahap 28)
  tag HMAC valid dan counter lebih besar dari counter terakhir sumber itu → hash key dikenal.
  Frame yang gagal di salah satu langkah dibuang.

**Waktu tanpa NTP:** produsen mengirim umur sampel saat frame dikirim; evaluator menghitung
`ts = waktu_terima_lokal − umur` dengan jam monotonic-nya sendiri. Keterlambatan udara
ESP-NOW (orde milidetik) diabaikan. Inilah yang membuat `max_age` tetap berarti tanpa jam
bersama.

`wire.py` berisi `encode_data()` dan `decode_data()` (Python `struct`) yang dipakai untuk
membuat test vector dan untuk uji round-trip.

## 7. Evaluator referensi — `rules/edge/evaluator.py`

Evaluator Python murni (tanpa Django) yang menjalankan **satu fragmen evaluator**, dengan
API yang sengaja meniru apa yang nanti ditulis dalam C++:

```python
rt = Runtime(fragment)
rt.receive(s=1, value=15.0, age_ms=0, now_ms=1000)   # baca lokal atau hasil decode frame
rt.receive_frame(src_mac, frame, now_ms=1000)         # frame broadcast: disaring, lalu receive()
out = rt.tick(now_ms=61000)                           # dipanggil tiap TICK_SECONDS
# out == [Transition("ACTIVATE", [{"do": "set", "key": "pump", "value": 1.0}])]
```

Semantik **harus identik** dengan cloud:

- logika tiga nilai `all_of` / `any_of` dari `rules/conditions.py`;
- mesin keadaan IDLE → PENDING → ACTIVE dari `step()` di `rules/engines/eca.py`
  (durasi, cooldown, clear dengan histeresis, "tidak diketahui" tidak memicu apa pun);
- perbandingan `compare()` dari `rules/models.py`, termasuk toleransi `==`;
- perbedaan yang disengaja: waktu dalam milidetik integer (jam monotonic) dan nilai
  float32.

Supaya tidak ada dua implementasi semantik yang bisa menyimpang, bagian yang murni
(`all_of`, `any_of`, `compare`) **diimpor** dari kode cloud; hanya penghitungan waktu dan
tipe data yang ditulis ulang. `step()` dibungkus adaptor (objek state berwaktu ms) alih-alih
disalin.

**Uji kesetaraan (inti klaim paper):** generator acak ber-seed membuat 1.000 aturan
(1–4 grup, 1–4 kondisi, histeresis acak, durasi/cooldown acak) dan urutan pembacaan
acak. Untuk setiap kasus, jalur cloud (`conditions.evaluate` + `step`) dan evaluator
referensi harus menghasilkan **urutan transisi dan waktu yang sama**, dengan asumsi semua
frame terkirim tanpa kehilangan. Nilai dan ambang dipilih yang representable di float32
supaya perbandingan adil; kasus float32 yang memang berbeda diuji terpisah dan
didokumentasikan.

## 8. Test vector untuk firmware — `firmware/microrule/`

```
firmware/microrule/
├── schema/fragment-v1.schema.json     JSON Schema fragmen
├── vectors/eval/*.json                skenario evaluator
├── vectors/wire/*.json                frame hex ↔ hasil decode
└── README.md                          cara memakai vector di uji native PlatformIO (tahap 27)
```

Format skenario evaluator:

```json
{
  "name": "duration_then_activate",
  "fragment": { "...": "fragmen evaluator" },
  "steps": [
    {"t": 0,     "rx": {"s": 1, "v": 15.0, "age": 0}, "expect": []},
    {"t": 30000, "tick": true, "expect": []},
    {"t": 60000, "tick": true, "expect": [{"tr": "ACTIVATE", "actions": [{"do": "set", "key": "pump", "value": 1.0}]}]}
  ]
}
```

Skenario minimal: aktif seketika, durasi, durasi terputus, cooldown, clear tanpa
histeresis, clear dengan histeresis, nilai usang → tidak diketahui (tetap IDLE / tetap
ACTIVE), umur dari frame, `==` float32, grup OR, sinyal lokal, frame dari MAC yang tidak
terdaftar diabaikan, hash key yang tidak dikenal diabaikan, satu frame mengisi sinyal dua
aturan.

Dibuat ulang dengan `python manage.py export_microrule_vectors`. Uji Django memastikan file
di repo sama dengan hasil generate (vector tidak boleh basi).

## 9. Perubahan data (migrasi `rules/0007_edge_plan`)

| Model / kolom | Isi |
|---|---|
| `Rule.edge_enabled` | Boolean, default `False`. Label: "Also run on my devices when the platform is unreachable (preview)". Di tahap 26 hanya menentukan apakah rencana disimpan; mesin cloud tidak berubah |
| `EdgePlan` | `rule` FK, `revision` (1, 2, …), `rev` (8 hex), `verdict`, `report` (JSON), `ir` (JSON), `created_at`, `created_by`, `is_current`. Unik (`rule`, `revision`) |
| `EdgeFragment` | `plan` FK, `device` FK, `role` (SRC / EVAL / SRC_EVAL), `payload` (JSON), `size_bytes`. Unik (`plan`, `device`). Untuk SRC, `payload` berisi kontribusi aturan ke tabel publikasi |
| Tabel publikasi | Belum menjadi model di tahap 26: dihitung dari fragmen SRC rencana terkini semua aturan edge (`services.publish_table(device)`), lengkap dengan `rev`. Disimpan saat distribusi di tahap 27 |

- Rencana dikompilasi ulang setiap kali aturan disimpan dengan `edge_enabled = True`, dan
  lewat tombol "Recompile". Bila `rev` tidak berubah, tidak ada revisi baru.
- Rencana lama tetap disimpan (riwayat revisi berguna untuk ack tahap 27 dan untuk paper).
- Rencana ditandai usang bila hash IR aturan saat ini ≠ `rev` rencana terkini (mis. MAC
  device berubah).
- Opsional (kecil, menyentuh `ota/services.py` tetapi bukan firmware): laporan firmware
  menerima `"caps": ["microrule/1"]` dan menyimpannya di `Device.capabilities` (JSON).
  Firmware tahap 27 yang akan mengirimnya; sebelum itu analisis hanya memberi info `NO_CAPS`.
- `portable.py`: `edge_enabled` ikut ekspor/impor; berkas lama tanpa kolom ini tetap bisa
  diimpor.

## 10. Antarmuka

1. **Form aturan ECA**: kotak centang `edge_enabled` (hanya tampil bila
   `MICRO_RULES["ENABLED"]`).
2. **Halaman detail aturan → bagian "Edge plan"** (pemilik aturan dan staf saja):
   - lencana verdict (Runs on devices / Partly / Platform only) dan revisi + `rev`;
   - daftar hasil analisis (block / warn / info) dengan teks untuk pengguna;
   - diagram penempatan SVG (dibuat di server seperti `flow_diagram.py`): node sensor →
     garis broadcast ESP-NOW → node evaluator, aksi kendali di bawah evaluator, aksi cloud di kotak
     "Platform";
   - kartu per device: peran, ukuran fragmen terhadap batas (bar), jumlah sumber, JSON
     fragmen (dapat dilipat), tombol unduh JSON; tombol unduh semua (zip berisi fragmen
     + IR + laporan);
   - catatan untuk pengguna: "Preview. Your devices do not run this rule yet."
3. **Halaman device**: daftar "Edge rules on this device" (aturan, peran, revisi) dan
   tabel publikasi gabungan (variabel yang dipancarkan, heartbeat).
4. **Daftar aturan**: lencana kecil "Edge" untuk aturan dengan `edge_enabled`.
5. **API v2** (untuk skrip evaluasi paper): `GET /api/v2/rules/<id>/edge-plan`.
6. **Dokumentasi pengguna**: halaman baru "Edge rules (preview)" di bab Rules, tampil hanya
   bila fitur aktif.

## 11. Model otoritas (ditetapkan sekarang, dijalankan tahap 27–29)

- **Kendali: edge-always.** Setelah fragmen ter-ack di tahap 27, aksi Set actuator / Send
  command aturan itu dijalankan device, baik platform terjangkau maupun tidak. Mesin cloud
  mencatat "handled by device" dan tidak mengirim desired state yang sama, sehingga tidak
  ada dua pengendali.
- **Notifikasi: platform.** Alarm, email, dan Telegram tetap dievaluasi cloud dari telemetri.
  Kejadian yang terjadi saat platform mati direkonsiliasi dari log device (tahap 29).
- **Override manual**: perubahan desired state oleh pengguna menang sampai aturan
  berpindah keadaan berikutnya (dirinci tahap 27).

Tahap 26 hanya menyimpan keputusan ini di dokumen dan di kolom verdict; tidak ada
perubahan di `rules/engines/`.

## 12. Struktur kode

```
rules/edge/
├── __init__.py
├── limits.py        batas dari settings.MICRO_RULES
├── ir.py            Rule (DB) → IR, JSON kanonik, hash
├── analyze.py       laporan edge-capable
├── compile.py       penempatan + fragmen + cek ukuran
├── wire.py          encode/decode frame v0
├── evaluator.py     evaluator referensi (tanpa Django)
├── vectors.py       pembuat test vector
├── diagram.py       SVG penempatan
└── services.py      compile_and_store(rule, user) → EdgePlan; publish_table(device)
rules/management/commands/export_microrule_vectors.py
rules/management/commands/edge_report.py   statistik semua aturan ECA → CSV (paper)
rules/tests_edge.py
```

## 13. Pengujian

| Kelompok | Isi |
|---|---|
| IR | konversi dari aturan contoh, determinisme hash, hash berubah bila ambang/MAC berubah |
| Analisis | satu uji per kode di bagian 3 |
| Penempatan | 1 device (sensor = aktuator), 2 device, 3 device, 2 aktuator (replikasi), sinyal dipakai dua evaluator, tabel publikasi gabungan dari dua aturan |
| Ukuran | fragmen dan frame terburuk di batas, tepat di atas batas → block |
| Wire | encode/decode round-trip, vector hex tetap, CRC-16 key, deteksi tabrakan hash, penyaringan MAC sumber |
| Evaluator | semua skenario bagian 8 |
| Kesetaraan | 1.000 kasus acak cloud vs evaluator referensi |
| Vector | file di repo = hasil generate; valid terhadap JSON Schema |
| View | akses (bukan pemilik → 404), fitur nonaktif → bagian tidak tampil, unduh JSON/zip |
| Regresi | seluruh uji `rules`, `devices`, `ota`, `userdocs` tetap lulus; mesin cloud tidak berubah |

## 14. Artefak untuk paper

- Definisi formal IR (grammar), fungsi dekomposisi, dan aturan penempatan (bagian 4–5).
- Bukti empiris kesetaraan semantik cloud vs edge (bagian 7) sebagai tabel/angka.
- `edge_report`: distribusi ukuran fragmen, jumlah sumber per evaluator, jumlah frame per
  detik per produsen (broadcast dibanding unicast teoretis per pasangan), dan persentase aturan FULL /
  PARTIAL / NONE pada kumpulan aturan contoh (`docs/contoh-data-dan-aturan.md`) dan aturan
  sintetis.
- Diagram penempatan sebagai gambar arsitektur.

## 15. Urutan pengerjaan

1. `limits.py`, `ir.py` + uji IR.
2. `analyze.py` + uji analisis.
3. `compile.py`, `wire.py` + uji penempatan, ukuran, wire.
4. `evaluator.py` + skenario + uji kesetaraan.
5. Test vector, JSON Schema, perintah `export_microrule_vectors`.
6. Migrasi `0007_edge_plan`, `services.py`, kompilasi saat aturan disimpan, ekspor/impor.
7. UI: form, bagian Edge plan, diagram, unduhan, halaman device, API.
8. Dokumentasi: `docs/tahap-26-micro-rule-model-dan-compiler.md`, halaman pengguna,
   `edge_report`, status pengembangan, bagian update di `deploy-vps.md`.

## 16. Di luar cakupan tahap 26

Firmware; pengiriman fragmen ke device; ESP-NOW sungguhan; kunci HMAC; unicast per aturan; perubahan mesin
cloud; model Flow dan state machine; event Schedule dan Inactivity di edge; failover,
lease, dan rekonsiliasi.

## 17. Selesai bila

- Aturan irigasi contoh menghasilkan verdict PARTIAL, tiga fragmen (S: src, T: src,
  P: eval), semuanya di bawah batas, dan tampil di halaman Edge plan.
- Uji kesetaraan 1.000 kasus lulus.
- Test vector dan schema ada di `firmware/microrule/` dan tervalidasi otomatis.
- Dengan `MICRO_RULES["ENABLED"] = False` tidak ada perubahan yang terlihat oleh pengguna.
