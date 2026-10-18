#!/bin/sh
# Runs inside the "backup" container: a compressed pg_dump every BACKUP_INTERVAL_HOURS, keeping
# BACKUP_KEEP_DAYS days of files in /backups. Restore with scripts/restore.ps1 (Windows) or restore.sh.
set -u
INTERVAL=${BACKUP_INTERVAL_HOURS:-24}
KEEP=${BACKUP_KEEP_DAYS:-14}
until pg_isready -h db -U postgres -d jalrakshak >/dev/null 2>&1; do sleep 3; done
while true; do
  f="/backups/jalrakshak-$(date -u +%Y%m%d-%H%M%S).dump"
  if pg_dump -h db -U postgres -d jalrakshak -Fc -f "$f.tmp"; then
    mv "$f.tmp" "$f"
    echo "backup written: $f ($(du -h "$f" | cut -f1))"
  else
    rm -f "$f.tmp"
    echo "BACKUP FAILED at $(date -u)" >&2
  fi
  find /backups -name 'jalrakshak-*.dump' -mtime +"$KEEP" -delete
  sleep $((INTERVAL * 3600))
done
