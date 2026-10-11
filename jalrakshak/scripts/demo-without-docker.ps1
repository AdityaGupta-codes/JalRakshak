# Runs the dashboard with the in-memory demo API (no Docker, no PostgreSQL). Needs Python 3.10+ on Windows.
# The C++ scheduler is compiled once inside Docker... or, if you have g++ (e.g. MSYS2), with "make" in scheduler/.
#   .\scripts\demo-without-docker.ps1      then open http://localhost:3000
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$exe = Join-Path $root "scheduler\build\jalrakshak-scheduler.exe"
if (-not (Test-Path $exe)) {
    if (Get-Command g++ -ErrorAction SilentlyContinue) {
        Write-Host "Compiling the scheduler with g++..."
        New-Item -ItemType Directory -Force -Path (Join-Path $root "scheduler\build") | Out-Null
        Push-Location (Join-Path $root "scheduler")
        g++ -std=c++17 -O2 -Iinclude src\*.cpp -o build\jalrakshak-scheduler.exe
        Pop-Location
    } else {
        Write-Host "No g++ found. Install MSYS2 (https://www.msys2.org) and its mingw-w64 g++, or use Docker: docker compose up --build" -ForegroundColor Yellow
        exit 1
    }
}
Start-Process python -ArgumentList "`"$root\frontend\dev\mock_api.py`" --bin `"$exe`" --port 8000"
Start-Process python -ArgumentList "-m http.server 3000" -WorkingDirectory "$root\frontend\public"
Start-Sleep -Seconds 2
Start-Process "http://localhost:3000"
Write-Host "Dashboard: http://localhost:3000   (close the two Python windows to stop)"
