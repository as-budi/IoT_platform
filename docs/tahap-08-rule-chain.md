# Tahap 8 — Rule engine: Flow-based rule chain

Tahap ini menambahkan model rule engine kedua: **rule chain**, yaitu aturan berbentuk
**graf node** seperti di ThingsBoard dan Node-RED. Model ECA dari
[tahap 7](tahap-07-rule-engine-eca.md) tetap ada; keduanya berjalan berdampingan di worker
yang sama.

```
                         ┌──True──► [volt_alarm: Create alarm]
Input ──► [over_voltage: voltage > 235?]
  │                      └──False─► [volt_ok: Clear alarm]
  │
  └─────► [apparent: apparent_power = voltage × current] ──Success──► [pf: power_factor = power ÷ apparent_power]
                                                                       │
              [pf_email: Send email] ◄──True── [pf_limit: Rate limit 600 s] ◄──True── [low_pf: power_factor < 0.8?]
```

Penjelasan mekanisme eksekusi lengkap, termasuk query database dan contoh langkah demi langkah,
ada di [flow-mekanisme.md](flow-mekanisme.md). Dokumen ini adalah ringkasannya.

## 1. Status model rule engine

| Model | Status | File |
|---|---|---|
| Event–Condition–Action | tersedia (tahap 7) | `rules/engines/eca.py` |
| **Flow-based rule chain** | **tersedia (tahap 8)** | `rules/engines/flow.py` |
| SQL over the message stream | direncanakan | `rules/engines/sql.py` |
| Complex event processing | direncanakan | `rules/engines/cep.py` |
| Production rules (RETE) | direncanakan | `rules/engines/production.py` |
| State machine | direncanakan | `rules/engines/state_machine.py` |

`FlowEngine` adalah turunan `RuleEngine` dengan `implemented = True`, sehingga worker
`rule_engine` otomatis ikut memanggil `on_message()` miliknya. `tick()` sengaja kosong: rule
chain hanya digerakkan oleh pesan.

## 2. Konsep

| Istilah | Arti |
|---|---|
| **Pesan** | satu paket telemetri dari satu device: `data` (key → nilai) dan `metadata` (nama device, protokol, waktu) |
| **Input** | titik masuk. Setiap pesan dari device yang didengarkan aturan mulai di sini |
| **Node** | satu kotak. Mengerjakan satu tugas pada pesan, lalu menghasilkan satu **label** |
| **Link** (panah) | `asal —label→ tujuan`: bila node asal selesai dengan label itu, pesan pergi ke node tujuan |
| **Label** | `True`/`False` untuk filter, `Success`/`Failure` untuk node lain |
| **Cabang** | jalur pesan. Dua panah berlabel sama dari satu node = dua cabang, masing-masing membawa **salinan** pesan |
| **Jejak (trace)** | daftar node yang dilewati satu pesan, beserta hasil dan keterangannya |

Aturan main penelusuran:

1. Node mengerjakan tugasnya dan menghasilkan label.
2. Pesan diteruskan lewat **semua** panah dengan label itu.
3. Tidak ada panah dengan label itu → cabang **selesai** (tidak dianggap error).
4. Penelusuran **depth-first**: satu cabang diikuti sampai habis, baru cabang berikutnya.
5. Graf **tidak boleh berputar**. Form menolak siklus, dan mesin berhenti setelah 50 langkah
   sebagai pengaman tambahan.

## 3. Katalog node

| Kategori | Node | Yang dilakukan | Label keluar |
|---|---|---|---|
| Filter | **Check value** | `key operator ambang`, mis. `temperature > 32` | True / False / Failure (key tidak ada) |
| Filter | **Value changed** | lolos bila nilai berubah ≥ ambang sejak nilai terakhir yang lolos (per device) | True / False / Failure |
| Filter | **Rate limit** | lolos paling banyak sekali tiap N detik (per device) | True / False |
| Enrichment | **Add latest value** | menambah nilai terakhir sebuah variabel (device mana pun milik pemilik) dari database | Success / Failure (tidak ada data / basi / tanpa akses) |
| Transformation | **Calculate** | `key = a op b`; operand berupa key pesan atau angka; op: + − × ÷ min max | Success / Failure (operand tidak ada, bagi nol) |
| Action | **Set actuator** | meminta keadaan baru aktuator (dilewati bila sudah diminta) | Success / Failure |
| Action | **Send command** | mengantre command untuk device | Success / Failure |
| Action | **Create alarm** | membuat alarm di device pesan, kecuali alarm aturan ini masih aktif | Success / Failure |
| Action | **Clear alarm** | meng-clear alarm aktif aturan ini di device pesan | Success / Failure |
| Action | **Send email** | mengirim email | Success / Failure |

Node aksi memakai fungsi yang sama dengan aksi ECA (`rules/actions.py`). Pesan alarm dan
email boleh berisi placeholder `{rule}`, `{device}`, `{deviceName}`, `{values}`, dan **key
pesan mana pun**, mis. `{temperature}` atau `{power_factor}` hasil node Calculate.

## 4. ECA dan rule chain: kapan memakai yang mana

| | ECA | Rule chain |
|---|---|---|
| Bentuk | satu formulir: When / If / Then / Clear | graf node dan panah |
| Keadaan aturan | IDLE → PENDING → ACTIVE | tidak ada; tiap pesan menelusuri graf dari awal |
| Kapan aksi jalan | hanya saat **berpindah** keadaan | **setiap kali** pesan sampai di node aksi |
| Pencegah pengulangan | mesin keadaan, cooldown | node *Value changed*, *Rate limit*, aksi idempoten |
| Waktu ("selama 60 s", "device diam") | ada | tidak ada (butuh `tick`) |
| Menghitung nilai baru | tidak bisa | node *Calculate* |
| Data dari device lain | otomatis untuk variabel di kondisi | eksplisit, node *Add latest value* |
| Cabang berbeda untuk hasil berbeda | hanya activate/clear | bebas (True, False, Failure, ...) |
| Cocok untuk | alarm ambang, otomasi sederhana dengan histeresis | pipeline: hitung → cek → beberapa aksi berbeda |

AND dan OR di rule chain tidak ditulis sebagai ekspresi, tetapi sebagai **bentuk graf**:

- **AND** = filter berurutan: `humid —True→ warm —True→ aksi`.
- **OR** = dua panah ke node yang sama: `too_acid —True→ alarm`, `too_alkaline —True→ alarm`.

## 5. Tabel database

```
Rule (model = "FLOW")
 ├── FlowSpec   (1-1)      device: pesan dari device mana yang masuk (kosong = semua device pemilik)
 ├── FlowNode   (1-banyak) name, kind, field sesuai jenis, memory (JSON, ingatan per device)
 ├── FlowLink   (1-banyak) source (NULL = Input), label, target, order
 ├── RuleState  (1-1)      snapshot = jejak pesan TERAKHIR; last_evaluated_at, last_activated_at
 └── RuleExecution         satu baris per pesan yang mengubah sesuatu; transition = "RUN", trace = jejak
Alarm                      sama dengan ECA (rule, device, severity, active/cleared, acknowledged)
```

Migrasi: `rules/migrations/0003_flow_rule_chain.py`. Migrasi ini membuat tiga tabel baru,
menambah kolom `trace` di `RuleExecution`, dan menambah pilihan transisi `RUN`.

## 6. Halaman

- **Rules → New rule → Use FLOW** membuka form tiga bagian:
  1. *Input*: pilih device, atau kosongkan untuk semua device Anda.
  2. *Nodes*: tambah node, beri nama unik (huruf, angka, `_`), lalu pilih jenisnya. Field
     yang tampil menyesuaikan jenis node, beserta penjelasan dan label keluarannya.
  3. *Links*: setiap baris dibaca "bila **From** selesai dengan **Label**, kirim pesan ke
     **To**". Pilihan label otomatis dibatasi sesuai jenis node asal.
- Saat disimpan, server memeriksa graf (`check_graph` di `rules/forms.py`): nama node,
  label yang sah, panah kembar, adanya panah dari Input, dan siklus.
- **Halaman detail** menampilkan:
  - **diagram** graf. Node dan panah yang dilewati pesan terakhir ditandai biru, dan setiap
    node menampilkan hasilnya (mis. `→ True`).
  - **tabel jejak** pesan terakhir.
  - daftar node.
  - alarm.
  - **execution log**. Setiap baris log bisa dibuka untuk melihat jalurnya.
- Diagram dan jejak diperbarui langsung lewat WebSocket, dengan mekanisme yang sama seperti
  halaman ECA (kabar `rule_eval` dan `rule`).
- Status rule chain di daftar aturan tampil sebagai **Listening**.

Execution log hanya mencatat pesan yang **mengubah sesuatu** (aktuator berganti, command
dikirim, alarm dibuat atau di-clear, email terkirim), atau yang aksinya gagal. Pesan yang
hanya menemukan "pompa sudah ON" tidak dicatat, supaya log tidak bertambah setiap 10 detik.
Jejaknya tetap terlihat di kotak diagram.

## 7. Struktur kode

| File | Isi |
|---|---|
| `rules/models.py` | `FlowSpec`, `FlowNode` (+ `MathOp`), `FlowLink`; `RuleExecution.trace`, transisi `RUN` |
| `rules/engines/flow.py` | `NODE_TYPES` (katalog), handler tiap node, `walk()` (penelusuran), `FlowEngine` |
| `rules/flow_diagram.py` | tata letak diagram SVG (kolom = jalur terpanjang dari Input) |
| `rules/forms.py` | `FlowSpecForm`, `FlowNodeForm`, `NodeFormSet`, `LinkFormSet`, `check_graph()` |
| `rules/views.py` | `FlowRuleFormView`, `save_flow_graph()`, `create_rule()`/`edit_rule()` (memilih form sesuai model), `flow_run_context()` |
| `rules/templates/rules/` | `rule_form_flow.html`, `rule_detail_flow.html`, `includes/node_form.html`, `link_row.html`, `flow_diagram.html`, `flow_run.html`, `flow_trace.html` |
| `rules/events.py` | kabar WebSocket untuk rule chain dikirim ke device asal pesan |
| `rules/actions.py` | dipakai ulang oleh node aksi |
| `rules/management/commands/seed_rules.py` | empat contoh rule chain (`DEMO_CHAINS`) |
| `rules/tests_flow.py` | tes mesin, node, form, halaman, diagram, dan skenario contoh |

## 8. Menjalankan

```bash
conda activate iot_platform
python manage.py migrate          # tabel rules_flowspec, rules_flownode, rules_flowlink
python manage.py seed_rules       # menambah 4 contoh rule chain (aturan yang sudah ada dilewati)
python manage.py runall           # web + mqtt + coap + worker rule engine
```

Contoh rule chain dari `seed_rules`:

| Aturan | Pemilik | Pola yang diperlihatkan |
|---|---|---|
| Lab power monitor | budi | dua cabang dari Input, dua Calculate berurutan, Failure saat bagi nol, Rate limit sebelum email |
| Pond pH watch | andi | Value changed, OR dengan dua panah ke node yang sama, alarm dibuat/di-clear node |
| Cold room door check | rina | AND dengan filter berurutan, Rate limit, Send command |
| Greenhouse watering chain | siti | Add latest value (stasiun cuaca), Failure bila data basi, satu hasil ke dua cabang. **Nonaktif bawaan**: memakai pompa yang sama dengan ECA "Smart irrigation" |

**Uji cepat** (login `budi` / `demo12345`, buka *Rules → Lab power monitor*):

```bash
python manage.py send_telemetry "Lab Power Meter" voltage=230 current=4 power=600
```

Hasil yang diharapkan:

- Diagram menandai jalur `Input → apparent → pf → low_pf → pf_limit → pf_email`.
- Email faktor daya rendah (0.652174) tercetak di terminal worker.
- Kirim lagi dengan `voltage=238`: alarm over-voltage muncul, tetapi `pf_limit` berhenti
  di **False**, sehingga tidak ada email kedua.

Skenario lengkap untuk keempat contoh ada di [flow-mekanisme.md](flow-mekanisme.md#9-contoh-lain-singkat).

## Latihan

1. Nonaktifkan dulu aturan ECA "Server room cooling" (memakai kipas yang sama), lalu buat
   rule chain "Fan by temperature" untuk Server Room Node:
   - `temperature > 28` →True→ set Lab Relay Board · Fan speed = 100.
   - →False→ set Fan speed = 25.

   Kirim 29, 29, 27. Berapa baris execution log yang muncul, dan mengapa bukan tiga?
2. Pada *Lab power monitor*, hapus node `pf_limit` dan sambungkan `low_pf —True→ pf_email`
   langsung. Kirim tiga pesan dengan faktor daya rendah. Berapa email yang terkirim?
3. Ubah *Pond pH watch* supaya alarm juga dibuat bila `ph` tidak ada di pesan (petunjuk:
   panah **Failure** dari `ph_changed`). Apa akibatnya untuk pesan yang hanya berisi
   `dissolved_oxygen`?
4. Tulis ulang aturan ECA "Poultry heat stress" sebagai rule chain:
   `(temperature > 32 AND humidity > 70) OR ammonia > 25`. Bagian mana yang tidak bisa ditiru
   (petunjuk: `duration 30 s` dan histeresis)?
5. Coba buat panah `low_pf —True→ apparent`. Pesan apa yang ditampilkan form, dan mengapa
   larangan ini penting?
