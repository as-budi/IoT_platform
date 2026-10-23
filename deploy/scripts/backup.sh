#!/bin/bash
# Backup harian (dijalankan iot-backup.timer sebagai user iot):
#   - dump database MySQL (dikompres)
#   - file firmware yang diunggah (private_media)
# Disimpan di /srv/iot/backups, yang lebih tua dari 14 hari dihapus.
# Backup di server yang SAMA tidak menolong bila servernya hilang: aktifkan juga
# snapshot otomatis di penyedia VPS, dan sesekali unduh backup ke komputer Anda.
set -euo pipefail
APP=/srv/iot/app
DEST=/srv/iot/backups
KEEP_DAYS=14
STAMP=$(date +%Y%m%d-%H%M)

# Baca DB_* dari .env tanpa menjalankan isinya sebagai skrip.
get() { grep -E "^$1=" "$APP/.env" | tail -1 | cut -d= -f2-; }
DB_NAME=$(get DB_NAME); DB_USER=$(get DB_USER); DB_PASSWORD=$(get DB_PASSWORD)

umask 077   # file backup hanya bisa dibaca pemiliknya
mkdir -p "$DEST"
# Password lewat variabel lingkungan, bukan argumen (argumen terlihat di `ps`).
MYSQL_PWD="$DB_PASSWORD" mysqldump --single-transaction --no-tablespaces -u "$DB_USER" "$DB_NAME" \
  | gzip > "$DEST/db-$STAMP.sql.gz"
if [ -d "$APP/private_media" ]; then
  tar -czf "$DEST/media-$STAMP.tar.gz" -C "$APP" private_media
fi
find "$DEST" -type f -mtime +"$KEEP_DAYS" -delete
echo "Backup done: $DEST/db-$STAMP.sql.gz"
