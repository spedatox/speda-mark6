# SPEDA Mark VI — build a (rebranded) desktop installer, pointed at your server.
#
#   powershell -File build-app.ps1 -ApiBase https://speda.yourdomain.com -ApiKey <SPEDA_API_KEY>
#   powershell -File build-app.ps1 -Agent ultron -ApiBase https://... -ApiKey <key>
#
# The API base + key are baked into the build (electron-vite MAIN_VITE_*), so the
# installed app talks to your server out of the box. ApiKey must match the
# SPEDA_API_KEY in the server's packages/igor/.env.
#
# -Agent picks the brand (name, model number, colour) AND the backend agent the
# app talks to (/chat/{agent}). One of: speda (default), ultron, centurion,
# sentinel, atomix, nightcrawler, optimus. See packages/heartbreaker profile/brands.ts.

param(
  [Parameter(Mandatory = $true)][string]$ApiBase,
  [Parameter(Mandatory = $true)][string]$ApiKey,
  [string]$Agent = "speda"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  Building SPEDA Mark VI [$Agent] -> $ApiBase" -ForegroundColor Cyan
Write-Host ""

# electron-vite exposes MAIN_VITE_* to the main process and VITE_* to the renderer.
$env:MAIN_VITE_SPEDA_API_BASE = $ApiBase
$env:MAIN_VITE_SPEDA_API_KEY  = $ApiKey
$env:VITE_AGENT               = $Agent

Set-Location (Join-Path $PSScriptRoot "packages\heartbreaker")

Write-Host "  > npm install" -ForegroundColor DarkCyan
npm install

Write-Host "  > npm run dist" -ForegroundColor DarkCyan
npm run dist

Write-Host ""
Write-Host "  Done. Installer is in packages\heartbreaker\dist\" -ForegroundColor Green
Write-Host "  It points at $ApiBase out of the box." -ForegroundColor Green
Write-Host ""
