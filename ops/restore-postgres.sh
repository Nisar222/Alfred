#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /protected/path/jamal_dialler_YYYYMMDDTHHMMSSZ.dump" >&2
  exit 64
fi

echo "This permanently replaces the current database. Type RESTORE to continue:"
read confirmation
[ "$confirmation" = "RESTORE" ] || { echo "Cancelled."; exit 1; }

docker compose exec -T db dropdb --if-exists --username="${POSTGRES_USER:-jamal}" "${POSTGRES_DB:-jamal_dialler}"
docker compose exec -T db createdb --username="${POSTGRES_USER:-jamal}" "${POSTGRES_DB:-jamal_dialler}"
docker compose exec -T db pg_restore --username="${POSTGRES_USER:-jamal}" --dbname="${POSTGRES_DB:-jamal_dialler}" --no-owner < "$1"
