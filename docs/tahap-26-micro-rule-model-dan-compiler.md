# Tahap 26 — Micro Rule: Model, Compiler, dan Strategi Pembanding

Tahap ini mengimplementasikan `docs/rencana-tahap-26-micro-rule.md` (strategi AE, evaluasi di
aktuator) dan `docs/rencana-tahap-26b-evaluator-di-sensor.md` (strategi SE, evaluasi di sensor).
Yang berubah **hanya platform**: aturan ECA bisa dianalisis, dipecah menjadi fragmen per device,
dibandingkan dalam dua strategi, dan disimulasikan. Firmware belum menjalankan apa pun; mesin
aturan di cloud tidak berubah perilakunya.

## 1. Ringkasan

| Bagian | Isi |
|---|---|
| `rules/edge/` | IR, analisis, penempatan AE/SE, compiler, frame ESP-NOW, evaluator referensi, pengirim/penerima STATE, metrik, simulator, test vector, diagram, layanan |
| Model | `Rule.edge_enabled`, `Rule.edge_strategy`, `EdgePlan`, `EdgeFragment` (migrasi `rules 0007_edge_plan`) |
| Halaman | Form ECA: *Also run on my devices (preview)*. Halaman aturan: bagian **Run on devices** (tabel perbandingan, tab AE/SE, diagram, fragmen, unduhan). Halaman device: **Rules that can run on this device**. Dokumentasi pengguna: `/docs/edge-rules/` |
| Perintah | `export_microrule_vectors`, `edge_report`, `edge_simulate`, `fill_test_macs` (MAC tiruan), `seed_edge_rules` (19 contoh aturan), `edge_batch` (simulasi massal, lihat `docs/metrik_dan_simulator.md` bagian 12) |
| Firmware | `firmware/microrule/` (JSON Schema fragmen, 20 test vector, README) |
| Pengaturan | `MICRO_RULES` di `.env`: `all` (semua pengguna), `admin` (hanya Admin), `off` (tidak tampil); kosong = `all` bila `DJANGO_DEBUG=True`, `off` bila tidak. Lihat `deploy-vps.md` bagian 14.6 |
| Tes | `rules/tests_edge.py` (54), `rules/tests_edge_se.py` (14), `devices/tests_test_macs.py` (4) |

## 2. Alur

```
Simpan aturan ECA (form / impor JSON)
  └─ save_eca_forms() ─► edge_after_save() ─► services.compile_and_store()
                                               (hanya bila fitur aktif DAN edge_enabled)
        untuk strategi "actuator" dan "sensor":
          analyze_rule()  = from_rule() ─► analyze_ir() + access_items() ─► compile_plan() ─► plan_items()
          + budget_items() (batas per device lintas aturan)
          ─► store(): revisi baru hanya bila hash berubah
```

Halaman aturan selalu menganalisis ulang secara langsung (`rule_context`), lalu membandingkan
hash dengan revisi tersimpan. Bila berbeda (mis. MAC device berubah), muncul peringatan dan tombol
**Update saved plan** (`POST /rules/<pk>/edge/compile/`). Kegagalan kompilasi saat menyimpan aturan
hanya dicatat di log dan tidak menggagalkan penyimpanan aturan.

## 3. IR dan hash (`ir.py`)

Bentuk IR sama dengan rencana bagian 4. Tambahan: `event`, `primary` (sinyal dari kondisi pertama,
dipakai kebijakan SE `primary`), dan `variable` (pk variabel, untuk tautan). Nama device tidak masuk
IR, MAC masuk. `rev` rencana = hash dari `{ir, strategy, policy}`, sehingga AE dan SE untuk aturan
yang sama punya `rev` berbeda.

## 4. Analisis (`analyze.py`)

Kode yang diimplementasikan:

| Kode | Level | Kode | Level |
|---|---|---|---|
| `MODEL` | block | `LIMIT_FRAGMENT`, `LIMIT_FRAME` | block |
| `EVENT_SCHEDULE`, `EVENT_INACTIVITY` | block | `LIMIT_PUBLISHED`, `LIMIT_SOURCES` | block |
| `NO_CONTROL` | block | `KEY_HASH` (per aturan dan lintas aturan) | block |
| `NO_MAC` | block | `DEVICE_BUDGET` | warn |
| `ACCESS` | block | `SE_RELAY`, `SE_EXTRA_CRITICAL`, `SE_MULTI_ACT`, `SE_SAME_AS_AE` | info |
| `LIMIT_SIGNALS`, `LIMIT_GROUPS`, `LIMIT_CONDITIONS`, `LIMIT_ACTIONS` | block | `SE_ALWAYS_ON` | warn |
| `MAX_AGE_ZERO` | warn | `AE_REPLICATED` | info |
| `MAX_AGE_RANGE` | block | `CLOUD_ACTION`, `COMMAND_HANDLER` | info |
| `FLOAT_EQ` | warn | | |

`ACCESS` memakai fungsi yang sama dengan mesin cloud (`can_watch` untuk sensor, `access.can(...,
OPERATOR)` untuk aktuator). Batas berasal dari `rules/edge/limits.py` dan bisa ditimpa lewat
`settings.MICRO_RULES`.

## 5. Penempatan dan fragmen (`placement.py`, `compile.py`)

- **AE**: setiap device aktuator menjadi evaluator dan hanya membawa aksinya sendiri.
- **SE**: satu sensor menjadi evaluator. Bila ada sumber yang juga aktuator, device itu dipilih.
  Selain itu kebijakan `primary` (bawaan), `most_signals`, atau `min_traffic` (butuh laju sampel).
  Akibatnya, bila device aktuator juga mengukur salah satu variabel aturan dan aktuatornya hanya
  satu, rencana SE **sama dengan AE**: fragmennya identik kecuali `rev` dan `strategy` (hash
  memasukkan strategi). Halaman aturan menjelaskannya dengan item `SE_SAME_AS_AE`. Untuk
  perbandingan di paper, pakai aturan yang aktuatornya tidak mengukur variabel aturan.
- Konsekuensi aturan "sumber sekaligus aktuator": bila device aktuator juga mengukur salah satu
  variabel aturan, SE memilih device itu sebagai evaluator. Dengan satu aktuator, rencana SE
  identik dengan AE kecuali `rev` dan `strategy` (keduanya ikut di-hash supaya device tahu rencana
  mana yang dipegangnya). Halaman aturan menandainya dengan item `SE_AT_ACTUATOR`.
- Sensor yang datanya dipakai evaluator di device lain mendapat bagian `publish` (broadcast,
  tanpa daftar penerima dan tanpa rule id). Sinyal dikenali lewat `kh` = CRC-16/CCITT-FALSE nama
  variabel, mis. `soil_moisture` = `0x6511`, `temperature` = `0xFF0F`.
- Peran digabung: `src`, `eval`, `act`, `src+eval`, `src+act`. Semua fragmen memakai `fmt:
  "microrule/1"`; `role` menentukan bagian yang ada (lihat `firmware/microrule/schema/`).
- `heartbeat` produsen = `max(5, max_age // 3)` detik (30 bila `max_age = 0`).
- SE: evaluator mendapat blok `emit` (heartbeat 10 s, 3 kali ulang berjarak 100 ms); aktuator
  mendapat `evaluator`, `state_timeout` (3 x heartbeat) dan `on_timeout: "hold"`.
- `publish_table(mac, fragmen...)` menggabungkan bagian `publish` lintas aturan untuk satu device
  (dipakai halaman device; disimpan saat distribusi tahap 27).

Ukuran untuk aturan irigasi contoh (3 device): AE = S 175 B, T 173 B, P 560 B; SE = S 491 B,
T 171 B, P 294 B. Frame DATA 25 byte, frame STATE 29 byte.

## 6. Frame (`wire.py`)

Sesuai rencana (DATA 17 + 8n byte, STATE 29 byte, tag HMAC 8 byte berisi nol). Umur sampel
dibulatkan setengah ke atas ke satuan 100 ms (`(age + 50) / 100`), sama dengan pembulatan
integer yang wajar di C.

## 7. Evaluator referensi (`evaluator.py`) dan temuan semantik

Evaluator memakai fungsi cloud apa adanya: `conditions.check/all_of/any_of`, `models.compare`,
`engines.eca.step`, dan fungsi baru `conditions.decide()`. `decide()` adalah potongan dari
`EcaEngine.judge()` yang dipindah ke `rules/conditions.py` supaya cloud dan evaluator memakai
satu kode untuk "boleh aktif / boleh pulih" (perilaku cloud tidak berubah; semua tes lama lulus).

**Temuan saat uji kesetaraan:** cloud menilai aturan pada setiap data baru, tetapi pada tick hanya
aturan yang **PENDING**. Versi awal evaluator menilai di setiap tick, dan uji acak menemukan
perbedaan: dengan histeresis, ekspresi clear yang menjadi "tidak diketahui" karena nilainya usang
membuat `decide()` mengizinkan aktivasi, sehingga evaluator berpindah ke PENDING pada tick
sedangkan cloud baru pada data berikutnya. Evaluator kini meniru cloud persis:
`step()` untuk data baru, `tick()` hanya bila PENDING. Aturan yang sama wajib diikuti firmware
(ditulis di `firmware/microrule/README.md`).

Uji `ParityTests.test_random_rules`: 1.000 aturan acak (1–3 sinyal, 1–3 grup, semua operator,
histeresis acak, durasi/cooldown/max_age acak) x 40 kejadian; transisi dan status evaluator sama
dengan jalur cloud (`conditions.evaluate` + `decide` + `step`) di setiap langkah.

## 8. Strategi SE: emitter dan aktuator

- `StateEmitter`: saat transisi mengirim STATE `repeat` kali, di luar itu setiap `heartbeat`.
- `ActuatorRuntime`: menerima hanya dari MAC evaluator, rule dan rev yang cocok, counter yang naik;
  menjalankan aksi hanya bila keadaan berbeda dari yang berlaku; menghitung transisi terlewat
  (`missed`); `restart()` membuat keadaan tidak diketahui (`None`) sampai frame berikutnya;
  `tick()` menjalankan `on_timeout` bila evaluator diam lebih dari `state_timeout`.

## 9. Metrik dan simulator

Penjelasan lengkap (rumus, algoritma, metode statistik, validitas): `docs/metrik_dan_simulator.md`.

`metrics.plan_metrics()` (ditampilkan di tabel perbandingan halaman aturan dan `edge_report`):
device kritis, hop terpanjang, frame per menit saat tenang, kemungkinan divergensi, sensor yang
harus selalu menyala, jumlah dan ukuran fragmen.

`sim.py` menjalankan fragmen hasil compiler dengan kode runtime yang sama, ditambah model udara
(kehilangan independen per penerima, latensi 5–30 ms) dan kegagalan device. "Kebenaran" adalah
evaluator yang menerima semua sampel seketika. Contoh (rata-rata 10 run, 1 jam, sampel tiap 5 s;
nilai = waktu salah keadaan / transisi terlewat / latensi rata-rata transisi yang cocok):

| Aturan | Skenario | AE | SE |
|---|---|---|---|
| Irigasi (S wajib di semua grup) | normal | 0,02 % / 0 % / 183 ms | 0,00 % / 0 % / 20 ms |
| | loss 20 % | 1,08 % / 9 % / 1393 ms | 1,03 % / 4 % / 101 ms |
| | S mati | 42,0 % / 89 % | 44,5 % / 89 % |
| | T mati | 5,9 % / 30 % | 5,9 % / 30 % |
| S < 5 ATAU T > 35, clear T < 30 | normal | 0,01 % / 0 % / 18 ms | 0,02 % / 0 % / 34 ms |
| | loss 20 % | 1,27 % / 15 % | 1,03 % / 18 % |
| | **S (evaluator SE) mati** | **5,6 % / 52 %** | **39,9 % / 78 %** |
| | T mati | 38,9 % / 77 % | 38,9 % / 77 % |

Catatan untuk paper:

- Keterbatasan L1 (titik kegagalan tambahan) baru terlihat bila sensor evaluator SE **tidak**
  wajib bagi aturan. Bila sensor itu wajib (irigasi), kedua strategi sama-sama gagal.
- SE bisa lebih cepat bila sensor evaluatornya adalah pemicu utama: durasi mulai dihitung
  tanpa hop radio, dan pada AE frame yang hilang menunda deteksi sampai sampel berikutnya.
  Perbandingan yang adil perlu melaporkan latensi per jalur sinyal.
- Kehilangan sensor menyebabkan aturan "macet" di kedua strategi karena semantik cloud: nilai
  "tidak diketahui" tidak pernah memulihkan aturan. Ini bukan bug compiler; kebijakan aman saat
  data usang (mis. pulih otomatis setelah N detik tanpa data) adalah kandidat fitur tahap 29.
- Jejak sensor masih sintetis (random walk). `edge_simulate --sample-file` menerima jejak nyata.

Contoh:

```bash
python manage.py edge_report --out edge.csv
python manage.py edge_simulate --rule 12 --loss 0,0.05,0.2 --kill "Soil node@600" --runs 10 --out sim.csv
python manage.py edge_simulate --rule 12 --strategy sensor --policy most_signals --out sim_most.csv
```

## 10. Test vector firmware

`firmware/microrule/vectors/`: 11 skenario evaluator, 6 skenario aktuator, 3 vector wire
(CRC-16, DATA, STATE). Uji `VectorTests` gagal bila file di repo berbeda dari hasil generator,
dan memvalidasi fragmen terhadap JSON Schema dengan validator kecil (tanpa paket `jsonschema`).
Setelah mengubah evaluator atau format frame: `python manage.py export_microrule_vectors`.

## 11. Perbedaan dari rencana

| Rencana | Implementasi | Alasan |
|---|---|---|
| API v2 `GET /api/v2/rules/<id>/edge-plan` | Unduhan JSON/zip di `/rules/<pk>/edge/<strategi>/` (login web) | API v2 memakai OAuth dan scope; endpoint aturan butuh scope baru. Unduhan sudah cukup untuk skrip paper |
| `Device.capabilities` dari laporan firmware (`caps`) | Belum | Opsional; ditunda ke tahap 27 bersama firmware yang mengirimnya |
| Fragmen aktuator `fmt: "microrule-act/1"` | Semua fragmen `microrule/1`, dibedakan `role` | Satu skema, satu parser di firmware |
| Penilaian di setiap tick | Tick hanya bila PENDING | Kesetaraan dengan cloud (bagian 7) |
| Contoh `kh` di rencana (`0x5A3C`, `0x91E0`) | Nilai sebenarnya `0x6511`, `0xFF0F` | Contoh rencana hanya ilustrasi |

## 12. Mencoba tanpa board ESP32: MAC tiruan

Analisis memblokir aturan yang device-nya belum melaporkan MAC (`NO_MAC`). Untuk mencoba fitur
dengan device simulasi atau data contoh, isi MAC tiruan:

```bash
python manage.py fill_test_macs --dry-run          # lihat dulu device mana yang akan diisi
python manage.py fill_test_macs                    # semua device yang MAC-nya masih kosong
python manage.py fill_test_macs --owner siti       # hanya device milik satu pengguna
python manage.py fill_test_macs --random           # MAC acak murni (bawaan: diturunkan dari token)
```

- Bawaan, MAC diturunkan dari access token (`02:` + 10 hex SHA-256 token), sama dengan
  `scripts/fake_mqtt_device.py` dan MQTT client, sehingga tidak berubah ketika device tiruan itu
  melaporkan firmware.
- Awalan `02` = *locally administered*, tidak bentrok dengan MAC kartu jaringan asli.
- `mac_reported_at` dibiarkan kosong; kartu Firmware menulis "test value, not reported by the
  device yet". Laporan firmware dari device sungguhan menimpa MAC tiruan.
- MAC yang sudah terisi tidak diubah kecuali dengan `--all`.
- Rencana micro rule aturan yang memakai device tersebut langsung dibuat ulang.
- Di server (`DEBUG=False`) perintah meminta `--yes`.

### Contoh aturan untuk semua kemungkinan

```bash
python manage.py seed_edge_rules                          # pemilik bawaan: siti; budi = pemilik device yang dibagikan
python manage.py seed_edge_rules --owner <username> --neighbour <username-lain>
python manage.py seed_edge_rules --reset                  # hapus aturan "Edge ..." pemilik lalu buat ulang
```

Membuat 14 device berawalan "Edge " (dengan MAC tiruan, kecuali "Edge New Sensor") dan 19 aturan,
semuanya dengan "Also run on my devices":

| Aturan | Yang ditunjukkan | Hasil |
|---|---|---|
| 01 Local loop | sensor dan aktuator satu board, tanpa radio; AE = SE | FULL |
| 02 One remote sensor | satu sensor ke aktuator di board lain; SE: sensor harus selalu menyala | FULL |
| 03 Two sensors, both required | AND lintas board; SE butuh dua hop | FULL |
| 04 Either sensor | OR lintas board; SE menambah device kritis (L1) | FULL |
| 05 Timing | "for at least" 60 s, cooldown 300 s, histeresis | FULL |
| 06 Two actuators | AE tereplikasi (bisa berbeda sesaat) vs SE satu keputusan | FULL |
| 07 Sensor at the actuator | SE memilih aktuator; rencana AE dan SE identik | FULL |
| 08 Evaluator policy | SE `primary` = Light Sensor, `most_signals` = Weather Station | FULL |
| 09 Command action | Send command `boost` (perlu handler di firmware) | FULL |
| 10 Control plus notifications | sirene di device; alarm + email di platform (nonaktif bawaan) | PARTIAL |
| 11 Shared broadcast | humidity dipakai aturan 08 dan 11; heartbeat gabungan 10 s | FULL |
| 12 No age limit | `max_age = 0` | FULL + peringatan |
| 13 Fractional equality | `= 12.5` | FULL + peringatan |
| 14 Scheduled | event Schedule | NONE |
| 15 Device silent | event "device sends no data" | NONE |
| 16 Notification only | tanpa aksi kendali | NONE |
| 17 Missing MAC | board tanpa MAC | NONE |
| 18 Too many variables | 9 variabel (batas 8) | NONE |
| 19 No control access | aktuator milik pengguna lain, dibagikan sebagai Viewer | NONE |

Tes `SeedEdgeRulesTests` memastikan setiap aturan menghasilkan verdict dan kode analisis yang
dijelaskan di tabel ini.

## 13. Langkah berikut (tahap 27)

Distribusi fragmen dan tabel publikasi lewat topik MQTT retained + ack (`EdgeFragment` →
status terkirim/diterima), runtime C++ yang lulus semua test vector, `caps` di laporan firmware,
dan mesin cloud yang melewati aksi kendali yang sudah ditangani device ("edge-always").
