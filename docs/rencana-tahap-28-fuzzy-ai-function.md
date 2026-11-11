# Rencana: Fuzzy, model AI, dan function Python di automation (tahap 28–30)

Status: **rencana**, belum dikerjakan.

## 1. Ringkasan

Tiga kemampuan baru untuk automation:

| Fitur | Bentuk di platform | Tahap |
|---|---|---|
| Logika **fuzzy** | objek **Fuzzy controller** (dirancang di halaman sendiri, dengan grafik) + node **Fuzzy** di rule chain + aksi ECA *Set actuator from fuzzy controller*; bisa dijalankan di ESP32 (micro rule) | 28 |
| **Model AI** | **Models** (registry ONNX) + node **Inference** + **variabel turunan** + halaman uji manual/CSV | 29 |
| **Function** Python | node **Function** (`def run(msg, ctx)`) yang dijalankan di sandbox terpisah; awalnya hanya admin | 30 |

Semuanya masuk ke **rule chain** sebagai node kelompok *Transformation*, di samping *Calculate*:
pesan masuk dengan nilai telemetri, node menghitung, hasilnya ditambahkan ke pesan, lalu node
berikutnya (Check, Set actuator, Create alarm, Telegram, Capture snapshot) memakainya. Log eksekusi,
jadwal, berbagi, ekspor/impor JSON, dan diagram rule chain tetap berlaku.

Urutan dipilih dari risiko terendah: fuzzy tidak menjalankan kode pengguna; model ONNX hanya
dieksekusi oleh runtime yang aman; function Python membutuhkan sandbox sungguhan.

## 2. Fondasi bersama (dikerjakan di tahap 28, dipakai 29 dan 30)

### 2.1 Set actuator dengan nilai dari pesan

Saat ini nilai *Set actuator* tetap (`value`). Fuzzy dan AI menghasilkan angka yang berubah, jadi:

- `FlowNode.value_source` = `fixed` (bawaan, seperti sekarang) | `key`;
- `FlowNode.value_key` = key pesan, mis. `fan_speed`;
- nilai dibatasi ke min/max variabel aktuator dan dibulatkan untuk on/off dan kategori;
- tetap idempoten: dilewati bila desired sudah sama (dengan toleransi `value_tolerance`, bawaan 0,
  supaya keluaran fuzzy yang berubah 0,01 tidak membanjiri device).

### 2.2 Variabel turunan (virtual sensor)

Variabel baru `Variable.Kind.DERIVED` ("Derived (computed)"): nilainya tidak dikirim device, tetapi
ditulis node rule chain **Store as variable** (key pesan → variabel turunan pada device asal pesan).
Disimpan sebagai `Telemetry` biasa, jadi otomatis tampil di dashboard, chart, unduhan, dan bisa
dipakai aturan lain. Tidak masuk kuota telemetri device, tidak menandai device online.

### 2.3 Node Expression (pengganti *Calculate* yang lebih luas)

*Calculate* (`a op b`) diperluas menjadi **Expression**: `key = <rumus>`, mis.
`heat_index = 0.5 * (t + 61 + (t - 68) * 1.2 + h * 0.094)`, `ok = t > 30 and h < 40`,
`level = min(max(x, 0), 100)`. Dievaluasi oleh evaluator ekspresi sendiri (AST Python yang
diwhitelist: angka, key pesan, `+ - * / % **`, perbandingan, `and/or/not`, `if-else` satu baris,
`min max abs round sqrt exp log sin cos`), tanpa `exec`. Node *Calculate* lama tetap berjalan
dan dimigrasikan menjadi Expression yang setara.

Expression saja sudah mencakup sebagian besar kebutuhan "function" mahasiswa, tanpa risiko sandbox.

### 2.4 Worker komputasi (`iot-compute`)

Inferensi model (tahap 29) dan function (tahap 30) tidak boleh dijalankan di proses web atau di
`rule_engine` (satu aturan yang macet atau rakus memori akan menghentikan semua aturan).

```
rule_engine ──(Redis: request {node, msg})──▶ iot-compute (N proses, systemd) ──▶ jawaban ≤ timeout
                ◀──────────────(Redis: result / error)──────────────┘
```

- layanan systemd baru `iot-compute.service` (bagian dari `iot.target`), N worker (bawaan 2);
- setiap permintaan punya batas waktu (bawaan 2 s); lewat batas → node keluar lewat **Failure**
  dengan keterangan *timeout*, aturan lain tetap jalan;
- model ONNX disimpan di cache worker (dimuat sekali per versi);
- tanpa Redis (laptop): dijalankan langsung di proses `rule_engine` dengan peringatan di log.

## 3. Tahap 28 — Fuzzy controller

### 3.1 Model data

| Model | Kolom utama |
|---|---|
| `FuzzyController` | owner, name, method (`MAMDANI` / `SUGENO`), and_op (`MIN` / `PRODUCT`), or_op (`MAX` / `PROB_SUM`), defuzz (`CENTROID` / `BISECTOR` / `MOM` / `WEIGHTED_AVERAGE` untuk Sugeno), resolution (titik diskretisasi, bawaan 101), `definition` (JSON di bawah), version |
| (berbagi) | seperti aturan: milik pemilik; dipakai aturan lain milik pemilik yang sama |

`definition`:

```json
{
  "inputs": [
    {"name": "temperature", "min": 0, "max": 50, "terms": [
      {"name": "cold", "shape": "trapezoid", "points": [0, 0, 15, 22]},
      {"name": "warm", "shape": "triangle",  "points": [18, 25, 32]},
      {"name": "hot",  "shape": "trapezoid", "points": [28, 35, 50, 50]}]},
    {"name": "humidity", "min": 0, "max": 100, "terms": [
      {"name": "dry",   "shape": "gauss", "points": [20, 12]},
      {"name": "humid", "shape": "gauss", "points": [80, 12]}]}
  ],
  "outputs": [
    {"name": "fan", "min": 0, "max": 100, "terms": [
      {"name": "off",  "shape": "triangle", "points": [0, 0, 30]},
      {"name": "mid",  "shape": "triangle", "points": [20, 50, 80]},
      {"name": "high", "shape": "triangle", "points": [70, 100, 100]}]}
  ],
  "rules": [
    {"if": [["temperature", "hot"], ["humidity", "dry"]], "op": "and", "then": [["fan", "high"]], "weight": 1},
    {"if": [["temperature", "warm"]], "then": [["fan", "mid"]]},
    {"if": [["temperature", "cold"]], "then": [["fan", "off"]]}
  ]
}
```

Bentuk fungsi keanggotaan: `triangle`, `trapezoid`, `gauss` (mean, sigma), `sigmoid`, `singleton`
(Sugeno: konstanta; Sugeno orde-1: `{"linear": [a, b, c]}` = a·x₁ + b·x₂ + c). Term boleh dinegasi
(`["temperature", "not hot"]`).

### 3.2 Mesin inferensi

`rules/fuzzy.py`, Python murni (tanpa scikit-fuzzy), deterministik:
fuzzifikasi → kekuatan aturan (AND/OR, bobot) → implikasi (min) → agregasi (max) →
defuzzifikasi. Input di luar rentang dipotong ke min/max. Bila tidak ada aturan yang aktif,
keluaran = `null` (node lewat **Failure**: *no rule fired*). Hasil juga memuat derajat tiap aturan
untuk ditampilkan di log (*explain*).

Validasi saat disimpan: nama unik, term ada, titik terurut di dalam rentang, minimal satu aturan,
setiap output dipakai, Sugeno hanya singleton/linear di output.

### 3.3 Halaman

- **Automation → Fuzzy controllers**: daftar, buat, salin, ekspor/impor JSON (format di atas).
- Editor:
  - variabel input/output dengan tabel term; **grafik fungsi keanggotaan** (Chart.js) diperbarui
    langsung saat titik diubah;
  - **tabel aturan** (baris: IF … AND/OR … THEN …, bobot); untuk 2 input dapat diisi sebagai
    **matriks** (baris = term input 1, kolom = term input 2, sel = term output);
  - **Try it**: slider untuk setiap input → derajat keanggotaan, aturan yang aktif, area agregasi,
    dan nilai keluaran (grafik);
  - **permukaan kendali** (2 input → heatmap keluaran) untuk melihat perilaku keseluruhan.
- Dokumentasi pengguna: bab baru *Fuzzy controllers*, dengan contoh kipas dan irigasi.

### 3.4 Pemakaian di aturan

| Model aturan | Bentuk |
|---|---|
| Rule chain | node **Fuzzy**: controller + pemetaan input (`temperature ← msg.temperature`, atau *Add latest value* sebelumnya) + key keluaran (`fan ← msg.fan_speed`); Success / Failure |
| ECA | aksi **Set actuator from fuzzy controller**: aktuator + controller + pemetaan input ke variabel; dijalankan setiap event (dengan cooldown ECA) |
| State machine | aksi yang sama di *on enter* (opsional, setelah ECA) |

### 3.5 Di device (micro rule)

Inferensi fuzzy hanya tabel dan aritmetika kecil, sehingga bisa ikut ke ESP32:

- IR micro rule (`rules/edge/ir.py`) mendapat aksi `{"do": "fuzzy", "c": <id>, "in": [s…], "p": …, "key": …}`
  dan bagian `fuzzy` berisi controller yang sudah **dikompilasi**: titik fungsi keanggotaan (float32),
  aturan sebagai indeks, dan metode;
- evaluator referensi (`rules/edge/evaluator.py`) dan simulator mendapat fungsi fuzzy yang sama
  dengan `rules/fuzzy.py`; test vector memastikan keluaran firmware = keluaran platform (selisih
  ≤ 1e-3 dari rentang);
- template firmware ESP32 mendapat `fuzzy.h` (±200 baris C, tanpa alokasi dinamis). Batas ukuran:
  ≤ 4 input, ≤ 7 term per variabel, ≤ 49 aturan, resolusi defuzzifikasi 51 titik;
- analisis "Run on devices" menolak controller yang melebihi batas, dengan alasannya.

Ini juga bahan evaluasi yang baik untuk arsitektur micro rule: kontrol fuzzy tetap berjalan saat
server atau jaringan putus.

### 3.6 Pengujian

Uji mesin dengan contoh buku teks (tipping problem Mamdani; Sugeno orde-0/1) dibandingkan dengan
nilai referensi scikit-fuzzy yang dihitung sekali dan disimpan di tes; validasi; node dan aksi
ECA; ekspor/impor; kesamaan evaluator Python ↔ C (test vector).

## 4. Tahap 29 — Model AI (ONNX)

### 4.1 Mengapa ONNX

- satu format untuk scikit-learn (`skl2onnx`), PyTorch (`torch.onnx.export`), Keras/TensorFlow
  (`tf2onnx`), XGBoost/LightGBM;
- dijalankan `onnxruntime` (CPU) yang **tidak menjalankan kode dari file model**;
- **pickle/joblib ditolak**: membuka file seperti itu = menjalankan kode pengunggahnya.

### 4.2 Model data

| Model | Kolom utama |
|---|---|
| `AiModel` | owner, name, description, task (`REGRESSION` / `CLASSIFICATION`), is_enabled |
| `AiModelVersion` | model, version, file (penyimpanan privat, seperti firmware), size, sha256, uploaded_at, **schema** (JSON di bawah), metrics (opsional, diisi pengguna), is_active |

`schema` (diisi lewat form, dicek terhadap input/output ONNX):

```json
{
  "inputs": [
    {"name": "temperature", "source": "value"},
    {"name": "temperature_mean_10m", "source": "mean", "key": "temperature", "window_s": 600},
    {"name": "humidity", "source": "value", "scale": {"mean": 60.2, "std": 14.8}}
  ],
  "onnx_input": "float_input",
  "outputs": {"kind": "classification", "labels": ["normal", "warning", "danger"],
              "label_key": "air_class", "probability_key": "air_prob"}
}
```

Sumber fitur: nilai sesaat dari pesan, atau jendela waktu dari riwayat (`mean`, `min`, `max`,
`std`, `delta`, `last_n` untuk model deret waktu), dihitung platform dengan query yang dibatasi.

### 4.3 Pemakaian

| Cara | Isi |
|---|---|
| node **Inference** (rule chain) | model + pemetaan fitur → prediksi (+ probabilitas) ke pesan; Failure bila fitur tidak lengkap/timeout |
| **variabel turunan** | "jalankan model ini setiap telemetri masuk dari device X, simpan ke variabel Y" (rule chain bawaan yang dibuat otomatis: Input → Inference → Store as variable) |
| halaman **Try it** | form isian fitur → prediksi; **unggah CSV** → unduh CSV dengan kolom prediksi (dijalankan di `iot-compute` sebagai tugas latar, maks. N baris) |
| aturan lanjutan | prediksi dipakai Check (mis. `air_prob > 0.8`) → alarm, Telegram, kamera |

### 4.4 Batas dan keamanan

- ukuran file ≤ `AI_MODEL_MAX_MB` (bawaan 20), kuota per pengguna `AI_MODEL_QUOTA_MB`;
- validasi saat unggah di `iot-compute`: file benar-benar ONNX, operator didukung, satu input
  float, uji satu inferensi dengan nilai nol (≤ batas waktu);
- inferensi hanya di `iot-compute` (timeout, `intra_op_num_threads=1`, batas memori proses);
- `AI_MODELS=off|admin|all` seperti `MICRO_RULES`;
- log aktivitas: unggah, aktifkan versi, hapus.

### 4.5 Ke device (lanjutan, bukan bagian 29)

Model kecil dapat dikonversi ke TensorFlow Lite Micro dan dikirim lewat OTA sebagai bagian
firmware; dicatat sebagai pekerjaan lanjutan bersama micro rule.

### 4.6 Pengujian

Model ONNX kecil buatan tes (regresi linear, klasifikasi pohon) dibuat saat tes dengan `skl2onnx`
(dependensi pengembangan saja); validasi skema; fitur jendela waktu; node; CSV; penolakan pickle,
file rusak, model terlalu besar; timeout.

## 5. Tahap 30 — Function node (Python)

### 5.1 Bentuk bagi pengguna

```python
def run(msg, ctx):
    t = msg["temperature"]
    ctx.state["count"] = ctx.state.get("count", 0) + 1     # memori kecil per node per device
    if t > 30:
        msg["alert"] = f"hot {t:.1f} °C (#{ctx.state['count']})"
        return msg            # lanjut lewat Success
    return None               # berhenti di sini
```

- `msg`: dict key → nilai (telemetri + hasil node sebelumnya) dan `msg["_meta"]` (device, waktu);
- kembalian: `msg` (Success), `None` (berhenti), atau `[msg_a, None]` untuk beberapa keluaran
  (Output 1, Output 2, ... di diagram), mirip Node-RED;
- `ctx.state` (JSON, ≤ 4 KB, per node per device), `ctx.log(...)` ke log aturan;
- modul yang boleh: `math`, `statistics`, `json`, `datetime`, `random`, `re`; tanpa `import` lain;
- editor dengan penomoran baris dan pewarnaan (seperti editor impor aturan) + tombol **Test** dengan
  contoh pesan (dijalankan di sandbox yang sama).

### 5.2 Sandbox

Kode pengguna = kode asing di server bersama. RestrictedPython atau `exec` dengan builtins dibatasi
**tidak cukup** (sering dibobol). Lapisan yang direncanakan:

| Lapisan | Rincian |
|---|---|
| proses terpisah | worker `iot-compute` khusus function, user Linux tersendiri tanpa akses ke `/srv/iot` |
| nsjail / bubblewrap | namespace baru, filesystem baca-saja minimal (interpreter + modul yang boleh), **tanpa jaringan** |
| seccomp | hanya syscall yang perlu |
| batas | CPU 1 s per panggilan, memori 64 MB, tanpa proses anak, ukuran keluaran ≤ 16 KB |
| protokol | JSON masuk/keluar lewat pipa; tidak ada objek Python yang dibagi |
| izin | `FUNCTION_NODES=off|admin|all` (bawaan `off`); log aktivitas setiap perubahan kode |

Bila sandbox tidak tersedia (mis. laptop tanpa nsjail), node Function hanya bisa disimpan, tidak
dijalankan, dengan pesan yang jelas.

### 5.3 Micro rule

Function node selalu dijalankan di server (tidak bisa dikompilasi ke ESP32); analisis
"Run on devices" menandainya sebagai bagian cloud.

### 5.4 Pengujian

Fungsi benar (Success/None/multi-output), state, log; serangan yang harus gagal: membaca berkas,
`import os/subprocess/socket`, akses `__subclasses__`, loop tak berujung, alokasi besar, fork;
keluaran terlalu besar.

## 6. Dampak pada bagian lain

| Bagian | Perubahan |
|---|---|
| `rules/models.py` | `FlowNode.Kind`: `EXPRESSION`, `FUZZY`, `INFERENCE`, `FUNCTION`, `STORE`; `value_source`/`value_key`/`value_tolerance`; FK ke `FuzzyController` / `AiModel`; `code` (Function) |
| `rules/engines/flow.py` | handler node baru; node dengan beberapa keluaran (Function) |
| `rules/forms.py`, templat node | form per node, pemetaan input/keluaran |
| `rules/portable.py` | ekspor/impor node baru; controller fuzzy ikut diekspor (model AI hanya disebut namanya) |
| `rules/edge/*` | aksi `fuzzy` di IR, evaluator, simulator, test vector, `fuzzy.h` |
| `devices/models.py` | `Variable.Kind.DERIVED` |
| `deploy/` | `iot-compute.service`, Redis wajib untuk 29–30, nsjail (30), `onnxruntime` di `requirements.txt` (29) |
| `userdocs` | bab Fuzzy controllers, AI models, Function node, Expression |
| `.env` | `FUZZY_MAX_*`, `AI_MODELS`, `AI_MODEL_MAX_MB`, `AI_MODEL_QUOTA_MB`, `FUNCTION_NODES`, `COMPUTE_WORKERS`, `COMPUTE_TIMEOUT_S` |

## 7. Tahapan pengerjaan

### Tahap 28a — Fondasi
Set actuator dari key pesan (+ toleransi), node Expression (migrasi Calculate), variabel turunan dan
node Store as variable.

### Tahap 28b — Fuzzy controller
Model, mesin inferensi, editor dengan grafik dan Try it, node Fuzzy, aksi ECA, ekspor/impor, dokumentasi.

### Tahap 28c — Fuzzy di device
IR + evaluator + simulator + test vector + `fuzzy.h` di template ESP32.

### Tahap 29a — Worker komputasi dan Models
`iot-compute`, registry ONNX, validasi, halaman Try it.

### Tahap 29b — Inference di aturan
Node Inference, fitur jendela waktu, variabel turunan otomatis, CSV batch.

### Tahap 30 — Function node
Sandbox (nsjail + seccomp), node Function dengan editor dan Test, hanya admin dulu; dibuka untuk
semua pengguna setelah uji keamanan.

## 8. Keputusan yang perlu diambil

1. Apakah fuzzy cukup di rule chain dan ECA, atau perlu juga di state machine?
2. Batas ukuran controller fuzzy untuk ESP32 (usulan: 4 input, 7 term, 49 aturan) — sesuai dengan
   kasus praktikum?
3. Model AI: siapa yang boleh mengunggah (`all` atau `admin` dulu), dan berapa batas ukuran/kuota?
4. Function node: apakah VPS boleh dipasangi nsjail dan layanan `iot-compute` dengan user terpisah?
   Bila tidak, tahap 30 dibatasi pada Expression saja.
5. Apakah Redis sudah dipakai di server (Channels)? Worker komputasi memakainya sebagai antrean.

## 9. Referensi

- Mamdani, E. H., & Assilian, S. (1975). An experiment in linguistic synthesis with a fuzzy logic controller.
- Takagi, T., & Sugeno, M. (1985). Fuzzy identification of systems and its applications to modeling and control.
- ONNX Runtime: https://onnxruntime.ai/ · skl2onnx: https://onnx.ai/sklearn-onnx/
- nsjail: https://github.com/google/nsjail
