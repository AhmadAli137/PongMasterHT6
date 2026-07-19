# Build the frontend and serve everything from the backend on :8080.
# Usage:  ./scripts/run-prod.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "Building frontend ..." -ForegroundColor Cyan
Push-Location "$root/frontend"
try {
    if (-not (Test-Path "node_modules")) { npm install }
    npm run build
} finally { Pop-Location }

Write-Host "Serving Edge Pong on http://localhost:8080 ..." -ForegroundColor Green
Push-Location "$root/backend"
try { python -m edgepong.main } finally { Pop-Location }
