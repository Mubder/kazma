# Keeps Kazma's container engine (the kazma-engine WSL distro) running.
# WSL stops a distro about 15 s after the last wsl.exe attached to it leaves,
# so this holds one, and starts it again when it ends (a `wsl --shutdown`, an
# update). Started at boot by the KazmaEngine scheduled task, with nobody
# signed in. Kazma's guard watches the engine; this only keeps it up.
$log = Join-Path $PSScriptRoot 'keep-engine.log'
function Say([string]$m) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.1" -Force }
    "$(Get-Date -Format o) $m" | Add-Content -Path $log -Encoding utf8
}
$wait = 5
while ($true) {
    Say 'starting kazma-engine'
    $started = Get-Date
    & wsl.exe -d kazma-engine -u root -- sleep infinity
    $code = $LASTEXITCODE
    # A run that lasted a while is a fresh failure; a quick end backs off.
    if (((Get-Date) - $started).TotalMinutes -ge 5) { $wait = 5 } else { $wait = [Math]::Min($wait * 2, 60) }
    Say "keep-alive ended (exit $code); starting again in $wait s"
    Start-Sleep -Seconds $wait
}
