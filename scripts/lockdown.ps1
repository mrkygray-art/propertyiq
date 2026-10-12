# PropertyIQ network lockdown. Changes Windows Firewall, so every mode except Status needs administrator rights.
#
#   -Mode App       Block PropertyIQ's programs (Ollama, Python, Node, Docker Desktop) from the public internet.
#                   Everything else on the computer, including remote access, keeps working.
#   -Mode Computer  App mode, plus block every program on the computer from public internet addresses.
#                   Local network and this computer's own services keep working. Remote-access sessions
#                   (AnyViewer and similar) WILL DISCONNECT. Use the "Restore internet" shortcut on this PC to undo.
#   -Mode Off       Remove every PropertyIQ rule and put the firewall back exactly as it was.
#   -Mode Status    Report the current state as JSON (no admin needed).
#
# All rules are in the firewall group "PropertyIQ Lockdown", so they're easy to find and remove.
# Previous firewall defaults are saved in C:\ProgramData\PropertyIQ\lockdown-state.json and restored by -Mode Off.
# A log and before/after connection evidence are written to <repo>\logs\.
param([ValidateSet("App", "Computer", "Off", "Status")][string]$Mode = "Status")

$ErrorActionPreference = "Stop"
$Group = "PropertyIQ Lockdown"
$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs"
$StateDir = "C:\ProgramData\PropertyIQ"
$StateFile = Join-Path $StateDir "lockdown-state.json"
$WslVm = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"   # Hyper-V firewall ID for WSL (where Docker Desktop runs)

# Public internet address ranges: everything except loopback, private (10/8, 172.16/12, 192.168/16),
# carrier-grade NAT (100.64/10), link-local (169.254/16) and multicast.
$PublicV4 = @("1.0.0.0-9.255.255.255", "11.0.0.0-100.63.255.255", "100.128.0.0-126.255.255.255",
              "128.0.0.0-169.253.255.255", "169.255.0.0-172.15.255.255", "172.32.0.0-192.167.255.255",
              "192.169.0.0-223.255.255.255")
$PublicV6 = @("2000::-3fff:ffff:ffff:ffff:ffff:ffff:ffff:ffff")

function Log($msg) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  [$Mode]  $msg"
    Add-Content -Path (Join-Path $LogDir "lockdown.log") -Value $line
    Write-Host $line
}

function Programs {
    $py = & (Join-Path $Root ".venv\Scripts\python.exe") -c "import sys; print(sys._base_executable)" 2>$null
    $list = @(
        @{ name = "Ollama"; path = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" },
        @{ name = "Ollama app"; path = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe" },
        @{ name = "Python (PropertyIQ API)"; path = Join-Path $Root ".venv\Scripts\python.exe" },
        @{ name = "Python (interpreter)"; path = $py },
        @{ name = "Node.js (PropertyIQ web)"; path = "C:\Program Files\nodejs\node.exe" },
        @{ name = "Docker backend"; path = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\com.docker.backend.exe" },
        @{ name = "Docker Desktop"; path = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe" }
    )
    $list | Where-Object { $_.path -and (Test-Path $_.path) }
}

function Connections {
    Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
        Where-Object { $_.RemoteAddress -notmatch '^(127\.|::1$|0\.0\.0\.0|::$)' } |
        ForEach-Object {
            $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            [pscustomobject]@{ process = $p.ProcessName; remote = "$($_.RemoteAddress):$($_.RemotePort)" }
        }
}

function Status {
    $rules = @(Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue)
    $profiles = Get-NetFirewallProfile | ForEach-Object { @{ name = $_.Name; outbound = "$($_.DefaultOutboundAction)" } }
    $state = if (Test-Path $StateFile) { Get-Content $StateFile -Raw | ConvertFrom-Json } else { $null }
    $mode = if ($rules.Count -eq 0) { "off" } elseif ($state -and $state.mode -eq "Computer") { "computer" } else { "app" }
    $hv = $null
    try { $hv = "$((Get-NetFirewallHyperVVMSetting -PolicyStore ActiveStore -Name $WslVm).DefaultOutboundAction)" } catch {}
    # Firewall products registered with Windows Security. A third-party one (e.g. McAfee) may take over from
    # Windows Firewall, so the checklist always confirms blocking with a real connection test.
    $products = @(try { Get-CimInstance -Namespace root\SecurityCenter2 -ClassName FirewallProduct -ErrorAction Stop |
        ForEach-Object { @{ name = $_.displayName; enabled = ((($_.productState -shr 12) -band 0xF) -eq 1) } } } catch {})
    [pscustomobject]@{
        mode = $mode; rule_count = $rules.Count; profiles = $profiles; wsl_outbound = $hv
        firewall_products = $products
        since = if ($state) { $state.applied_at } else { $null }
        checked_at = (Get-Date).ToString("s")
    }
}

function Evidence($label) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    $file = Join-Path $LogDir ("lockdown-evidence-{0}-{1}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $label)
    $reach = try { (New-Object Net.Sockets.TcpClient).ConnectAsync("1.1.1.1", 443).Wait(2500) } catch { $false }
    [pscustomobject]@{
        label = $label; at = (Get-Date).ToString("s"); status = Status
        powershell_can_reach_1_1_1_1_443 = $reach
        established_non_loopback_connections = @(Connections)
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $file -Encoding utf8
    Log "evidence saved: $file"
}

if ($Mode -eq "Status") { Status | ConvertTo-Json -Depth 4 -Compress; exit 0 }
trap { Log "ERROR: $_"; Write-Host "Something went wrong (logged in logs\lockdown.log). Closing in 30 seconds."; Start-Sleep 30; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "This needs administrator rights. Run it from the PropertyIQ Admin page or a desktop shortcut."; exit 1 }

New-Item -ItemType Directory -Force $StateDir | Out-Null
Evidence "before-$($Mode.ToLower())"

# Always start from a clean slate: remove our rules, restore saved defaults.
Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if (Test-Path $StateFile) {
    $old = Get-Content $StateFile -Raw | ConvertFrom-Json
    foreach ($p in $old.profiles) { Set-NetFirewallProfile -Name $p.name -DefaultOutboundAction $p.outbound }
    if ($old.wsl_outbound) { try { Set-NetFirewallHyperVVMSetting -Name $WslVm -DefaultOutboundAction $old.wsl_outbound } catch {} }
    Remove-Item $StateFile
    Log "previous firewall defaults restored"
}

if ($Mode -eq "Off") {
    Log "lockdown OFF: all PropertyIQ rules removed"
    Evidence "after-off"
    Write-Host "Internet access restored. This window closes in 10 seconds."
    Start-Sleep -Seconds 10
    exit 0
}

# Save the defaults we're about to change, so Off can put them back exactly.
$saved = [pscustomobject]@{
    mode = $Mode; applied_at = (Get-Date).ToString("s")
    profiles = @(Get-NetFirewallProfile | ForEach-Object { @{ name = $_.Name; outbound = "$($_.DefaultOutboundAction)" } })
    wsl_outbound = $(try { "$((Get-NetFirewallHyperVVMSetting -PolicyStore PersistentStore -Name $WslVm).DefaultOutboundAction)" } catch { $null })
}
$saved | ConvertTo-Json -Depth 4 | Set-Content -Path $StateFile -Encoding utf8

foreach ($prog in Programs) {
    New-NetFirewallRule -DisplayName "PropertyIQ Lockdown: block internet for $($prog.name)" -Group $Group `
        -Direction Outbound -Action Block -Program $prog.path -RemoteAddress ($PublicV4 + $PublicV6) -Profile Any | Out-Null
    Log "blocked public internet for $($prog.name) ($($prog.path))"
}

if ($Mode -eq "Computer") {
    # A block rule wins over Windows' many built-in allow rules, which a default-outbound change would not.
    New-NetFirewallRule -DisplayName "PropertyIQ Lockdown: block internet for every program" -Group $Group `
        -Direction Outbound -Action Block -RemoteAddress ($PublicV4 + $PublicV6) -Profile Any | Out-Null
    Log "whole computer: every program blocked from public internet addresses (local network still allowed)"
    try {
        Set-NetFirewallHyperVVMSetting -Name $WslVm -DefaultOutboundAction Block
        Log "WSL/Docker virtual machine: outbound blocked (Hyper-V firewall)"
    } catch { Log "WSL/Docker Hyper-V firewall setting not applied: $($_.Exception.Message)" }
}

Log "lockdown $Mode ON"
Start-Sleep -Seconds 2
Evidence "after-$($Mode.ToLower())"
Write-Host "Done. This window closes in 10 seconds."
Start-Sleep -Seconds 10
