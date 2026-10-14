# Data Contoh: Pengguna, Device, dan Aturan ECA Multi-Variabel

Dokumen ini menjelaskan data contoh yang dibuat oleh dua perintah, beserta skenario untuk
mencoba setiap aturan. Mekanisme ECA dijelaskan di [eca-mekanisme.md](eca-mekanisme.md).

```bash
python manage.py seed_demo       # pengguna, lokasi, device, variabel, 24 jam telemetri
python manage.py seed_rules      # 6 aturan ECA contoh
python manage.py seed_rules --reset   # hapus lalu buat ulang aturan contoh
```

Kedua perintah aman dijalankan berulang: data yang sudah ada tidak digandakan. Pada database
yang sudah berisi data, `seed_demo` hanya menambahkan yang belum ada.

## 1. Pengguna

Semua password: **`demo12345`**. Gunakan hanya di komputer pengembangan.

| Username | Role | Email | Isi akunnya |
|---|---|---|---|
| `admin` | Admin | – | melihat semua device dan aturan |
| `budi` | User | budi@example.com | ruang server dan lab elektronika |
| `siti` | User | siti@example.com | greenhouse: node tanah dan stasiun cuaca |
| `andi` | User | andi@example.com | kandang ayam dan kolam ikan |
| `rina` | User | rina@example.com | gudang pendingin vaksin |

Email hanya diisi untuk akun yang baru dibuat. Akun lama tetap seperti sebelumnya.

## 2. Lokasi dan device

| Lokasi | Device | Pemilik | Sensor | Aktuator |
|---|---|---|---|---|
| Server room | Server Room Node | budi | temperature (°C), humidity (%) | – |
| Electronics lab | Lab Power Meter | budi | voltage, current, power | – |
| Electronics lab | Lab Relay Board | budi | – | lamp (on/off), fan_speed (0–100 %) |
| Greenhouse A | Greenhouse Node | siti | temperature, humidity, soil_moisture (%) | pump (on/off) |
| Greenhouse A | **Greenhouse Weather Station** | siti | rain (mm/h), light (lux) | – |
| **Poultry house** | **Poultry House Node** | andi | temperature, humidity, ammonia (ppm) | exhaust_fan (on/off) |
| **Fish pond** | **Fish Pond Node** | andi | water_temp (°C), dissolved_oxygen (mg/L), ph | aerator (on/off) |
| **Cold storage** | **Cold Storage Unit** | rina | temperature (°C), humidity (%) | compressor_boost (on/off) |

Yang dicetak tebal adalah tambahan baru. Setiap sensor diberi riwayat 24 jam (satu titik
tiap 10 menit), dan setiap aktuator diberi riwayat keadaan.

## 3. Aturan contoh

Notasi: kondisi di dalam satu kurung digabung **AND**, kurung-kurung digabung **OR**.

### 3.1 Smart irrigation (siti), aturan lintas device

```
WHEN  data baru dari Greenhouse Node atau Greenhouse Weather Station
IF    (soil_moisture < 30 AND temperature > 32 AND rain < 0.5)
   OR (soil_moisture < 20 AND rain < 0.5)                 selama 60 s
THEN  pump = ON · alarm Warning "Irrigation started: {values}"
CLEAR bila (soil_moisture > 45) OR (rain > 2)
THEN  pump = OFF
cooldown 300 s · nilai > 900 s = tidak diketahui
```

Maksudnya: siram bila tanah kering dan tidak hujan. Saat udara panas, tanaman lebih cepat
haus, sehingga batas tanahnya dinaikkan ke 30 %. Penyiraman berhenti bila tanah sudah basah
**atau** hujan deras.

Pola yang ditunjukkan:

- tiga kondisi AND dalam satu grup;
- variabel dari **dua device**;
- kondisi clear berbentuk OR;
- cooldown agar pompa tidak menyala-padam berkali-kali dalam waktu singkat.

### 3.2 Poultry heat stress (andi)

```
IF    (temperature > 32 AND humidity > 70) OR (ammonia > 25)      selama 30 s
THEN  exhaust_fan = ON · alarm Major · email ke andi@example.com
CLEAR bila (temperature < 29 AND ammonia < 15)
THEN  exhaust_fan = OFF
```

Ayam broiler tertekan oleh panas yang **lembap**: panas saja atau lembap saja belum tentu
berbahaya. Kadar amonia tinggi berbahaya dengan sendirinya. Kipas baru dimatikan setelah suhu
**dan** amonia sama-sama aman (clear berbentuk AND).

### 3.3 Pond low oxygen (andi)

```
IF    (dissolved_oxygen < 4) OR (dissolved_oxygen < 5 AND water_temp > 30)
THEN  aerator = ON · alarm Critical
CLEAR bila dissolved_oxygen > 6
THEN  aerator = OFF
```

Ini contoh **ambang bertingkat**. Di bawah 4 mg/L kondisinya selalu berbahaya. Di bawah
5 mg/L berbahaya bila air hangat, karena air hangat menahan lebih sedikit oksigen dan ikan
lebih aktif. Tanpa durasi: aerator langsung menyala.

### 3.4 Cold chain breach (rina)

```
IF    (temperature > 8) OR (temperature > 6 AND humidity > 85)     selama 120 s
THEN  compressor_boost = ON · command "buzzer" {"seconds": 10} · alarm Critical
CLEAR bila (temperature < 5 AND humidity < 80)
THEN  compressor_boost = OFF
```

Vaksin harus tetap di 2–8 °C. Udara lembap pada suhu di atas 6 °C biasanya berarti segel pintu
bocor, sehingga peringatan diberikan **sebelum** batas 8 °C terlewati. Aturan ini memakai
empat jenis aksi sekaligus: set aktuator, kirim command, alarm, dan aksi saat pulih.

### 3.5 Server room cooling (budi), sensor dan aktuator di device berbeda

```
IF    (temperature > 30) OR (temperature > 27 AND humidity > 70)  selama 60 s
      (dari Server Room Node)
THEN  fan_speed = 100   (di Lab Relay Board)
CLEAR bila temperature < 25
THEN  fan_speed = 25
```

Aksi tidak harus mengenai device yang sama dengan sensornya. Aksi boleh mengenai device lain
milik pemilik yang sama.

### 3.6 Weather station silent (siti), event "device diam"

```
WHEN  Greenhouse Weather Station tidak mengirim data selama 900 s
THEN  alarm Warning
```

Aturan ini **nonaktif secara bawaan**, karena data contoh tidak terus dikirim dan alarmnya
akan langsung muncul. Aktifkan dari halaman aturan untuk mencobanya.

### 3.7 Contoh rule chain (tahap 8)

`seed_rules` juga membuat empat contoh model **Flow-based rule chain**:

| Aturan | Pemilik | Device |
|---|---|---|
| Lab power monitor | budi | Lab Power Meter |
| Pond pH watch | andi | Fish Pond Node |
| Cold room door check | rina | Cold Storage Unit |
| Greenhouse watering chain (nonaktif bawaan) | siti | Greenhouse Node |

Graf, pesan yang bisa dikirim, dan jejak hasilnya dijelaskan di
[flow-mekanisme.md](flow-mekanisme.md#8-contoh-lengkap-lab-power-monitor).

## 4. Mencoba aturan

### 4.1 Persiapan

```bash
python manage.py runall        # web + mqtt + coap + rule engine (lihat menjalankan-semua-layanan.md)
```

Login sebagai pemilik aturan, buka **Rules**, lalu buka aturannya. Kotak **Last evaluation**
memperlihatkan apa yang dilihat rule engine.

Data dikirim dengan perintah `send_telemetry` dari terminal lain. Perintah ini berpura-pura
menjadi device dan menyimpan data lewat jalur yang sama dengan HTTP/MQTT/CoAP:

```bash
python manage.py send_telemetry "<nama device>" key=nilai [key=nilai ...] [--owner username]
```

> **Penting: kirim semua variabel yang dipakai aturan.** Data dari `seed_demo` hanya sampai
> waktu perintah itu dijalankan. Bila sudah lewat dari batas umur aturan (10–15 menit),
> nilai-nilai lama itu dianggap **tidak diketahui**, dan aturan tidak akan aktif. Ini
> perilaku yang benar (lihat eca-mekanisme.md bagian 3). Skenario 4.6 sengaja menunjukkannya.

### 4.2 Kolam: aktif seketika (login `andi`)

```bash
python manage.py send_telemetry "Fish Pond Node" dissolved_oxygen=4.6 water_temp=31
```

Grup 2 (`DO < 5 AND air > 30`) bernilai benar, dan karena aturan ini tanpa durasi:

- status langsung **Active**,
- Aerator diminta ON,
- alarm **Critical** "Low oxygen in the pond: …" muncul.

```bash
python manage.py send_telemetry "Fish Pond Node" dissolved_oxygen=6.5
```

Kondisi clear `DO > 6` benar: Aerator diminta OFF dan alarm di-clear otomatis.

Variasi: kirim `dissolved_oxygen=4.6 water_temp=27`. Grup 2 salah (air tidak hangat) dan
grup 1 juga salah (4,6 tidak < 4), sehingga aturan **tidak** aktif.

### 4.3 Kandang ayam: AND, durasi, dan email (login `andi`)

```bash
python manage.py send_telemetry "Poultry House Node" temperature=34 humidity=75 ammonia=12
```

1. Status langsung **Pending**: grup 1 benar, menunggu 30 detik.
2. Setelah ±30 detik status menjadi **Active**:
   - Exhaust fan diminta ON,
   - alarm Major dibuat,
   - email ke andi@example.com tercetak di terminal proses yang menjalankan aksi: baris
     `rules |` pada `runall`, atau terminal `send_telemetry` bila tanpa Redis (mode inline).
     Bila `EMAIL_HOST` diisi, email terkirim sungguhan.
3. Pulihkan dengan:
   ```bash
   python manage.py send_telemetry "Poultry House Node" temperature=28
   ```
   Clear (`suhu < 29 AND amonia < 15`) benar karena amonia terakhir (12) masih segar. Kipas
   diminta OFF.

Variasi:

- Kirim `temperature=34 humidity=60 ammonia=12`: panas tetapi tidak lembap, sehingga aturan
  tidak aktif.
- Kirim `ammonia=30` (bersama suhu dan kelembapan normal): grup 2 benar, sehingga aturan aktif.

### 4.4 Gudang pendingin: command ke device (login `rina`)

```bash
python manage.py send_telemetry "Cold Storage Unit" temperature=7 humidity=90
```

Suhu belum melewati 8 °C, tetapi kelembapan 90 % memenuhi grup 2. Setelah 120 detik:

- Compressor boost diminta ON,
- command `buzzer {"seconds": 10}` muncul di daftar **Commands** halaman device,
- alarm **Critical** dibuat.

Rule chain "Cold room door check" bereaksi pada pesan yang sama tanpa menunggu: command
`buzzer {"seconds": 5}` langsung masuk antrean (lihat [flow-mekanisme.md](flow-mekanisme.md#92-cold-room-door-check-rina-and-dengan-filter-berurutan)).

```bash
python manage.py send_telemetry "Cold Storage Unit" temperature=4 humidity=75
```

Kedua kondisi clear benar, sehingga aturan pulih. Mengirim hanya `temperature=4` belum cukup
bila kelembapan terakhir masih 90: clear berbentuk AND.

### 4.5 Ruang server: lintas device (login `budi`)

```bash
python manage.py send_telemetry "Server Room Node" temperature=31 humidity=50
```

Setelah 60 detik, `fan_speed` di **Lab Relay Board** diminta 100 %. Kirim `temperature=24`
untuk mengembalikannya ke 25 %.

### 4.6 Greenhouse: lintas device dan data basi (login `siti`)

**Langkah A, sengaja salah.** Kirim data tanah dan suhu saja:

```bash
python manage.py send_telemetry "Greenhouse Node" soil_moisture=15 temperature=34
```

Status tetap **Idle**. Kotak *Last evaluation* menunjukkan baris `rain` dengan
**? unknown (stale …)**, karena curah hujan terakhir berasal dari `seed_demo` dan sudah
terlalu tua. Hasilnya:

- grup 1 = T AND T AND U = U,
- grup 2 = T AND U = U,
- ekspresi = U, sehingga pompa tidak dinyalakan berdasarkan data hujan yang tidak diketahui.

**Langkah B.** Kirim data stasiun cuaca, lalu data tanah:

```bash
python manage.py send_telemetry "Greenhouse Weather Station" rain=0
python manage.py send_telemetry "Greenhouse Node" soil_moisture=25 temperature=34
```

Grup 1 benar, status **Pending**, dan setelah 60 detik **Active**: pompa ON dan alarm Warning
"Irrigation started: …". Perhatikan pesan alarmnya memuat nilai dari dua device sekaligus.

**Langkah C.** Hujan turun:

```bash
python manage.py send_telemetry "Greenhouse Weather Station" rain=3
```

Grup clear kedua (`rain > 2`) benar, sehingga aturan pulih dan pompa OFF, walaupun tanah masih
kering.

**Langkah D, cooldown.** Hujan reda dan tanah masih kering. Dalam 5 menit sejak aktivasi
terakhir, kirim lagi `rain=0` lalu `soil_moisture=25 temperature=34`. Aturan bertahan di
**Pending** sampai 300 detik sejak aktivasi sebelumnya terlewati, baru kemudian aktif lagi.

### 4.7 Device diam (login `siti`)

1. Buka **Weather station silent**, lalu tekan **Enable**. Bila stasiun cuaca sudah lebih dari
   15 menit tidak mengirim data (misalnya `seed_demo` dijalankan kemarin), alarm Warning
   "Greenhouse Weather Station has sent no data for … s" langsung muncul dalam satu detik.
2. Kirim satu data (`rain=0`): aturan pulih dan alarm di-clear.
3. Jangan kirim data lagi. Setelah 15 menit alarm muncul kembali.

## 5. Di mana data contoh didefinisikan

| File | Isi |
|---|---|
| `devices/management/commands/seed_demo.py` | `DEMO_USERS`, `DEMO_LOCATIONS`, `DEMO_DEVICES`, `DEMO_ACTUATORS` |
| `rules/management/commands/seed_rules.py` | `DEMO_RULES`: setiap aturan sebagai daftar grup kondisi dan aksi |
| `devices/management/commands/send_telemetry.py` | perintah pengirim data tiruan |
| `rules/tests.py` → `SampleRulesTests` | setiap skenario di bagian 4 dijalankan sebagai tes otomatis |

Untuk menambah aturan contoh sendiri, salin salah satu entri `DEMO_RULES`. Kondisi ditulis
sebagai `[[(device, key, op, ambang), ...], ...]`: daftar luar = OR, daftar dalam = AND.
Setelah itu jalankan `python manage.py seed_rules`.
