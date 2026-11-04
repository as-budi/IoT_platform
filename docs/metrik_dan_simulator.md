# Metrik dan Simulator Micro Rule

Dokumen ini menjelaskan cara platform mengukur dan membandingkan dua strategi menjalankan
aturan di device (tahap 26): **AE** (*actuator evaluates*, evaluasi di device aktuator) dan
**SE** (*sensor evaluates*, evaluasi di satu device sensor, aktuator hanya menerima keputusan).
Isinya mencakup definisi, rumus, algoritma, asumsi, cara menjalankan, dan batas validitasnya
terhadap kondisi nyata.

Kode terkait:

| Berkas | Isi |
|---|---|
| `rules/edge/metrics.py` | metrik analitik |
| `rules/edge/sim.py` | simulator kejadian diskret |
| `rules/edge/evaluator.py`, `emitter.py`, `actuator.py`, `wire.py` | runtime yang dijalankan simulator (sama dengan acuan firmware) |
| `rules/management/commands/edge_report.py` | metrik semua aturan → CSV |
| `rules/management/commands/edge_simulate.py` | simulasi satu aturan → CSV |
| `rules/tests_edge.py` (`ParityTests`), `rules/tests_edge_se.py` | uji kesetaraan dan uji simulator |

Latar belakang: `docs/tahap-26-micro-rule-model-dan-compiler.md`,
`docs/rencana-tahap-26-micro-rule.md`, `docs/rencana-tahap-26b-evaluator-di-sensor.md`.

---

## 1. Dua alat yang saling melengkapi

| | Metrik analitik | Simulator |
|---|---|---|
| Pertanyaan | Apa konsekuensi **struktural** sebuah pembagian aturan? | Seberapa sering hasilnya **salah** bila dijalankan dengan radio tidak sempurna dan device yang bisa mati? |
| Masukan | IR aturan + rencana hasil compiler | IR + rencana + jejak data sensor + parameter lingkungan |
| Cara | dihitung langsung (rumus) | menjalankan device virtual dari waktu ke waktu |
| Waktu proses | seketika | ± 0,1 detik per satu jam simulasi |
| Tampil di | halaman aturan (tabel perbandingan), `edge_report` | `edge_simulate` (CSV) |
| Tidak menjawab | pengaruh waktu, kehilangan frame, data | (bisa menjawab semuanya, dalam batas model lingkungannya) |

---

## 2. Notasi

Sebuah aturan setelah dikompilasi:

- *D* = himpunan device yang terlibat, diberi nomor *p* = 1, 2, … (urut pk device).
- *S* = himpunan sinyal (variabel di kondisi), *src(s)* = device sumber sinyal *s*.
- *W* = ekspresi aktif: daftar grup *G₁ … Gₖ* (OR), tiap grup himpunan kondisi (AND).
  *dev(Gᵢ)* = himpunan device sumber dari kondisi di grup *Gᵢ*.
- *A* = himpunan device aktuator (target aksi Set actuator / Send command).
- *E* = himpunan evaluator: AE → *E = A*; SE → *E = {e}* satu device (lihat bagian 3).
- *P* = himpunan produsen: device yang memancarkan frame DATA
  = { *src(s)* | ada evaluator *e ∈ E* dengan *e ≠ src(s)* }.
- *hb* = heartbeat produsen (detik) = max(5, ⌊max_age / 3⌋), atau 30 bila max_age = 0.
- *hb_SE* = heartbeat frame keputusan SE (bawaan 10 detik).

---

## 3. Penempatan evaluator (dasar semua metrik)

```
fungsi evaluators(IR, strategi, kebijakan):
    A ← aktuator(IR)
    jika A kosong: kembalikan ∅
    jika strategi = AE: kembalikan A                      # evaluasi tereplikasi
    Src ← sumber sinyal(IR)
    jika Src ∩ A tidak kosong: kembalikan {min(Src ∩ A)}  # sensor di aktuator: tanpa hop tambahan
    jika |Src| = 1: kembalikan Src
    jika kebijakan = primary:      kembalikan {src(sinyal kondisi pertama di grup pertama)}
    jika kebijakan = min_traffic:  skor(p) = Σ laju sampel sinyal milik p
    selain itu (most_signals):     skor(p) = jumlah sinyal milik p
    kembalikan {p dengan skor tertinggi; bila seri, p terkecil}
```

Konsekuensi penting: bila sebuah device aktuator juga mengukur variabel aturan dan hanya ada
satu aktuator, rencana SE identik dengan AE (analisis menandainya `SE_AT_ACTUATOR`).

---

## 4. Metrik analitik (`metrics.plan_metrics`)

### 4.1 Device kritis

Device kritis = device yang bila mati membuat aksi aturan **tidak mungkin** terjadi.

```
sumber_wajib = dev(G₁) ∩ dev(G₂) ∩ … ∩ dev(Gₖ)     # device yang ada di SEMUA grup OR
kritis_AE    = A ∪ sumber_wajib
kritis_SE    = A ∪ sumber_wajib ∪ E
```

Alasan: tanpa sinyal dari sebuah device, kondisi yang memakainya bernilai "tidak diketahui";
grup AND yang memuatnya tidak pernah benar. Aturan masih bisa aktif lewat grup lain hanya bila
device itu tidak muncul di semua grup. Pada SE, evaluator juga kritis karena keputusan hanya
dibuat di sana.

Contoh: `(tanah[S] < 20 AND suhu[T] > 30) OR tanah[S] < 10` → sumber_wajib = {S}.
`tanah[S] < 5 OR suhu[T] > 35` → sumber_wajib = ∅, sehingga kritis_AE = {P}, kritis_SE = {S, P}.

Catatan: metrik ini tentang **aktivasi**. Kemampuan aturan untuk **pulih** juga bergantung pada
sumber di ekspresi clear; efek itu terlihat di simulator (bagian 9).

### 4.2 Hop terpanjang

Jumlah lompatan radio terpanjang dari sensor sampai aksi dieksekusi:

```
AE:  hop(s, a) = 0 bila src(s) = a, selain itu 1
SE:  hop(s, a) = [src(s) ≠ e] + [a ≠ e]
max_hops = maks atas semua sinyal s dan aktuator a
```

### 4.3 Frame per menit (keadaan tenang)

Batas bawah jumlah frame radio saat tidak ada perubahan nilai (hanya heartbeat):

```
frame_per_menit = Σ_{p ∈ P} 60 / hb  +  [SE dan ada aktuator selain e] · 60 / hb_SE
```

Bukan jumlah sebenarnya: setiap sampel baru juga memicu frame DATA (dibatasi `min_interval`
1 detik), dan setiap transisi SE memicu 3 frame STATE. Jumlah nyata didapat dari simulator
(`data_sent`, `state_sent`).

### 4.4 Divergensi mungkin

`divergence = (strategi = AE) dan |E| > 1`. Pada AE dengan dua aktuator, masing-masing
mengevaluasi sendiri; frame yang hilang ke salah satunya bisa membuat keduanya berbeda
keputusan sesaat. Pada SE semua aktuator mengikuti satu keputusan.

### 4.5 Sensor harus selalu menyala

`always_on_sensor = (strategi = SE) dan e ∉ A`. Evaluator SE harus terjaga untuk menghitung
durasi/cooldown, menerima frame sensor lain, dan memancarkan heartbeat keputusan; tidak cocok
dengan sensor baterai yang *deep sleep*.

### 4.6 Ukuran

`fragments` = jumlah fragmen, `total_bytes` = Σ ukuran JSON kanonik fragmen, `max_bytes` =
fragmen terbesar (batas device 1024 byte). Ukuran frame: DATA = 17 + 8n byte (n = jumlah
variabel yang dipancarkan), STATE = 29 byte.

### 4.7 Contoh (aturan irigasi: S tanah, T suhu, P pompa)

| Metrik | AE | SE |
|---|---|---|
| Device kritis | {S, P} = 2 | {S, P} = 2 |
| Hop terpanjang | 1 | 2 (T → S → P) |
| Frame per menit (tenang) | 2 × 60/100 = 1,2 | 60/100 + 60/10 = 6,6 |
| Divergensi | tidak | tidak |
| Sensor selalu menyala | tidak | ya (S) |
| Ukuran | 3 fragmen, 908 byte | 3 fragmen, 956 byte |

---

## 5. Simulator: gambaran umum

Simulator menjawab: "Bila rencana ini dijalankan oleh device sungguhan selama *T* detik,
dengan data sensor tertentu, radio yang kehilangan sebagian frame, dan device yang mati di
waktu tertentu, seberapa jauh perilaku aktuator menyimpang dari perilaku ideal?"

Prinsip utamanya: **logika tidak dimodelkan, tetapi dijalankan**. Setiap device virtual
memakai komponen yang sama dengan acuan firmware:

| Peran device | Komponen yang dijalankan |
|---|---|
| Produsen (`src`) | penyusun frame DATA (`wire.encode_data`) dengan heartbeat dan `min_interval` dari fragmen |
| Evaluator (`eval`) | `evaluator.Runtime` (penyaringan MAC/hash/counter, logika tiga nilai, durasi, cooldown, histeresis) |
| Pengirim keputusan SE | `emitter.StateEmitter` (frame STATE berulang + heartbeat) |
| Aktuator SE (`act`) | `actuator.ActuatorRuntime` (terapkan keadaan, abaikan duplikat, timeout, pulih setelah restart) |

Setiap frame benar-benar di-*encode* menjadi byte lalu di-*decode* penerima. Logika evaluator
sudah terbukti sama dengan mesin cloud (bagian 10) dan menjadi acuan test vector firmware. Jadi
yang disederhanakan hanya **lingkungan**: udara, waktu, data sensor, dan kegagalan device.

---

## 6. Algoritma simulasi kejadian diskret

### 6.1 Antrean kejadian

Simulator menyimpan antrean prioritas (min-heap) berisi tuple `(waktu_ms, nomor_urut, jenis,
data)`. Nomor urut menjaga urutan kejadian yang waktunya sama (FIFO), sehingga hasil
deterministik. Waktu tidak berjalan per detik, tetapi melompat ke kejadian berikutnya.

```
inisialisasi:
    untuk setiap sinyal s dan sampel (t, v) di jejak[s]:   push(t, SAMPLE, s, v)
    untuk t = 0, tick, 2·tick, … < T:                     push(t + 0,5, TICK)
    untuk setiap kegagalan (p, mati, hidup):              push(mati, FAIL, p); push(hidup, RECOVER, p)
    boot semua device (buat runtime sesuai fragmennya; evaluator SE langsung mulai heartbeat)

ulangi selama antrean tidak kosong:
    (t, jenis, data) ← pop()
    jika t > T: berhenti
    jalankan handler[jenis](data, t)
```

Tick digeser 0,5 ms supaya kejadian data pada milidetik yang sama diproses lebih dulu, seperti
firmware yang menangani data masuk sebelum pemeriksaan berkala.

### 6.2 Handler

```
SAMPLE(s, v, t):
    kebenaran.receive(s, v, umur 0, t); kebenaran.step(t)           # bagian 6.4
    p ← src(s); jika p mati: selesai                                # sensor mati tidak mengukur
    latest[p][key(s)] ← (v, t)
    jika p evaluator dan s sinyal lokal: evaluate(p, t)
    jika p memancarkan key(s):
        jika belum pernah kirim atau t − last_send[p] ≥ min_interval: PUBLISH(p, t)
        selain itu: push(last_send[p] + min_interval, PUBLISH, p)

PUBLISH(p, t):                                                      # frame DATA
    jika p mati atau tidak ada nilai: selesai
    sinyal ← [(hash(key), nilai, t − waktu_sampel) untuk setiap key yang dipancarkan p]
    counter[p] += 1; frame ← encode_data(counter[p], sinyal)
    BROADCAST(p, frame, t)
    last_send[p] ← t; push(t + hb·1000, HEARTBEAT, p, t)

HEARTBEAT(p, dikirim_pada, t):
    jika last_send[p] = dikirim_pada: PUBLISH(p, t)                 # tidak ada kiriman sejak itu

BROADCAST(pengirim, frame, t):
    untuk setiap device r ≠ pengirim yang mendengarkan pengirim:    # evaluator dengan pengirim di sources,
        jika random() < loss: hilang += 1; lanjut                   #   atau aktuator SE dengan evaluator = pengirim
        push(t + uniform(lat_min, lat_maks), DELIVER, r, pengirim, frame)

DELIVER(r, pengirim, frame, t):
    jika r mati: hilang += 1; selesai
    terkirim += 1
    jika frame DATA dan r evaluator: jika Runtime.receive_frame(MAC pengirim, frame, t) > 0: evaluate(r, t)
    jika frame STATE dan r aktuator SE: untuk setiap transisi dari receive_state(...): catat_level(r, ...)

evaluate(p, t, tick=salah):
    tr ← tick ? Runtime.tick(t) : Runtime.step(t)                   # tick hanya menilai bila PENDING
    jika tr: jika p aktuator: catat_level(p, tr = ACTIVATE, t)
             jika p evaluator SE: emitter.on_transition(tr, t); jadwalkan EMIT

EMIT(p, t):                                                         # frame STATE (SE)
    untuk setiap frame dari emitter.due(t): BROADCAST(p, frame, t)
    jadwalkan EMIT berikutnya pada emitter.next_due()

TICK(t):
    tr ← kebenaran.tick(t); jika tr: catat_kebenaran(t, tr)
    untuk setiap evaluator hidup: evaluate(p, t, tick=benar)
    untuk setiap aktuator SE hidup: transisi dari ActuatorRuntime.tick(t) → catat_level

FAIL(p):    tandai p mati
RECOVER(p): tandai p hidup; boot(p)
```

### 6.3 Model kegagalan device

- **Mati**: device tidak mengukur, tidak mengirim, tidak menerima, tidak mengevaluasi. Keluaran
  aktuator tetap di posisi terakhir (relay tidak berubah sendiri).
- **Hidup lagi** (`boot`): seperti reboot. Evaluator dibuat baru (keadaan aturan kembali IDLE,
  nilai sensor yang diingat hilang). Evaluator SE mulai lagi dengan keadaan "tidak aktif".
  Aktuator SE memanggil `restart()`: keadaan dianggap tidak diketahui sampai frame STATE
  berikutnya. Counter frame dianggap tersimpan di NVS, sehingga frame setelah reboot tidak
  dianggap *replay*.

### 6.4 Kebenaran (ground truth)

Simulator menjalankan satu `Runtime` tambahan yang menerima **setiap sampel seketika** (umur 0),
tanpa radio, tanpa kehilangan, tanpa device mati. Ia dinilai pada setiap sampel (`step`) dan
setiap tick (`tick`). Garis waktunya, *truth(t)* ∈ {aktif, tidak}, adalah keputusan yang akan
diambil platform bila jaringan sempurna. Karena memakai semantik yang sama dengan mesin cloud
(bagian 10), penyimpangan aktuator dari *truth* murni disebabkan oleh arsitektur dan lingkungan.

### 6.5 Garis waktu keadaan

Setiap aktuator *a* punya daftar `levels[a] = [(0, tidak), (t₁, v₁), (t₂, v₂), …]`, ditambah
satu entri hanya bila keadaannya berubah. Sumbernya: transisi evaluator di device itu (AE, atau
SE bila evaluator = aktuator) atau transisi `ActuatorRuntime` (SE).

---

## 7. Data sensor

### 7.1 Jejak sintetis (`synthetic_traces`)

```
untuk setiap sinyal s:
    Θ ← semua ambang kondisi (WHEN dan CLEAR) yang memakai s; bila kosong Θ = {0}
    rendah ← min(Θ) − 10;  tinggi ← maks(Θ) + 10;  langkah ← (tinggi − rendah) / 12
    v ← uniform(rendah, tinggi)
    t ← offset acak dalam [0, interval)                # sensor tidak sampling serempak
    selama t < T:
        v ← jepit(v + uniform(−langkah, +langkah), rendah, tinggi)
        catat (t, bulatkan(v, 2));  t ← t + interval   # bawaan interval 5 s
```

Random walk terbatas ini sengaja sering melewati ambang, sehingga aturan sering aktif dan pulih
dan kesalahan sempat muncul. Data nyata biasanya lebih halus, jadi angka absolut dari jejak
sintetis cenderung pesimistis; yang lebih dapat dipercaya adalah **perbandingan** AE dan SE pada
jejak yang sama.

### 7.2 Jejak nyata

`edge_simulate --sample-file jejak.json` menerima `{"<key variabel>": [[detik, nilai], ...]}`,
misalnya telemetri yang diekspor dari platform. Pada jejak nyata, celah data (sensor tidak
mengirim) ikut tersimulasikan karena tidak ada kejadian SAMPLE di celah itu.

---

## 8. Ukuran keluaran

Untuk setiap aktuator *a*, dengan *T* = lama simulasi dan jendela pencocokan *W* (bawaan 30 s):

### 8.1 Waktu salah keadaan

```
wrong_ms(a)  = ∫₀ᵀ [ level_a(t) ≠ truth(t) ] dt
wrong_ratio  = wrong_ms / T
```

Dihitung tepat dengan menggabungkan semua titik perubahan kedua garis waktu, lalu menjumlahkan
panjang selang yang nilainya berbeda (fungsi `_mismatch`).

### 8.2 Pencocokan transisi (terlewat, palsu, latensi)

```
dipakai ← ∅; latensi ← []; terlewat ← 0
untuk setiap transisi kebenaran (t, v), urut waktu:
    cari transisi aktuator pertama (u, v') dengan indeks ∉ dipakai, v' = v, dan t ≤ u ≤ t + W
    bila ada:   dipakai ← dipakai ∪ {indeks}; latensi.tambah(u − t)
    bila tidak: terlewat += 1
palsu = jumlah transisi aktuator − |dipakai|
```

- `missed`: transisi yang seharusnya terjadi tetapi tidak diikuti aktuator dalam *W* detik.
- `spurious`: transisi aktuator yang tidak berpasangan (terlambat lebih dari *W*, atau memang
  tidak seharusnya terjadi).
- `mean_latency_ms`, `max_latency_ms`: keterlambatan untuk transisi yang berpasangan.

### 8.3 Divergensi antar aktuator

`divergence_ms` = maks atas pasangan aktuator (*a*, *b*) dari ∫₀ᵀ [level_a(t) ≠ level_b(t)] dt.

### 8.4 Lalu lintas

`data_sent` (frame DATA), `state_sent` (frame STATE), `delivered` (salinan yang diterima),
`lost` (salinan yang hilang di udara atau tiba di device yang mati). Satu broadcast ke *k*
pendengar menghasilkan *k* salinan, sehingga `delivered + lost` = Σ salinan.

---

## 9. Menjalankan dan menganalisis

### 9.1 Parameter (`SimConfig`)

| Parameter | Bawaan | Arti |
|---|---|---|
| `duration_s` | 3600 | lama simulasi |
| `loss` | 0 | peluang satu salinan frame hilang (per penerima, independen) |
| `latency_ms` | (5, 30) | latensi radio, merata |
| `tick_ms` | 1000 | periode pemeriksaan berkala (sama dengan `MICRO_RULES["TICK_SECONDS"]`) |
| `seed` | 0 | benih bilangan acak (kehilangan dan latensi) |
| `failures` | [] | daftar (device, detik mati, detik hidup atau None) |
| `match_window_s` | 30 | jendela pencocokan transisi |

Parameter fragmen yang ikut menentukan hasil: `heartbeat` produsen (dari max_age), `min_interval`
(1 s), heartbeat/pengulangan STATE SE (10 s, 3×, jeda 100 ms), `state_timeout` dan `on_timeout`
aktuator SE (30 s, `hold`).

### 9.2 Perintah

```bash
# semua aturan, kedua strategi, metrik analitik
python manage.py edge_report --out edge.csv

# satu aturan: 2 strategi x 3 tingkat loss x (tanpa kegagalan + 1 skenario) x 10 run
python manage.py edge_simulate --rule 12 --loss 0,0.05,0.2 \
       --kill "Edge Soil Probe@600" --duration 3600 --runs 10 --out sim.csv

# device mati lalu hidup lagi; kebijakan SE lain; jejak nyata
python manage.py edge_simulate --rule 12 --kill "Edge Soil Probe@600-1800"
python manage.py edge_simulate --rule 12 --strategy sensor --policy most_signals
python manage.py edge_simulate --rule 12 --sample-file jejak.json
```

`--kill` menerima nama atau id device; setiap `--kill` menjadi skenario terpisah, ditambah
skenario tanpa kegagalan. Kolom CSV: `strategy, policy, loss, failure, run, actuator, wrong_ms,
wrong_ratio, truth_transitions, transitions, missed, spurious, mean_latency_ms, max_latency_ms,
divergence_ms, data_sent, state_sent, delivered, lost`.

### 9.3 Metode statistik

- **Pengulangan**: `--runs N` membuat N run dengan jejak sintetis dan seed berbeda
  (seed run = `--seed` × 1000 + nomor run). Laporkan rata-rata dan interval kepercayaan 95 %
  (mis. *t*-Student) per kombinasi strategi × loss × skenario.
- ***Common random numbers***: dalam satu run, AE dan SE memakai jejak sensor yang sama dan
  seed yang sama, sehingga perbedaan hasil terutama berasal dari strategi. Ini mengurangi
  varians perbandingan dan memungkinkan uji berpasangan (mis. *paired t-test* atau Wilcoxon
  atas selisih `wrong_ratio` per run). Catatan: urutan pemakaian bilangan acak kehilangan frame
  bisa berbeda antar strategi karena jumlah frame berbeda.
- **Determinisme**: dengan seed yang sama hasil selalu sama, sehingga setiap angka di paper bisa
  direproduksi.

### 9.4 Contoh hasil

Rata-rata 10 run, 1 jam, sampel tiap 5 s, jejak sintetis. Nilai = `wrong_ratio` / persentase
transisi terlewat / latensi rata-rata.

| Aturan | Skenario | AE | SE |
|---|---|---|---|
| Irigasi: `(S<20 ∧ T>30) ∨ S<10` (S wajib) | normal | 0,02 % / 0 % / 183 ms | 0,00 % / 0 % / 20 ms |
| | loss 20 % | 1,08 % / 9 % / 1393 ms | 1,03 % / 4 % / 101 ms |
| | S mati sejak 600 s | 42,0 % / 89 % | 44,5 % / 89 % |
| | T mati sejak 600 s | 5,9 % / 30 % | 5,9 % / 30 % |
| `S<5 ∨ T>35`, clear `T<30` (tidak ada sensor wajib) | normal | 0,01 % / 0 % / 18 ms | 0,02 % / 0 % / 34 ms |
| | loss 20 % | 1,27 % / 15 % | 1,03 % / 18 % |
| | **S (evaluator SE) mati** | **5,6 % / 52 %** | **39,9 % / 78 %** |
| | T mati | 38,9 % / 77 % | 38,9 % / 77 % |

Cara membaca:

1. **Titik kegagalan tambahan SE** baru tampak bila sensor evaluator tidak wajib bagi aturan
   (baris tebal). Bila sensor itu wajib, kedua strategi sama-sama gagal.
2. **Latensi AE lebih besar** pada irigasi karena evaluator AE baru mulai menghitung durasi saat
   frame tiba, lalu menunggu tick berikutnya (kuantisasi 1 detik); pada SE sensor tanah adalah
   evaluator sehingga durasi dimulai tanpa hop radio. Pada loss 20 %, frame yang hilang menunda
   deteksi sampai sampel atau heartbeat berikutnya.
3. **"T mati" pada aturan kedua** merugikan kedua strategi karena ekspresi clear hanya memakai T:
   tanpa T aturan tidak pernah pulih (nilai "tidak diketahui" tidak memulihkan aturan, sesuai
   semantik cloud). Ini temuan desain, bukan kesalahan simulator.

---

## 10. Uji kesetaraan semantik (dasar validitas logika)

Supaya simulator boleh memakai evaluator acuan sebagai pengganti logika firmware dan cloud,
kesetaraan evaluator dengan mesin cloud diuji (`ParityTests.test_random_rules`).

### 10.1 Pembangkitan aturan

Benih tetap 2026. Untuk setiap dari 1.000 kasus:

| Bagian | Pilihan acak |
|---|---|
| Variabel | 1–3, masing-masing di device berbeda |
| Ekspresi aktif | 1–3 grup OR × 1–3 kondisi AND |
| Kondisi | variabel acak, operator acak dari `< <= > >= == !=`, ambang bulat 0–20 |
| Ekspresi clear | ada pada 50 % aturan (1–2 grup), selain itu pulih saat ekspresi aktif salah |
| Durasi / cooldown / max_age | {0, 0, 5, 10} s / {0, 0, 8} s / {0, 15, 30, 300} s |
| Aksi | aktuator di device ke-4 (nyala saat aktif, mati saat pulih) |

Aturan dikompilasi oleh compiler sungguhan. Hasilnya 1.000 aturan yang semuanya berbeda;
514 dengan histeresis, 490 dengan durasi; keenam operator muncul ± 900 kali masing-masing.

### 10.2 Urutan kejadian dan pembanding

Setiap aturan menjalani 40 kejadian berjarak acak {0,5; 1; 2; 4; 8; 16} s: 60 % data baru
(variabel acak, nilai bulat 0–20), 40 % tick. Kejadian yang sama diberikan ke:

- **jalur cloud**: `conditions.evaluate` + `conditions.decide` + `eca.step`, dengan aturan
  penilaian `EcaEngine` (data baru selalu dinilai; tick hanya bila PENDING);
- **evaluator perangkat**: `Runtime.step` untuk data, `Runtime.tick` untuk tick.

Di **setiap** langkah transisi (ACTIVATE / CLEAR / tidak ada) dan status (IDLE / PENDING / ACTIVE)
harus sama. Total 40.000 langkah dengan 1.388 aktivasi dan 998 pemulihan; uji juga mewajibkan
lebih dari 500 transisi agar tidak lulus secara trivial.

Uji ini menemukan satu perbedaan pada versi awal (evaluator menilai ulang di setiap tick,
cloud hanya bila PENDING; dengan histeresis, ekspresi clear yang menjadi "tidak diketahui" karena
usang membuat aktivasi lolos lebih awal). Evaluator diperbaiki agar meniru cloud.

### 10.3 Cakupan

Nilai sengaja bilangan bulat (tepat dalam float32); pembulatan float32, format frame, dan
penyaringan frame diuji terpisah (test vector `float_equal`, `ignored_frames`, uji wire).
Kehilangan frame dan kegagalan device bukan bagian uji ini, melainkan wilayah simulator.

---

## 11. Validitas terhadap kondisi nyata

### 11.1 Yang dimodelkan tepat

| Aspek | Alasan |
|---|---|
| Logika keputusan | kode yang sama dengan acuan firmware, terbukti setara dengan cloud (bagian 10) dan dikunci test vector |
| Mekanisme waktu | tick, durasi, cooldown, umur data, heartbeat, pengulangan STATE, timeout aktuator sama dengan rancangan firmware |
| Isi frame | byte frame DATA/STATE yang sebenarnya, termasuk counter dan penyaringan MAC/hash |
| Restart device | keadaan hilang, counter tersimpan, aktuator SE pulih dari frame berikutnya |

### 11.2 Penyederhanaan dan dampaknya

| Penyederhanaan | Kenyataan | Dampak pada kesimpulan |
|---|---|---|
| Kehilangan Bernoulli, independen per penerima | kehilangan di udara sering beruntun (gangguan, penghalang bergerak) dan berkorelasi antar penerima | bisa terlalu optimistis; heartbeat dan pengulangan efektif melawan kehilangan acak, kurang efektif melawan kehilangan beruntun |
| Latensi merata 5–30 ms | ESP-NOW umumnya beberapa ms, dengan ekor akibat tabrakan/antrean | kecil, karena tick 1 detik mendominasi |
| Tanpa tabrakan dan kapasitas kanal | banyak pengirim bersamaan bisa saling tabrak | relevan untuk jaringan besar, kecil untuk 3–5 device |
| Kegagalan "bersih" (mati/hidup) | brownout, reboot berulang, Wi-Fi pindah kanal saat AP mati | perpindahan kanal adalah risiko nyata ESP-NOW (tahap 28) |
| Jam ideal | kristal ESP32 menyimpang ± puluhan ppm | dapat diabaikan: umur sampel relatif dan durasi aturan detik–menit |
| Aktuator seketika | relay/pompa punya waktu respons | tidak memengaruhi perbandingan strategi |
| Tanpa model energi | evaluator SE yang selalu menyala menguras baterai | baru tampil sebagai metrik ya/tidak |
| Data sintetis | data nyata lebih halus, berpola harian, kadang bolong | angka absolut belum boleh diklaim; arah perbandingan lebih kuat |

### 11.3 Metodologi agar mewakili kondisi nyata

Pendekatan baku: **kalibrasi → validasi → ekstrapolasi**.

1. **Kalibrasi lingkungan** (testbed tahap 28):
   - pasang 2–4 ESP32 pada beberapa jarak dan penghalang;
   - kirim frame bernomor dan catat frame yang hilang, RSSI, latensi;
   - turunkan *p*, sebaran latensi, dan parameter kehilangan beruntun
     (model Gilbert-Elliott: peluang pindah baik→buruk, buruk→baik, dan loss di tiap keadaan).
2. **Data nyata**: ekspor telemetri platform (mis. satu minggu data greenhouse) lalu jalankan
   `edge_simulate --sample-file`.
3. **Validasi**: jalankan skenario identik di testbed fisik (aturan, jejak, jadwal kegagalan
   sama) dan catat transisi aktuator. Bandingkan sebaran `wrong_ratio`, `missed`, dan latensi
   simulator dengan testbed; bila berada dalam interval kepercayaan yang sama, simulator
   dianggap tervalidasi untuk rentang kondisi itu.
4. **Ekstrapolasi**: simulator yang tervalidasi dipakai untuk eksperimen yang mahal atau
   mustahil di dunia nyata: banyak tingkat loss, ratusan run, topologi besar, kombinasi
   kegagalan. Testbed cukup membuktikan beberapa titik.

### 11.4 Rencana penyempurnaan

| Penyempurnaan | Tujuan |
|---|---|
| Model kehilangan beruntun Gilbert-Elliott | realisme radio (disebut di rencana 26b, belum diimplementasikan) |
| Perintah ekspor telemetri ke format `--sample-file` | memakai data nyata dengan mudah |
| Ringkasan per skenario (rata-rata, IK 95 %) di `edge_simulate` | tabel paper langsung |
| Model tabrakan/kapasitas kanal | jaringan dengan banyak device |
| Model energi (duty cycle × arus) | mengukur biaya "sensor selalu menyala" pada SE |
| Kebijakan aman saat data usang (tahap 29) | mengatasi aturan yang macet aktif saat sensor mati |

---

## 12. Simulasi massal: `edge_batch`

`edge_simulate` mensimulasikan satu aturan. Untuk kesimpulan yang dapat digeneralisasi (mis.
"untuk aturan seperti apa SE lebih buruk dari AE?") dibutuhkan ratusan sampai ribuan aturan
dengan bentuk berbeda. `edge_batch` melakukannya dalam satu perintah dan menulis satu CSV.

```bash
python manage.py edge_batch --rules 1000 --processes 8 --out batch.csv            # 1.000 aturan acak
python manage.py edge_batch --rules 1000 --failures none,se_evaluator,random_sensor --out batch.csv
python manage.py edge_batch --source db --runs 5 --out db.csv                       # aturan di database
```

### 12.1 Pembangkit aturan acak (`batch.random_rule`)

Aturan dibangkitkan tanpa database dan deterministik dari (`--seed`, nomor aturan), sehingga
1.000 aturan yang sama dapat dibuat ulang kapan saja:

```
n_sinyal ← acak 1..max_signals                                  (--max-signals, bawaan 3)
untuk setiap sinyal: device baru (75 %) atau device sensor yang sudah ada (25 %)
grup WHEN ← acak 1..max_groups                                  (--max-groups, bawaan 3)
    tiap grup: 1..max_conditions sinyal berbeda                  (--max-conditions, bawaan 3)
    tiap kondisi: operator berbobot < 30, <= 15, > 30, >= 15, = 5, ≠ 5; ambang acak 20..80
sinyal yang tidak dipakai kondisi dibuang, nomor dirapikan
CLEAR (50 %): kebalikan kondisi pertama dengan jarak 5..15 (mis. x < 40 → clear x > 52)
aktuator 1: di salah satu device sensor (peluang --p-colocated, bawaan 0,3) atau device sendiri
aktuator 2 (peluang --p-two-actuators, bawaan 0,25): device sendiri
durasi ∈ {0, 0, 10, 30, 60} s, cooldown ∈ {0, 0, 0, 30} s, max_age ∈ {60, 120, 300} s
```

Bobot operator membuat `=`/`≠` jarang, karena pada nilai kontinu `=` hampir tidak pernah benar.
Setiap aturan dianalisis dulu; aturan yang terblokir (mis. melebihi batas) dilewati dan dihitung.

### 12.2 Skenario dan pasangan perbandingan

Untuk setiap aturan, setiap run (`--runs`), setiap skenario kegagalan, dan setiap loss, kedua
strategi dijalankan dengan **jejak sensor yang sama, seed yang sama, dan device mati yang sama**.

| `--failures` | Device yang dimatikan pada `--fail-at` (bawaan 1/6 durasi), tanpa pulih | Berlaku bila |
|---|---|---|
| `none` | — | selalu |
| `se_evaluator` | device yang menjadi evaluator SE | evaluator SE bukan aktuator |
| `random_sensor` | satu device sensor acak yang bukan aktuator | ada sensor yang bukan aktuator |

Kolom `failed_required` mencatat apakah device yang mati muncul di **semua** grup WHEN (sensor
wajib). Inilah pembeda utama: bila sensor wajib mati, kedua strategi sama-sama gagal; bila tidak
wajib, AE masih bisa bekerja lewat grup lain sedangkan SE kehilangan evaluatornya.

### 12.3 Kolom CSV

Ciri aturan (sama untuk semua baris aturan itu):

| Kolom | Arti |
|---|---|
| `rule` | nomor aturan (1..N) |
| `n_signals`, `n_sensor_devices`, `n_actuators` | jumlah variabel, device sensor, aktuator |
| `n_groups`, `n_conditions`, `has_clear` | bentuk ekspresi |
| `duration`, `cooldown`, `max_age` | timing |
| `colocated` | ada aktuator yang juga mengukur variabel aturan |
| `required_sensors` | jumlah device sensor yang muncul di semua grup |
| `ae_critical`, `se_critical`, `ae_max_hops`, `se_max_hops` | metrik analitik (bagian 4) |
| `se_evaluator_is_actuator` | SE identik dengan AE untuk aturan ini (bagian 3) |

Hasil: `strategy, loss, failure, failed_required, run, actuator`, lalu kolom hasil yang sama dengan
`edge_simulate` (bagian 8).

### 12.4 Waktu proses

Satu aturan dengan 3 tingkat loss dan skenario `none,se_evaluator` memerlukan ± 12 simulasi
satu jam, sekitar 0,5 s. 1.000 aturan ± 8 menit dengan satu proses, ± 2 menit dengan
`--processes 4`. Hasil paralel identik dengan hasil satu proses. Untuk percobaan cepat:
`--duration 1800` atau `--rules 200`.

### 12.5 Contoh analisis

```python
import pandas as pd
df = pd.read_csv("batch.csv")

# 1) Kesalahan rata-rata per strategi, loss, dan skenario
print(df.groupby(["failure", "loss", "strategy"]).wrong_ratio.mean().unstack("strategy") * 100)

# 2) Selisih berpasangan SE - AE per aturan (positif = SE lebih buruk)
key = ["rule", "loss", "failure", "run", "actuator"]
pair = df.pivot_table(index=key, columns="strategy", values="wrong_ratio").reset_index()
pair["diff"] = (pair.sensor - pair.actuator) * 100

# 3) Kegagalan evaluator SE: wajib vs tidak wajib (keterbatasan L1)
f = df[df.failure == "se_evaluator"]
print(f.groupby(["failed_required", "strategy"]).wrong_ratio.mean().unstack() * 100)

# 4) Pengaruh bentuk aturan
print(df[df.failure == "none"].groupby(["n_sensor_devices", "strategy"]).wrong_ratio.mean().unstack() * 100)
```

Untuk statistik: gunakan uji berpasangan (Wilcoxon) atas `diff` per aturan, dan laporkan juga
jumlah aturan yang tidak pernah aktif (`truth_transitions = 0`) karena tidak menyumbang informasi
tentang kesalahan aktivasi.
