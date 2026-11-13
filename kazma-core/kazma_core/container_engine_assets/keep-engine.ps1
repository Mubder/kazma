# Keeps Kazma's container engine (the kazma-engine WSL distro) running
# (kazma_core/container_engine_setup.py; AGENTS.md section 63). Started at boot
# by the KazmaEngine scheduled task, with nobody signed in. Kazma's guard
# watches the engine; this keeps it up.
#
# WSL stops a distro about 15 s after the last wsl.exe attached to it leaves,
# so a holder stays attached: kazma-engine-hold, in the distro. It is started
# through WMI, not as a child of this script: Task Scheduler ends a task and
# everything it started at the very start of a Windows shutdown (measured,
# 2026-11-13), and the holder is how the KazmaEngineShutdown service stops the
# engine cleanly then -- its stdin and stdout are that service's pipes. With no
# service (an older setup) the holder is this script's own child, as before.
# A holder that ends (an update, `wsl --shutdown`) is started again, never once
# Windows is shutting down.
$ErrorActionPreference = 'Continue'
$log = Join-Path $PSScriptRoot 'keep-engine.log'
$pidFile = Join-Path $PSScriptRoot 'holder.pid'
$env:WSL_UTF8 = '1'
$pipeIn = '\\.\pipe\KazmaEngineHold-in'
$pipeOut = '\\.\pipe\KazmaEngineHold-out'
function Say([string]$m) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.1" -Force }
    "$(Get-Date -Format o) $m" | Add-Content -Path $log -Encoding utf8
}
try {
    Add-Type -Namespace KazmaEngine -Name Win32 -MemberDefinition `
        '[DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);'
} catch { }
function ShuttingDown {
    try { return [KazmaEngine.Win32]::GetSystemMetrics(0x2000) -ne 0 } catch { return $false }  # SM_SHUTTINGDOWN
}
function ServiceListening {
    try { return [System.IO.Directory]::GetFiles('\\.\pipe\') -contains $pipeIn } catch { return $false }
}
# The holder started through WMI earlier (this script may have been started again).
function PipedHolder {
    try {
        $saved = (Get-Content $pidFile -ErrorAction Stop | Select-Object -First 1).Trim()
        $p = Get-Process -Id ([int]$saved) -ErrorAction Stop
        if ($p.ProcessName -eq 'cmd') { return $p }
    } catch { }
    return $null
}
function StartPipedHolder {
    $command = 'cmd.exe /d /c wsl.exe -d kazma-engine -u root -e /usr/local/sbin/kazma-engine-hold piped ' +
               "<$pipeIn >$pipeOut"
    try {
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $command }
        if ($r.ReturnValue -eq 0) {
            Set-Content -Path $pidFile -Value $r.ProcessId -Encoding ascii
            Say "holding kazma-engine (pid $($r.ProcessId)): the KazmaEngineShutdown service can stop it at a shutdown"
            return $true
        }
        Say "the holder could not be started through WMI (return $($r.ReturnValue))"
    } catch { Say "the holder could not be started through WMI: $($_.Exception.Message)" }
    return $false
}
function StartPlainHolder {
    $info = New-Object System.Diagnostics.ProcessStartInfo 'wsl.exe'
    $info.Arguments = '-d kazma-engine -u root -e sleep infinity'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    try {
        $p = [System.Diagnostics.Process]::Start($info)
        Say 'holding kazma-engine (no KazmaEngineShutdown service: it is not stopped cleanly at a shutdown)'
        return $p
    } catch { Say "wsl.exe did not start: $($_.Exception.Message)"; return $null }
}
$plain = $null
$wait = 5
$nextPiped = Get-Date
$startedAt = $null
while ($true) {
    if (ShuttingDown) {
        Say 'Windows is shutting down: nothing more is started'
        while ($true) { Start-Sleep -Seconds 3600 }
    }
    $piped = PipedHolder
    if (-not $piped -and $startedAt) {
        # The holder ended: a run that lasted a while is a fresh failure; a quick end backs off.
        if (((Get-Date) - $startedAt).TotalMinutes -ge 5) { $wait = 5 } else { $wait = [Math]::Min($wait * 2, 60) }
        Say "the holder ended; starting it again in $wait s"
        $nextPiped = (Get-Date).AddSeconds($wait)
        $startedAt = $null
    }
    if (-not $piped -and (Get-Date) -ge $nextPiped -and (ServiceListening)) {
        if (StartPipedHolder) { $startedAt = Get-Date; Start-Sleep -Seconds 3; $piped = PipedHolder }
        else { $nextPiped = (Get-Date).AddSeconds(30) }
    }
    # The piped holder holds the distro: this script's own one is not needed.
    if ($piped -and $plain -and -not $plain.HasExited) {
        try { $plain.Kill() } catch { }
        $plain = $null
    }
    # No service (or its holder backing off): hold the distro as before.
    if (-not $piped -and -not $startedAt -and (-not $plain -or $plain.HasExited)) {
        $plain = StartPlainHolder
        if (-not $plain) { Start-Sleep -Seconds 30 }
    }
    Start-Sleep -Seconds 2
}
