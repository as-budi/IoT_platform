# Tahap 18: Log aktivitas dan pencarian aturan

Dua fitur:

1. **Activity log** (`/activity/`, menu *Monitor → Activity log*): riwayat semua kejadian
   penting di platform dalam satu halaman, dengan pencarian dan filter.
2. **Pencarian aturan** di halaman *Rules*: cari aturan lewat kata kunci, misalnya nama
   device, key variabel, isi pesan, atau jenis aksi.

---

## Bagian A — Log aktivitas

### Siapa melihat apa

| Pengguna | Yang terlihat |
|---|---|
| **Admin** (role Admin atau superuser) | semua kejadian dari semua pengguna, dengan kolom *Owner* dan filter *User* |
| Pengguna biasa | kejadian yang menyangkut **device, aturan, dan akunnya sendiri** |

Setiap baris punya dua kolom pengguna yang sengaja dibedakan:

| Kolom | Arti | Contoh |
|---|---|---|
| `owner` | pemilik sumber daya yang dibicarakan; **menentukan siapa boleh melihat** | pemilik device |
| `actor` / *By* | siapa atau apa yang melakukannya | `admin`, `rule engine`, `device`, `system`, `MQTT` |

Contoh: Admin mengirim command `reboot` ke device milik siti. Barisnya ber-owner **siti**
dengan pelaku **admin**. Siti melihat "Command reboot queued … by admin"; pengguna lain
tidak melihatnya.

Aturan aksesnya satu baris (`activity/log.py`):

```python
def visible_events(user):
    events = ActivityEvent.objects.select_related("device", "rule")
    if user.is_admin:
        return events.select_related("owner")
    return events.filter(owner=user)
```

Filter *User* tidak ada di form pengguna biasa. Kalau `?owner=<id orang lain>` diketik manual
di URL, hasilnya tetap hanya baris miliknya sendiri (diuji di
`test_each_user_sees_only_their_own_events_admin_sees_all`).

### Kejadian yang dicatat

| Kategori | Kejadian (`verb`) | Level |
|---|---|---|
| Device | `device.created` / `updated` / `deleted`, `device.token` (token baru), `device.online` / `offline` (tahap 16), `variable.created` / `deleted`, `location.*` | offline, hapus, token: Warning |
| Control | `actuator.set` (dari halaman, dashboard, aturan), `command.created`, `command.succeeded` / `failed` / `expired` | gagal/kedaluwarsa: Warning |
| Rule | `rule.created` / `updated` / `enabled` / `disabled` / `deleted`, `rule.activate` / `clear` / `run` / `skipped` beserta hasil aksinya | aksi gagal: **Error**, skipped: Warning |
| Alarm | `alarm.raised` / `acknowledged` / `cleared` | Critical/Major: Error, lainnya: Warning |
| Firmware | `firmware.queued` … `updated` / `failed` / `canceled` (tahap 9) | gagal: Error |
| Account | `account.login`, `logout`, `login_failed` (dengan alamat IP), `password`, `telegram` | login gagal: Warning |

Yang **sengaja tidak** dicatat:

- setiap baris telemetri, karena bisa jutaan per hari (riwayatnya ada di halaman variabel);
- penilaian aturan yang tidak mengubah apa-apa;
- command berpindah ke *Sent*: hanya dibuat dan hasil akhirnya yang dicatat.

Percobaan login dengan username yang tidak ada tidak punya owner, sehingga hanya terlihat
oleh Admin. Percobaan gagal ke akun yang ada terlihat juga oleh pemilik akun.

### Cara mencatat: sinyal Django

Satu kejadian bisa berasal dari banyak jalur. Aktuator, misalnya, bisa diubah dari halaman
device, dashboard, aksi ECA, atau node rule chain. Daripada menambah `record(...)` di setiap
jalur, `activity/signals.py` mendengarkan **sinyal** model:

```python
@receiver(post_save, sender=DesiredStateLog)
def desired_changed(sender, instance, created, **kwargs):
    if created:
        record("actuator.set", f"{variable.display_name} on {device.name} set to ON", ...)
```

| Sinyal | Untuk |
|---|---|
| `post_save` / `post_delete` | Device, Location, Variable, Rule, RuleExecution, Alarm, Command, DesiredStateLog, DeviceStatusEvent, OtaEvent, TelegramLink |
| `user_logged_in` / `user_logged_out` / `user_login_failed` | akun |

Hampir tidak ada kode lama yang diubah. Pengecualiannya:

- `CommandQuerySet.expire_due()`: `update()` tidak memicu sinyal, jadi sinyalnya dikirim
  sendiri untuk command yang kedaluwarsa;
- ganti password (satu panggilan `record`).

**Siapa pelakunya?** Sinyal tidak tahu request mana yang sedang berjalan. `ActorMiddleware`
menyimpan `request.user` di sebuah *contextvar* selama request berlangsung, dan `record()`
membacanya. Di proses tanpa request (mqtt_bridge, rule_engine, housekeeping) pelakunya
diisi teks, mis. `rule engine` atau `device`.

**Penghapusan berantai.** Bila pengguna dihapus, semua device, aturan, dan log miliknya ikut
terhapus (CASCADE). Sinyal `post_delete` membawa `origin`, yaitu objek yang penghapusannya
dimulai. Hanya penghapusan yang dimulai dari objek itu sendiri yang dicatat ("Device X
deleted"), bukan setiap device yang ikut terhapus. Ini juga mencegah baris log baru menunjuk
ke pengguna yang sedang dihapus.

**Log tidak boleh merusak pekerjaan utama.** `record()` menulis di dalam *savepoint*
(`transaction.atomic()`). Bila penulisan log gagal, hanya log yang batal; data device atau
aturannya tetap tersimpan.

**Baris log berdiri sendiri.** Nama device dan aturan disalin ke kolom teks (`device_name`,
`rule_name`). Bila device dihapus, `device` menjadi NULL (`SET_NULL`), tetapi pesannya tetap
terbaca: "Greenhouse Node is online".

### Halaman

- **Search**: setiap kata harus muncul di pesan, nama device, nama aturan, pelaku, atau kode
  kejadian (AND antar-kata), mis. `pump on`, `offline`, `failed`.
- **Category**, **Level** (*Warnings and errors* / *Errors only*), **Device**, **From** /
  **To** (tanggal, zona waktu server), dan untuk Admin **User**.
- 50 baris per halaman, terbaru di atas. Filter tersimpan di URL, jadi bisa dibagikan, mis.
  `/activity/?device=3&level=WARNING`.
- Halaman device: tautan **All activity of this device →** di atas *Connection history*.

### Ukuran dan retensi

Tabel `activity_activityevent` diberi index `(owner, at)`, `(at)`, dan `(device, at)`,
sesuai query halaman. Baris yang lebih tua dari `ACTIVITY_LOG_DAYS` hari (bawaan **180**,
`0` = simpan selamanya) dihapus worker `housekeeping` (tahap 16) sekali per jam.

### Riwayat sebelum tahap 18

Log baru terisi sejak fitur ini dipasang. Untuk mengisi kejadian lama dari tabel yang sudah
ada (device & aturan dibuat, aktuator, command, eksekusi aturan, alarm, status online,
firmware), dengan waktu aslinya:

```bash
python manage.py activity_backfill --dry-run    # hitung saja
python manage.py activity_backfill
```

Hanya kejadian yang lebih lama dari baris log pertama yang ditambahkan, jadi perintah ini
aman dijalankan dua kali. Login lama tidak bisa dipulihkan karena memang tidak pernah
disimpan.

---

## Bagian B — Pencarian aturan

Halaman *Rules* mendapat kotak pencarian dan dua pilihan:

```
[ greenhouse pump            ] [All models ▾] [Any state ▾] [Search]  Clear
4 of 5 rules match "greenhouse pump".
```

Satu kata cocok bila muncul (tanpa membedakan huruf besar/kecil) di salah satu bagian aturan:

| Bagian | Kolom |
|---|---|
| Aturan | nama, deskripsi, model (`ECA`, `FLOW`) |
| Kondisi ECA | key dan label variabel, nama device; device yang diamati event "device diam" |
| Aksi ECA | jenis (`email`, `telegram`, `set_actuator`), pesan, method command, penerima, aktuator & device tujuan |
| Rule chain | device Input, nama/jenis/key/pesan/method node, variabel & device di node |
| Admin saja | username pemilik |

Beberapa kata: **semuanya** harus cocok, boleh di bagian yang berbeda. `greenhouse pump`
menemukan aturan yang menyebut device *Greenhouse Node* **dan** aktuator *pump*.

Pilihan tambahan: **model** (ECA / FLOW) dan **state** (*Active now*, *Enabled*,
*Disabled*, *On a schedule*).

Implementasinya (`search_rules` di `rules/views.py`): kondisi dan aksi adalah tabel "banyak",
sehingga JOIN bisa mengembalikan satu aturan berkali-kali. Pencarian dijalankan sebagai
subquery id:

```python
matches = Rule.objects.filter(Q(name__icontains="pump") | Q(actions__message__icontains="pump") | ...)
rules = visible_rules(user).filter(pk__in=matches.values("pk"))
```

```sql
SELECT ... FROM rules_rule WHERE owner_id = 3 AND id IN (
    SELECT r.id FROM rules_rule r LEFT JOIN rules_ruleaction a ON ... WHERE r.name LIKE '%pump%' OR a.message LIKE '%pump%' ...
)
```

Hasilnya tanpa duplikat. Daftar aturan tetap memakai `visible_rules(user)`, jadi pengguna
biasa tidak akan menemukan aturan milik orang lain lewat pencarian.

---

## Menerapkan

```bash
python manage.py migrate                # tabel activity_activityevent
python manage.py activity_backfill      # opsional: isi dari riwayat lama
```

Di server: `sudo /srv/iot/app/deploy/scripts/update.sh`, lalu (opsional)

```bash
cd /srv/iot/app && sudo -u iot -H /srv/iot/venv/bin/python manage.py activity_backfill
```

Tidak ada layanan baru. Retensi dijalankan worker `iot-housekeeping` yang sudah ada.
Tambahkan `ACTIVITY_LOG_DAYS=180` ke `.env` bila ingin mengubah lamanya.

## Tes

```bash
python manage.py test activity
```

| Kelompok | Yang diuji |
|---|---|
| `ActivityTests` | owner dan pelaku, pemisahan log antar-pengguna dan Admin, variabel dari device, siklus aturan + aktuator + alarm, aksi gagal = Error, Admin bertindak pada device orang lain, login dan login gagal, penghapusan berantai, device terhapus tetap terbaca, filter, retensi, backfill |
| `RuleSearchTests` | setiap kolom pencarian, beberapa kata, tanpa duplikat, model dan state, aturan orang lain tidak muncul, username pemilik khusus Admin |
