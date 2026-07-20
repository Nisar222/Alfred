#!/usr/bin/env sh
set -eu

# Run from the repository root or by a systemd timer. Backups contain personal
# data, so BACKUP_DIR must be a protected, encrypted filesystem or mounted vault.
: "${BACKUP_DIR:?Set BACKUP_DIR to a protected backup directory}"

mkdir -p "$BACKUP_DIR"
umask 077
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$BACKUP_DIR/jamal_dialler_${timestamp}.dump"

docker compose exec -T db pg_dump \
  --username="${POSTGRES_USER:-jamal}" \
  --format=custom \
  --no-owner \
  --dbname="${POSTGRES_DB:-jamal_dialler}" > "$target"

# Retain 14 daily backups unless your approved retention policy says otherwise.
find "$BACKUP_DIR" -type f -name 'jamal_dialler_*.dump' -mtime +14 -delete
