# Tahap 23: Akun MQTT sandbox (belajar MQTT tanpa device)

Broker platform (tahap 18) hanya menerima login dengan **access token device**, dan setiap
device hanya boleh memakai topiknya sendiri. Ini aman, tetapi menyulitkan mahasiswa yang ingin
belajar MQTT (subscribe, publish, wildcard, retained, QoS) sebelum punya device.

Tahap ini memberi setiap pengguna **satu akun MQTT sandbox**:

| | Akun device | Akun sandbox |
|---|---|---|
| Username | access token device | `sbx-<id pengguna>` |
| Password | access token device | acak (18 byte), bisa diganti; **bukan** password login |
| Topik | `<prefix>/<token>/telemetry`, `…/desired`, … | `sandbox/<username>/#` (sendiri) dan `sandbox/public/#` (bersama) |
| Masuk ke dashboard, rule, notifikasi | ya | tidak: `mqtt_bridge` hanya mendengar `<prefix>/…` |
| Dibuat di | Devices → Add device | My profile → **MQTT sandbox** |

Pasangannya di aplikasi MQTT Client: tahap M5 (`mqtt_client/docs/tahap-m5-simulator-dan-sandbox.md`).

## 1. Model dan topik

`mqtt_bridge/models.py`:

```python
class SandboxAccount(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="mqtt_sandbox")
    password = models.CharField(max_length=64, default=new_password)    # secrets.token_urlsafe(18)
    created_at, password_changed_at

    username    -> "sbx-<user_id>"     (tetap walaupun username platform diganti)
    topic_root  -> "sandbox/<username platform>"
```

Username platform dipakai di topik supaya mudah dikenali (`sandbox/rina/sensor`). Username yang
memuat karakter selain `A-Z a-z 0-9 _ . -`, atau bernama `public`, memakai `sandbox/~<id>`.
Karakter `~` tidak pernah ada di nama yang lolos aturan itu, jadi dua pengguna tidak bisa
mendapat topik yang sama.

Password disimpan **apa adanya** di database (seperti access token device), karena platform perlu
menuliskannya ke file password Mosquitto dan memberikannya ke aplikasi MQTT Client lewat API.
Di file password Mosquitto, yang tersimpan hanya hash-nya.

## 2. Akun dan ACL di broker

`manage.py mqtt_accounts` (timer 2 menit, tahap 18) kini juga menulis akun sandbox pengguna
**aktif**:

```
# Akun sandbox pengguna (tahap 23): hanya di bawah sandbox/, tidak menyentuh topik device.
user sbx-5
topic readwrite sandbox/rina/#
topic readwrite sandbox/public/#
```

- Aturan `user` hanya berlaku untuk akun itu. Aturan `pattern` milik device (`%u` = token)
  tidak memberi akun sandbox akses apa pun, karena `sbx-5` bukan token device.
- Akun sandbox **tidak** bisa membaca atau menulis `<prefix>/…`; device tidak bisa menyentuh
  `sandbox/…`.
- Akun yang dinonaktifkan Admin (user tidak aktif) dikeluarkan dari file pada putaran timer
  berikutnya.
- Akun sandbox ikut dihitung dalam sidik jari file, jadi membuat, mengganti password, atau
  menghapus akun memicu penulisan ulang dan reload Mosquitto.

## 3. Halaman My profile

Kartu **MQTT sandbox** (`mqtt_bridge/templates/mqtt_bridge/sandbox_card.html`):

| Aksi | URL (POST) | Catatan |
|---|---|---|
| Create sandbox account | `/accounts/sandbox/enable/` | berlaku di broker ±2 menit kemudian |
| New password | `/accounts/sandbox/reset/` | klien dengan password lama ditolak ±2 menit kemudian |
| Remove sandbox account | `/accounts/sandbox/disable/` | |

Kartu menampilkan broker (TCP dan WebSocket), username, password (tersembunyi, tombol Copy), dan
kedua topik. Setiap aksi dicatat di log aktivitas (tahap 18): `sandbox.enabled`,
`sandbox.reset`, `sandbox.disabled`.

## 4. API untuk aplikasi lain

Scope baru **`mqtt:sandbox`** (*Use your MQTT sandbox account (topics under sandbox/), including
its password*):

```
GET /api/v2/me/sandbox          Authorization: Bearer <access token dengan scope mqtt:sandbox>

{"available": true, "enabled": true,
 "broker": {"mqtt": {...}, "websocket": "wss://iot.reslab-csub.cloud/mqtt", ...},
 "manage_url": "https://iot.reslab-csub.cloud/accounts/profile/#mqtt-sandbox",
 "username": "sbx-5", "password": "…", "topic_root": "sandbox/rina",
 "shared_root": "sandbox/public", "password_changed_at": "2026-10-22T07:50:00+07:00"}
```

- `available: false` bila fitur dimatikan di server; `enabled: false` bila pengguna belum
  membuat akun (tanpa username/password).
- Akun **tidak** bisa dibuat lewat API: pengguna membuatnya sendiri di platform (`manage_url`).
- Token tanpa scope `mqtt:sandbox` mendapat **403**. Aplikasi yang sudah login sebelum tahap ini
  harus meminta pengguna login ulang supaya halaman izin menanyakan scope baru.

## 5. Pengaturan

| `.env` | Bawaan | Arti |
|---|---|---|
| `MQTT_SANDBOX_ENABLED` | sama dengan `MQTT_DEVICE_LOGIN` | `True`/`False` untuk memaksa. Sandbox hanya bermakna di broker sendiri yang memakai akun (`MQTT_DEVICE_LOGIN=True`) |

Dengan `False`: kartu tidak tampil, URL `/accounts/sandbox/…` menjawab 404, API menjawab
`available: false`, dan `mqtt_accounts` tidak menulis akun sandbox.

## 6. Mencoba

```bash
python manage.py migrate                 # mqtt_bridge 0001 (SandboxAccount)
# My profile -> MQTT sandbox -> Create sandbox account
python manage.py mqtt_accounts           # di laptop dengan Mosquitto sendiri; di server timer yang menjalankannya
mosquitto_sub -h 127.0.0.1 -u sbx-5 -P '<password>' -t 'sandbox/#' -v &
mosquitto_pub -h 127.0.0.1 -u sbx-5 -P '<password>' -t sandbox/rina/hello -m hi        # diterima
mosquitto_pub -h 127.0.0.1 -u sbx-5 -P '<password>' -t sandbox/budi/hello -m hi        # dibuang broker (ACL)
mosquitto_pub -h 127.0.0.1 -u sbx-5 -P '<password>' -t iot/abc/telemetry -m '{}'       # dibuang broker (ACL)
```

Catatan: pada MQTT 3.1.1, publish ke topik terlarang tidak menghasilkan error di klien; broker
diam-diam membuangnya. Subscribe ke topik terlarang dijawab kode *failure* (0x80).

## 7. Berkas

| Berkas | Isi |
|---|---|
| `mqtt_bridge/models.py`, `migrations/0001_initial.py` | `SandboxAccount` |
| `mqtt_bridge/management/commands/mqtt_accounts.py` | `sandbox_acl()`, akun sandbox di file password |
| `mqtt_bridge/views.py`, `urls.py`, `admin.py` | aksi kartu, `sandbox_context()` |
| `mqtt_bridge/templates/mqtt_bridge/sandbox_card.html`, `accounts/templates/accounts/profile.html` | kartu di My profile |
| `api/v2.py` | `GET /api/v2/me/sandbox` |
| `config/settings.py` | `MQTT_SANDBOX_ENABLED`, scope `mqtt:sandbox` |
| `mqtt_bridge/tests_sandbox.py` | tes halaman, file akun/ACL, API |

Memasang di server: `docs/deploy-vps.md` bagian 14.4.
