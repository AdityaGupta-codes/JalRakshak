# Restores the database from a backup made by the "backup" service.
#   .\scripts\restore.ps1                      -> lists the backups
#   .\scripts\restore.ps1 jalrakshak-20261015-020000.dump
# WARNING: this replaces ALL current data with the backup. The backend is stopped during the restore.
param([string]$File)
$ErrorActionPreference = "Stop"
if (-not $File) {
    docker compose exec backup sh -c "ls -lh /backups"
    Write-Host "`nRun again with one of the file names above." -ForegroundColor Yellow
    exit 0
}
$answer = Read-Host "This replaces ALL current data with $File. Type YES to continue"
if ($answer -ne "YES") { Write-Host "Cancelled."; exit 1 }
docker compose stop backend
docker compose exec backup sh -c "pg_restore -h db -U postgres -d jalrakshak --clean --if-exists --no-owner /backups/$File"
docker compose start backend
Write-Host "Restored $File. Check the app, then the Operations page." -ForegroundColor Green
