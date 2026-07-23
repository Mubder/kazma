<#
.SYNOPSIS
  Give Windows a stable way to open Kazma running inside WSL2.

.DESCRIPTION
  WSL2 virtual IPs (172.28.x.x) change after restarts. This script pins:

    http://127.0.0.1:<Port>/     → current WSL eth IP:<Port>
    http://localhost:<Port>/

  via Windows portproxy, and optionally:

    http://kazma.wsl:<Port>/

  via the Windows hosts file.

  Requires: PowerShell as Administrator (portproxy + hosts).

.PARAMETER Distro
  WSL distro name that runs Kazma (default: Hermes_API_1).

.PARAMETER Port
  Host and guest port (default: 9090).

.PARAMETER Hostname
  Optional hosts alias (default: kazma.wsl). Pass "" to skip hosts update.

.EXAMPLE
  # Admin PowerShell, repo or anywhere:
  .\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090

  # Then in WSL (must listen on all interfaces):
  export KAZMA_HOST=0.0.0.0
  export KAZMA_SECRET='your-secret'
  export KAZMA_TRUST_LAN=1
  .venv/bin/kazma serve 9090

  # Browser (Windows):
  http://127.0.0.1:9090/
  http://localhost:9090/
  http://kazma.wsl:9090/
#>
[CmdletBinding()]
param(
    [string] $Distro = "Hermes_API_1",
    [int] $Port = 9090,
    [string] $Hostname = "kazma.wsl",
    [string] $ListenAddress = "127.0.0.1",
    # When invoked at boot (scheduled task), WSL networking may not be ready
    # yet. Wait up to this many seconds for a valid 172.x address before
    # giving up, so we never pin a stale/empty IP (the cause of 1033s).
    [int] $WaitForNetwork = 60
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host ""
    Write-Host "  [ERROR] Run this script in PowerShell AS ADMINISTRATOR." -ForegroundColor Red
    Write-Host "  Right-click PowerShell → Run as administrator, then re-run." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# Resolve WSL IPv4 (first eth-like address, skip docker bridges when possible).
# RETRY: at boot WSL may be up but not yet have an IP. Loop until we get a
# valid 172.x address (or time out) so the portproxy is never pinned to a
# stale/empty IP -- that drift is exactly what causes Cloudflare tunnel 1033s.
$wslIp = $null
$deadline = (Get-Date).AddSeconds($WaitForNetwork)
$attempt = 0
while ((Get-Date) -lt $deadline) {
    $attempt++
    $raw = (wsl -d $Distro -e bash -lc "hostname -I 2>/dev/null" 2>$null)
    if ($raw) {
        $cands = ($raw -split "\s+") | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' }
        # Prefer 172.28.x (WSL Hyper-V) then 172.x (skip docker 172.17.x)
        $wslIp = $cands | Where-Object { $_ -like "172.28.*" } | Select-Object -First 1
        if (-not $wslIp) {
            $wslIp = $cands | Where-Object { $_ -like "172.*" -and $_ -notlike "172.17.*" } | Select-Object -First 1
        }
        if (-not $wslIp) { $wslIp = $cands | Select-Object -First 1 }
    }
    if ($wslIp) { break }
    Write-Host "  [wait] WSL '$Distro' has no 172.x IP yet (attempt $attempt) -- retrying in 3s..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 3
}

if (-not $wslIp) {
    Write-Host "  [ERROR] Distro '$Distro' not reachable or has no IPv4 after ${WaitForNetwork}s." -ForegroundColor Red
    Write-Host "         Check: wsl -l -v  (is it Running?)" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "  Distro:  $Distro"
Write-Host "  WSL IP:  $wslIp"
Write-Host "  Port:    $Port"
Write-Host ""

# Refresh portproxy: localhost:Port → WSL_IP:Port
netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$Port 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenaddress=$ListenAddress listenport=$Port connectaddress=$wslIp connectport=$Port
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] portproxy add failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Write-Host "  portproxy: ${ListenAddress}:${Port}  ->  ${wslIp}:${Port}" -ForegroundColor Green

# Optional hosts alias -> same WSL IP (browser uses kazma.wsl:Port).
# Non-fatal: antivirus / another tool often locks hosts; localhost still works via portproxy.
if ($Hostname) {
    $hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
    $hostsOk = $false
    $lastErr = $null
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            $lines = Get-Content $hostsPath -ErrorAction Stop
            $filtered = @(
                $lines | Where-Object {
                    $_ -notmatch "\s$([regex]::Escape($Hostname))(\s|$)"
                }
            )
            $entry = "$wslIp`t$Hostname`t# Kazma WSL (managed by wsl_fixed_access.ps1)"
            $newContent = $filtered + $entry
            # Write via temp + move reduces lock contention vs Set-Content in-place
            $tmp = Join-Path $env:TEMP ("kazma-hosts-{0}.txt" -f [guid]::NewGuid().ToString("n"))
            [System.IO.File]::WriteAllLines($tmp, $newContent)
            [System.IO.File]::Copy($tmp, $hostsPath, $true)
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            $hostsOk = $true
            break
        } catch {
            $lastErr = $_
            Start-Sleep -Milliseconds (200 * $attempt)
        }
    }
    if ($hostsOk) {
        Write-Host "  hosts:    $Hostname  ->  $wslIp" -ForegroundColor Green
    } else {
        Write-Host "  hosts:    SKIPPED (file locked) - localhost still works via portproxy" -ForegroundColor Yellow
        Write-Host "            Close VPN/antivirus hosts editors, or edit hosts manually:" -ForegroundColor Yellow
        Write-Host "            $wslIp  $Hostname" -ForegroundColor Yellow
        if ($lastErr) {
            Write-Host "            ($($lastErr.Exception.Message))" -ForegroundColor DarkYellow
        }
    }
}

Write-Host ""
Write-Host "  Fixed URLs (Windows browser):" -ForegroundColor Cyan
Write-Host "    http://${ListenAddress}:${Port}/"
Write-Host "    http://localhost:${Port}/"
if ($Hostname) {
    Write-Host "    http://${Hostname}:${Port}/"
}
Write-Host ""
Write-Host "  In WSL, Kazma MUST listen on 0.0.0.0 (not only 127.0.0.1):" -ForegroundColor Yellow
Write-Host "    export KAZMA_HOST=0.0.0.0"
Write-Host "    export KAZMA_SECRET='your-strong-secret'"
Write-Host "    export KAZMA_TRUST_LAN=1   # optional single-operator auto-cookie"
Write-Host "    .venv/bin/kazma serve $Port"
Write-Host ""
Write-Host "  Then open /login once if you skip TRUST_LAN, and enter KAZMA_SECRET."
Write-Host ""
Write-Host "  Re-run this script after every 'wsl --shutdown' / reboot (IP changes)." -ForegroundColor Yellow
Write-Host ""

# Quick probe
try {
    $r = Invoke-WebRequest -Uri "http://${ListenAddress}:${Port}/health" -UseBasicParsing -TimeoutSec 4
    Write-Host "  health check: $($r.StatusCode) OK" -ForegroundColor Green
} catch {
    Write-Host "  health check: not reachable yet (start Kazma in WSL first)" -ForegroundColor Yellow
    Write-Host "  $($_.Exception.Message)"
}
