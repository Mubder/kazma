#Requires -Version 5.0
# ═══════════════════════════════════════════════════════════════════════════════
# Kazma (كاظمه) — Bootstrap Script v3 (PowerShell)
# Deterministic, fail-fast, idempotent initialization for Windows.
# Cross-platform equivalent of setup.sh.
# ═══════════════════════════════════════════════════════════════════════════════

<#
.SYNOPSIS
    Kazma (كاظمه) bootstrap script for Windows / PowerShell.

.DESCRIPTION
    Validates the environment (Python 3.11+, uv, kazma.yaml), syncs the
    virtual environment from pyproject.toml, and runs a foundation
    integrity check (core imports + test collection).

    Idempotent — safe to run multiple times.

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -Debug
#>

[CmdletBinding()]
param(
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

# ── Logging helpers ──────────────────────────────────────────────────────────

function Log-Ok($msg) { Write-Host "  ✅ $msg" -ForegroundColor Green }
function Log-Warn($msg) { Write-Host "  ⚠️  $msg" -ForegroundColor Yellow }
function Log-Fail($msg) { Write-Host "  ❌ $msg" -ForegroundColor Red }
function Log-Info($msg) { Write-Host "  ℹ️  $msg" -ForegroundColor Cyan }
function Log-Header($msg) { Write-Host "`n━━━ $msg ━━━" -ForegroundColor Cyan }

if ($Debug) {
    $VerbosePreference = "Continue"
}

# ── Helper: test if uv actually works ────────────────────────────────────────

function Test-UvWorks {
    try {
        $null = & uv --version 2>&1
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

# ── Banner ───────────────────────────────────────────────────────────────────

Write-Host "`n🇰🇼 Kazma (كاظمه) — Bootstrap v3 (PowerShell)`n" -ForegroundColor Green
Write-Host "   Autonomous AI Agent Framework`n" -ForegroundColor Cyan

# ── 1. Environmental Guardrails ──────────────────────────────────────────────

Log-Header "1. Environmental Guardrails"

# 1a. Python 3.11+
$PythonCmd = $null
$PythonCandidates = @("python", "python3", "py")

foreach ($cmd in $PythonCandidates) {
    try {
        $versionOutput = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $versionOutput) {
            $parts = $versionOutput.Trim() -split '\.'
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -ge 3 -and $minor -ge 11) {
                $PythonCmd = $cmd
                $fullVersion = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
                Log-Ok "Python $fullVersion detected ($cmd)"
                break
            }
        }
    } catch {
        # Try next candidate
    }
}

if (-not $PythonCmd) {
    Log-Fail "Python 3.11+ required but not found"
    Log-Info "Install from: https://www.python.org/downloads/"
    exit 1
}

# 1b. uv package manager
$UvInstalled = $false

if (Test-UvWorks) {
    $uvVersion = & uv --version 2>$null
    Log-Ok "uv detected: $uvVersion"
    $UvInstalled = $true
} else {
    Log-Warn "uv not found — installing via pip..."

    # Priority 1: pip
    & $PythonCmd -m pip install --user uv 2>&1 | ForEach-Object { Write-Verbose $_ }
    if (Test-UvWorks) {
        $uvVersion = & uv --version 2>$null
        Log-Ok "uv installed via pip: $uvVersion"
        $UvInstalled = $true
    }

    # Priority 2: official PowerShell installer
    if (-not $UvInstalled) {
        Log-Info "pip failed, trying official installer..."
        try {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression 2>&1 | ForEach-Object { Write-Verbose $_ }
            # Refresh PATH for the current session
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Test-UvWorks) {
                $uvVersion = & uv --version 2>$null
                Log-Ok "uv installed via installer: $uvVersion"
                $UvInstalled = $true
            }
        } catch {
            Log-Warn "Official installer failed: $_"
        }
    }

    if (-not $UvInstalled) {
        Log-Fail "Could not install a working uv"
        Log-Info "Fix manually:"
        Log-Info "  pip install uv"
        Log-Info "  # or: irm https://astral.sh/uv/install.ps1 | iex"
        Log-Info ""
        Log-Info "Or skip uv entirely:"
        Log-Info "  $PythonCmd -m pip install -e `".[dev,cli]`""
        exit 1
    }
}

# 1c. kazma.yaml presence
if (Test-Path "kazma.yaml") {
    Log-Ok "kazma.yaml found"
} else {
    Log-Fail "kazma.yaml not found in current directory"
    Log-Info "Run this script from the Kazma project root: cd kazma; .\setup.ps1"
    exit 1
}

# 1d. pyproject.toml readable
if (Test-Path "pyproject.toml" -PathType Leaf) {
    Log-Ok "pyproject.toml readable"
} else {
    Log-Fail "pyproject.toml not found"
    exit 1
}

# ── 2. The Sync Handshake ────────────────────────────────────────────────────

Log-Header "2. Sync Handshake (uv sync)"

& uv sync --extra dev --extra cli --extra tui 2>&1 | ForEach-Object { Write-Verbose $_ }
if ($LASTEXITCODE -eq 0) {
    Log-Ok "Environment synced from pyproject.toml (with dev + cli + tui extras)"
} else {
    $syncExit = $LASTEXITCODE
    Log-Fail "uv sync failed (exit code $syncExit)"

    Write-Host ""
    Log-Info "Running diagnostics..."

    # Disk space check
    try {
        $disk = Get-PSDrive -Name (Get-Location).Drive.Name
        $availGB = [math]::Round($disk.Free / 1GB, 1)
        if ($availGB -lt 1) {
            Log-Fail "Low disk space: ${availGB}GB available"
        } else {
            Log-Ok "Disk space: ${availGB}GB available"
        }
    } catch {
        Log-Warn "Could not check disk space"
    }

    # pyproject.toml syntax
    try {
        & $PythonCmd -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Log-Ok "pyproject.toml syntax valid"
        } else {
            Log-Warn "Could not validate pyproject.toml syntax"
        }
    } catch {
        Log-Warn "Could not validate pyproject.toml syntax"
    }

    # PyPI reachability
    try {
        $null = Invoke-WebRequest -Uri "https://pypi.org" -TimeoutSec 5 -UseBasicParsing
        Log-Ok "PyPI reachable"
    } catch {
        Log-Fail "Cannot reach PyPI — check network/proxy"
    }

    Write-Host ""
    Log-Info "Fallback — install without uv:"
    Log-Info "  $PythonCmd -m pip install -e `".[dev,cli,tui]`""
    exit 1
}

# ── 3. Foundation Integrity Check ────────────────────────────────────────────

Log-Header "3. Foundation Integrity Check"

# Cross-platform venv python path
$VenvPython = ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $VenvPython = ".venv\bin\python"
    if (-not (Test-Path $VenvPython)) {
        Log-Fail "Virtual environment not found at .venv\"
        Log-Info "uv sync may have failed silently. Try: uv sync --verbose"
        exit 1
    }
}

$IntroErrors = 0

function Check-Import($module, $label) {
    $null = & $VenvPython -c "import $module" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Log-Ok "$label loaded"
    } else {
        Log-Fail "$label not importable"
        $script:IntroErrors++
    }
}

Check-Import "aiosqlite" "aiosqlite"
Check-Import "langgraph" "LangGraph"
Check-Import "langgraph.checkpoint.sqlite.aio" "LangGraph SQLite checkpointer"
Check-Import "yaml" "PyYAML"
Check-Import "httpx" "httpx"
Check-Import "textual" "textual (TUI)"

if ($IntroErrors -gt 0) {
    Write-Host ""
    Log-Fail "$IntroErrors core import(s) failed"
    Log-Info "Fix: uv sync --reinstall"
    exit 1
}

$testOutput = & $VenvPython -m pytest tests/ --co -q 2>$null | Select-Object -Last 3
$testLine = ($testOutput | Where-Object { $_ -match '\d+' } | Select-Object -Last 1)
if ($testLine -match '(\d+)') {
    $testCount = $matches[1]
    Log-Ok "$testCount tests collected"
} else {
    Log-Warn "Could not collect tests (non-critical)"
}

# ── 4. Summary ───────────────────────────────────────────────────────────────

Log-Header "4. Setup Complete"

Write-Host ""
Log-Ok "Kazma is ready"
Write-Host ""
Log-Info "Run tests:      $VenvPython -m pytest tests/ -q"
Log-Info "Run agent:      $VenvPython -m kazma_core.agent"
Log-Info "Run TUI:        $VenvPython -m kazma_tui.tui"
Log-Info "Run Web UI:     $VenvPython serve.py"
Log-Info "Configuration:  kazma.yaml"
Log-Info "Documentation:  https://github.com/Mubder/kazma"
Write-Host ""
Write-Host "🇰🇼 كاظمه — Built to remember. Built to last." -ForegroundColor Green
Write-Host ""
