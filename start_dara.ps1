#!/usr/bin/env pwsh
<#
.SYNOPSIS
    DARA full-stack startup script.
    Starts Docker Desktop (if needed), waits for Postgres/Redis,
    launches the FastAPI backend, and opens the frontend dev server.

.USAGE
    Right-click → "Run with PowerShell"   OR   pwsh .\start_dara.ps1
#>

$ErrorActionPreference = "Continue"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║        DARA  —  Startup Script       ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 1. Docker Desktop ──────────────────────────────────────────
function Wait-Docker {
    $maxWait = 90
    $waited  = 0
    while ($waited -lt $maxWait) {
        $ok = docker info 2>$null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep 5
        $waited += 5
        Write-Host "  Waiting for Docker... ($waited/$maxWait s)" -ForegroundColor Yellow
    }
    return $false
}

$dockerRunning = docker info 2>$null; $dockerOk = $LASTEXITCODE -eq 0
if (-not $dockerOk) {
    Write-Host "► Starting Docker Desktop..." -ForegroundColor Yellow
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
    $dockerOk = Wait-Docker
}

if (-not $dockerOk) {
    Write-Host "✗ Docker Desktop did not start in time. Please open it manually." -ForegroundColor Red
    exit 1
}
Write-Host "✓ Docker is running" -ForegroundColor Green

# ── 2. Switch to right context ─────────────────────────────────
docker context use desktop-linux 2>$null
if ($LASTEXITCODE -ne 0) { docker context use default 2>$null }

# ── 3. Start containers ────────────────────────────────────────
Write-Host "► Starting Docker containers..." -ForegroundColor Yellow
docker compose -f docker-compose.dev.yml up -d 2>&1 | Out-Null

# Wait for Postgres to be healthy
Write-Host "► Waiting for Postgres to be ready..." -ForegroundColor Yellow
$pgReady = $false
for ($i = 0; $i -lt 20; $i++) {
    $status = docker inspect --format "{{.State.Health.Status}}" dara_postgres 2>$null
    if ($status -eq "healthy") { $pgReady = $true; break }
    Start-Sleep 3
    Write-Host "  Postgres status: $status ($($i*3)s)" -ForegroundColor Yellow
}
if ($pgReady) {
    Write-Host "✓ Postgres is healthy" -ForegroundColor Green
} else {
    Write-Host "⚠ Postgres health uncertain — continuing anyway" -ForegroundColor Yellow
}

# ── 4. FastAPI Backend ─────────────────────────────────────────
Write-Host "► Starting FastAPI backend on port 8000..." -ForegroundColor Yellow
$backend = Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$ROOT'; Write-Host 'DARA Backend' -ForegroundColor Cyan; python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload"
) -PassThru

# Wait for backend to be responsive
Write-Host "► Waiting for backend to be ready..." -ForegroundColor Yellow
$backendReady = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2 -ErrorAction Stop
        if ($h.status -eq "ok") { $backendReady = $true; break }
    } catch {}
    Start-Sleep 3
}
if ($backendReady) {
    Write-Host "✓ Backend is ready at http://localhost:8000" -ForegroundColor Green
} else {
    Write-Host "⚠ Backend may still be starting up" -ForegroundColor Yellow
}

# ── 5. Vite Frontend ───────────────────────────────────────────
Write-Host "► Starting Vite frontend..." -ForegroundColor Yellow
$frontend = Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$ROOT\admin_ui'; Write-Host 'DARA Frontend' -ForegroundColor Cyan; npm run dev"
) -PassThru

Start-Sleep 5

# ── 6. Find the actual Vite port ───────────────────────────────
$vitePort = $null
foreach ($port in 8080, 8081, 8082, 8083) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$port" -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $vitePort = $port; break }
    } catch {}
}

Write-Host ""
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  DARA is running!" -ForegroundColor Green
Write-Host ""
if ($vitePort) {
    Write-Host "  Frontend  → http://localhost:$vitePort" -ForegroundColor White
} else {
    Write-Host "  Frontend  → http://localhost:8081 (check terminal)" -ForegroundColor White
}
Write-Host "  Backend   → http://localhost:8000" -ForegroundColor White
Write-Host "  API Docs  → http://localhost:8000/docs" -ForegroundColor White
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Open browser
if ($vitePort) {
    Start-Process "http://localhost:$vitePort"
}
