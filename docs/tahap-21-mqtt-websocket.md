# Tahap 21: MQTT over WebSocket untuk browser

Tahap ini membuka broker MQTT platform untuk **browser**. Kebutuhan utamanya adalah aplikasi
MQTT client web (repo `mqtt_client`, rencana tahap M0), tetapi alamat yang sama juga bisa dipakai
halaman web apa pun dengan pustaka MQTT.js.

## 1. Mengapa perlu?

Browser tidak bisa membuka koneksi TCP biasa. Yang tersedia hanya HTTP(S) dan **WebSocket**.
Karena itu `mqtt://…:1883` dan `mqtts://…:8883`, yang dipakai ESP32 dan `mosquitto_pub`, tidak
bisa dijangkau dari JavaScript di halaman web.

MQTT punya cara resmi untuk masalah ini: paket MQTT dibungkus di dalam frame WebSocket (subprotokol
`mqtt`). Mosquitto bisa melayaninya; cukup tambahkan satu listener.

```
ESP32 / mosquitto_pub ──── mqtts://iot.domain:8883 ────────────┐
                                                               ▼
browser (MQTT.js) ─ wss://iot.domain/mqtt ─▶ Nginx :443 ─▶ Mosquitto :9001 (localhost)
                                              (TLS)            ▲
mqtt_bridge (server) ───── mqtt://127.0.0.1:1883 ──────────────┘
```

Ketiga jalur masuk ke **broker yang sama**, dengan akun dan ACL yang sama. Pesan yang dikirim
browser lewat WebSocket diterima ESP32 lewat TCP, dan sebaliknya.

## 2. Yang diubah

| File | Perubahan |
|---|---|
| `deploy/mosquitto/iot.conf` | listener `9001` di `127.0.0.1`, `protocol websockets`, `max_connections 500` |
| `deploy/nginx/iot-platform.conf` | `location = /mqtt` → `127.0.0.1:9001` (Upgrade/Connection), `limit_conn` 20 koneksi per IP |
| `config/settings.py`, `.env` | `MQTT_PUBLIC_WS_URL`, mis. `wss://iot.domain-anda/mqtt` (kosong = tidak ditampilkan) |
| `devices/views.py` (`mqtt_info`) | menambahkan `ws_url` |
| `device_detail.html`, tab MQTT | baris **WebSocket** dan contoh kode MQTT.js |
| `devices/tests.py` | tes alamat WebSocket tampil/tidak |

## 3. Mengapa lewat Nginx, bukan port 9001 langsung?

1. **TLS cukup di satu tempat.** Nginx sudah memegang sertifikat Let's Encrypt untuk situs web,
   jadi Mosquitto cukup mendengar WebSocket biasa di localhost. Tidak perlu sertifikat kedua
   untuk listener ini.
2. **Port 443.** Jaringan kampus atau kantor sering memblokir port selain 80/443. `wss://…/mqtt`
   lewat port 443 hampir selalu lolos.
3. **Port 9001 tidak dibuka di firewall.** Satu-satunya jalan masuk adalah lewat Nginx, yang
   membatasi jumlah koneksi per IP (`limit_conn iot_mqtt_ws 20`).

`per_listener_settings false` di `iot.conf` membuat `password_file` dan `acl_file` berlaku
untuk semua listener. Aturan keamanannya tetap sama: anonymous ditolak, username = password =
access token, dan setiap device hanya boleh memakai topiknya sendiri.

`proxy_read_timeout 1h`: koneksi MQTT hidup lama. Klien mengirim PINGREQ setiap *keep alive*
(biasanya 60 detik), jadi koneksi yang sehat tidak pernah diam selama 1 jam.

## 4. Mencoba

**Di server.** Langkahnya ada di `docs/deploy-lightsail.md` bagian 9.5 dan 14.3.

Cek jabat tangan WebSocket. Jawaban `101 Switching Protocols` berarti Nginx dan Mosquitto
tersambung:

```bash
curl -si --http1.1 https://iot.domain-anda/mqtt \
     -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
     -H "Sec-WebSocket-Protocol: mqtt" | head -1
# HTTP/1.1 101 Switching Protocols
```

**Dengan pustaka paho-mqtt (Python)**, yang juga mendukung WebSocket:

```python
import paho.mqtt.client as mqtt

TOKEN = "access-token-device"
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")
client.ws_set_options(path="/mqtt")
client.tls_set()                       # CA sistem (Let's Encrypt)
client.username_pw_set(TOKEN, TOKEN)
client.on_connect = lambda c, u, f, rc, p: print("connected:", rc)
client.connect("iot.domain-anda", 443)
client.publish(f"iot/{TOKEN}/telemetry", '{"temperature": 25.1}')
client.loop_forever()
```

**Dari halaman web**, salin contoh MQTT.js di tab MQTT halaman device. Bisa juga memakai MQTTX
(aplikasi desktop): Host `wss://iot.domain-anda`, Port `443`, Path `/mqtt`.

## 5. Keterbatasan

- **Hanya `wss://`.** Halaman `https://` tidak boleh membuka `ws://` (aturan *mixed content*
  browser), dan memang tidak ada jalur `ws://` tanpa TLS di server.
- **Batas laju pesan per klien belum ada di Mosquitto.** Yang dibatasi baru jumlah koneksi per
  IP (Nginx) dan total koneksi listener (`max_connections`).
- Broker lain yang hanya punya TCP 1883/8883 tidak bisa dijangkau browser. Aplikasi MQTT client
  akan menjangkaunya lewat **relay** di server (rencana M4 di repo `mqtt_client`).
