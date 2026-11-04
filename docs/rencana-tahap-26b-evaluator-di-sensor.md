# Rencana Tahap 26b — Strategi Pembanding: Evaluator di Device Sensor

Dokumen rencana. Sudah diimplementasikan; hasil dan perbedaannya ada di
`docs/tahap-26-micro-rule-model-dan-compiler.md`.

Dokumen ini melengkapi `docs/rencana-tahap-26-micro-rule.md`. Rencana utama menempatkan
evaluasi aturan di **device aktuator** (selanjutnya disebut strategi **AE**, *actuator
evaluates*). Dokumen ini merancang strategi pembanding: evaluasi di **device sensor**
(strategi **SE**, *sensor evaluates*), sedangkan aktuator hanya menerima keputusan dan
menjalankan aksi.

Tujuannya dua:

1. menyediakan implementasi SE yang adil (bukan versi lemah yang sengaja dibuat kalah),
   supaya perbandingan di paper sahih;
2. menunjukkan secara terukur keterbatasan SE: titik kegagalan tambahan, hop tambahan,
   keandalan pengiriman keputusan, dan ketidakcocokan dengan sensor yang tidur (deep sleep).

Semua yang ada di rencana utama tetap berlaku (IR, analisis, frame DATA broadcast, evaluator
referensi, test vector, feature flag) kecuali yang disebut berbeda di sini. Seperti tahap 26,
cakupannya **platform saja**: compiler, evaluator referensi, simulasi, dan halaman
perbandingan. Firmware SE dikerjakan bersama tahap 27–28 bila perbandingan di perangkat
nyata dibutuhkan.

## 1. Gambaran dua strategi

Aturan contoh: `(soil_moisture[S] < 20 AND temperature[T] > 30) OR soil_moisture[S] < 10`
selama 60 detik → `pump[P] = 1`; clear bila `soil_moisture[S] > 30` → `pump[P] = 0`.

```
AE (rencana utama)                         SE (dokumen ini)

 S ──DATA (broadcast)──┐                    T ──DATA (broadcast)──► S [evaluator]
                       ▼                                              │
 T ──DATA (broadcast)──► P [evaluator]                                │ STATE (broadcast)
                         └─► pump                                     ▼
                                                                      P ──► pump
 1 hop sensor → keputusan                   2 hop untuk T → P, 1 hop untuk S → P
 keputusan dibuat di tempat aksi            keputusan harus dikirim ke tempat aksi
```

## 2. Penempatan evaluator (`rules/edge/placement.py`)

Penempatan dipisah menjadi strategi yang bisa dipilih: `place(ir, strategy="actuator" | "sensor")`.
Bagian compiler lain (fragmen, ukuran, hash) dipakai bersama.

### a. Memilih device evaluator pada SE

| Kasus | Evaluator |
|---|---|
| Semua sinyal dari satu device | device itu |
| Sinyal dari beberapa device | dipilih dengan kebijakan di bawah |
| Sumber sinyal sekaligus aktuator | device itu (SE dan AE menjadi identik untuk aturan ini) |

Kebijakan untuk aturan multi-sensor (`MICRO_RULES["SE_POLICY"]`):

- `primary` (default): device dari kondisi pertama di grup pertama, sama dengan "device asal"
  di mesin cloud (`EcaSpec.primary_condition()`). Mudah dijelaskan ke pengguna.
- `most_signals`: device yang menyumbang sinyal terbanyak, sehingga sinyal yang harus
  di-relay paling sedikit.
- `min_traffic`: device dengan laju sampel tertinggi, sehingga frame DATA yang diteruskan
  lewat udara paling sedikit (butuh perkiraan laju dari telemetri 24 jam terakhir).

Tie-break selalu `device.pk` terkecil supaya deterministik. Ketiga kebijakan dijalankan di
simulasi (bagian 7) agar paper bisa menunjukkan bahwa keterbatasan SE tidak bergantung pada
pilihan kebijakan.

### b. Peran dan fragmen

| Device | Peran SE | Isi fragmen |
|---|---|---|
| Evaluator (mis. S) | `src+eval` atau `eval` | ekspresi, timing, **tanpa aksi lokal**, ditambah blok `emit` |
| Sensor lain (mis. T) | `src` | kontribusi ke tabel publikasi (sama dengan AE) |
| Aktuator (mis. P) | `act` | sumber keputusan (MAC evaluator), aksi on_activate / on_clear, kebijakan timeout |

Fragmen evaluator S:

```json
{
  "fmt": "microrule/1", "rule": 12, "rev": "4b7e02d1", "role": "src+eval", "strategy": "sensor",
  "self": 1,
  "sources": [{"p": 2, "mac": "AA:BB:CC:00:00:02"}],
  "signals": [{"s": 1, "from": 0, "kh": "0x6511"}, {"s": 2, "from": 2, "kh": "0xFF0F"}],
  "when":  [[[1, "<", 20.0], [2, ">", 30.0]], [[1, "<", 10.0]]],
  "clear": [[[1, ">", 30.0]]],
  "timing": {"duration": 60, "cooldown": 0, "max_age": 300, "tick": 1},
  "on_activate": [], "on_clear": [],
  "emit": {"heartbeat": 10, "repeat": 3, "repeat_gap_ms": 100}
}
```

Fragmen aktuator P:

```json
{
  "fmt": "microrule-act/1", "rule": 12, "rev": "4b7e02d1",
  "self": 3,
  "evaluator": {"p": 1, "mac": "AA:BB:CC:00:00:01"},
  "on_activate": [{"do": "set", "key": "pump", "value": 1.0}],
  "on_clear":    [{"do": "set", "key": "pump", "value": 0.0}],
  "state_timeout": 30,
  "on_timeout": "hold"
}
```

- `strategy` ikut di-hash, sehingga `rev` AE dan SE untuk aturan yang sama berbeda.
- `on_timeout`: `hold` (default, pertahankan keadaan terakhir) atau `clear` (jalankan
  on_clear bila evaluator diam lebih dari `state_timeout`). Default `hold` dipilih supaya
  perilaku sama dengan cloud: "tidak diketahui" tidak memicu apa pun.
- `state_timeout` default = 3 × `emit.heartbeat`.

## 3. Mengirim keputusan: frame STATE

Keputusan dikirim sebagai **keadaan** (level-triggered), bukan hanya sebagai **perintah
sekali kirim** (edge-triggered). Alasannya, broadcast ESP-NOW tidak punya ACK: bila satu
perintah "clear" hilang, pompa menyala terus. Dengan keadaan yang dipancarkan ulang
berkala, aktuator akhirnya selalu menyamakan diri.

Frame tipe 4 = STATE (tipe 1 = DATA, 2 = HELLO, 3 = EVENT, sesuai rencana utama):

| Offset | Ukuran | Kolom |
|---|---|---|
| 0 | 2 | magic `"MR"` |
| 2 | 1 | versi = 1 |
| 3 | 1 | tipe = 4 (STATE) |
| 4 | 4 | counter, uint32 LE (anti-replay, tahap 28) |
| 8 | 4 | rule id, uint32 LE |
| 12 | 4 | rev (4 byte pertama hash IR) |
| 16 | 1 | keadaan: 0 = tidak aktif (IDLE/PENDING), 1 = ACTIVE |
| 17 | 2 | nomor transisi, uint16, naik setiap ACTIVATE/CLEAR |
| 19 | 2 | umur transisi terakhir, uint16 (satuan 100 ms) |
| 21 | 8 | tag HMAC-SHA256 terpotong (nol di tahap 26) |

Ukuran tetap 29 byte.

Perilaku evaluator:

- saat transisi: kirim STATE `repeat` kali dengan jeda `repeat_gap_ms`;
- di luar transisi: kirim STATE setiap `heartbeat` detik.

Perilaku aktuator (`ActuatorRuntime`):

- terima frame hanya bila MAC sumber = evaluator, rule id cocok, dan rev cocok;
- bila nomor transisi berbeda dari yang terakhir diterapkan **dan** keadaan berbeda dari
  keadaan yang sedang berlaku, jalankan on_activate / on_clear; nomor yang sama berarti
  hanya heartbeat (duplikat dari pengulangan diabaikan);
- setelah restart, aktuator belum tahu keadaannya dan menerapkan keadaan dari frame STATE
  pertama yang diterima;
- bila tidak ada frame STATE selama `state_timeout`, jalankan kebijakan `on_timeout`.

## 4. Keterbatasan SE yang ingin ditunjukkan

Setiap keterbatasan dipasangkan dengan metrik yang dihitung compiler (analitik) atau
simulasi (bagian 7), supaya klaim di paper berbasis angka.

| # | Keterbatasan SE | Penjelasan | Metrik |
|---|---|---|---|
| L1 | **Titik kegagalan tambahan** | Di AE, evaluator *fate-sharing* dengan aktuator: bila aktuator mati, aksi memang tidak mungkin. Di SE, matinya evaluator sensor menghentikan aksi walaupun aktuator dan sensor lain sehat | jumlah device kritis per aturan; ketersediaan aturan saat satu device dimatikan |
| L2 | **Hop tambahan** | Sinyal dari sensor non-evaluator melewati dua hop (sensor → evaluator → aktuator) | jumlah hop maksimum; latensi ujung-ke-ujung |
| L3 | **Kehilangan berlipat** | Peluang keberhasilan dua hop ≈ (1 − p)², ditambah keputusan yang harus sampai | rasio aksi terlambat/terlewat pada loss *p* |
| L4 | **Keputusan harus diandalkan lewat radio** | Perlu frame STATE berulang dan heartbeat; tanpa itu satu frame hilang = aktuator salah keadaan | frame per detik tambahan; lama aktuator berbeda keadaan dengan evaluator |
| L5 | **Sensor harus selalu menyala** | Evaluator harus terus terjaga untuk timer durasi/cooldown, menerima DATA sensor lain, dan memancarkan heartbeat. Sensor bertenaga baterai biasanya *deep sleep*; aktuator umumnya bertenaga listrik tetap | duty cycle evaluator; perkiraan konsumsi energi |
| L6 | **Perintah palsu lebih berbahaya** | Frame STATE palsu langsung menggerakkan aktuator; di AE, data palsu masih harus lolos ekspresi kondisi | permukaan serangan (dibahas kualitatif) |
| L7 | **Kopling saat pembaruan aturan** | Mengubah aturan mengharuskan evaluator dan semua aktuator memegang `rev` yang sama; selama transisi aktuator mengabaikan keputusan | lama jendela tanpa kendali saat rollout |
| L8 | **Beban evaluator menumpuk** | Sensor populer bisa menjadi evaluator banyak aturan sekaligus | aturan per device, RAM fragmen |
| L9 | **Aktuator tidak tahu kenapa** | Aktuator hanya melihat keadaan, bukan nilai, sehingga tidak bisa menerapkan kebijakan lokal atas data usang | — (kualitatif) |

Supaya adil, kelebihan SE juga dicatat dan diukur:

| # | Kelebihan SE | Metrik |
|---|---|---|
| K1 | **Satu keputusan untuk semua aktuator**: tidak ada divergensi antar aktuator seperti evaluasi tereplikasi AE | lama dua aktuator berbeda keadaan |
| K2 | **Lalu lintas lebih kecil bila sinyal cepat dan aksi jarang**: hanya keadaan yang dipancarkan, bukan nilai mentah, untuk sensor yang juga evaluator | frame per detik total |
| K3 | **Nilai mentah sensor-evaluator tidak dipancarkan** (privasi) | — (kualitatif) |
| K4 | **Aktuator lebih sederhana** (cukup menerapkan keadaan) | ukuran fragmen dan RAM aktuator |
| K5 | **Pemulihan aktuator setelah restart** langsung dari heartbeat STATE | waktu pulih aktuator |

Dua catatan kejujuran untuk paper:

- Di **kedua** strategi, aturan yang ACTIVE tetap ACTIVE bila sinyal menjadi "tidak diketahui"
  (semantik cloud). Jadi masalah "pompa menyala terus saat sensor mati" bukan khas SE. Yang
  khas SE adalah aktuator tidak bisa membedakan "evaluator mati" dari "keadaan tidak berubah"
  tanpa heartbeat dan timeout.
- Untuk aturan yang semua sinyalnya dari satu device dan aktuatornya di device lain, L2 dan
  L3 hampir hilang. Perbedaan terbesar muncul pada aturan multi-sensor; ini yang perlu
  ditonjolkan di eksperimen.

## 5. Tambahan pada analisis

| Kode | Tingkat | Kapan |
|---|---|---|
| `SE_RELAY` | info | Aturan multi-sensor: sinyal sensor non-evaluator melewati dua hop |
| `SE_EXTRA_CRITICAL` | info | Evaluator bukan aktuator: jumlah device kritis bertambah satu |
| `SE_ALWAYS_ON` | warn | Evaluator harus selalu menyala; tidak cocok untuk sensor yang deep sleep |
| `SE_MULTI_ACT` | info | Beberapa aktuator berbagi satu keputusan (tanpa divergensi) |
| `SE_EVAL_LOAD` | warn | Device evaluator melebihi batas aturan per device (lintas aturan SE) |

Metrik analitik per rencana, dihitung compiler dan ditampilkan berdampingan untuk AE dan SE:
jumlah device kritis, hop maksimum, frame per detik (DATA + STATE), jumlah fragmen dan
ukurannya per device, serta divergensi yang mungkin (ya/tidak).

## 6. Evaluator referensi dan uji

- `Runtime` dari rencana utama dipakai tanpa perubahan untuk evaluator SE; aksi lokalnya
  kosong dan transisinya diteruskan ke `StateEmitter` (pengulangan + heartbeat).
- `ActuatorRuntime` baru: `receive_state(src_mac, frame, now_ms)` dan `tick(now_ms)`.
- **Uji kesetaraan tanpa kehilangan frame:** untuk 1.000 aturan acak, urutan aksi di aktuator
  pada AE, pada SE, dan pada mesin cloud harus sama (dengan toleransi latensi hop yang
  dideklarasikan). Ini memastikan perbedaan di eksperimen hanya berasal dari arsitektur,
  bukan dari bug semantik.
- Skenario test vector tambahan: duplikat dari pengulangan diabaikan, nomor transisi
  melompat (frame hilang) tetap menyamakan keadaan, rev berbeda diabaikan, timeout `hold`
  dan `clear`, aktuator restart lalu pulih dari heartbeat, frame STATE dari MAC lain
  diabaikan.
- Vector wire untuk frame STATE (hex ↔ hasil decode).

## 7. Simulasi pembanding (`rules/edge/sim.py`)

Perbandingan yang meyakinkan butuh kegagalan dan kehilangan frame, sesuatu yang belum ada
sebelum firmware tahap 27–28. Karena itu tahap 26b menambahkan simulator kejadian diskret
kecil (Python murni, tanpa Django) yang menjalankan AE dan SE dengan fragmen hasil compiler
yang sama.

- Model jaringan: broadcast dengan peluang kehilangan *p* per penerima (independen, opsional
  Gilbert-Elliott untuk kehilangan beruntun), latensi per hop dari distribusi sederhana.
- Model device: produsen dengan laju sampel, evaluator dengan tick 1 detik, aktuator.
- Injeksi kegagalan: matikan/hidupkan device tertentu pada waktu tertentu; restart.
- Data masukan: jejak telemetri sintetis atau jejak nyata yang diekspor dari platform.
- Keluaran (CSV): latensi sensor→aksi, aksi terlewat/terlambat, lama aktuator berada di
  keadaan salah terhadap "kebenaran" (cloud tanpa kehilangan), frame per detik, divergensi
  antar aktuator, ketersediaan aturan.
- Perintah: `python manage.py edge_simulate --rule <id> --strategy ae,se --loss 0,0.05,0.2
  --kill S@600 --runs 30 --out hasil.csv`.

Skenario minimal untuk paper:

| Skenario | Yang ditunjukkan |
|---|---|
| Tanpa kegagalan, *p* = 0 | AE = SE = cloud (sanity check) |
| *p* dinaikkan 0 → 30 % | L3, L4, K2 |
| Evaluator sensor S mati | L1 (SE berhenti, AE berperilaku sesuai semantik nilai usang) |
| Sensor non-evaluator T mati | kedua strategi setara |
| Aktuator P restart | K5 |
| Dua aktuator, *p* > 0 | K1 vs divergensi AE |
| Pembaruan aturan bertahap | L7 |

Simulasi ini dipakai lagi di tahap 30 dan dibandingkan dengan hasil perangkat nyata, sehingga
paper bisa menunjukkan bahwa simulator tervalidasi.

## 8. Perubahan data dan antarmuka

- `EdgePlan.strategy` (`ACTUATOR` / `SENSOR`). Unik (`rule`, `strategy`, `revision`);
  `is_current` per strategi. Kedua rencana dikompilasi bila `edge_enabled`.
- `EdgeFragment.role` mendapat nilai baru `ACT`.
- `Rule.edge_strategy` (default `ACTUATOR`) baru dibutuhkan saat distribusi tahap 27; di
  tahap 26b hanya rencana yang dibuat.
- Halaman Edge plan: tab **Actuator evaluates** / **Sensor evaluates**, plus tabel
  perbandingan metrik analitik (bagian 5) dan dua diagram penempatan berdampingan.
- `edge_report` menulis metrik kedua strategi per aturan.
- Fitur tetap di balik `MICRO_RULES["ENABLED"]`; strategi SE ditandai sebagai "comparison"
  dan tidak ditawarkan ke pengguna umum.

## 9. Struktur kode tambahan

```
rules/edge/
├── placement.py     place(ir, strategy, policy) — AE dan SE
├── actuator.py      ActuatorRuntime (SE)
├── emitter.py       StateEmitter (SE): pengulangan + heartbeat
├── metrics.py       metrik analitik per rencana
└── sim.py           simulator kejadian diskret
rules/management/commands/edge_simulate.py
firmware/microrule/vectors/act/*.json    skenario ActuatorRuntime
firmware/microrule/vectors/wire/state_*.json
rules/tests_edge_se.py
```

`compile.py` di rencana utama dipecah: logika penempatan pindah ke `placement.py`, sisanya
(fragmen, ukuran, hash) tetap.

## 10. Urutan pengerjaan

Dikerjakan setelah langkah 1–4 rencana utama (IR, analisis, compiler AE, evaluator
referensi) selesai, karena SE memakai semuanya.

1. `placement.py`: pindahkan penempatan AE, tambah SE dan tiga kebijakan + uji.
2. Frame STATE di `wire.py`, `emitter.py`, `actuator.py` + skenario + vector.
3. Uji kesetaraan tiga arah (cloud, AE, SE) tanpa kehilangan frame.
4. `metrics.py` dan kode analisis SE.
5. `sim.py` + `edge_simulate` + skenario bagian 7.
6. Model (`strategy`, role `ACT`), migrasi digabung dengan `0007_edge_plan`, tab dan tabel
   perbandingan di UI.
7. Dokumentasi: bagian SE di `docs/tahap-26-micro-rule-model-dan-compiler.md`, hasil
   simulasi awal sebagai tabel.

## 11. Di luar cakupan

Firmware SE; pemilihan evaluator dinamis (evaluator berpindah saat sensor mati, yaitu
failover, dibahas di tahap 29); strategi hibrida (mis. evaluator di sensor untuk aturan
satu-sensor dan di aktuator untuk aturan multi-sensor) — dicatat sebagai kemungkinan
temuan dari perbandingan ini.

## 12. Selesai bila

- Aturan irigasi contoh menghasilkan dua rencana: AE (S: src, T: src, P: eval) dan SE
  (S: src+eval, T: src, P: act), lengkap dengan metrik berdampingan.
- Uji kesetaraan tiga arah lulus.
- `edge_simulate` menghasilkan CSV untuk semua skenario di bagian 7, dan skenario "evaluator
  sensor mati" menunjukkan ketersediaan SE turun sementara AE tidak.
