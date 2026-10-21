# Tahap 20: Model aturan state machine

Model aturan keenam di halaman *New rule* sekarang bisa dipakai: **State machine**. Sampai
tahap ini ada dua model:

| Model | Yang diingat | Cocok untuk |
|---|---|---|
| ECA (tahap 7) | satu keadaan per aturan: Idle / Pending / Active | satu ambang dengan aksi saat aktif dan pulih |
| Rule chain (tahap 8) | hampir tidak ada (ingatan kecil per node) | mengolah setiap pesan lewat graf |
| **State machine** (tahap 20) | **keadaan bernama per device**, berapa lama di keadaan itu | alarm bertahap, mode device, siklus hidup |

Contoh yang sulit ditulis dengan ECA:

```
                temperature > 8 selama 120 s          600 s di WARM
      ●──► NORMAL ──────────────────────────► WARM ───────────────────► BREACH
             ▲  ▲                               │                        │
             │  └──── temperature <= 7.5 ───────┘                        │
             └─────────────── temperature <= 7 selama 60 s ──────────────┘
             │
             └── tombol "Start defrost" ──► DEFROST ──(1800 s / tombol "End defrost")──► NORMAL
      * (keadaan mana pun) ──device offline──► OFFLINE
```

Dengan ECA, "WARM sudah 10 menit" butuh aturan kedua yang mengetahui keadaan aturan pertama.
Di state machine, keadaan dan lamanya adalah inti modelnya.

---

## 1. Konsep

| Istilah | Arti | Tabel |
|---|---|---|
| **Keadaan** (state) | nama seperti NORMAL, WARM, BREACH. Tepat satu adalah **keadaan awal** | `MachineState` |
| **Transisi** | panah dari keadaan asal ke keadaan tujuan, dengan **pemicu** | `MachineTransition` |
| **Asal `*`** | "dari keadaan mana pun", mis. `* → OFFLINE` | `source = NULL` |
| **Aksi masuk / keluar** | aksi biasa (aktuator, command, alarm, email, Telegram, clear alarm) milik satu keadaan | `RuleAction.state`, `run_on = ENTER / EXIT` |
| **Keadaan saat ini** | keadaan sebuah device di aturan ini, sejak kapan, penantian yang berjalan | `MachineInstance` |

### Satu keadaan per device

Pilihan pada tahap ini: **keadaan dimiliki device, bukan aturan.** Satu aturan bisa
mengamati satu device tertentu, atau **setiap device milik saya**:

```
Aturan "Heat stages" (setiap device milik siti)
    Greenhouse Node     HOT       sejak 3 menit
    Second Node         NORMAL    sejak 2 jam
```

Karena itu kondisi menyebut **key** (`temperature`), bukan satu variabel tertentu seperti di
ECA. Key itu dicari di device yang sedang dinilai. Device yang tidak punya key tersebut menilai
kondisinya sebagai *tidak diketahui* dan tetap di keadaannya.

"Setiap device milik saya" hanya mencakup device **milik** pemilik aturan, tidak termasuk device
yang dibagikan kepadanya (sama dengan rule chain, tahap 19). Device bersama dipilih satu per satu.

## 2. Pemicu transisi

| Pemicu | Kapan terpenuhi | Dinilai saat |
|---|---|---|
| **Condition** | kondisi bernilai BENAR, dan bila ada `for N s`, sudah bertahan N detik | ada data baru; penantian `for N s` diselesaikan oleh tick |
| **After some time in the state** | device sudah N detik di keadaan asal | tick |
| **Device sends no data** | tidak ada data N detik (`Device.last_seen_at`) | tick |
| **Device goes offline** | status device offline (tahap 16) | tick |
| **Button** | pengguna menekan tombol di halaman aturan | request web |

**Tick** adalah pemeriksaan berkala oleh worker `rule_engine` (setiap `RULE_ENGINE_TICK` detik,
bawaan 1). Tick hanya menyentuh device yang memang punya sesuatu untuk diperiksa: keadaannya
punya transisi berwaktu, atau ada kondisi `for N s` yang sedang ditunggu.

### Kondisi sebagai satu baris teks

```
temperature > 35 and humidity < 40 or temperature > 45
```

- Operator: `>` `>=` `<` `<=` `==` (atau `=`) `!=`, juga `≥ ≤ ≠`.
- Nilai: angka, atau `ON` / `OFF` (1 / 0).
- `and` lebih kuat daripada `or`, seperti kali sebelum tambah. Baris di atas berarti
  `(temperature > 35 AND humidity < 40) OR (temperature > 45)`.

Itu bentuk "OR dari AND" yang sama dengan kondisi ECA (tahap 7), hanya ditulis sebagai teks.
`parse_condition()` di `rules/engines/state_machine.py` mengubahnya menjadi baris-baris
`MachineCondition` (key, operator, ambang, nomor grup). `format_condition()` mengubahnya kembali
menjadi teks untuk form, diagram, dan JSON.

Logika tiga nilai tahap 7 tetap berlaku. Nilai yang tidak ada, atau lebih tua dari
*ignore values older than* (bawaan 300 s), bernilai **tidak diketahui**, dan transisi tidak
berjalan.

## 3. Cara mesin bekerja (`rules/engines/state_machine.py`)

```
step(aturan, device):
    keadaan = keadaan device saat ini (dibuat di keadaan AWAL bila belum ada, tanpa aksi)
    untuk setiap transisi, BERURUTAN seperti di form:
        lewati bila asalnya bukan keadaan ini dan bukan "*"
        lewati "* → X" bila device sudah di X
        nilai pemicunya
        yang PERTAMA terpenuhi -> pindah, berhenti
    simpan nilai dan hasil penilaian (halaman detail)

pindah(dari A ke B):
    aksi EXIT milik A, lalu aksi ENTER milik B
    simpan keadaan B, waktu masuk = sekarang, penantian dikosongkan
    catat RuleExecution(MOVE, device, A, B, hasil aksi)
```

Keputusan desain:

- **Urutan menentukan.** Bila dua transisi sama-sama terpenuhi, yang lebih atas di form yang
  berjalan. Urutan bisa diatur dengan menyusun baris.
- **Satu langkah per penilaian.** Setelah pindah ke B, transisi milik B baru dinilai pada data
  atau tick berikutnya. Tanpa batas ini, dua transisi yang saling bolak-balik bisa berputar
  selamanya dalam satu pesan.
- **`* → X` tidak berlaku saat device sudah di X.** Tanpa aturan ini, `* → OFFLINE` akan
  menembak terus setiap tick selama device offline.
- **Panah ke dirinya sendiri** hanya boleh untuk *After some time* dan tombol. Contoh
  `CRITICAL → CRITICAL after 600 s` = ulangi aksi CRITICAL setiap 10 menit (pengingat). Kondisi,
  "diam", atau "offline" yang tetap benar akan menembak tanpa henti, jadi form menolaknya.
- **Keadaan awal tanpa aksi.** Device yang baru terlihat langsung ditempatkan di keadaan awal.
  Aksi *on enter* keadaan awal hanya berjalan bila device kembali ke sana lewat transisi.
- **Disimpan = mulai dari awal.** Menyimpan, mengaktifkan, atau menonaktifkan aturan menghapus
  semua `MachineInstance`; setiap device mulai lagi di keadaan awal. Keadaan lama mungkin sudah
  tidak ada di versi aturan yang baru.
- **Dua proses tidak memindahkan device yang sama bersamaan.** Baris `MachineInstance` dikunci
  (`select_for_update`) selama penilaian.

### Penantian "for N s"

```
t=20  temperature 45  -> HOT → CRITICAL benar, butuh 60 s  -> pending = {"12": "t=20"}
t=50  tick            -> masih benar, baru 30 s            -> tunggu
t=55  temperature 35  -> SALAH                             -> pending dihapus (penantian batal)
t=60  temperature 45  -> benar lagi                        -> pending = {"12": "t=60"}
t=121 tick            -> 61 s                              -> pindah ke CRITICAL
```

Mekanismenya sama dengan "durasi" ECA. Bedanya, di sini ada satu penantian per transisi per
device, disimpan di `MachineInstance.pending`.

## 4. Aksi masuk dan keluar

Aksinya sama dengan aksi ECA (`rules/actions.py`), ditambah satu jenis baru:

| Jenis | Catatan di state machine |
|---|---|
| Set actuator, Send command | target harus device yang boleh **dikontrol** pemilik aturan (Operator ke atas, tahap 19) |
| Raise alarm | satu alarm aktif per aturan per device; tidak dibuat ulang selama masih aktif |
| **Clear alarm** (baru) | clear alarm aktif aturan ini pada device yang berpindah |
| Email, Telegram | placeholder baru: `{state}`, `{from_state}`, `{to_state}` |

ECA tidak butuh *Clear alarm* karena alarm ECA di-clear otomatis saat aturan pulih. State
machine tidak punya "pulih": keadaan mana yang berarti aman ditentukan pembuat aturan. Polanya:

```
masuk BREACH  -> Raise alarm (critical)
keluar BREACH -> Clear alarm
```

Hasil di log eksekusi, dengan awalan keadaan:

```
NORMAL → WARM      ✓ Enter WARM · Raise alarm: Warning alarm #41
WARM → BREACH      ✓ Exit WARM · Clear alarm: Cleared #41
                   ✓ Enter BREACH · Raise alarm: Critical alarm #42
```

## 5. Halaman

**Form** (*Rules → New rule → State machine*):

1. **Devices to watch**: satu device, atau setiap device milik saya; batas umur data.
2. **States**: nama, arti, warna, *Initial*. Minimal dua keadaan.
3. **Transitions**: dari → ke, pemicu, lalu isian sesuai pemicu (kondisi + `for N s`, detik,
   atau teks tombol). Pilihan nama keadaan diperbarui saat nama keadaan diketik.
4. **Actions**: keadaan, *When entering* / *When leaving*, jenis aksi.

**Detail**:

- **Diagram** SVG (`rules/machine_diagram.py`). Keadaan berderet dari kiri dengan keadaan awal
  ditandai ●. Panah maju melengkung di atas, panah mundur di bawah, dan `*` datang dari bawah.
  Warna panah menunjukkan pemicu. Keadaan yang sedang ditempati device diberi garis tebal dan
  jumlah device-nya. Tombol **Export PNG** sama dengan rule chain.
- **Devices**: keadaan setiap device, sejak kapan, nilai terakhir, transisi yang sedang
  ditunggu ("→ CRITICAL in 60 s"), dan tombol manual. Kotak ini diperbarui langsung lewat
  WebSocket (kabar `rule_eval`).
- Tabel transisi (berurutan), keadaan beserta aksinya, JSON, alarm, dan log perpindahan.

**Halaman device**: kartu **State machines** menampilkan keadaan device itu di aturan milik
pengguna. Aturan pengguna lain tidak ditampilkan, termasuk aturan penerima share pada device
saya.

### Tombol manual

`POST /rules/<id>/devices/<device>/press/<transition>/`, dengan syarat:

- aturan terlihat oleh pengguna (pemiliknya atau Admin);
- pengguna boleh **mengontrol** device itu (Operator ke atas, tahap 19), selain itu 403;
- device sedang berada di keadaan asal tombol.

Log mencatat siapa yang menekan: `✓ Button: “Start defrost” pressed by rina`. Log aktivitas
juga mencatat pengguna itu sebagai pelakunya.

## 6. Akses (tahap 19)

| Bagian | Syarat |
|---|---|
| Device yang diamati (satu device tertentu) | terlihat oleh pemilik aturan: miliknya atau dibagikan (Viewer cukup) |
| "Setiap device milik saya" | hanya device milik sendiri |
| Target aksi aktuator / command / alarm | Operator ke atas |
| Tombol | pengguna yang menekan: Operator ke atas pada device itu |

Semua diperiksa ulang saat berjalan. Bila share dicabut, device tidak lagi dinilai dan
keadaannya berhenti di keadaan terakhir.

## 7. JSON

```json
{
  "format": "iot-platform.rule", "version": 1, "model": "STATE_MACHINE",
  "name": "Cold room states", "enabled": true,
  "machine": {
    "device": "Cold Storage Unit", "max_age_seconds": 300,
    "states": [{"name": "NORMAL", "description": "2–8 °C", "tone": "OK", "initial": true}, ...],
    "transitions": [
      {"from": "NORMAL", "to": "WARM", "trigger": "CONDITION", "condition": "temperature > 8", "seconds": 120, "label": ""},
      {"from": "*", "to": "OFFLINE", "trigger": "OFFLINE", "condition": "", "seconds": null, "label": ""}
    ],
    "actions": [{"state": "BREACH", "kind": "ALARM", "run_on": "ENTER", "severity": "CRITICAL", "message": "..."}]
  }
}
```

Impor memakai form yang sama, jadi semua pemeriksaan di atas ikut berlaku.

## 8. Yang diubah

| File | Isi |
|---|---|
| `rules/models.py` + migrasi `0006_state_machine` | `MachineSpec`, `MachineState`, `MachineTransition`, `MachineCondition`, `MachineInstance`; `RuleAction.state`, `RunOn.ENTER/EXIT`, `Kind.CLEAR_ALARM`; `RuleExecution.device/from_state/to_state`, `Transition.MOVE` |
| `rules/engines/state_machine.py` | mesin (`on_message`, `tick`, `step`, `move`, `press`), kondisi teks |
| `rules/actions.py` | `run_state_actions()`, aksi *Clear alarm* |
| `rules/forms.py` | `MachineSpecForm`, `StateFormSet`, `TransitionFormSet`, `MachineActionFormSet`, `check_machine()` |
| `rules/views.py`, `rules/urls.py` | form, detail, kotak device, tombol manual, impor, pencarian |
| `rules/machine_diagram.py`, template `rule_form_machine.html`, `rule_detail_machine.html`, `includes/machine_*.html` | tampilan |
| `rules/portable.py` | ekspor/impor JSON |
| `rules/management/commands/seed_rules.py` | contoh *Cold room states* (rina) |
| `activity/signals.py` | log `rule.move` |
| `devices/views.py`, `device_detail.html` | kartu *State machines* |
| `static/js/flow_export.js` | keterangan warna PNG bisa diganti (`data-legend`) |

## 9. Menerapkan dan mencoba

```bash
python manage.py migrate          # rules 0006
python manage.py seed_rules       # menambah "Cold room states" (rina)
```

Di server: `sudo /srv/iot/app/deploy/scripts/update.sh`. Tidak ada layanan baru: aturan dinilai
oleh `iot-rules` yang sudah ada.

Mencoba contoh sebagai **rina**:

```bash
python manage.py send_telemetry "Cold Storage Unit" temperature=9.5
```

1. Halaman aturan *Cold room states*: device di **NORMAL**, dan kolom *Next* menulis
   "→ WARM in 120 s".
2. Dua menit kemudian device pindah ke **WARM** dan alarm *warning* muncul. Kirim
   `temperature=7` untuk kembali ke NORMAL, atau tunggu 10 menit untuk **BREACH**.
3. Di NORMAL, tekan **Start defrost**: device ke **DEFROST**, dan suhu tidak diamati sampai
   **End defrost** atau 30 menit.
4. Matikan device (atau tunggu `DEVICE_OFFLINE_SECONDS`): `* → OFFLINE`.

## 10. Tes

```bash
python manage.py test rules.tests_state_machine
```

| Kelompok | Yang diuji |
|---|---|
| `ConditionTextTests` | `and` sebelum `or`, ON/OFF, operator alternatif, pesan error |
| `EngineTests` | siklus lengkap (aksi masuk/keluar, penantian, pengulangan 600 s, clear alarm), penantian batal, urutan + satu langkah per pesan, nilai tidak diketahui, offline dari keadaan mana pun, device diam, device tertentu mulai tanpa data, keadaan per device, toggle mengulang dari awal, log aktivitas |
| `AccessTests` | Viewer membaca tetapi tidak mengontrol, share dicabut, tombol (log, pelaku, keadaan asal), tombol butuh Operator |
| `FormTests` | membuat, mengubah (nama keadaan berubah, id tetap), validasi, target aksi harus Operator |
| `JsonTests` | ekspor → impor → ekspor sama; impor salah tidak menyimpan apa pun |
| `PageTests` | daftar, pencarian, detail, kotak device, halaman device, aturan orang lain tersembunyi |

Ditambah `SampleRulesTests.test_cold_room_states` untuk contoh dari `seed_rules`.
