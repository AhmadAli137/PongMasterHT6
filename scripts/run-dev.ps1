# Launch the Edge Pong backend (sim mode) and the Vite dev server together.
# Usage:  ./scripts/run-dev.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "Starting Edge Pong backend (sim) on :8080 ..." -ForegroundColor Cyan
$backend = Start-Process -PassThru -WorkingDirectory "$root/backend" `
    -FilePath "python" -ArgumentList "-m", "edgepong.main"

Start-Sleep -Seconds 2
Write-Host "Starting Vite dev server on :5173 ..." -ForegroundColor Cyan
try {
    Push-Location "$root/frontend"
    npm run dev
} finally {
    Pop-Location
    Write-Host "Stopping backend (pid $($backend.Id)) ..." -ForegroundColor Yellow
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
}
