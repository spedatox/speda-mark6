$ErrorActionPreference = "Stop"

$PROJECT_ROOT = $PSScriptRoot
$API_DIR = Join-Path $PROJECT_ROOT "packages\api"

# Set a custom window title
$Host.UI.RawUI.WindowTitle = "SPEDA Mark VI - Terminal"

# Helper function for standardized, color-coded logging
function Write-Log {
    param (
        [Parameter(Mandatory=$true)]
        [string]$Message,
        [ValidateSet("INFO", "SUCCESS", "WARN", "ERROR", "SYSTEM")]
        [string]$Level = "INFO"
    )
    $time = Get-Date -Format "HH:mm:ss"
    switch ($Level) {
        "INFO"    { Write-Host "[$time] " -NoNewline -ForegroundColor DarkGray; Write-Host "[~] " -NoNewline -ForegroundColor Cyan; Write-Host $Message -ForegroundColor White }
        "SUCCESS" { Write-Host "[$time] " -NoNewline -ForegroundColor DarkGray; Write-Host "[✓] " -NoNewline -ForegroundColor Green; Write-Host $Message -ForegroundColor White }
        "WARN"    { Write-Host "[$time] " -NoNewline -ForegroundColor DarkGray; Write-Host "[!] " -NoNewline -ForegroundColor Yellow; Write-Host $Message -ForegroundColor Yellow }
        "ERROR"   { Write-Host "[$time] " -NoNewline -ForegroundColor DarkGray; Write-Host "[x] " -NoNewline -ForegroundColor Red; Write-Host $Message -ForegroundColor Red }
        "SYSTEM"  { Write-Host "[$time] " -NoNewline -ForegroundColor DarkGray; Write-Host "[*] " -NoNewline -ForegroundColor Magenta; Write-Host $Message -ForegroundColor Gray }
    }
}

# Clear and print ASCII Header
Clear-Host
Write-Host ""
Write-Host " ╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host " ║                                                              ║" -ForegroundColor Cyan
Write-Host " ║   ███████╗██████╗ ███████╗██████╗  █████╗                    ║" -ForegroundColor Cyan
Write-Host " ║   ██╔════╝██╔══██╗██╔════╝██╔══██╗██╔══██╗                   ║" -ForegroundColor Cyan
Write-Host " ║   ███████╗██████╔╝█████╗  ██║  ██║███████║                   ║" -ForegroundColor Cyan
Write-Host " ║   ╚════██║██╔═══╝ ██╔══╝  ██║  ██║██╔══██║                   ║" -ForegroundColor Cyan
Write-Host " ║   ███████║██║     ███████╗██████╔╝██║  ██║                   ║" -ForegroundColor Cyan
Write-Host " ║   ╚══════╝╚═╝     ╚══════╝╚═════╝ ╚═╝  ╚═╝                   ║" -ForegroundColor Cyan
Write-Host " ║                                                              ║" -ForegroundColor Cyan
Write-Host " ║             MARK VI - PERSONAL EXECUTIVE ASSISTANT           ║" -ForegroundColor DarkCyan
Write-Host " ╚══════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Log "Initializing boot sequence..." "SYSTEM"
Start-Sleep -Milliseconds 400

# Dependency Checks
Write-Log "Verifying core dependencies..." "INFO"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Log "Critical failure: 'uv' not found in PATH." "ERROR"
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Log "Critical failure: 'npm' not found in PATH." "ERROR"
    exit 1
}
Write-Log "Dependencies verified." "SUCCESS"

# Start API
Write-Log "Igniting backend neural net (http://localhost:8000)..." "INFO"
$apiCmd = "Set-Location '$API_DIR'; Write-Host '  SPEDA API  -  http://localhost:8000' -ForegroundColor Cyan; uv run uvicorn app.main:app --port 8000 --reload --log-level info"
$apiProcess = Start-Process -FilePath "powershell" -ArgumentList @("-NoExit", "-Command", $apiCmd) -PassThru

# Spinner for API Wait
$time = Get-Date -Format "HH:mm:ss"
Write-Host "[$time] " -NoNewline -ForegroundColor DarkGray
Write-Host "[~] " -NoNewline -ForegroundColor Cyan
Write-Host "Awaiting API handshake...  " -NoNewline -ForegroundColor White

$spinner = @("|", "/", "-", "\")
$ready = $false
$counter = 0

# 600 iterations of 100ms = 60 seconds max
for ($i = 0; $i -lt 600; $i++) {
    Write-Host -NoNewline "`b$($spinner[$counter % 4])" -ForegroundColor Cyan
    $counter++
    Start-Sleep -Milliseconds 100

    if ($i % 10 -eq 0) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("127.0.0.1", 8000)
            $tcp.Close()
            $ready = $true
            break
        } catch { }
    }
}

# Clear the spinner character and move to the next line
Write-Host "`b "

if ($ready) {
    Write-Log "API connection established." "SUCCESS"
} else {
    Write-Log "API timeout (60s). Proceeding with degraded startup." "WARN"
}

# ── Forge link probe ────────────────────────────────────────────────────────
# The backend launches the Forge peer (Optimus Mark II) as a lifespan child; it
# connects back over the agents WebSocket a moment after boot. Probe GET /agents
# so the operator sees, at a glance, whether Optimus is running on the Forge or
# on its in-process fallback. Best-effort, non-fatal — a miss never blocks boot.
if ($ready) {
    # The endpoint is X-API-Key authenticated. Prefer an explicit env var, then
    # the managed override (~/.speda/.env), then the repo .env, else the dev key.
    function Get-SpedaApiKey {
        if ($env:SPEDA_API_KEY) { return $env:SPEDA_API_KEY }
        foreach ($f in @((Join-Path $env:USERPROFILE ".speda\.env"), (Join-Path $PROJECT_ROOT ".env"))) {
            if (Test-Path $f) {
                $line = Select-String -Path $f -Pattern '^\s*SPEDA_API_KEY\s*=' -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($line) {
                    $val = ($line.Line -replace '^\s*SPEDA_API_KEY\s*=', '').Trim().Trim('"')
                    if ($val) { return $val }
                }
            }
        }
        return "dev-key"
    }
    $apiKey = Get-SpedaApiKey
    $forgeOnline = $false
    # The peer registers within a few seconds of boot — retry briefly.
    for ($p = 0; $p -lt 6; $p++) {
        try {
            $agents = Invoke-RestMethod -Uri "http://127.0.0.1:8000/agents" `
                -Headers @{ "X-API-Key" = $apiKey } -TimeoutSec 3 -ErrorAction Stop
            if ($agents | Where-Object { $_.agent_id -eq "optimus" }) { $forgeOnline = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 800
    }
    if ($forgeOnline) {
        Write-Log "FORGE LINK ESTABLISHED - Optimus Mark II online." "SUCCESS"
    } else {
        Write-Log "Forge peer offline - in-process fallback active." "WARN"
    }
}

Write-Host ""
Write-Host " ──────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Log "Launching desktop interface..." "SYSTEM"
Write-Host " ──────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

Set-Location $PROJECT_ROOT

try {
    npm run heartbreaker:dev
} finally {
    Write-Host ""
    Write-Host " ╔══════════════════════════════════════════════════════════════╗" -ForegroundColor DarkGray
    Write-Host " ║                     INITIATING SHUTDOWN                      ║" -ForegroundColor DarkGray
    Write-Host " ╚══════════════════════════════════════════════════════════════╝" -ForegroundColor DarkGray
    Write-Host ""
    
    Write-Log "Terminating backend processes..." "INFO"
    if ($null -ne $apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue
    }
    
    # Catch any orphaned children. The backend's lifespan normally stops the
    # Forge peer + local sandbox itself; this sweep is belt-and-braces for a
    # backend that crashed without running its shutdown. Match uvicorn workers,
    # the Forge peer (`forge connect`), and the local sandbox exec server.
    $orphanProcs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*uvicorn*" -or
            $_.CommandLine -like "*forge connect*" -or
            $_.CommandLine -like "*sandbox*server.py*"
        }

    foreach ($proc in $orphanProcs) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Log "Killed orphaned worker (PID: $($proc.ProcessId))" "SYSTEM"
    }
    
    Write-Log "System offline. Goodbye." "SUCCESS"
    Write-Host ""
}