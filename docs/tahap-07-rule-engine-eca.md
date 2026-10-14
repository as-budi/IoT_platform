# Tahap 7 — Rule engine: Event–Condition–Action (ECA)

Tahap ini menambahkan **rule engine**: aturan yang bereaksi otomatis terhadap data device,
misalnya "bila kelembapan tanah < 20 % selama 60 detik, nyalakan pompa". Penjelasan umum
tentang rule engine dan keenam modelnya ada di [rule-engine-iot.md](rule-engine-iot.md).

```
device ──HTTP/MQTT/CoAP──► save_telemetry() ──► MySQL
                                │
                                └─publish()─► antrean Redis ──► worker `rule_engine`
                                                                   │  nilai aturan ECA
                                                                   ▼
                                            set aktuator · command · alarm · email
```

## 1. Enam model, satu yang diimplementasikan

Halaman **Rules → New rule** menampilkan keenam model sebagai pilihan:

| Model | Status di tahap ini | File |
|---|---|---|
| Event–Condition–Action | **tersedia** | `rules/engines/eca.py` |
| Flow-based rule chain | direncanakan (tersedia sejak [tahap 8](tahap-08-rule-chain.md)) | `rules/engines/flow.py` |
| SQL over the message stream | direncanakan | `rules/engines/sql.py` |
| Complex event processing | direncanakan | `rules/engines/cep.py` |
| Production rules (RETE) | direncanakan | `rules/engines/production.py` |
| State machine | direncanakan | `rules/engines/state_machine.py` |

Setiap model adalah satu class turunan `RuleEngine` (`rules/engines/base.py`) dengan dua
method:

- `on_message(message, now)`: ada pesan baru, misalnya telemetri.
- `tick(now)`: dipanggil berkala, untuk aturan yang bergantung pada waktu.

Daftar `ENGINES` di `rules/engines/__init__.py` memuat keenamnya. Worker hanya memanggil
mesin yang `implemented = True`. Model yang masih direncanakan sudah punya ringkasan, contoh,
dan rencana implementasi di docstring file-nya. Pilihannya tampil di UI dengan tombol
"Coming in a later stage". Field `Rule.model` menyimpan model sebuah aturan.

Untuk mengimplementasikan model lain nanti, langkahnya:

1. Isi `on_message`/`tick` di file model itu.
2. Ubah `implemented` menjadi `True`.
3. Buat tabel spesifikasi dan form untuk model itu.

Tabel aksi, keadaan, log, dan alarm dipakai bersama.

## 2. Bentuk aturan ECA

```
WHEN  new data for Soil moisture, Temperature                        (event)
IF    (Soil moisture < 20 AND Temperature > 30) OR Soil moisture < 10
      for 60 s                                                        (condition + durasi)
THEN  set Water pump to ON; raise a major alarm                       (aksi "when the rule activates")
CLEAR when Soil moisture > 30                                         (histeresis)
THEN  set Water pump to OFF; clear the alarm                          (aksi "when the rule clears")
```

> Mekanisme lengkap, termasuk logika tiga nilai, query database, dan contoh linimasa, ada di
> **[eca-mekanisme.md](eca-mekanisme.md)**.

| Bagian | Tempat | Keterangan |
|---|---|---|
| Event | `EcaSpec.event` | `TELEMETRY` (data baru untuk salah satu variabel di kondisi) atau `INACTIVITY` (device tidak mengirim data) |
| Condition | tabel `EcaCondition`, role `WHEN` | grup-grup kondisi `variabel op ambang`: AND di dalam grup, OR antar-grup. Operator `>`, `≥`, `<`, `≤`, `=`, `≠`; nilai on/off memakai 1/0 |
| Clear | `EcaCondition`, role `CLEAR` | opsional, bentuk sama; kosong = pulih begitu kondisi bernilai salah |
| Durasi | `EcaSpec.duration_seconds` | kondisi harus bertahan sekian detik |
| Cooldown | `EcaSpec.cooldown_seconds` | jeda minimum antara dua aktivasi |
| Batas umur data | `EcaSpec.max_age_seconds` | nilai variabel yang lebih tua dari ini dianggap *tidak diketahui* |
| Device diam | `EcaSpec.device`, `silence_seconds` | untuk event `INACTIVITY` |

Aksi (`RuleAction`, boleh lebih dari satu, masing-masing dijalankan saat *activate* atau
*clear*):

| Aksi | Isi | Catatan |
|---|---|---|
| Set actuator | aktuator + nilai | memakai `Variable.set_desired()`; dilewati bila desired sudah sama |
| Send command | device + method + params + masa berlaku | membuat `Command` biasa, dikirim lewat HTTP/MQTT/CoAP seperti biasa |
| Raise alarm | keparahan + pesan | dibuat saat aktif, **di-clear otomatis** saat aturan pulih |
| Send email | penerima + pesan | penerima kosong = email pemilik aturan |

Pesan alarm/email boleh memakai placeholder: `{rule}`, `{device}`, `{variable}`, `{value}`,
`{threshold}`, dan `{values}`. `{variable}` dan `{value}` merujuk ke variabel yang datanya
memicu penilaian. Untuk aturan dengan beberapa variabel, `{values}` lebih jelas karena
menuliskan semuanya, misalnya `Soil moisture = 15, Temperature = 32`.

## 3. Mekanisme: aturan sebagai mesin keadaan kecil

Setiap aturan punya `RuleState` di database dengan tiga status:

```
            kondisi terpenuhi               durasi & cooldown terlewati
    IDLE ──────────────────────► PENDING ────────────────────────────► ACTIVE
      ▲                             │                                      │
      └─── kondisi tak terpenuhi ───┘                                      │
      └──────────────────────── kondisi clear terpenuhi ◄──────────────────┘
```

Logikanya ada di satu fungsi tanpa database, `step()` di `rules/engines/eca.py`, supaya
mudah dibaca dan diuji. Aksi **hanya** dijalankan saat berpindah ke ACTIVE atau kembali ke
IDLE, tidak pada setiap pesan. Sifat ini menyelesaikan beberapa masalah dari
rule-engine-iot.md bagian 4:

- **Flapping.** Durasi, histeresis, dan cooldown menahan aturan agar tidak menyala-padam
  terus. Untuk aturan satu variabel, form menolak histeresis yang tumpang tindih, misalnya
  aktif < 20 dan clear > 15 (nilai 17 memenuhi keduanya). Untuk ekspresi gabungan, mesin
  tidak mengaktifkan aturan selama ekspresi clear juga benar.
- **Loop.** Aksi *set pump = ON* membuat device melapor `pump = 1`. Laporan itu bisa memicu
  penilaian lagi, tetapi aturan sudah ACTIVE, jadi tidak ada aksi baru.
- **Idempotensi.** *Set actuator* dilewati bila desired sudah bernilai sama. *Raise alarm*
  tidak membuat alarm kedua selama alarm aturan itu masih aktif.
- **State tahan restart.** `RuleState` disimpan di database, sehingga worker yang di-restart
  tetap ingat "sudah PENDING sejak 10:03:12".
- **Satu aturan dinilai satu proses.** Baris `RuleState` dikunci dengan
  `select_for_update()` selama penilaian.

Aksi dijalankan **setelah** keadaan baru tersimpan. Bila satu aksi gagal (misalnya SMTP
mati), aksi lain tetap jalan. Hasil setiap aksi dicatat di `RuleExecution` dan tampil di
*Execution log* halaman aturan. Pengecekan hak akses diulang saat aksi dijalankan: aturan
hanya boleh menyentuh device yang boleh dilihat pemiliknya.

Waktu yang dipakai adalah **waktu proses** (saat worker menilai), bukan `ts` dari device.
Data lama yang dikirim belakangan (buffer saat Wi-Fi putus) tetap dinilai sebagai data baru.
Penanganan *event time* menjadi bagian model CEP.

## 4. Antrean dan worker

`save_telemetry()` membuat **pesan** seragam (`rules/queue.py`), mengikuti pola ThingsBoard:

```json
{"type": "TELEMETRY",
 "originator": {"device": 3},
 "data": [{"variable": 7, "key": "soil_moisture", "value": 18.5, "ts": "2026-10-11T03:47:25+00:00"}],
 "metadata": {"protocol": "MQTT", "received_at": "2026-10-11T03:47:25+00:00"}}
```

Pesan dikirim **setelah commit** (`transaction.on_commit`), sama seperti kabar WebSocket.
Tujuan pesan ditentukan oleh `RULE_ENGINE_MODE`:

| Mode | Bawaan bila | Jalannya |
|---|---|---|
| `worker` | `REDIS_URL` diisi | `RPUSH` ke list Redis `rule-engine:messages`; worker mengambil dengan `BLPOP` (FIFO) |
| `inline` | `REDIS_URL` kosong | dinilai langsung di proses yang menyimpan data, setelah commit |
| `off` | — | rule engine mati |

Mengapa memakai worker?

- Penyimpanan data tetap cepat walaupun ada aksi lambat (email).
- Satu worker memproses pesan **berurutan**, jadi pesan dari device yang sama tidak saling
  mendahului.
- Hanya worker yang menjalankan `tick()` setiap `RULE_ENGINE_TICK` detik. Tanpa tick, aturan
  "selama 60 detik" dan "device diam 5 menit" tidak pernah selesai bila tidak ada data baru.
  Karena itu **mode inline pun tetap membutuhkan worker** untuk bagian waktu.

Bila worker mati, antrean dibatasi 10.000 pesan (`LTRIM`). Saat `manage.py test`, mode
dipaksa `inline` agar data tes tidak masuk antrean Redis sungguhan.

## 5. Alarm

`Alarm` punya siklus hidup dua sumbu, seperti ThingsBoard:

- **active / cleared:** apakah penyebabnya masih ada;
- **unacknowledged / acknowledged:** apakah sudah ada yang menangani.

Alarm dibuat oleh aksi *Raise alarm* dan di-clear oleh aturan itu sendiri saat pulih
(`cleared_by` kosong = oleh aturan). Pengguna bisa melakukan *Acknowledge* dan *Clear* dari
halaman **Alarms** atau halaman device. Alarm aktif yang belum di-acknowledge muncul di tiga
tempat:

- lencana merah di side menu (`rules/context_processors.py`),
- spanduk di dashboard,
- tabel di halaman device.

Setiap perubahan alarm dikirim lewat WebSocket (`{"type": "alarm", "rule": 4, ...}`), lalu halaman
device dan dashboard memuat ulang dirinya.

### Pembaruan langsung di halaman aturan

Halaman detail aturan, daftar aturan, dan daftar alarm memperbarui dirinya tanpa ditekan
refresh. Caranya sama dengan halaman device: WebSocket sebagai jalur utama, ditambah
"versi data" yang diperiksa berkala sebagai cadangan (`static/js/auto_refresh.js`).

- Worker mengirim kabar `{"type": "rule", "rule": 4, "status": "ACTIVE", "transition": "ACTIVATE"}`
  (`rules/events.py`) setiap kali status aturan berubah: IDLE → PENDING, ke ACTIVE, atau
  kembali ke IDLE. Kabar dikirim **setelah** baris log tersimpan, sehingga halaman yang
  memuat ulang langsung menampilkannya.
- Kabar dikirim ke group **device asal** aturan, jadi tidak perlu consumer WebSocket baru.
  - Halaman detail berlangganan `ws/devices/<id>/` milik device itu. Nilai variabel pemicu
    diperbarui di tempat; kabar `rule`/`alarm` untuk aturan ini memuat ulang halaman.
  - Daftar aturan dan daftar alarm berlangganan `ws/dashboard/`.
- Halaman lain (device, variabel, dashboard) mengabaikan kabar `rule`; lihat
  `IGNORED_BY_DEFAULT` di `auto_refresh.js`.
- Cadangan polling: `/rules/updates/`, `/rules/<id>/updates/`, dan
  `/rules/alarms/updates/` mengembalikan versi data, yang berubah bila status, log, atau
  alarm berubah.

Kabar dari proses worker hanya sampai ke browser bila channel layer-nya Redis
(`REDIS_URL` terisi). Tanpa Redis, halaman tetap terbarui lewat pemeriksaan cadangan
(paling lambat 30 detik).

## 6. Struktur kode

| File | Isi |
|---|---|
| `rules/models.py` | `RuleModel`, `Rule`, `EcaSpec`, `RuleAction`, `RuleState`, `RuleExecution`, `Alarm` |
| `rules/engines/` | `base.py`, `eca.py` (implementasi), lima file model yang direncanakan, `ENGINES` |
| `rules/conditions.py` | penilaian ekspresi OR-berisi-AND dengan logika tiga nilai (tanpa database) |
| `rules/actions.py` | empat jenis aksi, `run_actions()`, placeholder pesan |
| `rules/queue.py` | bentuk pesan, `publish()`, Redis `push`/`pop`, `process()` |
| `rules/management/commands/rule_engine.py` | worker `python manage.py rule_engine` |
| `rules/forms.py` | `RuleForm`, `EcaSpecForm` (validasi histeresis), `ActionFormSet` |
| `rules/views.py`, `urls.py`, `templates/rules/` | daftar, pilih model, form ECA, detail, alarm |
| `api/services.py` | `save_telemetry()` kini juga memanggil `rule_queue.publish()` |

## 7. Menjalankan

```bash
conda activate iot_platform
python manage.py migrate            # tabel rules_*
python manage.py runserver          # terminal 1
python manage.py rule_engine        # terminal 2 (worker)
python manage.py simulate_devices --loop   # atau mqtt_bridge / coap_server / fake device
```

Pengaturan di `.env`:

```
RULE_ENGINE_MODE=        # kosong = worker bila REDIS_URL diisi, inline bila tidak
RULE_ENGINE_TICK=1
EMAIL_HOST=              # kosong = email dicetak di terminal proses yang menjalankan aksi
```

Untuk email sungguhan, isi `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, dan `DEFAULT_FROM_EMAIL`. Untuk Gmail, pakai *App Password*.

**Contoh uji cepat dengan Greenhouse Node (akun `siti`):**

1. Rules → New rule → **Use ECA**.
2. Isi aturan:
   - Event: *Greenhouse Node · Soil moisture*.
   - Condition: `<` `20`, *for at least* `5` detik.
   - Clear: `>` `30`.
3. Tambahkan aksi:
   - *Set actuator* → Water pump = 1, dijalankan *when the rule activates*.
   - *Set actuator* → Water pump = 0, dijalankan *when the rule clears*.
   - *Raise alarm*, keparahan Major.
4. Kirim data dari terminal (ganti TOKEN dengan access token device):
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/TOKEN/telemetry -H "Content-Type: application/json" -d '{"soil_moisture": 15}'
   ```
5. Setelah ±5 detik, terminal worker menampilkan
   `Rule "..." activated -> Set actuator: Water pump = ON; Raise alarm: ...`.
   Dashboard menunjukkan pompa "Waiting for device" dan lencana alarm.
6. Kirim `{"soil_moisture": 35}`: aturan *cleared*, pompa diminta OFF, alarm di-clear.

## Latihan

1. Buat aturan tanpa histeresis (clear kosong) dengan ambang 20. Kirim nilai 19, 21, 19, 21.
   Berapa kali pompa berganti? Bandingkan dengan histeresis 20/30.
2. Buat aturan *Device sends no data* 60 detik untuk sebuah device, lalu hentikan device
   tiruannya. Kapan alarm muncul, dan kapan hilang setelah device menyala lagi?
3. Matikan worker, kirim beberapa data, lalu jalankan lagi. Periksa panjang antrean dengan
   `redis-cli llen rule-engine:messages` sebelum dan sesudahnya.
4. Tambahkan placeholder `{unit}` ke pesan alarm (petunjuk: `EcaEngine.context()`).
