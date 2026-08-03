# Industry smoke — offline matrix rows (no live LLM / SearXNG required)
# Usage: pwsh -File scripts/industry_smoke.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = Get-Location }
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pytest `
  tests/test_industry_smoke_matrix.py `
  tests/test_kb_smart_reindex.py `
  tests/test_research_session.py `
  tests/test_proxy_scraping_coverage.py `
  kazma-core/tests/test_memory_eval_golden.py `
  -q --tb=line
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "industry_smoke OK — offline matrix rows passed"
Write-Host "Manual live rows: docs/docs/ops/smoke-matrix.md"
