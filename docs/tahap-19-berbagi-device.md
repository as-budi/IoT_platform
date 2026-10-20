# Tahap 19: Berbagi device antar-pengguna dan grup

Sampai tahap 18 aturan aksesnya sederhana: **Admin melihat semua device, pengguna lain hanya
device miliknya.** Di lab atau kelas, satu device sering dipakai bersama: dosen memasang node
greenhouse, mahasiswa memantau datanya, asisten boleh menyalakan pompa, dan teknisi boleh
mengganti firmware. Tahap ini menambah **berbagi device**, seperti berbagi dokumen di Google Drive:

```
siti (pemilik) ──► Greenhouse Node ──► budi        sebagai Operator
                                   └─► grup "Kelompok 3" sebagai Viewer  (andi, rina, dodi)
```

---

## 1. Tiga peran

| Kemampuan | Viewer | Operator | Editor | Pemilik / Admin |
|---|:-:|:-:|:-:|:-:|
| Melihat device, variabel, chart, history, alarm, firmware, log | ✓ | ✓ | ✓ | ✓ |
| Memakai sensornya di **kondisi** aturan (ECA, Input / Latest value rule chain) | ✓ | ✓ | ✓ | ✓ |
| Mengontrol aktuator, mengirim command, acknowledge / clear alarm | | ✓ | ✓ | ✓ |
| Memakai device sebagai **target aksi** aturan (Set actuator, Send command, Alarm) | | ✓ | ✓ | ✓ |
| Melihat **access token** dan info koneksi; membuat token baru | | | ✓ | ✓ |
| Mengubah pengaturan device dan variabel; memperbarui firmware (OTA) | | | ✓ | ✓ |
| **Menghapus** device; mengatur siapa yang mendapat akses | | | | ✓ |

Beberapa keputusan desain:

- **Token hanya untuk Editor.** Access token adalah "password" device. Siapa pun yang
  memegangnya bisa mengirim data palsu atas nama device itu. Viewer dan Operator tidak perlu
  token, jadi kartu *Connect a device* disembunyikan.
- **Firmware termasuk "mengubah device"**, jadi butuh Editor. Paket firmware tetap milik
  pengunggahnya: Editor hanya bisa memasang paket yang ia unggah sendiri.
- **Lokasi tetap milik pemilik device.** Editor yang mengubah device memilih dari lokasi
  *pemiliknya*, bukan lokasinya sendiri. Penerima share melihat nama lokasi tanpa tautan,
  karena halaman lokasi orang lain tetap 404.
- **Peran tertinggi berlaku.** Bila budi mendapat Viewer langsung dan Editor lewat grup,
  ia Editor.

## 2. Model data (app `sharing`)

```
ShareGroup (name, owner)               "Kelompok 3", dibuat siti
  └── ShareGroupMember (group, user)    siti (otomatis), andi, rina, dodi

DeviceShare (device, user | group, role, created_by)
  Greenhouse Node → budi          OPERATOR
  Greenhouse Node → Kelompok 3    VIEWER
```

| Constraint | Maksud |
|---|---|
| `deviceshare_not_user_and_group` (CHECK) | satu baris tidak boleh untuk pengguna **dan** grup sekaligus; "harus salah satu" dijaga form dan `DeviceShare.clean()` |
| `deviceshare_unique_user`, `deviceshare_unique_group` | satu baris per (device, pengguna) dan per (device, grup); berbagi ulang = mengganti peran |
| `sharegroupmember_unique` | seorang pengguna hanya sekali di satu grup |
| `sharegroup_unique_name_per_owner` | nama grup unik per pemilik |

Mengapa dua UNIQUE biasa cukup? Di MySQL, SQLite, dan PostgreSQL, `NULL` tidak dianggap sama
dengan `NULL` di index UNIQUE. Baris `(device=1, user=5, group=NULL)` dan
`(device=1, user=NULL, group=2)` tidak bertabrakan. Unique bersyarat (`WHERE user IS NOT NULL`)
tidak dipakai karena MySQL tidak mendukungnya.

Mengapa CHECK-nya bukan "tepat satu" (XOR)? Versi pertama memakai XOR dan gagal di MySQL saat
grup dihapus (`Check constraint 'deviceshare_user_xor_group' is violated`). Untuk FK yang boleh
NULL dengan CASCADE, Django di MySQL lebih dulu menjalankan
`UPDATE sharing_deviceshare SET group_id = NULL ...` lalu baru `DELETE`, karena MySQL tidak bisa
menunda pemeriksaan constraint sampai akhir transaksi. Di antara kedua perintah itu barisnya
kosong dua-duanya. SQLite menunda pemeriksaan, sehingga tes di SQLite lulus. Migrasi
`sharing 0002` mengganti constraint-nya; tesnya `test_deleting_user_or_group_removes_their_shares`.

Penghapusan berantai: device dihapus → share-nya terhapus; grup dihapus → share ke grup itu
terhapus; pengguna dihapus → share dan keanggotaannya terhapus.

## 3. Satu tempat aturan akses: `sharing/access.py`

Prinsip tahap 3 tetap: **aturan akses ditulis di satu tempat.** Fungsi lama
`visible_devices(user)` tetap ada dan dipakai di mana-mana, hanya isinya yang berubah:

```python
def visible_devices(user, level=access.VIEWER):
    return access.devices_for(user, level).select_related("owner", "location__owner")
```

```python
def devices_for(user, level=VIEWER, *, shared=True):
    devices = Device.objects.all()
    if user.is_admin:
        return devices
    allowed = Q(owner=user)
    if shared and level != OWNER:
        allowed |= Q(pk__in=shares_of(user).filter(role__in=roles_at_least(level)).values("device_id"))
    return devices.filter(allowed)
```

`shares_of(user)` = share langsung ke dia **atau** ke grup yang ia ikuti. SQL-nya kira-kira:

```sql
SELECT ... FROM devices_device
WHERE owner_id = 5
   OR id IN (SELECT s.device_id FROM sharing_deviceshare s
             LEFT JOIN sharing_sharegroupmember m ON m.group_id = s.group_id
             WHERE (s.user_id = 5 OR m.user_id = 5) AND s.role IN ('OPERATOR', 'EDITOR'))
```

Subquery `id IN (...)` dipakai, bukan JOIN langsung, supaya device yang dibagikan lewat dua
jalan (langsung + grup) tidak muncul dua kali di daftar.

Karena semua bagian sudah memakai `visible_devices()`, banyak hal otomatis ikut: dashboard,
WebSocket (tahap 5), daftar alarm, log aktivitas, pilihan device di form aturan, dan pemeriksaan
rule engine.

### 404 atau 403?

| Keadaan | Jawaban | Alasan |
|---|---|---|
| Device tidak dibagikan sama sekali | **404** | sama seperti sebelumnya: device orang lain "tidak ada" |
| Terlihat, tetapi perannya kurang (Viewer menekan ON) | **403** | pengguna tahu device-nya ada; pesan menjelaskan peran yang dibutuhkan |

```python
class OwnedDeviceMixin(LoginRequiredMixin):
    access_level = access.VIEWER          # DeviceUpdateView: EDITOR, DeviceDeleteView: OWNER

    def get_object(self, queryset=None):
        device = super().get_object(queryset)
        self.access_role = access.require(self.request.user, device, self.access_level)
        return device
```

| View | Peran minimal |
|---|---|
| Device / variabel detail, chart, `updates/` | Viewer |
| Set aktuator, command, acknowledge / clear alarm | Operator |
| Edit device, tambah/ubah/hapus variabel, token baru, mulai/batal OTA | Editor |
| Hapus device, tambah/ubah/hapus share | Pemilik (atau Admin) |

**Tombol mengikuti peran, tetapi tombol bukan pengaman.** Template menyembunyikan tombol yang
tidak boleh dipakai (`can_operate`, `can_edit`, `is_owner`, dan tag
`{% can_access device_id "OPERATOR" as ... %}`). Pemeriksaan yang sebenarnya tetap ada di view
POST, sehingga request yang dibuat manual tetap mendapat 403 (diuji di
`test_viewer_sees_but_cannot_act`).

Tag `can_access` menyimpan hasilnya di cache per request. Dashboard dengan 20 aktuator mengisi
cache sekali (`access.remember(request, devices)`, 2 query), bukan 20 query.

## 4. Aturan (rule engine)

Sesuai pilihan "**Ya, sesuai peran**":

| Bagian aturan | Device yang boleh dipilih |
|---|---|
| Kondisi ECA, event "device diam", Input rule chain (satu device), node *Latest value* | Viewer ke atas |
| Aksi *Set actuator*, *Send command*; node yang sama di rule chain | Operator ke atas |
| Aksi *Alarm* pada device bersama | Operator ke atas (alarm tampil di daftar alarm pemilik device) |
| Aksi email / Telegram | tidak menyentuh device: Viewer cukup |
| Rule chain Input "— Any of my devices —" | hanya device **milik** pemilik aturan, tidak termasuk device bersama |

Form ECA dan rule chain memakai dua queryset: `devices` (boleh dibaca) dan `action_devices`
(boleh dikontrol). Aktuator device yang hanya Viewer tidak muncul di pilihan target aksi.

### Diperiksa ulang saat berjalan

Share bisa dicabut atau diturunkan kapan saja, setelah aturannya dibuat. Karena itu rule engine
memeriksa ulang setiap kali:

- **Kondisi ECA**: `readable_variables()` membuang variabel dari device yang sudah tidak terlihat.
  Nilainya dianggap *tidak diketahui* (logika tiga nilai, tahap 7), sehingga aturan tidak aktif.
- **Event "device diam"**: dilewati bila device tidak terlihat lagi.
- **Rule chain**: Input dan node *Latest value* memeriksa `visible_devices(rule.owner)`.
- **Aksi**: `allowed_devices(rule)` = `visible_devices(rule.owner, OPERATOR)`. Viewer yang
  mencoba menyalakan pompa mendapat hasil gagal di log eksekusi:
  `✗ Set actuator: The rule owner has no access to Greenhouse Node.`

### Pemilik device tahu siapa yang mengontrol

Aksi aturan menulis desired state tanpa nama pengguna ("rule engine"). Bila aturan milik orang
lain (lewat share) mengontrol device, pemilik device mendapat satu baris log tambahan:

```
Control · Rule "Irrigation" of budi: Water pump = ON        by budi
```

## 5. Log aktivitas

Kategori baru **Sharing**:

| Kejadian | Terlihat oleh |
|---|---|
| `share.added`, `share.changed`, `share.removed` | pemilik device dan semua yang berbagi device itu; saat dicabut, penerimanya mendapat baris sendiri ("Your access to … was removed") |
| `group.created`, `group.member_added`, `group.member_removed`, `group.deleted` | pemilik grup dan anggota yang bersangkutan |

Penerima share juga melihat kejadian device itu dari kategori **Device, Control, Alarm,
Firmware, Sharing**, termasuk riwayat sebelum ia mendapat akses. Kategori **Rule** dan
**Account** milik pemilik device tidak terlihat: nama dan isi aturan pribadinya tetap pribadi.

```python
SHARED_CATEGORIES = [Category.DEVICE, Category.CONTROL, Category.ALARM, Category.FIRMWARE, Category.SHARING]
return events.filter(Q(owner=user) | Q(device_id__in=shared, category__in=SHARED_CATEGORIES))
```

## 6. Halaman

**Halaman device → kartu Sharing** (pemilik / Admin):

```
Who                                   Role          Shared               
budi                                  [Operator ▾] Save   20 Oct 2026 by siti   Remove
Group Kelompok 3 · 4 members          [Viewer   ▾] Save   20 Oct 2026 by siti   Remove

User [budi      ]  or group [— No group — ▾]  Role [Viewer ▾]  [Share]
```

- Pengguna dicari dengan username (huruf besar/kecil tidak dibedakan), harus aktif.
- Tidak bisa berbagi ke pemilik device atau ke Admin (Admin sudah melihat semua).
- Grup yang bisa dipilih: grup yang **diikuti** pemilik device (Admin: semua grup).

Penerima melihat lencana **Shared with you · Operator** di judul, dari mana aksesnya (langsung
atau lewat grup), dan tombol **Leave** untuk share langsung.

**Devices**: device bersama tampil dengan keterangan *Shared by siti · Operator*, dan filter
**Access**: *Mine and shared* / *Only mine* / *Shared with me*.

**Sharing groups** (menu samping):

- Siapa pun bisa membuat grup. Pembuatnya otomatis anggota dan tidak bisa dikeluarkan.
- Pemilik grup menambah anggota dengan username, mengeluarkan anggota, mengganti nama, atau
  menghapus grup.
- Anggota melihat grup, anggota lain, dan device yang dibagikan ke grup itu, lalu bisa **Leave**.
- Anggota baru langsung mendapat semua akses grup. Anggota yang keluar langsung kehilangannya.

## 7. Yang diubah

| File | Isi |
|---|---|
| `sharing/` (app baru) | model, `access.py`, form, view, URL `/sharing/`, template, sinyal log, admin, tes |
| `devices/views.py` | `visible_devices(user, level)`, `access_level` per view, 403, filter Access, konteks peran |
| `devices/forms.py` | lokasi dari pemilik device |
| `devices/templatetags/device_tags.py` | tag `can_access` |
| template device, dashboard (control form), alarm, firmware | tombol mengikuti peran; token hanya Editor |
| `rules/forms.py`, `rules/views.py` | `action_devices` untuk target aksi; alarm butuh Operator |
| `rules/actions.py`, `rules/engines/eca.py`, `rules/engines/flow.py` | pemeriksaan ulang saat berjalan, log aksi pada device orang lain |
| `ota/views.py` | mulai/batal OTA butuh Editor |
| `activity/` | kategori Sharing (migrasi `0002`), `visible_events` untuk device bersama |
| `realtime/tests.py` | fixture device sudah online (perbaikan tes tahap 16) |

## 8. Menerapkan

```bash
python manage.py migrate      # sharing 0001 + 0002, activity 0002
```

Di server: `sudo /srv/iot/app/deploy/scripts/update.sh` (sudah menjalankan `migrate` dan
`collectstatic`). Tidak ada layanan baru.

## 9. Mencoba

1. Login sebagai **siti** → device *Greenhouse Node* → kartu **Sharing** → User `budi`,
   Role **Viewer** → **Share**.
2. Login sebagai **budi** di browser lain (atau jendela private): device muncul di *Devices*
   dengan *Shared by siti · Viewer*. Chart terlihat, tombol ON/OFF diganti *View-only access*,
   kartu *Connect a device* tidak ada.
3. Kembali sebagai siti, ganti peran budi menjadi **Operator**. Muat ulang halaman budi:
   tombol ON/OFF muncul dan bisa dipakai. Log aktivitas siti menulis "Water pump … set to ON
   by budi".
4. Sebagai budi, buat aturan ECA dengan kondisi sensor siti dan aksi pompa siti. Turunkan budi
   ke Viewer: aksi aturan itu gagal dengan *has no access*. Cabut share-nya: aturan berhenti.
5. **Sharing groups → New group** "Kelompok 3", tambahkan beberapa username, lalu bagikan
   device ke grup itu dari kartu Sharing.

## 10. Tes

```bash
python manage.py test sharing
```

| Kelompok | Yang diuji |
|---|---|
| `AccessTests` | peran langsung dan lewat grup, peran tertinggi, tanpa duplikat, Admin, keluar grup |
| `PageTests` | 404 tanpa share; Viewer: token tersembunyi dan semua POST 403; Operator: kontrol dan command; Editor: lokasi pemilik, tidak bisa menghapus; daftar device dan filter; dashboard; alarm |
| `ManageSharesTests` | berbagi, berbagi ulang = ganti peran, ubah peran, hapus, sasaran tidak sah, Leave, Admin, orang lain 403 |
| `GroupTests` | buat grup, anggota, nama kembar, anggota keluar, pemilik tidak bisa keluar, hapus grup |
| `RuleSharingTests` | Viewer membaca tetapi tidak mengontrol, Operator mengontrol dan pemilik diberi tahu, share dicabut menghentikan aturan, alarm butuh Operator, pilihan form |
| `ActivitySharingTests` | penerima melihat kejadian device, bukan aturan pribadi pemilik; log keanggotaan grup |
