#!/bin/bash
# Memperbarui aplikasi di server setelah ada perubahan kode (jalankan dengan sudo):
#   sudo /srv/iot/app/deploy/scripts/update.sh
# Urutan: ambil kode -> pasang paket -> migrasi -> file statis -> cek -> restart.
# Bila satu langkah gagal, skrip berhenti dan layanan lama tetap berjalan.
set -euo pipefail
APP=/srv/iot/app
VENV=/srv/iot/venv
as_iot() { sudo -u iot -H "$@"; }

cd "$APP"
echo "==> git pull"
as_iot git -C "$APP" pull --ff-only
echo "==> pip install"
as_iot "$VENV/bin/pip" install --quiet -r requirements.txt
echo "==> check --deploy"
as_iot "$VENV/bin/python" manage.py check --deploy --fail-level WARNING
echo "==> migrate"
as_iot "$VENV/bin/python" manage.py migrate --noinput
echo "==> collectstatic"
as_iot "$VENV/bin/python" manage.py collectstatic --noinput --verbosity 0
echo "==> restart"
systemctl restart iot.target
systemctl --no-pager --lines=0 status iot-web iot-mqtt iot-rules iot-housekeeping | grep -E "●|Active:" || true
echo "Done."
