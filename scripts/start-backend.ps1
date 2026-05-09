# AIOpsOS Backend Startup (Windows PowerShell)
# For local dev: assumes DB/Redis/Kafka are running in Docker already.
# Usage: .\scripts\start-backend.ps1 [-NoEnv] [-NoMigrate]

param(
    [switch]$NoEnv,
    [switch]$NoMigrate
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $RootDir

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  AIOpsOS - Backend Startup (Windows)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# Helpers ---------------------------------------------------------------

function _ComputePoetryHash {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $raw = (Get-Content "pyproject.toml" -Raw) + (Get-Content "poetry.lock" -Raw)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($raw)
    [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "")
}

function _CheckPort($Port, $Name) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $tcp.Connect("localhost", $Port)
        Write-Host "  $Name (localhost:$Port) - OK" -ForegroundColor Green
    }
    catch {
        Write-Host "  ERROR: $Name (localhost:$Port) is not reachable." -ForegroundColor Red
        Write-Host "  Start infrastructure first: docker compose -f deploy/docker-compose.dev.yml up -d"
        exit 1
    }
    finally {
        $tcp.Dispose()
    }
}

# 0. Python env ----------------------------------------------------------

function Init-PythonEnv {
    Write-Host ""
    Write-Host "[0/3] Initializing Python environment..." -ForegroundColor Yellow

    if (-not (Get-Command poetry -ErrorAction SilentlyContinue)) {
        Write-Host "  ERROR: Poetry is not installed." -ForegroundColor Red
        Write-Host "  Install from: https://python-poetry.org/docs/#installation" -ForegroundColor Red
        exit 1
    }

    Set-Location "$RootDir\server"
    $hashFile = Join-Path $RootDir "server\.poetry-hash"

    if (-not (Test-Path ".venv")) {
        Write-Host "  No venv found. Running poetry install..." -ForegroundColor Yellow
        poetry install --no-root
        if ($LASTEXITCODE -ne 0) { throw "poetry install failed" }
        Write-Host "  Done." -ForegroundColor Green
        _ComputePoetryHash | Set-Content $hashFile
        return
    }

    $currentHash = _ComputePoetryHash
    $savedHash = if (Test-Path $hashFile) { (Get-Content $hashFile).Trim() } else { "" }

    if ($currentHash -ne $savedHash) {
        Write-Host "  Dependencies changed. Running poetry install..." -ForegroundColor Yellow
        poetry install --no-root
        if ($LASTEXITCODE -ne 0) { throw "poetry install failed" }
        Write-Host "  Done." -ForegroundColor Green
        $currentHash | Set-Content $hashFile
    }
    else {
        Write-Host "  Python environment is up to date." -ForegroundColor Green
    }
}

if (-not $NoEnv) { Init-PythonEnv }
else {
    Write-Host ""
    Write-Host "[0/3] Skipping environment (-NoEnv)" -ForegroundColor DarkGray
}

# 1. Check infrastructure connectivity -----------------------------------

Write-Host ""
Write-Host "[1/3] Checking infrastructure connectivity..." -ForegroundColor Yellow
_CheckPort 5432 "PostgreSQL"
_CheckPort 6379 "Redis"
_CheckPort 9094 "Kafka"
Write-Host "  All services reachable." -ForegroundColor Green

# 2. DB Migrations --------------------------------------------------------

if (-not $NoMigrate) {
    Write-Host ""
    Write-Host "[2/3] Running database migrations..." -ForegroundColor Yellow
    Set-Location "$RootDir\server"
    poetry run alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Migration failed" }
    Write-Host "  Migrations complete." -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "[2/3] Skipping migrations (-NoMigrate)" -ForegroundColor DarkGray
}

# 3. FastAPI Server -------------------------------------------------------

Write-Host ""
Write-Host "[3/3] Starting FastAPI server..." -ForegroundColor Yellow
Write-Host "  -> http://localhost:8000" -ForegroundColor White
Write-Host "  -> API docs: http://localhost:8000/docs" -ForegroundColor White
Write-Host ""

Set-Location "$RootDir\server"
poetry run python run_server.py
