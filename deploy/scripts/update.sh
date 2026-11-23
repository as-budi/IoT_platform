#!/bin/bash
# Memperbarui IoT Platform di server (container, fase 4 rencana container). Jalankan dengan sudo:
#   sudo /srv/iot/app/deploy/scripts/update.sh
#
# Urutan: git pull -> build image -> check --deploy -> migrate -> collectstatic
#         -> (sekali saja) hentikan unit systemd lama iot-web/-mqtt/-rules/-housekeeping/-coap
#         -> docker compose up -d -> tunggu web menjawab di 127.0.0.1:8001.
# check/migrate/collectstatic berjalan di container sementara dari image BARU sebelum layanan
# diganti; bila gagal, skrip berhenti dan versi lama tetap melayani.
# Cara lama (virtualenv + systemd): deploy/scripts/update-systemd.sh.
set -euo pipefail
APP="$(cd "$(dirname "$0")/../.." && pwd)"
as_iot() { sudo -u iot -H "$@"; }
OLD_UNITS="iot-web.service iot-mqtt.service iot-rules.service iot-housekeeping.service iot-coap.service"

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }
[ -f "$APP/.env" ] || { echo "Missing $APP/.env (docs/deploy-vps.md, 7.4)." >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is not installed (docs/deploy-vps.md, bagian Container)." >&2; exit 1; }

# CoAP sedang dipakai (unit lama aktif) tetapi belum diaktifkan untuk container: berhenti SEBELUM
# apa pun diubah, supaya device CoAP tidak tiba-tiba kehilangan server.
if systemctl is-active --quiet iot-coap.service && ! grep -qE '^COMPOSE_PROFILES=.*\bcoap\b' "$APP/.env"; then
  echo "iot-coap is running but COMPOSE_PROFILES=coap is not in $APP/.env: add it first." >&2
  exit 1
fi

# Container berjalan dengan UID/GID user iot di host: bisa menulis private_media/, staticfiles/,
# dan /srv/iot/mosquitto milik iot.
APP_UID=$(id -u iot); APP_GID=$(id -g iot)
export APP_UID APP_GID
dc() { docker compose --project-directory "$APP" -f "$APP/compose.yml" "$@"; }
manage() { dc run --rm -T manage python manage.py "$@"; }

echo "==> git pull"
as_iot git -C "$APP" pull --ff-only
as_iot mkdir -p "$APP/staticfiles" "$APP/private_media"

echo "==> build"
dc build --pull

echo "==> check --deploy"
manage check --deploy --fail-level WARNING
echo "==> migrate"
manage migrate --noinput
echo "==> collectstatic"
manage collectstatic --noinput --verbosity 0

# Pertama kali: layanan systemd lama masih memegang port 8001 / 5683 dan koneksi mqtt_bridge.
active=""
for unit in $OLD_UNITS; do systemctl is-active --quiet "$unit" && active="$active $unit"; done
if [ -n "$active" ]; then
  echo "==> stop systemd services (first switch to containers):$active"
  systemctl disable --now iot.target $OLD_UNITS 2>/dev/null || true
fi

echo "==> up"
dc up -d --remove-orphans
for i in $(seq 1 45); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/accounts/login/ || true)
  if [ "${code:-000}" != 000 ] && [ "$code" -lt 500 ]; then break; fi
  [ "$i" = 45 ] && { echo "web does not answer on 127.0.0.1:8001 (docker compose logs web)." >&2; exit 1; }
  sleep 1
done
dc ps
docker image prune -f >/dev/null
echo "Done ($(git -C "$APP" rev-parse --short HEAD))."
