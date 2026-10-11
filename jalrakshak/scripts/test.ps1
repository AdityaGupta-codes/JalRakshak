# Runs every test suite inside Docker, so nothing but Docker Desktop is needed on Windows.
#   From the project folder in PowerShell:   .\scripts\test.ps1
# (If PowerShell refuses to run scripts:   Set-ExecutionPolicy -Scope Process Bypass   then try again.)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$net = "jalrakshak-test-net"
$db = "jalrakshak-test-db"

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $what" -ForegroundColor Red; Cleanup; exit 1 } }
function Cleanup { docker rm -f $db 2>$null | Out-Null; docker network rm $net 2>$null | Out-Null }

Step "1/4  C++ scheduler: build and run its tests"
docker run --rm -v "${root}/scheduler:/src" -w /src gcc:13 sh -c "make clean >/dev/null && make && make test"
Check "scheduler tests"

Step "2/4  Start a throw-away PostgreSQL for the tests"
Cleanup
docker network create $net | Out-Null
docker run -d --rm --name $db --network $net -e POSTGRES_PASSWORD=test -e POSTGRES_DB=jalrakshak_test postgres:16 | Out-Null
for ($i = 0; $i -lt 30; $i++) {
    docker exec $db pg_isready -h 127.0.0.1 -U postgres 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 1
}
Check "test database did not start"

Step "3/4  Backend tests (service layer, real database, HTTP API) and data-science tests"
docker run --rm --network $net -v "${root}:/app" -w /app `
    -e SCHEDULER_BIN=/app/scheduler/build/jalrakshak-scheduler `
    -e JAL_TEST_DSN="postgresql://postgres:test@${db}:5432/jalrakshak_test" `
    python:3.12 sh -c "pip install -q --root-user-action=ignore -r backend/requirements.txt -r ml/requirements.txt httpx && cd backend && python -m unittest discover -s tests -t . && cd ../ml && python -m unittest discover -s tests -t ."
Check "backend / ml tests"

Step "4/4  Concurrency demo (4 clients apply a plan at the same moment)"
docker run --rm --network $net -v "${root}:/app" -w /app/backend `
    -e SCHEDULER_BIN=/app/scheduler/build/jalrakshak-scheduler `
    -e JAL_TEST_DSN="postgresql://postgres:test@${db}:5432/jalrakshak_test" `
    python:3.12 sh -c "pip install -q --root-user-action=ignore -r requirements.txt && python -m tests.concurrency_demo"
Check "concurrency demo"

Cleanup
Write-Host "`nAll tests passed." -ForegroundColor Green
