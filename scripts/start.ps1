# Start PropertyIQ: database, local model, API and web app, then open the browser.
# The "PropertyIQ" desktop shortcut runs this. Safe to run again: anything already running is left alone.
# Stop: close the "PropertyIQ API" and "PropertyIQ Web" windows. Docker and Ollama keep running on their own.
# Log: logs\start.log

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root "apps\web"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "start.log"
$host.UI.RawUI.WindowTitle = "Starting PropertyIQ"
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
if (-not (Test-Path $docker)) { $docker = "docker" }

function Say($msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $log -Value $line
}

function Fail($msg) {
    Say "PROBLEM: $msg"
    Write-Host ""
    Write-Host "PropertyIQ did not start. Details are in $log" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

# True if something accepts TCP connections on 127.0.0.1:port.
function Up($port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { return $c.ConnectAsync("127.0.0.1", $port).Wait(700) -and $c.Connected } catch { return $false } finally { $c.Dispose() }
}

function WaitFor($port, $seconds, $what) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Up $port) { return $true }
        if ($i % 10 -eq 0) { Say "  waiting for $what... ($i s)" }
        Start-Sleep -Seconds 1
    }
    return (Up $port)
}

function DockerEngineReady() {
    & $docker info --format "{{.ServerVersion}}" *> $null
    return ($LASTEXITCODE -eq 0)
}

Add-Content -Path $log -Value "`n===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') start ====="
Write-Host "PropertyIQ - synthetic data, private local model`n" -ForegroundColor Cyan

# 1. Database (Postgres in Docker)
if (Up 5432) {
    Say "Database: running"
} else {
    if (-not (DockerEngineReady)) {
        $desktop = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"
        if (-not (Test-Path $desktop)) { $desktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe" }
        Say "Database: starting Docker Desktop (this can take a minute or two)"
        if (Test-Path $desktop) { Start-Process $desktop } else { Fail "Docker Desktop is not installed where expected." }
        for ($i = 0; $i -lt 180 -and -not (DockerEngineReady); $i += 3) {
            if ($i % 15 -eq 0) { Say "  waiting for Docker to finish starting... ($i s)" }
            Start-Sleep -Seconds 3
        }
        if (-not (DockerEngineReady)) { Fail "Docker didn't finish starting within 3 minutes. Open Docker Desktop, wait until it says 'Engine running', then try again." }
    }
    Say "Database: starting the propertyiq-postgres container"
    Push-Location $root
    & $docker compose up -d 2>&1 | ForEach-Object { Add-Content -Path $log -Value "  $_" }
    Pop-Location
    if (-not (WaitFor 5432 60 "the database")) { Fail "The database container didn't start. In Docker Desktop, check the propertyiq-postgres container." }
    Say "Database: running"
}

# 2. Local model (Ollama normally starts at sign-in)
if (Up 11434) {
    Say "Local model: running"
} else {
    $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
    if (-not (Test-Path $ollama)) { Fail "Ollama is not installed where expected." }
    Say "Local model: starting Ollama"
    Start-Process $ollama
    if (-not (WaitFor 11434 60 "Ollama")) { Fail "Ollama didn't start. Open Ollama from the Start menu, then try again." }
    Say "Local model: running"
}

# 3. Web app build (first run only)
if (-not (Test-Path (Join-Path $web ".next\BUILD_ID"))) {
    Say "Web app: building (first run only, about a minute)"
    Push-Location $web
    $env:NEXT_TELEMETRY_DISABLED = "1"
    npm.cmd run build 2>&1 | ForEach-Object { Add-Content -Path $log -Value "  $_" }
    Pop-Location
}

# 4. API and web app, each in its own window
if (Up 8000) {
    Say "API: running"
} else {
    Say "API: starting (window 'PropertyIQ API')"
    Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-Command",
        "`$host.UI.RawUI.WindowTitle = 'PropertyIQ API - keep open'; Set-Location '$root'; .\.venv\Scripts\python.exe -m uvicorn propertyiq.main:app --app-dir services/api --host 127.0.0.1 --port 8000"
    if (-not (WaitFor 8000 60 "the API")) { Fail "The API didn't start. Look at the 'PropertyIQ API' window for the error." }
}
if (Up 3000) {
    Say "Web app: running"
} else {
    Say "Web app: starting (window 'PropertyIQ Web')"
    Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-Command",
        "`$host.UI.RawUI.WindowTitle = 'PropertyIQ Web - keep open'; Set-Location '$web'; `$env:NEXT_TELEMETRY_DISABLED = '1'; npm.cmd run start"
    if (-not (WaitFor 3000 60 "the web app")) { Fail "The web app didn't start. Look at the 'PropertyIQ Web' window for the error." }
}

Say "Ready: opening http://127.0.0.1:3000"
Start-Process "http://127.0.0.1:3000"
Write-Host "`nLeave the 'PropertyIQ API' and 'PropertyIQ Web' windows open while you use the app." -ForegroundColor Green
Write-Host "This window closes in 8 seconds."
Start-Sleep -Seconds 8
