# Tahap 17: Notifikasi Telegram

Aturan sekarang bisa mengirim pesan Telegram, di samping email:

| Tempat | Nama | Kapan berjalan |
|---|---|---|
| ECA, bagian *Then — actions* | **Send Telegram message** | saat aturan aktif atau pulih (`Run`) |
| Rule chain, kategori Action | node **Send Telegram message** | setiap pesan yang sampai ke node (pasang *Rate limit* di depannya) |

Contoh pesan di HP:

```
Greenhouse Node: soil 18 %, rain 0 mm/h → pump ON
```

Mengapa Telegram? Gratis, pesannya langsung muncul di HP, dan API-nya cukup satu permintaan
HTTPS. Email sering terlambat atau masuk folder spam, dan WhatsApp butuh akun bisnis
berbayar.

---

## 1. Konsep: satu bot untuk seluruh platform

```
platform ──HTTPS POST sendMessage──► api.telegram.org ──► chat pengguna
          (token bot dari .env)
```

- Platform punya **satu bot** (dibuat sekali lewat @BotFather). Tokennya disimpan di `.env`
  sebagai `TELEGRAM_BOT_TOKEN`.
- Bot **tidak boleh** mengirim pesan ke sembarang orang. Telegram hanya mengizinkan bot
  menulis ke chat yang **pernah menekan START** pada bot itu (atau grup yang memuat bot).
  Ini mencegah spam, dan karena itu setiap pengguna harus "menyambungkan" akunnya sekali.
- Tujuan pesan adalah **chat id** (angka), bukan nomor HP atau username.

## 2. Menyambungkan akun (deep link)

Bot tidak tahu siapa pengguna platform. Penghubungnya adalah **kode sekali pakai**:

```
1. Profil → Connect Telegram
      server membuat kode acak (TelegramLinkCode, berlaku 15 menit)
      halaman menampilkan https://t.me/<bot>?start=<kode>
2. Pengguna membuka tautan dan menekan START
      Telegram mengirim pesan "/start <kode>" ke bot, DARI chat milik pengguna
3. Pengguna menekan "I pressed Start"
      server memanggil getUpdates, menemukan "/start <kode>",
      menyimpan chat.id ke TelegramLink(user), menghapus kode,
      bot membalas "Connected to the IoT platform account ..."
```

Kode adalah bukti bahwa chat ini milik akun yang membuat kode. Siapa pun yang menekan START
dengan tautan itu akan menerima notifikasi akun tersebut, jadi tautan tidak boleh dibagikan.
Karena itu masa berlakunya singkat dan hanya bisa dipakai sekali.

### Mengapa getUpdates, bukan webhook?

Telegram punya dua cara menyerahkan pesan yang masuk ke bot:

| Cara | Kebutuhan | Dipakai? |
|---|---|---|
| **Webhook**: Telegram memanggil URL HTTPS kita | alamat HTTPS publik; tidak jalan di laptop | tidak |
| **getUpdates**: kita bertanya ke Telegram | hanya koneksi keluar | ya, saat tombol ditekan |

Menyambungkan akun hanya terjadi sesekali, jadi platform cukup bertanya ke Telegram ketika
pengguna menekan **I pressed Start**. Tidak perlu worker tambahan atau port masuk, dan cara ini
sama di laptop maupun server.

Semua pesan `/start` dalam satu kali baca langsung diproses. Bila dua pengguna menyambungkan
akun bersamaan, keduanya beres sekaligus. Posisi baca disimpan di `TelegramBotState`
(`offset`), sehingga Telegram tidak mengirim ulang pesan yang sudah diproses.

## 3. Mengirim pesan

`notifications/telegram.py`: klien kecil tanpa paket tambahan (hanya `urllib`).

```http
POST https://api.telegram.org/bot<TOKEN>/sendMessage
Content-Type: application/json

{"chat_id": "777000111", "text": "Greenhouse Node: soil 18", "disable_web_page_preview": true}
```

Jawaban Telegram selalu `{"ok": true, "result": {...}}` atau
`{"ok": false, "description": "Forbidden: bot was blocked by the user"}`. Deskripsi itu
dicatat di log eksekusi aturan.

Keputusan desain:

- **Teks biasa**, tanpa `parse_mode` Markdown/HTML. Nilai sensor dan nama device bisa memuat
  karakter seperti `_` atau `*` yang merusak format, jadi tidak ada yang perlu di-escape.
- Pesan lebih dari 4.096 karakter (batas Telegram) dipotong.
- Batas waktu 10 detik per panggilan. Telegram yang lambat tidak menahan aksi lain terlalu
  lama, dan kegagalan satu aksi tidak menghentikan aksi lain (sama dengan email, tahap 7).
- **Token tidak pernah masuk ke pesan error atau log.** Error jaringan ditulis ulang menjadi
  "Cannot reach Telegram: ..." tanpa URL.

## 4. Aksi ECA dan node rule chain

| Field | Isi |
|---|---|
| **Message** | teks dengan placeholder yang sama dengan email: `{rule}`, `{device}`, `{variable}`, `{value}`, `{values}`, `{transition}`; ECA berjadwal juga `{time}` (tahap 14); rule chain juga setiap key pesan, mis. `{temperature}`, `{deviceName}` |
| **Recipients** | kosong = chat yang disambungkan **pemilik aturan** di profilnya; atau id chat dipisah koma |

Id chat yang diterima:

| Bentuk | Contoh | Untuk |
|---|---|---|
| angka | `123456789` | chat pribadi |
| angka negatif | `-1001234567890` | grup / supergrup (bot harus anggota grup) |
| `@nama` | `@lab_iot_alerts` | channel publik (bot harus admin channel) |

Isian lain, misalnya alamat email, ditolak form dengan pesan *Not a Telegram chat id*.

Hasil di log eksekusi:

```
✓ Send Telegram message: Telegram to the owner
✗ Send Telegram message: The rule owner has not connected Telegram (My profile → Telegram).
✗ Send Telegram message: -100222: Telegram: Bad Request: chat not found (sent to 111)
```

Pada rule chain, node keluar lewat **Failure** bila pengiriman gagal, jadi kegagalan bisa
dicabangkan (mis. *Failure → Send email* sebagai cadangan).

Ekspor/impor JSON (tahap 13) ikut mendukung:

```json
{"kind": "TELEGRAM", "run_on": "ACTIVATE", "message": "{device}: soil {value}", "recipients": ""}
```

## 5. Yang diubah

| File | Isi |
|---|---|
| `notifications/` (app baru) | `telegram.py` klien API, `link.py` menyambungkan akun, model `TelegramLink`, `TelegramLinkCode`, `TelegramBotState`, halaman Connect/Test/Disconnect |
| `accounts/templates/accounts/profile.html` | kartu **Telegram** di profil |
| `rules/models.py` + migrasi `0005_telegram_action` | pilihan `TELEGRAM` di `RuleAction.Kind` dan `FlowNode.Kind` (tanpa kolom baru: memakai `message` dan `recipients`) |
| `rules/actions.py` | `send_telegram()`, dipakai ECA dan node rule chain |
| `rules/forms.py` | validasi id chat |
| `rules/engines/flow.py` | `NODE_TYPES[TELEGRAM]` |
| `config/settings.py`, `.env.example` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_API_URL` |

## 6. Mencoba

1. Buat bot: Telegram → **@BotFather** → `/newbot`. Simpan token-nya.
2. `.env` di laptop:

   ```ini
   TELEGRAM_BOT_TOKEN=123456789:AA...
   TELEGRAM_BOT_USERNAME=nama_bot_anda_bot
   ```

   Restart `runall`.
3. **My profile → Connect Telegram → Open Telegram → START → I pressed Start**, lalu
   **Send test**.
4. Buka aturan contoh *Pond low oxygen* (andi) → Edit → **Add action** → *Send Telegram
   message*, isi Message `Pond: {values}`, kosongkan Recipients, simpan.
5. Picu aturannya:

   ```bash
   python manage.py send_telemetry "Fish Pond Node" dissolved_oxygen=3.5 water_temp=29
   ```

   Pesan muncul di Telegram, dan log eksekusi aturan menulis `Telegram to the owner`.

Untuk grup kelas: tambahkan bot ke grup, kirim pesan apa saja di grup, lalu buka
`https://api.telegram.org/bot<TOKEN>/getUpdates` di browser untuk melihat `chat.id` grup
(angka negatif). Isi angka itu di **Recipients**. Lakukan langkah ini sebelum ada yang
menekan **I pressed Start**, karena tombol itu menandai pesan-pesan tersebut sudah dibaca.

```bash
python manage.py test notifications
```

Tes memakai Telegram tiruan (`unittest.mock`), jadi tidak butuh token atau internet.

## 7. Keamanan

- Token bot = kunci penuh atas bot. Simpan hanya di `.env` (mode 600 di server), jangan
  di-commit. Bila bocor: @BotFather → `/revoke`, lalu ganti di `.env`.
- Pengguna hanya bisa menyambungkan **akunnya sendiri** (kode dibuat dari sesi login-nya).
- Pemilik aturan bisa menulis id chat lain di **Recipients**, tetapi Telegram hanya
  mengantarkan ke chat yang pernah memulai bot atau grup yang memuat bot. Bot tidak bisa
  dipakai untuk mengirim pesan ke orang asing.
- Notifikasi yang terlalu sering bisa membuat Telegram membatasi bot (±30 pesan/detik, ±20
  pesan/menit ke satu grup). Di rule chain, pasang node *Rate limit*; di ECA, aksi hanya
  berjalan saat aturan berpindah keadaan.
