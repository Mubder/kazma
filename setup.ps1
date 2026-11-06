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

# No [CmdletBinding()]: it would make this an advanced script, which gets
# PowerShell's own -Debug, and PowerShell then refuses to start the script at
# all ("A parameter with the name 'Debug' was defined multiple times").
# tests/test_powershell_scripts_start.py holds every shipped script to that.
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

# ── Helper: run a program ────────────────────────────────────────────────────
# Windows PowerShell 5.1 turns every line a program writes to stderr into an
# error record once stderr is redirected (2>&1, 2>$null), and under "Stop"
# the first one ends this script: uv prints "Using CPython ..." to stderr, so
# setup stopped at uv sync. Every program runs through Invoke-Native, with
# "Continue" for that call; the script judges it by $LASTEXITCODE, which is
# -1 when the program did not start at all.
# tests/test_powershell_scripts_start.py holds every shipped script to that.

function Invoke-Native {
    param([scriptblock]$Command, [switch]$StdoutOnly)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = -1
    try {
        if ($StdoutOnly) { & $Command 2>$null }
        else { & $Command 2>&1 | ForEach-Object { "$_" } }
    } catch {
        # Not there at all (PowerShell throws even under "Continue").
        Write-Verbose "$_"
        $global:LASTEXITCODE = -1
    } finally {
        $ErrorActionPreference = $saved
    }
}

# ── Helper: test if uv actually works ────────────────────────────────────────

function Test-UvWorks {
    $null = Invoke-Native -StdoutOnly { uv --version }
    return $LASTEXITCODE -eq 0
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
        $versionOutput = Invoke-Native -StdoutOnly { & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" }
        if ($LASTEXITCODE -eq 0 -and $versionOutput) {
            $parts = "$versionOutput".Trim() -split '\.'
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -ge 3 -and $minor -ge 11) {
                $PythonCmd = $cmd
                $fullVersion = Invoke-Native -StdoutOnly { & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" }
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
    $uvVersion = Invoke-Native -StdoutOnly { uv --version }
    Log-Ok "uv detected: $uvVersion"
    $UvInstalled = $true
} else {
    Log-Warn "uv not found — installing via pip..."

    # Priority 1: pip
    Invoke-Native { & $PythonCmd -m pip install --user uv } | ForEach-Object { Write-Verbose $_ }
    if (Test-UvWorks) {
        $uvVersion = Invoke-Native -StdoutOnly { uv --version }
        Log-Ok "uv installed via pip: $uvVersion"
        $UvInstalled = $true
    }

    # Priority 2: official PowerShell installer
    if (-not $UvInstalled) {
        Log-Info "pip failed, trying official installer..."
        try {
            Invoke-Native { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression } | ForEach-Object { Write-Verbose $_ }
            # Refresh PATH for the current session
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Test-UvWorks) {
                $uvVersion = Invoke-Native -StdoutOnly { uv --version }
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
        Log-Info "  $PythonCmd -m pip install -e `".[rag,dev,tui]`""
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

# rag = vector memory; dev = tests/lint; tui = kazma-tui; postgres = the
# driver for the database `kazma setup postgres` sets up.
# There is no [cli] extra — kazma-cli is a first-class package in the wheel.
# Full set: uv sync --inexact --all-extras (heavy: torch, Playwright, WeasyPrint, Temporal).
#
# --inexact, and every extra the install already has: a plain `uv sync`
# makes the environment exactly the extras it names and removes the rest, so
# a re-run of this script removed whatever was installed after the first run
# (the drill VM, 2026-11-06: the Postgres driver, with KAZMA_DATABASE_URL set).
$DefaultExtras = @("rag", "dev", "tui", "postgres")
$extras = [System.Collections.Generic.List[string]]::new()
foreach ($x in $DefaultExtras) { $extras.Add($x) }
$existingPython = @(".venv\Scripts\python.exe", ".venv\bin\python") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($existingPython) {
    $present = Invoke-Native -StdoutOnly { & $existingPython -I -c "from kazma_cli.update import extras_in_this_environment as e; print(' '.join(e()))" }
    if ($LASTEXITCODE -eq 0) {
        foreach ($x in ("$present" -split '\s+')) { if ($x -and -not $extras.Contains($x)) { $extras.Add($x) } }
    }
}
$syncArgs = @("--inexact")
foreach ($x in $extras) { $syncArgs += @("--extra", $x) }
Log-Info "Installing Kazma's packages; the first run downloads PyTorch and can take several minutes"
Invoke-Native { uv sync @syncArgs } | ForEach-Object { Write-Verbose $_ }
if ($LASTEXITCODE -eq 0) {
    Log-Ok "Environment synced from pyproject.toml (extras: $($extras -join ', '))"
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
        $null = Invoke-Native -StdoutOnly { & $PythonCmd -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))" }
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
    Log-Info "  $PythonCmd -m pip install -e `".[rag,dev,tui]`""
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
        Log-Info "uv sync may have failed silently. Try: uv sync $($syncArgs -join ' ') --verbose"
        exit 1
    }
}

$IntroErrors = 0

function Check-Import($module, $label) {
    $null = Invoke-Native { & $VenvPython -c "import $module" }
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
    Log-Info "Fix: uv sync $($syncArgs -join ' ') --reinstall"
    exit 1
}

$testOutput = @(Invoke-Native { & $VenvPython -m pytest tests/ --co -q })
$testLine = ($testOutput | Where-Object { $_ -match '(\d+) tests? collected' } | Select-Object -Last 1)
if ($testLine -match '(\d+) tests? collected') {
    $testCount = $matches[1]
    Log-Ok "$testCount tests collected"
} else {
    # Say why: pytest's own last words (it refused to start on every fresh
    # install while the dev extra lacked pytest-timeout).
    $why = ($testOutput | Where-Object { "$_".Trim() } | Select-Object -Last 1)
    Log-Warn "Could not collect tests (non-critical): $why"
}

# ── 4. Summary ───────────────────────────────────────────────────────────────

Log-Header "4. Setup Complete"

Write-Host ""
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Log-Ok ".env created from .env.example — add a provider API key"
}

Log-Ok "Kazma is ready"
Write-Host ""

# ── 5. System dependencies (resilience plan K2) ──────────────────────────────
# OCR with Arabic, LibreOffice, ClamAV and its daily refresh, the Arabic fonts
# and Docker: the plan is shown first, and nothing is installed without a yes.
Log-Header "5. System dependencies"
if ([Environment]::UserInteractive) {
    # One run: it checks, shows the plan once, asks, and installs on a yes
    # (from an elevated terminal: it says so before asking when it is not).
    & $VenvPython -m kazma_cli setup system
    if ($LASTEXITCODE -ne 0) { Log-Info "Later:          $VenvPython -m kazma_cli setup system" }
} else {
    & $VenvPython -m kazma_cli setup system --dry-run
    Log-Info "To install them: $VenvPython -m kazma_cli setup system"
}
Write-Host ""
$ScriptsDir = Split-Path $VenvPython
Log-Info "Configure:      edit .env (OPENAI_API_KEY or another provider)"
Log-Info "Run Web UI:     & '$(Join-Path $ScriptsDir 'kazma.exe')' serve     # http://127.0.0.1:9090"
Log-Info "Run TUI:        & '$(Join-Path $ScriptsDir 'kazma-tui.exe')'"
Log-Info "Run tests:      $VenvPython scripts\fast_test.py"
Log-Info "Watch/reload:   $VenvPython scripts\service\kazma_guard.py --install"
Log-Info "Everything:     uv sync --inexact --all-extras"
Log-Info "Docs:           https://github.com/Mubder/kazma"
Write-Host ""
Write-Host "🇰🇼 كاظمه — Built to remember. Built to last." -ForegroundColor Green
Write-Host ""
