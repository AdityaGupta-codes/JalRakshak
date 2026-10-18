#!/bin/sh
# Linux/macOS version of restore.ps1:  ./scripts/restore.sh [file.dump]
set -e
if [ -z "${1:-}" ]; then docker compose exec backup sh -c "ls -lh /backups"; exit 0; fi
printf "This replaces ALL current data with %s. Type YES to continue: " "$1"; read -r a
[ "$a" = "YES" ] || { echo Cancelled; exit 1; }
docker compose stop backend
docker compose exec backup sh -c "pg_restore -h db -U postgres -d jalrakshak --clean --if-exists --no-owner /backups/$1"
docker compose start backend
