$ErrorActionPreference = "Stop"

$PROJECT_ROOT = $PSScriptRoot
$API_DIR = Join-Path $PROJECT_ROOT "packages\api"

Clear-Host
Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "   SPEDA  Mark VI" -ForegroundColor Cyan
Write-Host "   Personal Executive Digital Assistant" -ForegroundColor Cyan
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  [ERROR] uv not found in PATH." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "  [ERROR] npm not found in PATH." -ForegroundColor Red
    exit 1
}

Write-Host "  > Starting backend (http://localhost:8000)" -ForegroundColor DarkCyan

$apiCmd = "Set-Location '$API_DIR'; Write-Host '  SPEDA API  -  http://localhost:8000' -ForegroundColor Cyan; uv run uvicorn app.main:app --port 8000 --reload --log-level info"

$apiProcess = Start-Process -FilePath "powershell" -ArgumentList @("-NoExit", "-Command", $apiCmd) -PassThru

Write-Host "  > Waiting for API to be ready..." -ForegroundColor DarkGray

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", 8000)
        $tcp.Close()
        $ready = $true
        break
    } catch { }
}

if ($ready) {
    Write-Host "  [OK] API is ready" -ForegroundColor Green
} else {
    Write-Host "  [WARN] API did not respond in 60s, continuing anyway" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  > Launching desktop app..." -ForegroundColor DarkCyan
Write-Host "  ============================================" -ForegroundColor DarkGray
Write-Host ""

Set-Location $PROJECT_ROOT

try {
    npm run heartbreaker:dev
} finally {
    Write-Host ""
    Write-Host "  Shutting down..." -ForegroundColor DarkGray
    if ($null -ne $apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue
    }
    $uvicornProcs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*uvicorn*" }
    foreach ($proc in $uvicornProcs) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  Done. Goodbye." -ForegroundColor Cyan
    Write-Host ""
}
