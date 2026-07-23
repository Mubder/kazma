<#
.SYNOPSIS
  One-time fix for the Cloudflare-tunnel 1033 / "I have to run wsl_fixed_access
  manually every reboot" problem.

.DESCRIPTION
  Rebuilds the two boot scheduled tasks that bridge Windows 127.0.0.1:9090 to
  the WSL Kazma server, fixing the bugs that caused recurring tunnel 1033s:

    1. KazmaAutoStart used the WRONG path (~/kazma/start-web.sh instead of
       ~/kazma/scripts/start-web.sh) so it never started Kazma and hung.
    2. KazmaWSL (the portproxy task) raced ahead of WSL networking at boot and
       pinned a stale/empty IP. It now calls the hardened wsl_fixed_access.ps1
       which waits up to 60s for a valid WSL IP before pinning.

  After running this once, the bridge sets itself up automatically on every
  boot/login — no manual wsl_fixed_access.ps1 needed.

.PARAMETER Distro
  WSL distro that runs Kazma (default: Hermes_API_1).

.PARAMETER Port
  Host/guest port (default: 9090).

.EXAMPLE
  # Admin PowerShell:
  .\scripts\fix-cloudflare-tunnel-tasks.ps1
  .\scripts\fix-cloudflare-tunnel-tasks.ps1 -Distro Hermes_API_1 -Port 9090
#>
[CmdletBinding()]
param(
    [string] $Distro = "Hermes_API_1",
    [int]    $Port = 9090
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
    Write-Host ""
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot   # .../kazma
$pinScript = Join-Path $repoRoot "scripts\wsl_fixed_access.ps1"
if (-not (Test-Path $pinScript)) {
    Write-Host "  [ERROR] Cannot find $pinScript" -ForegroundColor Red
    exit 1
}

# ── 1. Stop + remove the broken KazmaAutoStart (wrong path, hung) ──────
Write-Host "  [1/4] Removing broken KazmaAutoStart task..." -ForegroundColor Cyan
Stop-ScheduledTask    -TaskName "KazmaAutoStart"  -ErrorAction SilentlyContinue | Out-Null
Unregister-ScheduledTask -TaskName "KazmaAutoStart" -Confirm:$false -ErrorAction SilentlyContinue

# ── 2. Rebuild KazmaAutoStart with the CORRECT script path ─────────────
Write-Host "  [2/4] Recreating KazmaAutoStart (correct path)..." -ForegroundColor Cyan
$actStart = New-ScheduledTaskAction `
    -Execute "wsl.exe" `
    -Argument "-d $Distro -- bash -lc '~/kazma/scripts/start-web.sh'"
$trigStart = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "KazmaAutoStart" -Action $actStart -Trigger $trigStart -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "        OK -> wsl -d $Distro -- ~/kazma/scripts/start-web.sh" -ForegroundColor Green

# ── 3. Rebuild KazmaWSL (portproxy) — now waits for WSL networking ─────
Write-Host "  [3/4] Recreating KazmaWSL portproxy task (waits for WSL IP)..." -ForegroundColor Cyan
Unregister-ScheduledTask -TaskName "KazmaWSL" -Confirm:$false -ErrorAction SilentlyContinue
$actProxy = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$pinScript`" -Distro $Distro -Port $Port -WaitForNetwork 60"
Register-ScheduledTask -TaskName "KazmaWSL" -Action $actProxy -Trigger $trigStart -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "        OK -> waits up to 60s for a valid WSL IP before pinning portproxy" -ForegroundColor Green

# ── 4. Also fire the portproxy NOW so you don't have to reboot ──────────
Write-Host "  [4/4] Running the portproxy fix once now (so no reboot needed)..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $pinScript -Distro $Distro -Port $Port -WaitForNetwork 30

Write-Host ""
Write-Host "  DONE. On every reboot/login the bridge now sets itself up." -ForegroundColor Green
Write-Host "  You should no longer need to run wsl_fixed_access.ps1 manually." -ForegroundColor Green
Write-Host ""
Write-Host "  Verify (no admin needed):" -ForegroundColor Cyan
Write-Host "    netsh interface portproxy show v4tov4"
Write-Host "    curl http://127.0.0.1:$Port/health"
Write-Host ""
