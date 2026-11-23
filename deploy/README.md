# Berkas deploy

Contoh konfigurasi server produksi untuk panduan [docs/deploy-vps.md](../docs/deploy-vps.md).
Semua berkas memakai jalur dan nama berikut; ubah bila berbeda:

| Hal | Nilai |
|---|---|
| Pengguna Linux aplikasi | `iot` |
| Kode | `/srv/iot/app` (hasil `git clone`) |
| Virtualenv Python | `/srv/iot/venv` |
| Akun broker Mosquitto | dibuat di `/srv/iot/mosquitto/`, disalin ke `/etc/mosquitto/iot/` |
| Backup | `/srv/iot/backups` |
| Domain contoh | `iot.contoh.my.id` (ganti dengan domain Anda) |

| Berkas | Dipasang ke |
|---|---|
| `nginx/iot-platform-http.conf` | `/etc/nginx/sites-available/iot-platform` (sebelum ada sertifikat) |
| `nginx/iot-platform.conf` | `/etc/nginx/sites-available/iot-platform` (setelah sertifikat terbit) |
| `../compose.yml`, `../Dockerfile` | layanan Python platform sebagai container (fase 4, `docs/deploy-vps.md` 14.7) |
| `scripts/update.sh` | pembaruan dengan container; `scripts/update-systemd.sh` untuk cara lama |
| `systemd/*.service`, `*.timer`, `*.path`, `iot.target` | `/etc/systemd/system/` (sejak fase 4: timer, `iot-mqtt-reload.*`, backup; unit layanan `iot-web/-mqtt/-rules/-housekeeping/-coap` dan `iot.target` hanya untuk cara lama) |
| `mosquitto/iot.conf` | `/etc/mosquitto/conf.d/iot.conf` |
| `mosquitto/certbot-deploy-hook.sh` | `/etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh` |
| `scripts/backup.sh` | dijalankan oleh `iot-backup.timer` |
| `env.production.example` | contoh isi `/srv/iot/app/.env` |
