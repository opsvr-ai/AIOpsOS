# AIOpsOS Frontend Startup (Windows PowerShell)
# Usage: .\scripts\start-frontend.ps1

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $RootDir

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  AIOpsOS - Frontend Startup (Windows)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# 1. Node / pnpm ---------------------------------------------------------
Write-Host ""
Write-Host "[1/2] Checking Node.js environment..." -ForegroundColor Yellow

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "  ERROR: Node.js is not installed." -ForegroundColor Red
    Write-Host "  Install from: https://nodejs.org/ (LTS recommended)" -ForegroundColor Red
    exit 1
}

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host "  pnpm not found. Installing via corepack..." -ForegroundColor Yellow
    corepack enable
    corepack prepare pnpm@latest --activate
    if ($LASTEXITCODE -ne 0) { throw "pnpm setup failed" }
}

# 2. Install deps + Dev Server -------------------------------------------
Write-Host ""
Write-Host "[2/2] Syncing dependencies and starting Vite..." -ForegroundColor Yellow
Set-Location "$RootDir\web"
pnpm install
if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }

Write-Host ""
Write-Host "Starting Vite dev server..." -ForegroundColor Yellow
Write-Host "  -> http://localhost:5173" -ForegroundColor White
Write-Host ""

pnpm dev
