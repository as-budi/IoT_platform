# ECA di IoT Platform: Mekanisme Eksekusi dan Pengecekan Data

Dokumen ini menjelaskan cara kerja aturan **Event–Condition–Action (ECA)**:

- bentuk aturannya,
- perjalanan satu data sensor sampai aksi dijalankan,
- query database yang dipakai rule engine,
- contoh lengkap langkah demi langkah.

Pengenalan rule engine ada di [rule-engine-iot.md](rule-engine-iot.md); ringkasan tahap 7 ada
di [tahap-07-rule-engine-eca.md](tahap-07-rule-engine-eca.md).

Daftar isi:

1. [Bentuk aturan ECA](#1-bentuk-aturan-eca)
2. [Ekspresi kondisi: OR berisi AND](#2-ekspresi-kondisi-or-berisi-and)
3. [Logika tiga nilai dan data basi](#3-logika-tiga-nilai-dan-data-basi)
4. [Tabel database sebuah aturan](#4-tabel-database-sebuah-aturan)
5. [Alur eksekusi dari data masuk sampai aksi](#5-alur-eksekusi-dari-data-masuk-sampai-aksi)
6. [Pengecekan data di database](#6-pengecekan-data-di-database)
7. [Mesin keadaan: IDLE, PENDING, ACTIVE](#7-mesin-keadaan-idle-pending-active)
8. [Contoh lengkap: linimasa sebuah aturan](#8-contoh-lengkap-linimasa-sebuah-aturan)
9. [Jaminan, batasan, dan cara menelusuri masalah](#9-jaminan-batasan-dan-cara-menelusuri-masalah)
10. [Latihan](#10-latihan)

---

## 1. Bentuk aturan ECA

Sebuah aturan ECA menjawab tiga pertanyaan:

| Bagian | Pertanyaan | Di project ini |
|---|---|---|
| **E**vent | *Kapan* aturan diperiksa? | data baru untuk salah satu variabel di kondisi, atau device tidak mengirim data |
| **C**ondition | *Apakah* keadaan sekarang memenuhi syarat? | ekspresi AND/OR atas nilai terkini beberapa variabel |
| **A**ction | *Apa* yang dilakukan? | set aktuator, kirim command, alarm, email |

Ditambah empat pengaman agar aturan berperilaku baik di dunia nyata:

| Pengaman | Field | Gunanya |
|---|---|---|
| Durasi | `duration_seconds` | kondisi harus bertahan sekian detik dulu (menyaring lonjakan sesaat) |
| Kondisi clear | kondisi ber-role `CLEAR` | histeresis: aktif bila < 20, pulih baru bila > 30 |
| Cooldown | `cooldown_seconds` | jeda minimum antara dua aktivasi |
| Batas umur data | `max_age_seconds` | nilai yang lebih tua dari ini dianggap *tidak diketahui* |

Contoh yang dipakai di seluruh dokumen ini:

```
Aturan "Irrigation"  (pemilik: siti)

WHEN   data baru untuk Soil moisture atau Temperature (device Greenhouse Node)
IF     (Soil moisture < 20 AND Temperature > 30)  OR  (Soil moisture < 10)
       selama 60 detik
THEN   Water pump = ON, alarm Major "{device}: {values}"
CLEAR  bila Soil moisture > 30
THEN   Water pump = OFF   (alarm di-clear otomatis)

Cooldown 0 detik · nilai lebih tua dari 300 detik = tidak diketahui
```

Maksudnya: siram tanaman bila tanah agak kering **dan** udara panas, atau bila tanah sudah
sangat kering apa pun suhunya. Berhenti bila tanah sudah basah.

---

## 2. Ekspresi kondisi: OR berisi AND

### 2.1 Bentuknya

Kondisi disusun sebagai **kotak-kotak (grup)**:

- di dalam satu kotak, semua kondisi digabung dengan **AND**;
- antar-kotak digabung dengan **OR**.

```
┌ Grup 1 ───────────────────────────┐
│ Soil moisture < 20                 │
│ AND Temperature > 30               │      (Soil < 20 AND Temp > 30)
└────────────────────────────────────┘
                 OR                            OR
┌ Grup 2 ───────────────────────────┐
│ Soil moisture < 10                 │      (Soil < 10)
└────────────────────────────────────┘
```

Dalam logika matematika bentuk ini disebut **Disjunctive Normal Form (DNF)**. Kelebihannya:

- form cukup dua tingkat (grup dan baris), tanpa tanda kurung bersarang;
- **setiap** ekspresi AND/OR bisa diubah ke bentuk ini.

### 2.2 Mengubah ekspresi lain ke bentuk ini

Gunakan sifat distributif `(A OR B) AND C = (A AND C) OR (B AND C)`:

| Yang diinginkan | Ditulis sebagai grup |
|---|---|
| `A AND B AND C` | Grup 1: A, B, C |
| `A OR B OR C` | Grup 1: A · Grup 2: B · Grup 3: C |
| `(A OR B) AND C` | Grup 1: A, C · Grup 2: B, C |
| `(A OR B) AND (C OR D)` | Grup 1: A, C · Grup 2: A, D · Grup 3: B, C · Grup 4: B, D |

**NOT** tidak diperlukan karena setiap operator punya lawannya: `NOT (x < 20)` sama dengan
`x ≥ 20`, dan `NOT (x = 1)` sama dengan `x ≠ 1`.

### 2.3 Di database

Setiap baris kondisi adalah satu record `EcaCondition`:

| Kolom | Arti |
|---|---|
| `spec_id` | aturan pemiliknya (`EcaSpec`) |
| `role` | `WHEN` (kondisi aktif) atau `CLEAR` (kondisi pulih) |
| `group` | nomor grup; nomor sama = AND, nomor beda = OR |
| `order` | urutan di dalam grup (hanya untuk tampilan) |
| `variable_id`, `operator`, `threshold` | perbandingannya, mis. variabel 7 `<` 20 |

Saat form disimpan, nomor grup dirapikan menjadi 1, 2, 3, … (`save_conditions()` di
`rules/views.py`). Halaman bisa saja mengirim nomor 3 dan 7 setelah ada grup yang dihapus.

---

## 3. Logika tiga nilai dan data basi

### 3.1 Masalahnya

Aturan contoh dinilai ketika Soil moisture mengirim data. Nilai Temperature saat itu
**tidak ada di pesan**, jadi diambil dari data terakhir di database. Bagaimana kalau sensor
suhu sudah dua jam mati? Nilai 32 °C dari dua jam lalu bukan gambaran keadaan sekarang.

Memperlakukannya sebagai "benar" bisa menyalakan pompa secara keliru. Memperlakukannya
sebagai "salah" juga keliru: bila aturan memakai kondisi clear bawaan ("pulih bila kondisi
salah"), pompa malah dimatikan karena sensor suhu rusak. Jawaban yang jujur adalah
**tidak diketahui**.

### 3.2 Aturan penggabungan (logika Kleene)

Setiap kondisi bernilai **benar (T)**, **salah (F)**, atau **tidak diketahui (U)**.
Sebuah kondisi bernilai U bila variabelnya belum pernah punya data (*no data*) atau nilai
terakhirnya lebih tua dari `max_age_seconds` (*stale*).

| A | B | A AND B | A OR B |
|---|---|---|---|
| T | T | T | T |
| T | F | F | T |
| T | U | **U** | T |
| F | F | F | F |
| F | U | F | **U** |
| U | U | U | U |

Mudah diingat:

- **AND** pasti salah bila ada satu yang salah; kalau tidak ada yang salah tetapi ada U, hasilnya U.
- **OR** pasti benar bila ada satu yang benar; kalau tidak ada yang benar tetapi ada U, hasilnya U.

Kodenya ada di `all_of()` dan `any_of()` (`rules/conditions.py`).

### 3.3 Dari hasil ekspresi ke keputusan

| | Tanpa kondisi clear | Dengan kondisi clear |
|---|---|---|
| **Boleh aktif** bila | ekspresi aktif = T | ekspresi aktif = T **dan** ekspresi clear ≠ T |
| **Boleh pulih** bila | ekspresi aktif = **F** | ekspresi clear = T |

Hasil U tidak pernah mengaktifkan dan tidak pernah memulihkan aturan: aturan **bertahan**
di keadaannya. Syarat tambahan "clear ≠ T" mencegah aturan aktif lalu langsung pulih bila
kedua ekspresi kebetulan benar bersamaan.

### 3.4 Contoh kecil

Data terakhir: Soil = 15 (baru saja), Temperature = 32 (umurnya 10 menit), batas umur 300 detik.

| Kondisi | Nilai | Umur | Hasil |
|---|---|---|---|
| Soil < 20 | 15 | 0 s | T |
| Temp > 30 | 32 | 600 s | **U** (stale) |
| Grup 1 = T AND U | | | **U** |
| Soil < 10 (grup 2) | 15 | 0 s | F |
| Ekspresi = U OR F | | | **U** → pompa tidak dinyalakan |

Bila Soil turun menjadi 8, grup 2 bernilai T. Ekspresinya menjadi `U OR T = T`, dan aturan
aktif walaupun suhu tidak diketahui. Ini memang yang diinginkan: tanah sangat kering harus
disiram apa pun suhunya.

---

## 4. Tabel database sebuah aturan

Aturan contoh tersimpan sebagai baris-baris berikut (id hanya ilustrasi).

`rules_rule`

| id | owner | name | model | is_enabled |
|---|---|---|---|---|
| 1 | siti | Irrigation | ECA | 1 |

`rules_ecaspec` (satu baris per aturan ECA)

| rule_id | event | duration_seconds | cooldown_seconds | max_age_seconds | device_id | silence_seconds |
|---|---|---|---|---|---|---|
| 1 | TELEMETRY | 60 | 0 | 300 | NULL | NULL |

`rules_ecacondition`

| id | spec_id | role | group | order | variable_id | operator | threshold |
|---|---|---|---|---|---|---|---|
| 1 | 1 | WHEN | 1 | 0 | 7 (soil_moisture) | `<` | 20 |
| 2 | 1 | WHEN | 1 | 1 | 9 (temperature) | `>` | 30 |
| 3 | 1 | WHEN | 2 | 0 | 7 | `<` | 10 |
| 4 | 1 | CLEAR | 1 | 0 | 7 | `>` | 30 |

`rules_ruleaction`

| id | rule_id | kind | run_on | target_variable_id | value | severity | message |
|---|---|---|---|---|---|---|---|
| 1 | 1 | SET_ACTUATOR | ACTIVATE | 8 (pump) | 1 | | |
| 2 | 1 | ALARM | ACTIVATE | | | MAJOR | `{device}: {values}` |
| 3 | 1 | SET_ACTUATOR | CLEAR | 8 | 0 | | |

`rules_rulestate`: **ingatan** aturan, satu baris per aturan, terus diperbarui.

| rule_id | status | pending_since | last_value | last_evaluated_at | last_activated_at | snapshot |
|---|---|---|---|---|---|---|
| 1 | PENDING | 10:00:10 | 15 | 10:00:40 | NULL | `{...}` (lihat 6.4) |

`rules_ruleexecution`: **riwayat**, satu baris setiap kali aturan aktif atau pulih.

| id | rule_id | transition | value | snapshot | results | ok |
|---|---|---|---|---|---|---|
| 31 | 1 | ACTIVATE | 15 | `{"Greenhouse Node · Soil moisture": 15, "Greenhouse Node · Temperature": 32}` | `[{"action": "Set actuator", "ok": true, "detail": "Water pump = ON"}, …]` | 1 |

`rules_alarm`: dibuat oleh aksi ALARM.

| id | rule_id | device_id | severity | message | cleared_at | acknowledged_at |
|---|---|---|---|---|---|---|
| 12 | 1 | 3 | MAJOR | Greenhouse Node: Greenhouse Node · Soil moisture = 15, Greenhouse Node · Temperature = 32 | NULL | NULL |

---

## 5. Alur eksekusi dari data masuk sampai aksi

```
 ESP32 ──MQTT──► mqtt_bridge ─┐
 ESP32 ──HTTP──► runserver  ──┼─► ① save_telemetry() ─► MySQL (INSERT telemetry)
 ESP32 ──CoAP──► coap_server ─┘          │ ② setelah COMMIT
                                         ▼
                              Redis list "rule-engine:messages"   (RPUSH)
                                         │ ③ BLPOP
                                         ▼
                       worker  manage.py rule_engine
                         ④ pilih aturan kandidat                    (query 1)
                         ⑤ ambil nilai terakhir variabel lain       (query 2)
                         ⑥ nilai ekspresi (tiga nilai)              (tanpa DB)
                         ⑦ kunci RuleState, langkah mesin keadaan   (SELECT … FOR UPDATE)
                         ⑧ bila berpindah: jalankan aksi            (desired / command / alarm / email)
                         ⑨ catat RuleExecution                      (INSERT)
                         ⑩ kabari browser lewat WebSocket           (Redis channel layer)
                       + setiap 1 detik: tick() untuk durasi dan "device diam"
```

**① Simpan data** (`api/services.save_telemetry`). Baris telemetri disimpan dalam satu
transaksi. Kolom `last_seen_at` device ikut diperbarui.

**② Bentuk pesan dan antrekan** (`rules/queue.publish`). Pesan dikirim **setelah COMMIT**
(`transaction.on_commit`), sehingga worker tidak pernah membaca data yang ternyata batal
disimpan.

```json
{"type": "TELEMETRY",
 "originator": {"device": 3},
 "data": [{"variable": 7, "key": "soil_moisture", "value": 15.0, "ts": "2026-10-12T03:00:10+00:00"}],
 "metadata": {"protocol": "MQTT", "received_at": "2026-10-12T03:00:10.120+00:00"}}
```

Tanpa Redis (`RULE_ENGINE_MODE=inline`), langkah ③–⑩ langsung dijalankan di proses yang
menyimpan data, juga setelah COMMIT.

**③ Ambil pesan** (`rules/management/commands/rule_engine.py`). Worker menunggu dengan
`BLPOP` paling lama 1 detik, lalu memanggil `queue.process(message)`. Pesan itu diteruskan
ke setiap model rule engine yang sudah diimplementasikan; saat ini hanya ECA
(`EcaEngine.on_message`).

**④ Pilih aturan kandidat.** Hanya aturan yang kondisinya (WHEN **atau** CLEAR) menyebut
salah satu variabel di pesan, plus aturan "device diam" milik device pengirim. Aturan lain
tidak disentuh sama sekali (query di 6.1).

**⑤ Siapkan nilai** (`latest_values()` di `rules/engines/eca.py`):

- variabel yang **ada di pesan** memakai nilai pesan (`source = "message"`);
- variabel lain memakai baris telemetri **terbaru** di database (`source = "database"`),
  diambil dengan satu query untuk semua variabel itu (6.2).

**⑥ Nilai ekspresi** (`conditions.evaluate()`): setiap kondisi bernilai T/F/U (bagian 3),
digabung per grup dengan AND, lalu antar-grup dengan OR. Untuk ekspresi clear juga dilakukan
hal yang sama. Hasilnya menjadi dua keputusan, *boleh aktif* dan *boleh pulih* (3.3). Rincian
lengkapnya disimpan sebagai `snapshot`.

**⑦ Langkah mesin keadaan** (`EcaEngine.evaluate()` dan `step()`). Baris `RuleState` dikunci
(`SELECT … FOR UPDATE`) agar dua proses tidak menilai aturan yang sama bersamaan. Lalu
`step()` menentukan perpindahan (bagian 7), dan status, `snapshot`, serta waktu disimpan.

**⑧ Jalankan aksi** (`rules/actions.run_actions`). Langkah ini **hanya** terjadi bila status
berpindah ke ACTIVE (aksi *when the rule activates*) atau dari ACTIVE ke IDLE (aksi *when
the rule clears*, ditambah clear otomatis alarm aturan ini). Setiap aksi berdiri sendiri: satu
aksi gagal tidak menghentikan aksi lain. Sebelum aksi dijalankan, hak akses diperiksa ulang:
target harus device yang boleh dilihat pemilik aturan.

**⑨ Catat riwayat.** Satu baris `RuleExecution` berisi transisi, nilai pemicu, snapshot
nilai semua variabel, dan hasil setiap aksi.

**⑩ Kabari browser** (`rules/events.py`):

| Kejadian | Kabar WebSocket | Reaksi halaman detail aturan |
|---|---|---|
| status berubah (IDLE→PENDING, →ACTIVE, →IDLE) | `{"type": "rule", ...}` | halaman dimuat ulang |
| dinilai ulang, status tetap | `{"type": "rule_eval", ...}` | kotak *Last evaluation* diambil ulang |
| alarm berubah | `{"type": "alarm", ...}` | halaman dimuat ulang |

**Tick** (`EcaEngine.tick()`, setiap `RULE_ENGINE_TICK` detik, tanpa pesan):

- Aturan **PENDING** dinilai ulang dengan data terakhir di database: apakah durasi sudah
  terpenuhi dan kondisinya masih benar? Bila nilai lain sudah basi selama menunggu,
  aturan kembali ke IDLE.
- Aturan **device diam** dihitung dari `Device.last_seen_at`.

---

## 6. Pengecekan data di database

Query di bawah ini ditulis dalam SQL MySQL yang disederhanakan (Django menyusunnya dari
ORM). Untuk satu pesan, rule engine menjalankan kira-kira empat query baca ditambah satu
transaksi tulis untuk setiap aturan kandidat.

### 6.1 Memilih aturan kandidat

ORM (`EcaEngine.on_message`):

```python
self.rules().filter(eca__event="TELEMETRY",
                    eca__conditions__variable_id__in=[7]).distinct()
```

SQL:

```sql
SELECT DISTINCT r.*, s.*
FROM rules_rule r
JOIN rules_ecaspec      s ON s.rule_id = r.id
JOIN rules_ecacondition c ON c.spec_id = s.rule_id
WHERE r.model = 'ECA' AND r.is_enabled = 1
  AND s.event = 'TELEMETRY'
  AND c.variable_id IN (7);
```

- `DISTINCT` diperlukan karena satu aturan bisa punya beberapa kondisi untuk variabel yang
  sama. Tanpanya, aturan bisa dinilai dua kali.
- Kolom `variable_id` adalah foreign key, jadi sudah ber-index. Database langsung melompat ke
  kondisi yang relevan tanpa membaca semua aturan.

Lalu Django mengambil semua kondisi aturan kandidat sekaligus (`prefetch_related`):

```sql
SELECT * FROM rules_ecacondition WHERE spec_id IN (1, 4)
ORDER BY role, `group`, `order`, id;
```

### 6.2 Mengambil nilai terakhir variabel lain

Untuk aturan contoh, pesan hanya berisi Soil moisture (id 7). Temperature (id 9) diambil dari
database:

```python
newest = Telemetry.objects.filter(variable=OuterRef("pk")).order_by("-ts")
Variable.objects.filter(pk__in=[9]).annotate(
    last_value=Subquery(newest.values("value")[:1]),
    last_ts=Subquery(newest.values("ts")[:1]),
)
```

```sql
SELECT v.id,
       (SELECT t.value FROM devices_telemetry t
         WHERE t.variable_id = v.id ORDER BY t.ts DESC LIMIT 1) AS last_value,
       (SELECT t.ts    FROM devices_telemetry t
         WHERE t.variable_id = v.id ORDER BY t.ts DESC LIMIT 1) AS last_ts
FROM devices_variable v
WHERE v.id IN (9);
```

Hasilnya, misalnya:

| id | last_value | last_ts |
|---|---|---|
| 9 | 32.0 | 2026-10-12 02:59:40 |

**Mengapa cepat walau tabel telemetri berisi jutaan baris?** Tabel `devices_telemetry` punya
index gabungan `telemetry_var_ts (variable_id, ts)`. Untuk `WHERE variable_id = 9 ORDER BY
ts DESC LIMIT 1`, MySQL cukup melompat ke ujung bagian index milik variabel 9 dan membaca satu
entri. Biayanya hampir sama, berapa pun jumlah barisnya. Perintah berikut memperlihatkannya:

```sql
EXPLAIN SELECT value, ts FROM devices_telemetry
WHERE variable_id = 9 ORDER BY ts DESC LIMIT 1;
-- key: telemetry_var_ts   rows: 1   Extra: Backward index scan
```

**Mengapa nilai dari pesan dipakai, bukan dari database?** Dalam mode worker, pesan diproses
sedikit setelah disimpan. Saat itu device mungkin sudah mengirim nilai yang lebih baru. Setiap
pesan dinilai dengan nilainya **sendiri** supaya urutan kejadian tetap benar: pesan 15 dinilai
sebagai 15, bukan sebagai nilai berikutnya.

### 6.3 Memeriksa umur data

Umur dihitung di Python, bukan di SQL:

```
umur = waktu proses sekarang − last_ts
umur > max_age_seconds  →  kondisi = tidak diketahui ("stale (600 s old)")
tidak ada baris sama sekali →  kondisi = tidak diketahui ("no data")
```

Untuk nilai dari pesan, `last_ts` adalah `ts` yang dikirim device. Data lama yang baru
terkirim (misalnya hasil buffer saat Wi-Fi putus) bisa langsung dianggap basi. Ini disengaja:
keputusan menyalakan pompa sebaiknya tidak didasarkan pada data satu jam yang lalu.

### 6.4 Menyimpan keadaan (transaksi tulis)

```sql
BEGIN;
SELECT * FROM rules_rulestate WHERE rule_id = 1 FOR UPDATE;   -- kunci baris
UPDATE rules_rulestate
   SET status = 'PENDING', pending_since = '…', last_value = 15,
       last_evaluated_at = '…', snapshot = '{…}'
 WHERE rule_id = 1;
COMMIT;
```

Isi `snapshot` (dipakai kotak *Last evaluation* di halaman detail):

```json
{"at": "2026-10-12T03:00:10+00:00",
 "when": {"result": true, "groups": [
    {"group": 1, "result": true, "conditions": [
       {"label": "Soil moisture", "operator": "<", "threshold": 20, "value": 15.0,
        "age": 0,  "source": "message",  "result": true,  "reason": ""},
       {"label": "Temperature",   "operator": ">", "threshold": 30, "value": 32.0,
        "age": 30, "source": "database", "result": true,  "reason": ""}]},
    {"group": 2, "result": false, "conditions": [
       {"label": "Soil moisture", "operator": "<", "threshold": 10, "value": 15.0,
        "age": 0,  "source": "message",  "result": false, "reason": ""}]}]},
 "clear": {"result": false, "groups": [ … Soil moisture > 30 … ]},
 "values": {"Greenhouse Node · Soil moisture": 15.0, "Greenhouse Node · Temperature": 32.0}}
```

Bila status berpindah, aksi dijalankan **setelah** COMMIT ini, lalu riwayatnya ditambahkan:

```sql
INSERT INTO rules_ruleexecution (rule_id, transition, value, snapshot, results, ok, created_at)
VALUES (1, 'ACTIVATE', 15, '{"…Soil moisture": 15, "…Temperature": 32}', '[…]', 1, '…');
```

### 6.5 Query milik aksi

| Aksi | Pemeriksaan & penulisan |
|---|---|
| Set actuator | baca `desired_value` terbaru; bila sudah sama, **dilewati** (idempoten). Bila beda, `set_desired()`: `SELECT … FOR UPDATE` variabel, `UPDATE desired_*`, `INSERT` ke log desired state |
| Send command | `INSERT` ke `devices_command` (status QUEUED, dengan batas waktu) |
| Raise alarm | cari alarm aktif aturan ini untuk device itu; bila sudah ada, tidak membuat yang baru. Bila belum, `INSERT` ke `rules_alarm` |
| Send email | tidak menyentuh database; dikirim lewat SMTP (atau dicetak di terminal) |
| (saat CLEAR) | `UPDATE rules_alarm SET cleared_at = …` untuk alarm aktif aturan ini |

---

## 7. Mesin keadaan: IDLE, PENDING, ACTIVE

```
                boleh aktif                      durasi DAN cooldown terpenuhi
     IDLE ─────────────────────► PENDING ─────────────────────────────────► ACTIVE
      ▲  ▲                          │                                          │
      │  └── tidak boleh aktif ─────┘   (termasuk bila hasilnya "tidak diketahui")│
      └──────────────────────────── boleh pulih ◄──────────────────────────────┘
                                   (hasil "tidak diketahui" = tetap ACTIVE)
```

Fungsi `step()` di `rules/engines/eca.py` mengatur perpindahan ini:

- Di **ACTIVE**: pulih bila *boleh pulih*. Selain itu tetap ACTIVE, tanpa aksi.
- Di **IDLE/PENDING**: bila *tidak boleh aktif*, kembali ke IDLE. Bila boleh:
  - dari IDLE, catat `pending_since = sekarang`;
  - aktif bila `sekarang ≥ pending_since + durasi` **dan**
    `sekarang ≥ last_activated_at + cooldown`.

Aksi dijalankan **hanya pada perpindahan**, tidak pada setiap pesan. Akibatnya:

- pompa tidak dinyalakan ulang setiap 10 detik selama tanah kering;
- email tidak membanjiri kotak masuk;
- tidak terjadi loop. Aksi "pompa ON" membuat device melapor `pump = 1`. Laporan itu memicu
  penilaian aturan yang menyebut `pump`, tetapi aturan yang sudah ACTIVE tidak menjalankan
  aksi lagi.

---

## 8. Contoh lengkap: linimasa sebuah aturan

Aturan "Irrigation" dari bagian 1: durasi 60 s, batas umur 300 s, clear bila Soil > 30.
Notasi: **S** = Soil moisture, **T** = Temperature; G1 = `S<20 AND T>30`, G2 = `S<10`;
Akt = ekspresi aktif; Clr = ekspresi clear `S>30`.

| Waktu | Kejadian | S (sumber, umur) | T (sumber, umur) | G1 | G2 | Akt | Clr | Status | Aksi |
|---|---|---|---|---|---|---|---|---|---|
| 10:00:00 | T = 28 | — (no data) | 28 (pesan, 0) | F | U | **U** | U | IDLE | – |
| 10:00:05 | S = 25 | 25 (pesan, 0) | 28 (DB, 5) | F | F | F | F | IDLE | – |
| 10:00:40 | T = 32 | 25 (DB, 35) | 32 (pesan, 0) | F | F | F | F | IDLE | – |
| 10:01:00 | S = 15 | 15 (pesan, 0) | 32 (DB, 20) | **T** | F | **T** | F | **PENDING** (sejak 10:01:00) | – |
| 10:01:30 | tick | 15 (DB, 30) | 32 (DB, 50) | T | F | T | F | PENDING | – (baru 30 s) |
| 10:02:00 | tick | 15 (DB, 60) | 32 (DB, 80) | T | F | T | F | **ACTIVE** | pompa ON, alarm Major |
| 10:02:10 | pump = 1 (laporan device) | — | — | — | — | — | — | ACTIVE | – (aturan tidak menyebut `pump`, bukan kandidat) |
| 10:05:00 | T = 27 | 15 (DB, 240) | 27 (pesan, 0) | F | F | F | F | ACTIVE | – (dengan clear, F saja **tidak** memulihkan) |
| 10:07:30 | S = 22 | 22 (pesan, 0) | 27 (DB, 150) | F | F | F | F | ACTIVE | – (pita histeresis 20–30) |
| 10:15:00 | S = 31 | 31 (pesan, 0) | 27 (DB, 600) | F | F | F | **T** | **IDLE** | pompa OFF, alarm di-clear |

Catatan per baris:

- **10:00:00**: Soil belum pernah mengirim data, sehingga semua kondisi Soil bernilai U.
  G1 tetap F karena `T > 30` salah (`U AND F = F`), tetapi G2 = U, sehingga ekspresinya
  `F OR U = U`. Hasil U tidak mengaktifkan apa pun.
- **10:01:00**: G1 benar, tetapi durasi 60 s belum terpenuhi, jadi status menjadi PENDING.
  Browser menerima kabar `rule` dengan status PENDING.
- **10:02:00**: tick menilai ulang dengan data di database. Kedua nilai masih di bawah 300 s,
  G1 masih benar, dan 60 s sudah lewat, sehingga aturan menjadi ACTIVE.
  - Aksi pompa: desired `pump` berubah menjadi 1, lalu `mqtt_bridge` mengirimkannya ke device.
  - Aksi alarm: alarm dibuat dengan pesan
    `Greenhouse Node: Greenhouse Node · Soil moisture = 15, Greenhouse Node · Temperature = 32`.
- **10:05:00**: ekspresi aktif kini salah. Tanpa kondisi clear, aturan akan pulih di sini.
  Karena ada kondisi clear `S > 30`, aturan tetap ACTIVE sampai tanah benar-benar basah.
- **10:15:00**: nilai T sudah 600 s (basi), tetapi tidak berpengaruh karena ekspresi clear
  hanya memakai S. Aturan pulih: pompa diminta OFF dan alarm di-clear (`cleared_by` kosong =
  oleh aturan).

**Skenario lain dengan aturan yang sama:**

| Situasi | Hasil | Mengapa |
|---|---|---|
| Sensor suhu mati sejak 09:00, S = 15 pada 10:00 | IDLE, tidak aktif | T basi → G1 = U; G2 = F; Akt = U |
| Sensor suhu mati, S = 8 | aktif (setelah 60 s) | G2 = T; `U OR T = T` |
| ACTIVE, lalu sensor tanah mati selama 1 jam | tetap ACTIVE | Clr = U, bukan T → tidak pulih (pompa tidak dimatikan berdasarkan data yang tidak ada) |
| PENDING, lalu S naik ke 21 sebelum 60 s | kembali IDLE | Akt = F; tidak ada aksi; timer diulang dari nol saat kondisi benar lagi |
| Pompa diaktifkan manual dari dashboard | aturan tidak terpengaruh | aturan hanya bereaksi pada data sensor, bukan desired state |

**Aturan lintas device.** Kondisi boleh menyebut variabel dari device berbeda milik pengguna
yang sama, misalnya "Soil < 20 (Greenhouse Node) AND Rain < 1 (Weather station)". Data dari
**kedua** device memicu penilaian ulang. Device asal aturan, yaitu tempat alarm dibuat dan
kabar WebSocket dikirim, adalah device milik variabel di kondisi **pertama** grup **pertama**.

---

## 9. Jaminan, batasan, dan cara menelusuri masalah

### 9.1 Jaminan

- **Berurutan.** Satu worker memproses pesan satu per satu sesuai urutan masuk antrean.
- **Tanpa aksi ganda.** Kunci `SELECT … FOR UPDATE` pada `RuleState` menjamin satu
  perpindahan hanya dijalankan satu kali, walaupun ada dua proses (misalnya worker dan mode
  inline).
- **Idempoten.** *Set actuator* dilewati bila desired sudah sama; *Raise alarm* tidak membuat
  alarm ganda.
- **Tahan restart.** Status, `pending_since`, dan waktu aktivasi tersimpan di database.
  Worker yang dinyalakan ulang melanjutkan dari keadaan yang sama.

### 9.2 Batasan (disengaja)

- **Waktu proses, bukan waktu kejadian.** Durasi dan cooldown dihitung dengan jam server.
- **Nilai terkini saja.** "Rata-rata 10 menit", "naik 5 °C dalam 1 menit", atau "A lalu B"
  butuh jendela waktu dan riwayat, dan itu wilayah model **CEP**.
- **Aksi tidak diulang otomatis.** Bila email gagal, kegagalannya tercatat di log tetapi tidak
  dicoba lagi. Sistem produksi biasanya punya antrean percobaan ulang (*retry*) dan
  *dead-letter queue*.
- **Satu pesan, satu penilaian per aturan.** Bila sebuah pesan memuat beberapa titik waktu
  untuk variabel yang sama, yang dinilai hanya titik terbarunya.

### 9.3 Menelusuri "mengapa aturan saya tidak aktif?"

1. **Halaman detail aturan → kotak *Last evaluation*.** Kotak ini menampilkan nilai yang
   dilihat mesin, umur dan sumbernya, serta hasil setiap kondisi. Hasil "? unknown (stale …)"
   berarti sensor itu terlambat mengirim.
2. **Status PENDING** berarti kondisi sudah benar dan aturan sedang menunggu durasi atau
   cooldown.
3. **Execution log** menampilkan nilai saat transisi dan hasil setiap aksi (✓/✗ beserta
   alasannya).
4. **Terminal worker** (`rule_engine` atau baris `rules |` di `runall`) mencetak setiap
   transisi.
5. **Shell Django**, untuk melihat keadaan mentah:
   ```bash
   python manage.py shell -c "
   from rules.models import RuleState
   s = RuleState.objects.get(rule__name='Irrigation')
   print(s.status, s.pending_since, s.snapshot['when'])"
   ```
6. **Uji tanpa sensor**, dengan mengirim data palsu lewat HTTP:
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/TOKEN/telemetry \
        -H "Content-Type: application/json" -d '{"soil_moisture": 15, "temperature": 32}'
   ```

---

## 10. Latihan

1. Tulis `(Soil < 20 OR Rain < 1) AND Temperature > 30` dalam bentuk grup. Ada berapa grup,
   dan apa isi masing-masing?
2. Pada linimasa bagian 8, ubah `max_age_seconds` menjadi 30. Pada pukul berapa aturan
   menjadi ACTIVE, atau tidak pernah? Jelaskan dengan tabel T/F/U.
3. Buat aturan tanpa kondisi clear dengan ekspresi `Soil < 20 AND Temperature > 30`, lalu
   matikan sensor suhu saat aturan ACTIVE. Apakah pompa dimatikan? Bandingkan dengan perilaku
   logika dua nilai (U dianggap F).
4. Jalankan `EXPLAIN` pada query 6.2 di MySQL untuk salah satu variabel Anda. Index apa yang
   dipakai? Hapus sementara index `telemetry_var_ts` di database percobaan dan bandingkan
   hasil `EXPLAIN`-nya.
5. Buat dua aturan yang memakai variabel `pump` di kondisinya, satu menyalakan dan satu
   mematikan. Apakah bisa terjadi loop? Gunakan bagian 7 untuk menjelaskannya.
