# Tahap 14: Penjadwalan aturan (ECA dan rule chain)

Sampai tahap 13, aturan hanya bergerak karena **data**: pesan telemetri masuk, atau device
diam terlalu lama. Banyak kebutuhan IoT justru bergerak karena **jam**:

- siram tanaman setiap pagi pukul 06:00;
- nyalakan lampu kandang 18:00 dan matikan 06:00;
- kirim laporan suhu ruang dingin setiap hari kerja pukul 07:00;
- putar aerator kolam 10 menit setiap jam.

Tahap ini menambahkan jadwal **ke dalam rule engine yang sudah ada**, bukan sebagai fitur
terpisah. Jadwal hanyalah *pemicu* (event) baru:

| Model | Pemicu sebelumnya | Pemicu baru |
|---|---|---|
| ECA | data baru (`TELEMETRY`), device diam (`INACTIVITY`) | **`SCHEDULE`** — "On a schedule" |
| Rule chain (FLOW) | setiap pesan telemetri | **Input "On a schedule"** — satu pesan buatan pada setiap waktu jadwal |

Mengapa digabung dengan rule, bukan modul "Scheduler" sendiri? Karena semua yang dibutuhkan
setelah jam tiba sudah ada di rule engine: kondisi (cek dulu apakah tanah kering), aksi
(aktuator, command, alarm, email), log eksekusi, kepemilikan dan izin device, ekspor/impor
JSON, serta worker yang berjalan terus. Fitur terpisah akan menduplikasi semuanya.

---

## 1. Tiga bentuk jadwal

Satu aturan punya paling banyak satu jadwal (`RuleSchedule`, relasi 1-1 dengan `Rule`).

| Repeat | Field | Contoh | Waktu yang dihasilkan |
|---|---|---|---|
| **At set times** (`DAILY`) | `times` + `weekdays` | `06:00, 18:30`, Senin–Jumat | Sen 06:00, Sen 18:30, Sel 06:00, … |
| **Every N minutes** (`INTERVAL`) | `interval_minutes` + `weekdays` | 15, setiap hari | 00:00, 00:15, 00:30, …, 23:45 |
| **Once** (`ONCE`) | `run_at` | 20 Okt 2026 07:30 | hanya 20 Okt 2026 07:30 |

Catatan:

- **Jam 24 jam**, dipisah koma. `6:00` diterima dan disimpan sebagai `06:00`; urutan dan
  duplikat dirapikan. Maksimal 24 jam per hari.
- **Hari** memakai angka 0–6 seperti `datetime.weekday()` Python: 0 = Senin … 6 = Minggu.
  Di database disimpan sebagai teks, mis. `"01234"` = Senin–Jumat.
- **Interval dihitung dari pukul 00:00**, bukan dari saat aturan disimpan. Interval 15 menit
  selalu jatuh di :00, :15, :30, :45 — mudah ditebak dan sama setelah server restart. Interval
  yang tidak membagi 24 jam (mis. 90 menit) dimulai lagi dari 00:00 keesokan harinya:
  …, 21:00, 22:30, **00:00**, 01:30, …
- **Once** yang waktunya sudah lewat tidak dijalankan lagi; form menolak waktu lampau saat
  jadwal dibuat atau diubah.

### Zona waktu

Semua jam memakai zona waktu server, `TIME_ZONE` di `config/settings.py` (`Asia/Jakarta`).
Database tetap menyimpan waktu dalam UTC (`USE_TZ = True`, tahap 1); perhitungan jadwal
dilakukan di jam lokal lalu diubah ke waktu *aware*:

```python
# rules/schedules.py
def _local(day, clock):
    """Tanggal + jam dinding di zona waktu server -> datetime aware."""
    return timezone.make_aware(datetime.combine(day, clock), timezone.get_current_timezone())
```

Jadi "06:00" berarti 06:00 WIB (= 23:00 UTC hari sebelumnya). Bila server dipindah ke zona
waktu lain, ubah `TIME_ZONE`, bukan jam di setiap aturan.

---

## 2. Model data

```
Rule ──1:1── RuleSchedule           (tabel rules_ruleschedule, migrasi rules/0004)
               kind                 DAILY / INTERVAL / ONCE
               times                "06:00,18:30"
               weekdays             "0123456"
               interval_minutes     15
               run_at               2026-10-20 00:30 UTC
               active_for_seconds   600   (khusus ECA, lihat bagian 4)
               next_run_at          kapan jadwal berikutnya   (diindeks)
               last_run_at          waktu jadwal terakhir yang diambil worker
```

Perubahan lain di migrasi yang sama:

| Tabel | Perubahan |
|---|---|
| `EcaSpec.event` | pilihan baru `SCHEDULE` ("On a schedule") |
| `FlowSpec.trigger` | field baru: `TELEMETRY` (bawaan, perilaku lama) atau `SCHEDULE` |
| `RuleExecution.transition` | pilihan baru `SKIPPED` — jadwal tiba tetapi tidak dijalankan |

Migrasi ini hanya **menambah**: aturan lama tetap `TELEMETRY` dan berjalan seperti sebelumnya.

### Mengapa `next_run_at` disimpan?

Cara naif: setiap detik, untuk setiap aturan berjadwal, hitung "apakah sekarang waktunya?".
Itu boros, dan rapuh: bila satu tick terlewat (worker sibuk 2 detik), jadwal 06:00:00 bisa
terlewat.

Cara yang dipakai: waktu berikutnya dihitung **sekali** lalu disimpan. Setiap tick worker
cukup bertanya ke database:

```sql
SELECT ... FROM rules_ruleschedule s JOIN rules_rule r ON r.id = s.rule_id
WHERE r.model = 'ECA' AND r.is_enabled AND s.next_run_at <= NOW()
ORDER BY s.next_run_at;
```

Query ini memakai index `next_run_at`, sehingga biayanya hampir nol walaupun ada ribuan aturan,
dan jadwal tidak pernah "terlewati" hanya karena tick-nya tidak tepat di detik itu: 06:00:00
yang baru diperiksa pukul 06:00:01 tetap `<= NOW()`.

`next_run_at` dihitung ulang ketika:

| Kejadian | Di mana |
|---|---|
| aturan disimpan (form Edit / impor JSON) | `save_schedule()` di `rules/views.py` |
| aturan diaktifkan (tombol Enable) | `RuleToggleView` → `schedules.reschedule(rule)` |
| jadwal diambil worker | `schedules.claim()` |
| seed contoh | `seed_rules` |

Aktifkan ulang dihitung **dari sekarang**: jadwal yang lewat selama aturan nonaktif tidak
"dikejar".

---

## 3. Worker: mengambil jadwal yang jatuh tempo

Tidak ada proses baru. Worker `manage.py rule_engine` (tahap 7) sudah memanggil `tick(now)`
setiap `RULE_ENGINE_TICK` detik untuk setiap mesin aturan. `EcaEngine.tick()` dan
`FlowEngine.tick()` kini juga menangani jadwal:

```
tick(now)
  └─ for schedule in schedules.due(model, now):        next_run_at <= now, aturan aktif
       claimed = schedules.claim(schedule, now)
         ├─ None      → proses lain sudah mengambilnya, lewati
         ├─ "missed"  → terlambat > RULE_SCHEDULE_GRACE_SECONDS → log "Skipped"
         └─ "fire"    → jalankan aturan (bagian 4 untuk ECA, bagian 5 untuk rule chain)
```

### `claim()`: klaim dulu, baru jalankan

```python
# rules/schedules.py
scheduled = schedule.next_run_at
following = next_run(schedule, max(now, scheduled))
updated = RuleSchedule.objects.filter(pk=schedule.pk, next_run_at=scheduled).update(
    next_run_at=following, last_run_at=scheduled
)
if not updated:
    return None          # baris sudah dimajukan proses lain
```

SQL-nya:

```sql
UPDATE rules_ruleschedule
SET next_run_at = '2026-10-19 23:00:00', last_run_at = '2026-10-18 23:00:00'
WHERE rule_id = 12 AND next_run_at = '2026-10-18 23:00:00';
```

Ini *compare-and-set*: hanya proses yang masih melihat `next_run_at` lama yang berhasil
mengubah baris. Dua hal penting:

1. **Tidak pernah dijalankan dua kali**, walaupun tanpa sengaja ada dua worker berjalan.
2. Jadwal dimajukan **sebelum** aturan dijalankan. Bila sebuah aksi gagal atau error, jadwal
   tidak diulang setiap detik — kegagalan tercatat sekali di log, dan aturan menunggu jadwal
   berikutnya.

### Jadwal yang terlewat (worker mati)

Bayangkan worker mati pukul 05:50 dan baru hidup lagi pukul 08:30. Haruskah pompa dinyalakan
pukul 08:30 untuk "jadwal 06:00"? Biasanya **tidak**: tanaman mungkin sudah disiram hujan,
dan menyalakan aktuator jauh dari jam yang diharapkan bisa berbahaya.

Aturannya:

| Keterlambatan | Hasil |
|---|---|
| ≤ `RULE_SCHEDULE_GRACE_SECONDS` (bawaan 300 s) | dijalankan seperti biasa |
| lebih dari itu | **tidak dijalankan**; log mencatat `Skipped: Missed the 06:00 run: the rule engine was not running (9000 s late, limit 300 s)` |

Yang dilewati hanya **satu** baris log (untuk jadwal yang paling awal), lalu `next_run_at`
dihitung dari *sekarang*. Jadwal interval 1 menit yang terlewat 3 jam tidak menghasilkan 180
baris log.

Atur batasnya di `.env`:

```bash
RULE_SCHEDULE_GRACE_SECONDS=300
```

---

## 4. ECA dengan event "On a schedule"

```
WHEN  setiap hari 06:00                          (jadwal, bukan data)
IF    soil_moisture < 40 AND rain < 0.5          (opsional)
THEN  pump = ON                                  (aksi "on activate")
CLEAR setelah 600 s                              (active_for_seconds)
THEN  pump = OFF                                 (aksi "on clear")
```

### Kondisi = syarat tambahan, opsional

Pada event `TELEMETRY`, kondisi adalah *pemicu*. Pada event `SCHEDULE`, pemicunya jam;
kondisi hanya **syarat** saat jam itu tiba:

- **tanpa kondisi** → aksi berjalan setiap waktu jadwal;
- **dengan kondisi** → kondisi dinilai sekali dengan data terakhir di database (fungsi
  `judge()` yang sama dengan tahap 7, termasuk batas umur data `max_age_seconds`).
  Bila hasilnya *benar*, aturan aktif. Bila *salah* atau *tidak diketahui* (data tidak ada
  atau terlalu tua), jadwal dicatat `Skipped` beserta nilai yang dilihat:

  ```
  ⏭ Schedule: 06:00 run skipped: condition is false (Greenhouse Node · Soil moisture = 55, ...)
  ```

Kondisi "Clear when" (histeresis) **tidak dipakai** untuk aturan berjadwal dan dihapus saat
disimpan. Durasi ("for at least") dan cooldown juga dipaksa 0: jadwal sendiri yang
menentukan kapan dan seberapa sering aturan berjalan. "Ignore values older than" tetap dipakai
dan sebaiknya cukup besar (contoh seed memakai 3600 s), karena sensor bisa saja mengirim data
hanya setiap beberapa menit.

### Keadaan dan clear otomatis

Aturan ECA punya keadaan IDLE / ACTIVE (tahap 7). Untuk jadwal, perpindahannya:

```
         jam jadwal tiba & kondisi benar            active_for_seconds terlewat
  IDLE ─────────────────────────────────► ACTIVE ─────────────────────────────► IDLE
          aksi "on activate"                         aksi "on clear" + alarm di-clear
```

`active_for_seconds` ("Keep active for") menentukan **berapa lama** aturan aktif:

| `active_for_seconds` | Perilaku | Cocok untuk |
|---|---|---|
| `600` | ACTIVATE pukul 06:00, CLEAR otomatis pukul 06:10 | "nyalakan pompa 10 menit" |
| `0` | hanya ACTIVATE; aturan langsung kembali ke IDLE tanpa aksi clear | email/command/alarm sekali tembak |

Clear otomatis diperiksa setiap tick (`EcaEngine.clear_expired`), dari
`RuleState.last_activated_at + active_for_seconds`. Karena keadaan disimpan di database,
clear tetap terjadi walaupun worker sempat restart di antara 06:00 dan 06:10 (asalkan worker
hidup lagi; clear dijalankan begitu worker kembali).

Form menolak aksi "on clear" tanpa "Keep active for": aksi itu tidak akan pernah berjalan.

Contoh garis waktu (jadwal 06:00 dan 07:00, keep active 7200 s):

| Waktu | Kejadian | Log |
|---|---|---|
| 06:00 | kondisi benar → ACTIVE, pompa ON | Activated |
| 07:00 | jadwal tiba, aturan masih ACTIVE | Skipped: still active from the previous run |
| 08:00 | 7200 s sejak 06:00 → CLEAR, pompa OFF | Cleared |

### Device asal dan placeholder

Aturan berjadwal tidak punya device pengirim pesan. Device "asal" (tempat alarm dibuat dan
kabar WebSocket dikirim) diambil dari:

1. device variabel di kondisi pertama, kalau tidak ada
2. device target aksi pertama (aktuator atau command), kalau tidak ada
3. tidak ada: aksi **alarm** gagal dengan pesan *"No device to attach the alarm to: add a
   condition or a device action."* Aksi email tetap berjalan.

Placeholder pesan alarm/email: `{rule}`, `{device}`, `{values}`, `{transition}`, dan
placeholder baru **`{time}`** = jam jadwal, mis. `06:00`.

### Telemetri tidak memicu aturan berjadwal

`EcaEngine.on_message()` hanya memilih aturan `event = TELEMETRY`. Nilai soil yang masuk pukul
05:30 tidak membuat aturan berjadwal aktif; nilai itu hanya *dibaca* saat jadwal tiba.

---

## 5. Rule chain dengan Input "On a schedule"

Rule chain berjadwal tidak menerima telemetri. Pada setiap waktu jadwal, worker membuat
**satu pesan buatan** dan memasukkannya ke node Input:

```json
{
  "data": {"hour": 7, "minute": 0, "weekday": 0, "day": 19, "month": 10},
  "metadata": {
    "trigger": "schedule",
    "scheduledAt": "2026-10-19T07:00:00+07:00",
    "deviceId": 5,
    "deviceName": "Cold Storage Unit",
    "ts": "2026-10-19T00:00:00+00:00"
  }
}
```

- `data` berisi **waktu jadwal** (jam lokal), sehingga node `CHECK` bisa membuat cabang
  menurut waktu, mis. `weekday >= 5` untuk akhir pekan, atau `hour < 12` untuk pagi.
- Data sensor **tidak** ada di pesan. Ambil dengan node **Add latest value** (`LATEST`):
  node itu menambahkan nilai terakhir sebuah variabel ke pesan, dan keluar lewat `Failure`
  bila datanya tidak ada atau terlalu tua.
- "Messages from" (device) **opsional** untuk jadwal. Bila diisi, device itu menjadi device
  pesan: `{deviceName}` terisi, dan node alarm (`CREATE_ALARM` / `CLEAR_ALARM`) tahu harus
  menempel ke device mana. Bila kosong, node alarm keluar lewat `Failure` dengan pesan
  *"Alarms need a device: choose one under Input → Device."*
- Ingatan node (`CHANGED`, `THROTTLE`) memakai kunci `"schedule"` bila tidak ada device.

Contoh seed **Cold room daily report**:

```
Input (Sen–Jum 07:00)
  └─► temp_now: add latest temperature ──Success──► hum_now: add latest humidity ──Success──► report: email
            └──Failure──► no_data: create alarm ◄──Failure──┘
```

Email yang terkirim:

```
Cold room report 19/10 7:00 — temperature 4.5 °C, humidity 70 %.
```

Seperti rule chain biasa, log eksekusi hanya mencatat run yang **mengubah sesuatu** (atau yang
aksinya gagal); jejak run terakhir selalu terlihat di kotak *Rule chain · last message* dengan
keterangan 🕑 *scheduled run*. Jadwal yang terlewat dicatat `Skipped`.

---

## 6. Form dan halaman

### Form ECA

Langkah 1 *When — event* mendapat pilihan ketiga **On a schedule**. Memilihnya menampilkan
kotak jadwal:

- **Repeat**: At set times / Every N minutes / Once — field yang tampil mengikuti pilihan;
- **At** (jam), **On** (hari, dengan pintasan *Every day · Mon–Fri · Sat–Sun*),
  **Every (minutes)**, **On (date and time)**;
- **Keep active for (seconds)**.

Langkah 2 *If — condition* tetap tampil (opsional), bagian *Clear when* disembunyikan.
Di *Timing & safeguards* hanya "Ignore values older than" yang tampil.

Validasi (`RuleScheduleForm` di `rules/forms.py`):

| Isian | Pesan error |
|---|---|
| jam kosong (At set times) | Enter at least one time, e.g. 06:00. |
| `25:00`, `6.30`, … | “25:00” is not a time like 06:00 |
| tidak ada hari dicentang | Choose at least one day. |
| interval kosong / > 1440 | Enter the number of minutes. / At most 1440 minutes (once a day). |
| Once tanpa tanggal / di masa lalu | Choose the date and time. / Choose a time in the future. |
| aksi "on clear" dengan keep active 0 | “On clear” actions need “Keep active for” … |

Form jadwal hanya divalidasi bila event (atau Input rule chain) memang "On a schedule". Bila
aturan diubah kembali ke telemetri, jadwalnya dihapus.

### Form rule chain

Kartu *1 Input* mendapat pilihan **Telemetry messages** / **On a schedule**. Pilihan jadwal
menampilkan kotak jadwal yang sama (tanpa "Keep active for", karena rule chain tidak punya
keadaan ACTIVE).

### Halaman detail

- Kartu *Rule* (ECA) menampilkan `When 🕑 every day at 06:00`, `If … / always (no condition)`,
  `Clear after 600 s`, lalu ringkasan **Schedule**: tiga waktu berikutnya
  (`Next: Mon 19 Oct 06:00 · Tue 20 Oct 06:00 · …`), waktu jadwal terakhir, dan lama aktif.
- Kartu *Nodes* (rule chain) menjelaskan isi pesan jadwal; kartu *State* menampilkan
  ringkasan Schedule yang sama.
- Aturan nonaktif menulis: *The rule is disabled, so it does not run.*
- Baris log `Skipped` tampil dengan badge bergaris putus-putus dan ikon ⏭.
- Kotak *Last evaluation* (ECA dengan kondisi) memperlihatkan nilai dan hasil setiap kondisi
  pada jadwal terakhir, termasuk saat dilewati.

---

## 7. Format JSON (ekspor/impor, lanjutan tahap 13)

ECA:

```json
{
  "format": "iot-platform.rule", "version": 1, "model": "ECA",
  "name": "Morning watering", "description": "...", "enabled": false,
  "eca": {
    "event": "SCHEDULE",
    "schedule": {
      "kind": "DAILY",
      "times": ["06:00"],
      "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      "interval_minutes": null,
      "run_at": null,
      "active_for_seconds": 600
    },
    "device": null, "silence_seconds": null,
    "duration_seconds": 0, "cooldown_seconds": 0, "max_age_seconds": 3600,
    "when": [[
      {"device": "Greenhouse Node", "variable": "soil_moisture", "operator": "<", "threshold": 40.0},
      {"device": "Greenhouse Weather Station", "variable": "rain", "operator": "<", "threshold": 0.5}
    ]],
    "clear": []
  },
  "actions": [
    {"kind": "SET_ACTUATOR", "run_on": "ACTIVATE", "device": "Greenhouse Node", "variable": "pump", "value": 1.0},
    {"kind": "SET_ACTUATOR", "run_on": "CLEAR", "device": "Greenhouse Node", "variable": "pump", "value": 0.0}
  ]
}
```

Rule chain: bagian `flow` mendapat `trigger` dan (bila `SCHEDULE`) `schedule`, tanpa
`active_for_seconds`:

```json
"flow": {
  "trigger": "SCHEDULE",
  "schedule": {"kind": "DAILY", "times": ["07:00"], "weekdays": ["mon", "tue", "wed", "thu", "fri"],
               "interval_minutes": null, "run_at": null},
  "device": "Cold Storage Unit",
  "nodes": [ ... ],
  "links": [ ... ]
}
```

| Field | Isi |
|---|---|
| `kind` | `DAILY`, `INTERVAL`, `ONCE` |
| `times` | daftar `"HH:MM"` (DAILY) |
| `weekdays` | `mon` … `sun` (atau angka 0–6); tidak ada = setiap hari |
| `interval_minutes` | 1–1440 (INTERVAL) |
| `run_at` | jam lokal server `"2026-10-20T06:00"` (ONCE); boleh juga ISO dengan offset |
| `active_for_seconds` | khusus ECA |

`next_run_at` dan `last_run_at` **tidak** diekspor: keduanya keadaan, bukan definisi, dan
dihitung ulang saat aturan hasil impor disimpan. File rule chain dari tahap 13 (tanpa
`trigger`) tetap bisa diimpor dan dianggap `TELEMETRY`.

Impor melewati form yang sama (tahap 13, konsep 2), jadi semua validasi jadwal di atas ikut
berlaku, dengan pesan seperti `Schedule times: “25:00” is not a time like 06:00`.

---

## 8. Mencoba

### Contoh dari seed

```bash
python manage.py migrate
python manage.py seed_rules          # menambah dua contoh baru; contoh lama dilewati
```

| Aturan | Pemilik | Isi | Bawaan |
|---|---|---|---|
| **Morning watering** (ECA) | siti | setiap hari 06:00, bila tanah < 40 % dan tidak hujan: pompa ON 10 menit | nonaktif (pompa yang sama dengan *Smart irrigation*) |
| **Cold room daily report** (FLOW) | rina | Sen–Jum 07:00: email suhu & kelembapan terakhir; alarm bila data tidak ada | nonaktif (mengirim email) |

### Mencoba tanpa menunggu pagi

1. Login sebagai **siti** → Rules → **Smart irrigation** → *Disable* (supaya tidak berebut
   pompa).
2. Buka **Morning watering** → *Edit*. Ubah jam menjadi dua menit dari sekarang, mis.
   `14:32`, dan *Keep active for* `60`. Centang *Enabled* → *Save rule*.
   Kartu Schedule menampilkan `Next: … 14:32`.
3. Pastikan worker berjalan di terminal lain:

   ```bash
   python manage.py rule_engine
   ```

4. Kirim data tanah kering dan tidak hujan (data harus lebih baru dari 3600 s):

   ```bash
   python manage.py send_telemetry "Greenhouse Node" soil_moisture=30
   python manage.py send_telemetry "Greenhouse Weather Station" rain=0
   ```

5. Pukul 14:32 terminal worker menulis `Rule "Morning watering" activated`, pompa menjadi ON
   (halaman device), dan satu menit kemudian `cleared` → pompa OFF.
6. Ulangi dengan `soil_moisture=60`: log mencatat `Skipped: 14:34 run skipped: condition is
   false (...)`.
7. Hentikan worker (Ctrl+C) melewati jam jadwal lebih dari 5 menit, lalu jalankan lagi: log
   mencatat `Missed the … run`.

Untuk rule chain: login sebagai **rina**, buka **Cold room daily report**, ubah jam ke dua menit
dari sekarang, aktifkan, kirim `send_telemetry "Cold Storage Unit" temperature=4.5 humidity=70`,
lalu lihat email di terminal `runserver`/worker (bila `EMAIL_HOST` kosong, email dicetak ke
terminal proses yang menjalankan aturan — di sini worker).

### Tes otomatis

```bash
python manage.py test rules.tests_schedule
```

Isi tesnya:

| Kelompok | Yang diuji |
|---|---|
| `NextRunTests` | perhitungan waktu DAILY/INTERVAL/ONCE, lompat akhir pekan, interval 90 menit, teks ringkasan |
| `EcaScheduleTests` | aktif tepat waktu lalu clear setelah `active_for`, kondisi salah/tidak diketahui → Skipped, jadwal terlewat, grace period, `active_for = 0`, masih aktif → Skipped, interval, telemetri tidak memicu, aturan nonaktif, klaim hanya sekali, alarm tanpa device, `{time}` |
| `FlowScheduleTests` | pesan jadwal berjalan di graf, telemetri tidak memicu, node alarm tanpa device, placeholder waktu, jadwal terlewat |
| `ScheduleFormTests` | impor/ekspor bolak-balik, semua pesan validasi, kondisi opsional, beralih ke telemetri menghapus jadwal, Enable menghitung dari sekarang, halaman detail/edit |
| `SeededScheduleTests` | contoh seed, ekspor-impor bolak-balik, laporan harian benar-benar mengirim email |

---

## 9. Deploy ke server

Tidak ada layanan baru: penjadwalan berjalan di `iot-rule-engine.service` yang sudah ada.

```bash
sudo /srv/iot/app/deploy/scripts/update.sh   # ambil kode + migrate + collectstatic + restart
```

`update.sh` menjalankan `migrate` (membuat tabel `rules_ruleschedule`) dan me-restart semua layanan (`iot.target`), termasuk worker.
Opsional, tambahkan ke `/srv/iot/app/.env`:

```bash
RULE_SCHEDULE_GRACE_SECONDS=300
```

Periksa:

```bash
sudo journalctl -u iot-rule-engine -f      # baris "Rule ... activated" pada jam jadwal
```

Pastikan jam server benar (`timedatectl` → `System clock synchronized: yes`). Zona waktu
sistem operasi tidak berpengaruh; yang dipakai `TIME_ZONE` Django.

---

## 10. Batasan dan catatan desain

- **Ketelitian = `RULE_ENGINE_TICK`** (bawaan 1 s). Jadwal 06:00:00 berjalan antara
  06:00:00 dan 06:00:01.
- **Satu jadwal per aturan.** Untuk "Sen–Jum 06:00 dan Sab–Min 08:00", buat dua aturan,
  atau satu rule chain DAILY 06:00 & 08:00 dengan node `CHECK` pada `weekday`/`hour`.
- **Tidak ada ekspresi cron.** Tiga bentuk di atas mencakup hampir semua kebutuhan dan
  bisa diisi lewat form tanpa menghafal sintaks `*/15 6-18 * * 1-5`.
- **Menyimpan atau menonaktifkan aturan yang sedang ACTIVE** mengembalikannya ke IDLE
  *tanpa* menjalankan aksi clear (sama dengan tahap 7). Bila pompa sedang ON karena jadwal,
  matikan pompa secara manual dari halaman device.
- **Perubahan jam musim panas (DST)** tidak berlaku untuk Asia/Jakarta. Untuk zona dengan
  DST, jam yang tidak ada (mis. 02:30 saat jam maju) ditangani `make_aware` Django.
- File yang terlibat:

| File | Isi |
|---|---|
| `rules/models.py` | `RuleSchedule`, `EcaSpec.Event.SCHEDULE`, `FlowSpec.trigger`, `RuleExecution.Transition.SKIPPED` |
| `rules/schedules.py` | perhitungan waktu (`next_run`), `due`, `claim`, `log_skipped` |
| `rules/engines/eca.py` | `run_schedules`, `fire_schedule`, `clear_expired`, `schedule_device` |
| `rules/engines/flow.py` | `schedule_message`, `FlowEngine.tick` / `run_schedule` |
| `rules/forms.py` | `RuleScheduleForm`, `schedule_checks`, `FlowSpecForm.trigger` |
| `rules/views.py` | `build_schedule_form`, `save_schedule`, `schedule_context`, toggle |
| `rules/portable.py` | `schedule` di JSON ekspor/impor |
| `rules/templates/rules/includes/schedule_fields.html`, `schedule_summary.html` | tampilan |
| `rules/migrations/0004_rule_schedule.py` | migrasi |
| `rules/tests_schedule.py` | 40 tes |
