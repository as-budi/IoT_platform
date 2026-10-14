# Rule Chain di IoT Platform: Mekanisme Eksekusi dan Contoh

Dokumen ini menjelaskan cara kerja model **Flow-based rule chain**:

- bentuk graf dan pesannya,
- perjalanan satu data sensor dari device sampai aksi,
- algoritma penelusuran graf, langkah demi langkah,
- query database yang dijalankan,
- contoh lengkap dengan jejak (trace) yang dihasilkan mesin.

Semua jejak di dokumen ini adalah keluaran asli `rules/engines/flow.py` untuk data contoh dari
`seed_demo` + `seed_rules`. Skenarionya juga dijalankan sebagai tes di
`rules/tests_flow.py` (`SampleChainTests`). Ringkasan tahap ini ada di
[tahap-08-rule-chain.md](tahap-08-rule-chain.md); perbandingan dengan ECA ada di
[eca-mekanisme.md](eca-mekanisme.md).

Daftar isi:

1. [Graf, node, panah, label](#1-graf-node-panah-label)
2. [Pesan rule chain](#2-pesan-rule-chain)
3. [Tabel database sebuah rule chain](#3-tabel-database-sebuah-rule-chain)
4. [Alur eksekusi dari data masuk sampai aksi](#4-alur-eksekusi-dari-data-masuk-sampai-aksi)
5. [Algoritma penelusuran graf](#5-algoritma-penelusuran-graf)
6. [Cara kerja setiap jenis node](#6-cara-kerja-setiap-jenis-node)
7. [Pengecekan data di database](#7-pengecekan-data-di-database)
8. [Contoh lengkap: Lab power monitor](#8-contoh-lengkap-lab-power-monitor)
9. [Contoh lain (singkat)](#9-contoh-lain-singkat)
10. [AND, OR, dan pola umum dalam graf](#10-and-or-dan-pola-umum-dalam-graf)
11. [Jaminan, batasan, dan cara menelusuri masalah](#11-jaminan-batasan-dan-cara-menelusuri-masalah)
12. [Latihan](#12-latihan)

---

## 1. Graf, node, panah, label

Rule chain adalah **graf berarah**. Kotaknya adalah **node**, garisnya adalah **panah**
(link). Setiap panah punya **label**:

```
Input ──► [too_hot] ──True──► [fan_on]
              │
              └────False──► [fan_off]
```

Setiap node menyelesaikan tugasnya dengan **tepat satu label**:

| Kategori node | Label yang mungkin |
|---|---|
| Filter (Check value, Value changed, Rate limit) | `True`, `False`, dan `Failure` bila key yang diperiksa tidak ada di pesan (Rate limit tidak memeriksa key, jadi tidak punya Failure) |
| Enrichment, Transformation, Action | `Success` atau `Failure` |

Setelah node selesai, mesin mencari panah dari node itu **dengan label yang sama**:

- tidak ada panah → cabang selesai,
- satu panah → pesan pergi ke node tujuan,
- dua panah atau lebih → pesan **disalin**, satu salinan untuk setiap panah.

Panah dengan label lain diabaikan. Inilah inti model ini: **label menentukan jalan**.

Node **Input** bukan baris di tabel node. Di database, panah dari Input ditandai dengan
`source = NULL`. Panah dari Input selalu berlabel `Success`.

## 2. Pesan rule chain

Penyimpanan data (`save_telemetry()`) mengirim pesan ke antrean dalam bentuk umum
(`rules/queue.py`):

```json
{"type": "TELEMETRY",
 "originator": {"device": 2},
 "data": [{"variable": 5, "key": "voltage", "value": 230, "ts": "2026-10-14T02:18:54+00:00"},
          {"variable": 6, "key": "current", "value": 4,   "ts": "2026-10-14T02:18:54+00:00"},
          {"variable": 7, "key": "power",   "value": 600, "ts": "2026-10-14T02:18:54+00:00"}],
 "metadata": {"protocol": "MQTT", "received_at": "…"}}
```

`FlowEngine` mengubahnya menjadi **pesan rule chain** (`new_message()`), yang lebih mudah
dipakai node:

```json
{"data":     {"voltage": 230, "current": 4, "power": 600},
 "metadata": {"deviceId": 2, "deviceName": "Lab Power Meter",
              "protocol": "MQTT", "ts": "2026-10-14T02:18:54+00:00"}}
```

- `data` berisi nilai **terbaru** setiap key di pesan. Bila satu pesan memuat beberapa titik
  waktu untuk key yang sama, yang dipakai adalah yang paling akhir.
- Node *Add latest value* dan *Calculate* **menambah key** ke `data`, mis.
  `apparent_power`. Node berikutnya bisa memakai key itu seperti key dari device.
- Placeholder pesan alarm dan email bisa memakai key `data` maupun `metadata`, mis.
  `{deviceName}: {voltage} V`.

Rule chain hanya melihat **isi pesan itu sendiri**. Ini berbeda dengan ECA, yang otomatis
mengambil nilai terakhir semua variabel di kondisinya dari database. Di rule chain, data lain
diambil secara **eksplisit** dengan node *Add latest value* (lihat [bagian 6](#6-cara-kerja-setiap-jenis-node)).

## 3. Tabel database sebuah rule chain

Contoh: aturan **Lab power monitor** milik `budi` (id aturan dan node hanya ilustrasi).

`rules_rule`

| id | owner | name | model | is_enabled |
|---|---|---|---|---|
| 7 | budi | Lab power monitor | FLOW | 1 |

`rules_flowspec`: pesan dari device mana yang masuk ke Input.

| rule_id | device_id |
|---|---|
| 7 | 2 (Lab Power Meter) |

`device_id = NULL` berarti "semua device yang boleh dilihat pemilik aturan".

`rules_flownode`: satu baris per node. Kolom yang tidak dipakai jenis node itu dibiarkan
kosong, sama seperti `RuleAction`.

| id | name | kind | key | operator | threshold | operand_a | math_op | operand_b | seconds | severity / message | memory |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 21 | over_voltage | CHECK | voltage | `>` | 235 | | | | | | `{}` |
| 22 | volt_alarm | CREATE_ALARM | voltage | | | | | | | MAJOR / "Over-voltage on {deviceName}: {voltage} V" | `{}` |
| 23 | volt_ok | CLEAR_ALARM | | | | | | | | | `{}` |
| 24 | apparent | CALCULATE | apparent_power | | | voltage | `*` | current | | | `{}` |
| 25 | pf | CALCULATE | power_factor | | | power | `/` | apparent_power | | | `{}` |
| 26 | low_pf | CHECK | power_factor | `<` | 0.8 | | | | | | `{}` |
| 27 | pf_limit | THROTTLE | | | | | | | 600 | | `{"2": {"at": "…"}}` |
| 28 | pf_email | EMAIL | | | | | | | | message: "Low power factor on {deviceName}: …" | `{}` |

`rules_flowlink`: satu baris per panah. Urutan baris (`order`) menentukan urutan cabang
ditelusuri.

| order | source_id | label | target_id | dibaca |
|---|---|---|---|---|
| 0 | NULL | SUCCESS | 21 | Input → over_voltage |
| 1 | NULL | SUCCESS | 24 | Input → apparent |
| 2 | 21 | TRUE | 22 | over_voltage —True→ volt_alarm |
| 3 | 21 | FALSE | 23 | over_voltage —False→ volt_ok |
| 4 | 24 | SUCCESS | 25 | apparent —Success→ pf |
| 5 | 25 | SUCCESS | 26 | pf —Success→ low_pf |
| 6 | 26 | TRUE | 27 | low_pf —True→ pf_limit |
| 7 | 27 | TRUE | 28 | pf_limit —True→ pf_email |

Tabel yang dipakai bersama ECA:

| Tabel | Isi untuk rule chain |
|---|---|
| `rules_rulestate` | `snapshot` = jejak pesan **terakhir** (kotak diagram di halaman detail), `last_evaluated_at` = waktu pesan terakhir, `last_activated_at` = waktu terakhir ada node aksi yang berjalan. `status` tidak dipakai (selalu IDLE) |
| `rules_ruleexecution` | satu baris per pesan yang **mengubah sesuatu**: `transition = 'RUN'`, `results` = hasil node aksi, `snapshot` = isi pesan, `trace` = jejak lengkap |
| `rules_alarm` | alarm yang dibuat node *Create alarm* |

**Kolom `memory`** adalah "ingatan" node, dipakai oleh *Value changed* dan *Rate limit*.
Kuncinya adalah id device asal pesan, supaya setiap device punya ingatannya sendiri. Ingatan
dikosongkan setiap kali aturan disimpan dari form.

## 4. Alur eksekusi dari data masuk sampai aksi

```
Device ──HTTP/MQTT/CoAP──► save_telemetry()
                               │ 1. INSERT devices_telemetry (3 baris)
                               │ 2. publish(pesan) setelah COMMIT
                               ▼
                      antrean Redis "rule-engine:messages"   (atau langsung, mode inline)
                               ▼
                      worker: queue.process(pesan)
                               │ untuk setiap mesin yang implemented: ECA, lalu FLOW
                               ▼
FlowEngine.on_message(pesan, now)
   3. pilih aturan kandidat: model FLOW, aktif, device cocok atau "semua device"
   4. untuk setiap kandidat:
        a. device pesan boleh dilihat pemilik aturan?  tidak → lewati
        b. run(): ubah ke pesan rule chain, lalu walk() menelusuri graf
        c. simpan RuleState (jejak terakhir)
        d. ada aksi yang mengubah sesuatu?  ya → INSERT RuleExecution, kabar "rule"
                                             tidak → kabar "rule_eval"
```

Beberapa catatan:

- **Satu pesan, beberapa aturan.** Setiap rule chain yang cocok menelusuri grafnya sendiri,
  satu per satu. Aturan ECA untuk device yang sama juga tetap berjalan.
- **Akses diperiksa dua kali.**
  - Saat pesan masuk: pesan dari device milik orang lain tidak pernah masuk ke rule chain
    "semua device".
  - Saat node aksi atau enrichment berjalan: target harus device yang boleh dilihat pemilik
    aturan.
- **Kesalahan satu node tidak menghentikan worker.** Error apa pun di dalam node menjadi
  label `Failure` dengan keterangannya, lalu penelusuran berlanjut lewat panah `Failure`
  (bila ada).
- **Kabar WebSocket** dikirim ke group device **asal pesan**:
  - `rule_eval`: halaman detail mengambil ulang kotak diagram dan jejak.
  - `rule`: ada baris log baru, halaman dimuat ulang.

  Halaman rule chain yang mendengarkan semua device berlangganan kabar dashboard (semua
  device pemilik).

## 5. Algoritma penelusuran graf

Fungsi `walk()` memakai **tumpukan (stack)**. Setiap isi tumpukan adalah satu "tugas":
`(node yang harus dijalankan, salinan pesan untuk node itu, nama node sebelumnya)`.

```python
stack = [(node tujuan, salinan(pesan), "Input") untuk setiap panah dari Input, urutan terbalik]
while stack:
    if langkah >= 50: catat "Stopped after 50 steps"; berhenti
    node, msg, dari = stack.pop()                  # ambil tugas TERATAS
    label, keterangan = execute(node, msg)        # node bekerja, boleh mengubah msg
    panah = panah dari node ini dengan label itu
    catat baris jejak (langkah, dari, node, label, keterangan, isi msg, tujuan berikutnya)
    for p in reversed(panah):
        stack.push((p.target, salinan(msg), node.name))
```

Tiga keputusan desain:

1. **Depth-first.** `pop()` mengambil tugas yang terakhir dimasukkan, sehingga satu cabang
   diikuti sampai habis sebelum cabang berikutnya. Panah dimasukkan **terbalik** supaya panah
   pertama (urutan terkecil) diambil lebih dulu. Jejak pun terbaca seperti cerita, satu cabang
   demi satu cabang.
2. **Salinan per cabang** (`copy.deepcopy`). Bila cabang A menambah key `apparent_power`,
   cabang B tidak melihatnya. Setiap cabang punya dunianya sendiri, sehingga urutan cabang
   tidak mengubah hasil.
3. **Batas 50 langkah.** Form menolak graf yang berputar, tetapi data bisa saja diubah dari
   admin atau langsung di database. Batas ini mencegah worker berputar selamanya.

Contoh isi tumpukan untuk pesan pertama Lab power monitor (bagian 8). Bagian atas tumpukan
ditulis di **kiri**.

| Langkah | Diambil | Hasil | Didorong | Tumpukan sesudahnya |
|---|---|---|---|---|
| awal | | | over_voltage, apparent | [over_voltage, apparent] |
| 1 | over_voltage | False | volt_ok | [volt_ok, apparent] |
| 2 | volt_ok | Success | (tidak ada panah) | [apparent] |
| 3 | apparent | Success | pf | [pf] |
| 4 | pf | Success | low_pf | [low_pf] |
| 5 | low_pf | True | pf_limit | [pf_limit] |
| 6 | pf_limit | True | pf_email | [pf_email] |
| 7 | pf_email | Success | (tidak ada panah) | [] → selesai |

**Mengapa graf tidak boleh berputar?** Pada siklus `a → b → a`, sebuah pesan akan beredar
selamanya. Form memeriksanya dengan **DFS berwarna** (`check_graph()` di `rules/forms.py`):

- **putih**: node belum dikunjungi,
- **abu-abu**: node sedang ditelusuri (ada di jalur saat ini),
- **hitam**: node sudah selesai.

Bila penelusuran bertemu node abu-abu, berarti ada jalan yang kembali ke dirinya sendiri.
Form lalu menampilkan pesan seperti:

```
The links form a loop: hot → calc → fan → hot. A rule chain must not loop back.
```

**Node yang dicapai lewat dua jalan** (*fan-in*) dijalankan **sekali untuk setiap
kedatangan**. Contoh `hot —True→ alarm` dan `humid —True→ alarm`: bila keduanya True,
`alarm` berjalan dua kali. Itu aman karena *Create alarm* tidak membuat alarm kedua selama
yang pertama masih aktif. Diagram menempatkan node seperti ini di kolom setelah jalur
**terpanjang** yang menuju ke sana.

## 6. Cara kerja setiap jenis node

### Filter

**Check value**: `key operator ambang`.

| Situasi | Label | Keterangan di jejak |
|---|---|---|
| `voltage` = 238, `> 235` | True | `voltage = 238 > 235 → yes` |
| `voltage` = 230, `> 235` | False | `voltage = 230 > 235 → no` |
| pesan tidak berisi `voltage` | Failure | `the message has no 'voltage'` |

Perbandingan memakai fungsi `compare()` yang sama dengan ECA. Untuk `=` dan `≠`, nilainya
dianggap sama bila selisihnya kurang dari 1e-9.

**Value changed**: lolos (True) bila nilai bergeser **minimal sebesar ambang** dari nilai
terakhir yang **lolos**, per device.

```
ingatan[device] kosong              → True,  simpan nilai          ("first value")
|nilai − ingatan[device]| ≥ ambang  → True,  simpan nilai          ("6.2 → 7.4 (changed by 1.2)")
selain itu                          → False, ingatan TIDAK berubah ("last passed 6.2 (needs 0.1)")
```

Ingatan hanya diperbarui saat lolos. Akibatnya, perubahan kecil yang **berkumpul** tetap
terdeteksi. Contoh dengan ambang 0.5 dan nilai berturut-turut 25 → 25.3 → 25.6: pesan kedua
False (selisih 0.3), pesan ketiga True (selisih 0.6 dari 25). Pola ini disebut **deadband**.

**Rate limit**: lolos paling banyak sekali tiap N detik, per device.

```
belum pernah lolos, atau sudah ≥ N detik sejak terakhir lolos → True, simpan waktu sekarang
selain itu                                                   → False ("last passed 120 s ago, limit 600 s")
```

Ingatan kedua node ini dibaca dan ditulis dengan **baris terkunci** (`SELECT … FOR UPDATE`).
Dalam mode inline, dua proses bisa memproses pesan bersamaan; tanpa kunci, keduanya bisa
sama-sama menganggap dirinya "yang pertama" lalu sama-sama mengirim email.

### Enrichment

**Add latest value**: mengambil nilai **terakhir yang tersimpan** sebuah variabel dari
database, lalu menambahkannya ke `data` dengan nama `key` (kosong = key variabel itu sendiri).

| Situasi | Label | Keterangan |
|---|---|---|
| ada data, umurnya ≤ batas | Success | `rain = 0 from Greenhouse Weather Station · Rainfall (30 s old)` |
| tidak ada data sama sekali | Failure | `… has no data` |
| data lebih tua dari batas detik | Failure | `… is stale (86395 s old, limit 900 s)` |
| variabel milik device yang tidak boleh dilihat pemilik aturan | Failure | `the rule owner has no access to …` |

Inilah padanan "nilai terakhir dari database" dan "data basi" pada ECA. Bedanya, di rule
chain keduanya ditulis **eksplisit** sebagai node, dan data basi diarahkan lewat panah
**Failure**. Pada ECA, data basi menjadi nilai *unknown*.

### Transformation

**Calculate**: `key = a op b`. Operand berupa key pesan atau angka (`"2"`, `"0.5"`).
Operasinya `+ − × ÷ min max`. Hasil dibulatkan sampai 6 angka desimal.

| Situasi | Label | Keterangan |
|---|---|---|
| normal | Success | `power_factor = power ÷ apparent_power = 0.652174` |
| operand berupa key yang tidak ada di pesan | Failure | `the message has no 'current'` |
| bagi dengan nol | Failure | `division by zero` |

Tidak ada `eval()` atau skrip bebas. ThingsBoard memakai skrip JavaScript/TBEL di sandbox;
di sini rumus dibatasi pada satu operasi per node supaya aman dan mudah ditelusuri. Rumus
yang lebih panjang ditulis sebagai beberapa node berurutan.

### Action

Node aksi memanggil fungsi yang sama dengan aksi ECA (`rules/actions.py`). Kolom `FlowNode`
sengaja dinamai sama dengan `RuleAction` (`target_variable`, `value`, `method`, `severity`,
`message`, …), sehingga node bisa langsung diserahkan ke fungsi-fungsi itu.

| Node | Bila berhasil | Dianggap "mengubah sesuatu"? |
|---|---|---|
| Set actuator | `Water pump = ON` | ya. Bila desired sudah sama: `Water pump = ON (already requested)`, **tidak** |
| Send command | `Cold Storage Unit: buzzer (command #1)` | ya |
| Create alarm | `Major alarm #1` | ya. Bila alarm aturan ini masih aktif di device itu: `Alarm #1 is still active`, **tidak** |
| Clear alarm | `Cleared alarm #1` | ya. Bila tidak ada alarm aktif: `No active alarm`, **tidak** |
| Send email | `Email to budi@example.com` | ya (selalu) |

Bila aksi gagal (mis. pemilik aturan tidak punya akses ke aktuator), node keluar lewat
**Failure**. Kegagalan itu juga dicatat di execution log dengan tanda ✗.

**Mengapa log hanya untuk pesan yang "mengubah sesuatu"?** Rule chain tidak punya keadaan
ACTIVE. Selama suhu tinggi, setiap pesan (mis. tiap 10 detik) sampai di node *Set actuator*.
Bila setiap pesan dicatat, log bertambah 8.640 baris per hari tanpa informasi baru.

## 7. Pengecekan data di database

SQL berikut disederhanakan dari query yang disusun Django.

### 7.1 Memilih aturan kandidat

```python
Rule.objects.filter(model="FLOW", is_enabled=True)
    .filter(Q(flow__device_id=2) | Q(flow__device__isnull=True))
```

```sql
SELECT r.*, f.*
FROM rules_rule r
JOIN rules_flowspec f ON f.rule_id = r.id
WHERE r.model = 'FLOW' AND r.is_enabled = 1
  AND (f.device_id = 2 OR f.device_id IS NULL);
```

Node dan panah semua kandidat diambil sekaligus (`prefetch_related`), hanya dua query
berapa pun jumlah aturannya:

```sql
SELECT * FROM rules_flownode WHERE rule_id IN (7) ORDER BY `order`, id;
SELECT * FROM rules_flowlink WHERE rule_id IN (7) ORDER BY `order`, id;
```

Setelah itu, **penelusuran graf berjalan di memori**: `Graph` menyusun kamus
`(node_id, label) → [panah]`. Mencari panah berikutnya tidak memerlukan query.

### 7.2 Pemeriksaan akses

Untuk setiap kandidat:

```sql
SELECT 1 FROM devices_device WHERE id = 2 AND owner_id = <pemilik aturan> LIMIT 1;
-- Admin: tanpa syarat owner_id
```

### 7.3 Query milik node

| Node | Query |
|---|---|
| Check value, Calculate | tidak ada (hanya isi pesan) |
| Value changed, Rate limit | `BEGIN; SELECT … FROM rules_flownode WHERE id = 27 FOR UPDATE; UPDATE rules_flownode SET memory = '{"2": {"at": "…"}}' WHERE id = 27; COMMIT;` (UPDATE hanya bila lolos) |
| Add latest value | cek akses seperti 7.2, lalu `SELECT value, ts FROM devices_telemetry WHERE variable_id = 11 ORDER BY ts DESC LIMIT 1` |
| Set actuator | sama dengan aksi ECA: cek akses, baca `desired_value`, lalu `set_desired()` bila berbeda |
| Send command | `INSERT INTO devices_command …` |
| Create alarm | `SELECT … FROM rules_alarm WHERE rule_id = 7 AND device_id = 2 AND cleared_at IS NULL`; bila kosong, `INSERT` |
| Clear alarm | query yang sama, lalu `UPDATE rules_alarm SET cleared_at = …` untuk setiap alarm aktif |

Query *Add latest value* cepat karena index `telemetry_var_ts (variable_id, ts)`, sama seperti
pada ECA ([eca-mekanisme.md bagian 6.2](eca-mekanisme.md#62-mengambil-nilai-terakhir-variabel-lain)).

**Mengapa nilai pesan diambil dari pesan, bukan dari database?** Alasannya sama dengan ECA.
Dalam mode worker, pesan diproses sedikit setelah disimpan, dan device mungkin sudah mengirim
nilai yang lebih baru. Setiap pesan dinilai dengan nilainya sendiri, sehingga urutan kejadian
tetap benar.

### 7.4 Menyimpan hasil

```sql
-- selalu: jejak pesan terakhir
UPDATE rules_rulestate
   SET last_evaluated_at = '…', last_activated_at = '…',      -- yang kedua hanya bila ada aksi
       snapshot = '{"at": "…", "device": 2, "device_name": "Lab Power Meter",
                    "input": {"voltage": 230, …}, "trace": [ … ]}'
 WHERE rule_id = 7;

-- hanya bila ada aksi yang mengubah sesuatu atau gagal
INSERT INTO rules_ruleexecution (rule_id, transition, results, ok, snapshot, trace, created_at)
VALUES (7, 'RUN', '[{"action": "pf_email (Send email)", "ok": true, …}]', 1,
        '{"Lab Power Meter · voltage": 230, …}', '[ …jejak… ]', '…');
```

Bentuk satu baris jejak:

```json
{"step": 4, "node": 25, "name": "pf", "kind": "CALCULATE", "kind_label": "Calculate",
 "category": "Transformation", "from": "apparent", "label": "SUCCESS",
 "detail": "power_factor = power ÷ apparent_power = 0.652174",
 "data": {"voltage": 230, "current": 4, "power": 600, "apparent_power": 920, "power_factor": 0.652174},
 "next": ["low_pf"]}
```

`data` adalah isi pesan **sesudah** node itu bekerja. Dari kolom ini terlihat key mana yang
ditambahkan oleh setiap node.

## 8. Contoh lengkap: Lab power monitor

**Tujuan aturan:**

- alarm bila tegangan listrik lab melebihi 235 V, dan alarm itu di-clear saat tegangan normal
  kembali;
- email bila faktor daya beban turun di bawah 0.8, paling banyak satu email tiap 10 menit.

Faktor daya = daya nyata (W) ÷ daya semu (V × A). Device Lab Power Meter hanya mengirim
`voltage`, `current`, dan `power`, jadi faktor daya harus **dihitung** di aturan. Itulah
sebabnya contoh ini memakai rule chain, bukan ECA.

```
         ┌─► over_voltage: voltage > 235 ──True──► volt_alarm (Create alarm, MAJOR)
         │                               └─False─► volt_ok (Clear alarm)
Input ───┤
         └─► apparent: apparent_power = voltage × current
                 └─Success─► pf: power_factor = power ÷ apparent_power
                                 └─Success─► low_pf: power_factor < 0.8
                                                 └─True─► pf_limit: Rate limit 600 s
                                                              └─True─► pf_email
```

Coba sendiri (akun `budi`):

```bash
python manage.py send_telemetry "Lab Power Meter" voltage=230 current=4 power=600
```

### Pesan 1, pukul 09:00:00 — `voltage=230, current=4, power=600`

| # | Dari | Node | Hasil | Yang terjadi | Berikutnya |
|---|---|---|---|---|---|
| 1 | Input | over_voltage | False | voltage = 230 > 235 → no | volt_ok |
| 2 | over_voltage | volt_ok | Success | No active alarm | *(selesai)* |
| 3 | Input | apparent | Success | apparent_power = voltage × current = 920 | pf |
| 4 | apparent | pf | Success | power_factor = power ÷ apparent_power = 0.652174 | low_pf |
| 5 | pf | low_pf | True | power_factor = 0.652174 < 0.8 → yes | pf_limit |
| 6 | low_pf | pf_limit | True | first message, limit 600 s | pf_email |
| 7 | pf_limit | pf_email | Success | Email to budi@example.com | *(selesai)* |

- Langkah 1–2 adalah cabang pertama dari Input; langkah 3–7 cabang kedua (depth-first).
- Langkah 2: tidak ada alarm untuk di-clear, jadi tidak ada yang berubah.
- Langkah 3–4 menambah dua key ke pesan:
  `{"voltage": 230, "current": 4, "power": 600, "apparent_power": 920, "power_factor": 0.652174}`.
- Langkah 6: ingatan `pf_limit` menjadi `{"2": {"at": "09:00:00"}}`.
- Langkah 7: email terkirim dengan isi `Low power factor on Lab Power Meter: 0.652174 (600 W of 920 VA).`
- **Log:** satu baris RUN berisi hasil `volt_ok` dan `pf_email`. Email terhitung "mengubah
  sesuatu", jadi pesan ini dicatat.

### Pesan 2, pukul 09:02:00 — `voltage=238, current=4, power=600`

| # | Dari | Node | Hasil | Yang terjadi | Berikutnya |
|---|---|---|---|---|---|
| 1 | Input | over_voltage | **True** | voltage = 238 > 235 → yes | volt_alarm |
| 2 | over_voltage | volt_alarm | Success | Major alarm #1 | *(selesai)* |
| 3 | Input | apparent | Success | apparent_power = voltage × current = 952 | pf |
| 4 | apparent | pf | Success | power_factor = power ÷ apparent_power = 0.630252 | low_pf |
| 5 | pf | low_pf | True | power_factor = 0.630252 < 0.8 → yes | pf_limit |
| 6 | low_pf | pf_limit | **False** | last passed 120 s ago, limit 600 s | *(tidak ada panah False: selesai)* |

- Alarm "Over-voltage on Lab Power Meter: 238 V" muncul. Nilai alarmnya 238, karena node
  `volt_alarm` diberi key `voltage`.
- Faktor daya masih rendah, tetapi `pf_limit` menahannya: **tidak ada email kedua**. Tanpa
  node ini, setiap pesan selama faktor daya rendah akan mengirim email.
- **Log:** satu baris, berisi hasil `volt_alarm`.

Bila tegangan tetap 238 V pada pesan berikutnya, langkah 2 menjadi
`Alarm #1 is still active`. Tidak ada yang berubah, sehingga pesan itu tidak dicatat di log.

### Pesan 3, pukul 09:04:00 — `voltage=230, current=0, power=0` (beban dimatikan)

| # | Dari | Node | Hasil | Yang terjadi | Berikutnya |
|---|---|---|---|---|---|
| 1 | Input | over_voltage | False | voltage = 230 > 235 → no | volt_ok |
| 2 | over_voltage | volt_ok | Success | Cleared alarm #1 | *(selesai)* |
| 3 | Input | apparent | Success | apparent_power = voltage × current = 0 | pf |
| 4 | apparent | pf | **Failure** | division by zero | *(tidak ada panah Failure: selesai)* |

- Tegangan normal kembali, sehingga alarm di-clear oleh node, bukan oleh mesin keadaan
  seperti di ECA.
- Arus nol membuat pembagian tidak mungkin. Node `pf` keluar lewat **Failure**, dan karena
  tidak ada panah Failure, cabang itu berhenti dengan tenang. Bila ingin diberi tahu, tambahkan
  panah `pf —Failure→ …`.

### Apa yang tampil di halaman detail

- Diagram menandai node dan panah yang dilewati pesan terakhir. Setiap node menampilkan
  hasilnya, mis. `pf → Failure`.
- Tabel jejak sama persis dengan tabel di atas.
- Execution log berisi tiga baris (pesan 1, 2, 3). Setiap baris bisa dibuka lewat
  *Path through the chain* untuk melihat jejaknya.

## 9. Contoh lain (singkat)

Hidupkan aturan contoh lalu kirim pesannya dengan `send_telemetry`. Jejak di bawah adalah
hasil sebenarnya.

### 9.1 Pond pH watch (andi): Value changed dan OR

```
Input ─► ph_changed (≥ 0.1) ─True─► too_acid: ph < 6.5 ─True──► ph_alarm
                                        └─False─► too_alkaline: ph > 8.5 ─True──► ph_alarm
                                                                 └─False─► ph_ok (Clear alarm)
```

| Waktu | Pesan | Jalur | Ingatan `ph_changed` sesudahnya |
|---|---|---|---|
| 09:00 | ph=6.2 | ph_changed **True** (first value) → too_acid True → ph_alarm (*Major alarm #3*) | 6.2 |
| 09:01 | ph=6.25 | ph_changed **False** (*ph = 6.25, last passed 6.2 (needs 0.1)*) → selesai | 6.2 (tetap) |
| 09:02 | ph=7.4 | ph_changed True (*6.2 → 7.4*) → too_acid False → too_alkaline False → ph_ok (*Cleared alarm #3*) | 7.4 |
| 09:03 | ph=8.9 | ph_changed True → too_acid False → too_alkaline **True** → ph_alarm (*Major alarm #4*) | 8.9 |
| 09:04 | dissolved_oxygen=6 | ph_changed **Failure** (*the message has no 'ph'*) → selesai | 8.9 |

- `ph_alarm` punya dua panah masuk. Itulah bentuk **OR** di graf: "terlalu asam **atau**
  terlalu basa".
- Pesan 09:01 berhenti di node pertama, karena perubahan 0.05 terlalu kecil untuk dinilai
  ulang.

### 9.2 Cold room door check (rina): AND dengan filter berurutan

```
Input ─► humid: humidity > 85 ─True─► warm: temperature > 6 ─True─► buzz_limit (300 s) ─True─► buzzer (command)
```

| Waktu | Pesan | Jalur |
|---|---|---|
| 09:00:00 | temperature=7, humidity=80 | humid **False** → selesai |
| 09:00:30 | temperature=7, humidity=90 | humid True → warm True → buzz_limit True (*first message*) → buzzer (*Cold Storage Unit: buzzer (command #1)*) |
| 09:01:30 | temperature=7, humidity=90 | humid True → warm True → buzz_limit **False** (*last passed 60 s ago, limit 300 s*) |

Kedua filter harus True berturut-turut. Itulah bentuk **AND**. Aturan ECA "Cold chain breach"
juga bereaksi pada pesan yang sama dengan mengirim command buzzer 10 detik: dua model berjalan
berdampingan.

### 9.3 Greenhouse watering chain (siti): enrichment, data basi, dan dua cabang

Aturan ini nonaktif bawaan karena memakai pompa yang sama dengan ECA "Smart irrigation".
Untuk mencobanya, **nonaktifkan Smart irrigation dulu**, lalu aktifkan aturan ini.

```
Input ─► dry: soil_moisture < 25 ─True─► rain_now: Add latest value (Weather Station · rain, max 900 s)
          │                                 ├─Success─► no_rain: rain < 0.5 ─True─► pump_on ─► mail_limit (3600 s) ─True─► notify (email)
          │                                 │                               └─False─► pump_off
          │                                 ├─Success─► rain_back (Clear alarm)
          │                                 └─Failure─► stale_rain (Create alarm, WARNING)
          └─False─► wet: soil_moisture > 45 ─True─► pump_off
```

| Waktu | Pesan dari | Jalur di rule chain |
|---|---|---|
| 09:00:00 | Greenhouse Node, soil=20 | dry True → rain_now **Failure** (*stale (86395 s old, limit 900 s)*) → stale_rain (*Warning alarm #2*) |
| 09:00:30 | Weather Station, rain=0 | — (aturan ini hanya mendengarkan Greenhouse Node) |
| 09:01:00 | Greenhouse Node, soil=20 | dry True → rain_now Success (*rain = 0 … (30 s old)*) → **cabang 1:** no_rain True → pump_on (*Water pump = ON*) → mail_limit True → notify (email); **cabang 2:** rain_back (*Cleared alarm #2*) |
| 09:01:10 | Greenhouse Node, soil=19 | jalur sama, tetapi pump_on *already requested*, mail_limit False, rain_back *No active alarm* → tidak ada log |
| 09:01:20 | Weather Station, rain=3 | — |
| 09:01:30 | Greenhouse Node, soil=19 | dry True → rain_now (*rain = 3, 10 s old*) → no_rain **False** → pump_off (*Water pump = OFF*); rain_back |
| 09:03:00 | Greenhouse Node, soil=50 | dry False → wet True → pump_off (*already requested*) |

Hal yang diperlihatkan contoh ini:

- **Enrichment lintas device.** Nilai hujan diambil dari database milik device lain. Pesan dari
  stasiun cuaca sendiri tidak memicu aturan, jadi hujan pada 09:01:20 baru "terlihat" saat
  Greenhouse Node mengirim data pada 09:01:30. Bandingkan dengan ECA "Smart irrigation", yang
  dinilai juga saat stasiun cuaca mengirim data, karena variabel hujan ada di kondisinya.
- **Data basi diarahkan, bukan didiamkan.** Panah Failure membuat alarm WARNING yang
  menjelaskan mengapa penyiraman dilewati. Pada ECA, data basi hanya menjadi *unknown*.
- **Satu hasil, dua cabang.** `rain_now` punya dua panah Success. Kedua cabang menerima
  salinan pesan yang sama (sudah berisi `rain`), lalu berjalan sendiri-sendiri.
- **Fan-in.** `pump_off` dicapai dari `no_rain —False→` dan `wet —True→`.

## 10. AND, OR, dan pola umum dalam graf

| Kebutuhan | Bentuk graf |
|---|---|
| A AND B | `A —True→ B —True→ aksi` |
| A OR B | `A —True→ aksi` dan `B —True→ aksi` (aksi bisa berjalan dua kali; pakai aksi idempoten atau letakkan Rate limit di depannya) |
| A OR B, aksi tepat sekali | `A —True→ aksi`, `A —False→ B —True→ aksi` (B hanya diperiksa bila A salah, seperti *too_acid / too_alkaline*) |
| NOT A | pakai panah `False` |
| if / else | panah `True` ke satu aksi, `False` ke aksi lain (aktif / clear) |
| "kalau data tidak ada" | panah `Failure` |
| hitung dulu, baru cek | `Calculate —Success→ Check value` |
| butuh nilai device lain | `Add latest value —Success→ …`, dengan `Failure` untuk data basi |
| jangan ulangi aksi | `Value changed` (bila nilainya tidak berubah berarti) atau `Rate limit` (paling banyak sekali tiap N detik) |
| histeresis (aktif < 20, mati > 30) | `x < 20 —True→ ON`, `x < 20 —False→ x > 30 —True→ OFF`; di antara 20 dan 30 tidak ada aksi |

Yang **tidak** bisa dibuat dengan rule chain di tahap ini:

- **"Selama 60 detik."** Butuh pemeriksaan berkala (`tick`) dan keadaan per aturan. Pakai
  ECA dengan *for at least*, atau model CEP nanti.
- **"Device diam 15 menit."** Tidak ada pesan, jadi tidak ada yang masuk ke Input. Pakai
  ECA dengan event *Device sends no data*.

## 11. Jaminan, batasan, dan cara menelusuri masalah

### 11.1 Jaminan

- Setiap pesan dari device yang didengarkan menelusuri graf **tepat sekali** per aturan.
- Cabang tidak saling memengaruhi, karena masing-masing membawa salinan pesan.
- Error di satu node tidak menghentikan penelusuran cabang lain, aturan lain, maupun worker.
- Graf yang disimpan lewat form tidak berputar. Batas 50 langkah melindungi graf yang diubah
  dari luar form.
- Aksi hanya menyentuh device yang boleh dilihat pemilik aturan. Ini diperiksa ulang setiap
  kali aksi berjalan.
- Aksi set aktuator dan create alarm idempoten: pesan berulang tidak menimbulkan perubahan
  berulang.

### 11.2 Batasan (disengaja)

- Tidak ada node skrip. Rumus dibatasi satu operasi per node *Calculate*.
- Tidak ada pemicu waktu (lihat bagian 10).
- Satu rule chain tidak bisa memanggil rule chain lain. Di ThingsBoard ada node "Rule Chain"
  untuk itu.
- Ingatan node dikosongkan setiap kali aturan disimpan.
- Execution log hanya mencatat pesan yang mengubah sesuatu. Jejak **semua** pesan tidak
  disimpan; yang ada hanya jejak pesan terakhir di kotak diagram.

### 11.3 Menelusuri "mengapa aturan saya tidak jalan?"

1. **Apakah ada pesan yang masuk?**
   - Lihat "from … · N s ago" di kotak diagram.
   - Bila kosong: aturan nonaktif, device di Input bukan device yang mengirim data, atau
     worker tidak berjalan (`python manage.py rule_engine`, atau `runall`).
2. **Di node mana pesan berhenti?**
   - Baca tabel jejak. Baris terakhir sebuah cabang menunjukkan hasil node dan
     "end of branch".
   - Sering kali hasilnya ada tetapi tidak ada panah berlabel itu, misalnya `Failure` atau
     `False`.
3. **Failure "the message has no 'x'".**
   - Device tidak mengirim key itu di pesan ini. Ini wajar bila device mengirim key yang
     berbeda-beda per pesan.
   - Periksa juga ejaan key (kolom `data` di jejak memperlihatkan key yang benar-benar ada).
4. **Failure "stale".** Device sumber *Add latest value* sudah lama tidak mengirim data, atau
   batas detiknya terlalu kecil.
5. **Aksi jalan tetapi tidak ada di log.** Hasilnya *already requested*, *still active*, atau
   *No active alarm*: tidak ada yang berubah (lihat bagian 6).
6. **Email terlalu banyak.** Tambahkan *Rate limit* tepat sebelum node email.

## 12. Latihan

1. Gambarkan isi tumpukan langkah demi langkah untuk pesan 09:01:00 pada Greenhouse
   watering chain. Mengapa `rain_back` dijalankan **setelah** `notify`, bukan sebelumnya?
2. Pada Lab power monitor, ubah urutan dua panah dari Input (cabang `apparent` lebih dulu).
   Apakah ada hasil yang berubah, selain urutan baris jejak? Jelaskan dengan konsep salinan
   pesan.
3. Buat rule chain yang menghitung titik embun sederhana `dew = temperature − (100 − humidity) / 5`
   (rumus pendekatan, berlaku kira-kira untuk RH > 50 %). Berapa node *Calculate* yang
   dibutuhkan? Kirim temperature=30, humidity=80 dan periksa hasilnya di jejak.
4. Pada contoh Pond pH watch, ambang *Value changed* 0.1. Kirim ph 7.00, 7.05, 7.09, 7.12.
   Pesan mana yang lolos? Bagaimana bila ingatan diperbarui pada **setiap** pesan, bukan hanya
   saat lolos?
5. Rancang rule chain yang mengirim email "Greenhouse Node offline?" bila
   *Add latest value* atas variabel Greenhouse Node lebih tua dari 10 menit, dipicu oleh pesan
   dari Weather Station. Bandingkan dengan event *Device sends no data* di ECA: kapan masing-
   masing tidak bekerja?
