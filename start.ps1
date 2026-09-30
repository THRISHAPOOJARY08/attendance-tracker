# start.ps1 — Attendance Web App launcher
# Run from the attendance-web folder:
#   .\start.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Attendance Tracker Web App — Starting   " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Copy env.txt → .env if missing ────────────────────────────────────────
if (-not (Test-Path ".env")) {
    if (Test-Path "env.txt") {
        Write-Host "[...] Copying env.txt to .env" -ForegroundColor Yellow
        Copy-Item "env.txt" ".env"
    } else {
        Write-Host "[ERROR] No env.txt found." -ForegroundColor Red; exit 1
    }
}
Write-Host "[OK] .env ready." -ForegroundColor Green

# ── 2. Check Python ───────────────────────────────────────────────────────────
try {
    $v = python --version 2>&1
    Write-Host "[OK] $v" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python not found. Install Python 3.11+ from https://python.org" -ForegroundColor Red; exit 1
}

# ── 3. Virtual environment ────────────────────────────────────────────────────
if (-not (Test-Path ".venv")) {
    Write-Host "[...] Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}
$activate = ".venv\Scripts\Activate.ps1"
if (Test-Path $activate) { & $activate }
Write-Host "[OK] Virtual environment ready." -ForegroundColor Green

# ── 4. Install dependencies ───────────────────────────────────────────────────
Write-Host "[...] Installing dependencies..." -ForegroundColor Cyan
pip install -r requirements.txt --quiet
Write-Host "[OK] Dependencies installed." -ForegroundColor Green

# ── 5. Check if any users exist — run seed if not ────────────────────────────
Write-Host "[...] Checking for existing users..." -ForegroundColor Cyan
$userCheck = python -c "
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from motor.motor_asyncio import AsyncIOMotorClient
async def check():
    c = AsyncIOMotorClient(os.environ.get('MONGO_URI','mongodb://localhost:27017'))
    db = c[os.environ.get('MONGO_DB_NAME','attendance_web')]
    n = await db['users'].count_documents({})
    c.close()
    print(n)
asyncio.run(check())
" 2>$null

if ($userCheck -eq "0" -or $userCheck -eq $null) {
    Write-Host ""
    Write-Host "  No users found. Let's create the first manager account." -ForegroundColor Yellow
    Write-Host ""
    python seed.py
    Write-Host ""
}

# ── 6. Launch ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  Open in your browser:" -ForegroundColor Green
Write-Host "  http://localhost:8000" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Press Ctrl+C to stop the server." -ForegroundColor Gray
Write-Host ""

uvicorn app.main:app --reload --port 8000
