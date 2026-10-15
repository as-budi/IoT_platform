#!/bin/sh
# /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
# Certbot menjalankan berkas ini setiap kali sertifikat diperbarui (± tiap 60 hari).
# Mosquitto berjalan sebagai user "mosquitto" yang tidak boleh membaca /etc/letsencrypt,
# jadi sertifikat disalin ke /etc/mosquitto/certs dengan pemilik yang tepat.
set -eu
DOMAIN=iot.contoh.my.id
install -d -m 750 -o root -g mosquitto /etc/mosquitto/certs
install -m 644 -o root -g mosquitto "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" /etc/mosquitto/certs/fullchain.pem
install -m 640 -o root -g mosquitto "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   /etc/mosquitto/certs/privkey.pem
systemctl reload mosquitto
