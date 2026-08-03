# Memory smoke — run golden + priority batch (local dev / CI)
# Usage: pwsh -File scripts/memory_smoke.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = Get-Location }
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pytest `
  kazma-core/tests/test_memory_eval_golden.py `
  kazma-core/tests/test_memory_priority_batch.py `
  kazma-core/tests/test_memory_v2_phase_a.py `
  kazma-core/tests/test_memory_v2_phase_b.py `
  -q --tb=line
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "memory_smoke OK"
