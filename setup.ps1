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
    Installs uv (and through it Python, when this machine has none), Kazma's
    packages, then hands over to `kazma setup install`: Express (the default),
    Full or Custom, chosen from one list of parts. It checks everything first,
    shows one plan, asks once, installs, and records what it did, so a second
    run continues where the first stopped. A foundation check (core imports,
    test collection) ends it.

    Installing system packages and the service needs administrator rights: in
    a terminal it opens an administrator window for itself (-NoElevate stays
    in this one).

    Idempotent — safe to run multiple times.

    Start it with setup.cmd, beside it: a fresh Windows refuses to run
    PowerShell script files ("running scripts is disabled on this system"),
    and setup.cmd runs this one with the policy bypassed for that one process.

.EXAMPLE
    .\setup.cmd
    .\setup.cmd -Mode full
    .\setup.cmd -Mode express -Yes
    .\setup.cmd -Port 9191      # Kazma's page on another port (9090 by default)
    .\setup.cmd -Debug
#>

# No [CmdletBinding()]: it would make this an advanced script, which gets
# PowerShell's own -Debug, and PowerShell then refuses to start the script at
# all ("A parameter with the name 'Debug' was defined multiple times").
# tests/test_powershell_scripts_start.py holds every shipped script to that.
param(
    [switch]$Debug,
    [string]$Mode = "",
    [switch]$Yes,
    # Kazma's page port. Unset: a first install asks (Enter keeps 9090), and an
    # install made before keeps its port. `kazma setup port N` changes it later.
    [int]$Port = 0,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"

# A person at a terminal: someone can answer a question (not -NonInteractive,
# not a redirected input). Nothing is asked, and no window is opened, otherwise.
$NonInteractiveFlag = @([Environment]::GetCommandLineArgs() | Where-Object { "$_" -like "-NonI*" }).Count -gt 0
$Interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and -not $NonInteractiveFlag

# ── Logging helpers ──────────────────────────────────────────────────────────

# Kazma's colours, readable on a light terminal and a dark one: its royal
# blue (#3b82f6, the web app's identity colour) and darker green, amber and
# red than a terminal's bright defaults (bright yellow and cyan all but vanish
# on white). 24-bit where the terminal takes escape codes, the nearest console
# colour where it does not, none when the output goes to a file or NO_COLOR is set.
$PlainOutput = [bool]$env:NO_COLOR
try { $PlainOutput = $PlainOutput -or [Console]::IsOutputRedirected } catch { Write-Verbose "$_" }
$UseEscapes = $false
try { $UseEscapes = [bool]$Host.UI.SupportsVirtualTerminal } catch { Write-Verbose "$_" }
$KazmaTones = @{
    brand = @("59;130;246", "Blue")
    ok    = @("22;163;74", "DarkGreen")
    warn  = @("217;119;6", "DarkYellow")
    fail  = @("220;38;38", "Red")
    dim   = @("100;116;139", "DarkGray")
}

function Write-Kazma([string]$text, [string]$tone = "", [switch]$Bold) {
    if ($PlainOutput -or -not $tone) { Write-Host $text; return }
    $rgb, $console = $KazmaTones[$tone]
    if ($UseEscapes) {
        $esc = [char]27
        $weight = if ($Bold) { "1;" } else { "" }
        Write-Host "$esc[${weight}38;2;${rgb}m$text$esc[0m"
    } else {
        Write-Host $text -ForegroundColor $console
    }
}

function Log-Ok($msg) { Write-Kazma "  ✅ $msg" ok }
function Log-Warn($msg) { Write-Kazma "  ⚠️  $msg" warn }
function Log-Fail($msg) { Write-Kazma "  ❌ $msg" fail }
function Log-Info($msg) { Write-Kazma "  ℹ️  $msg" }
function Log-Header($msg) { Write-Kazma "`n━━━ $msg ━━━" brand -Bold }

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

# ── Helper: install uv, verified ─────────────────────────────────────────────
# One uv release, with the SHA-256 of each Windows archive written here
# (2026-11, supply chain). Setup used to pipe https://astral.sh/uv/install.ps1
# into Invoke-Expression, in an administrator window: it ran whatever that
# address served that day. Now it downloads this release's archive from
# GitHub, checks it against the hash below before anything is extracted or
# run, and stops on a mismatch. The hashes are uv's own .sha256 files of the
# release, which GitHub's asset digests matched. setup.sh does the same;
# tests/test_setup_downloads_verified.py holds both to it.
$UvVersion = "0.13.6"
$UvArchiveSha256 = @{
    "x86_64-pc-windows-msvc"  = "a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f"
    "aarch64-pc-windows-msvc" = "c2426c0c8e28b53d8c440e47a18d96ad4db51e159866c0e81435b196d7f0081c"
    "i686-pc-windows-msvc"    = "32ceb64db231cbedfd499f7654c72e4c0b564f2846f9e76c0a4a7b2d03d7e67c"
}

function Get-UvTarget {
    # A 32-bit PowerShell on a 64-bit Windows names the machine here.
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ("$arch".ToUpper()) {
        "AMD64" { return "x86_64-pc-windows-msvc" }
        "ARM64" { return "aarch64-pc-windows-msvc" }
        "X86"   { return "i686-pc-windows-msvc" }
    }
    return ""
}

function Add-UvToUserPath([string]$Dir) {
    # As uv's own installer does: later terminals (and `kazma update`) find it.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @("$userPath" -split ";" | Where-Object { $_ })
    if ($parts -notcontains $Dir) {
        [Environment]::SetEnvironmentVariable("Path", (@($Dir) + $parts) -join ";", "User")
    }
}

# The download, the hash and the unzip are .NET calls: Windows PowerShell 5.1
# started from a PowerShell 7 terminal inherits 7's module path and cannot
# load Get-FileHash ("not recognized"), and its Invoke-WebRequest progress bar
# makes a download many times slower.
function Save-UvArchive([string]$Url, [string]$Path) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $client = New-Object System.Net.WebClient
    try { $client.DownloadFile($Url, $Path) } finally { $client.Dispose() }
}

function Get-Sha256Hex([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return -join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") })
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Expand-UvArchive([string]$Archive, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
}

function Install-PinnedUv {
    $target = Get-UvTarget
    if (-not $target -or -not $UvArchiveSha256.ContainsKey($target)) {
        Log-Fail "uv $UvVersion has no Windows build for this processor ($env:PROCESSOR_ARCHITECTURE)"
        return $false
    }
    $name = "uv-$target.zip"
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$name"
    $work = Join-Path ([IO.Path]::GetTempPath()) ("kazma-uv-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try {
        $archive = Join-Path $work $name
        try {
            Save-UvArchive $url $archive
        } catch {
            Log-Fail "Could not download $url"
            Log-Info "  $_"
            return $false
        }
        $expected = $UvArchiveSha256[$target]
        $actual = Get-Sha256Hex $archive
        if ($actual -ne $expected) {
            Log-Fail "The uv download does not match its recorded SHA-256: nothing was installed"
            Log-Info "  $name"
            Log-Info "  expected $expected"
            Log-Info "  got      $actual"
            return $false
        }
        $dir = Join-Path $env:USERPROFILE ".local\bin"
        try {
            $unpacked = Join-Path $work "unpacked"
            Expand-UvArchive $archive $unpacked
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            foreach ($exe in @("uv.exe", "uvx.exe", "uvw.exe")) {
                $found = Get-ChildItem -LiteralPath $unpacked -Recurse -Filter $exe | Select-Object -First 1
                if ($found) { Copy-Item -LiteralPath $found.FullName -Destination (Join-Path $dir $exe) -Force }
            }
        } catch {
            Log-Fail "Could not unpack uv into $dir"
            Log-Info "  $_"
            return $false
        }
        if (-not (Test-Path -LiteralPath (Join-Path $dir "uv.exe"))) {
            Log-Fail "The uv archive held no uv.exe"
            return $false
        }
        try { Add-UvToUserPath $dir } catch { Log-Warn "uv is in $dir; add it to PATH for later terminals ($_)" }
        $env:Path = "$dir;$env:Path"
        return $true
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ── Banner ───────────────────────────────────────────────────────────────────

# Which Kazma this installs, read before anything is installed: the version
# pyproject.toml declares and, in a git checkout, the commit.
$KazmaVersion = ""
$pyproject = Join-Path $PSScriptRoot "pyproject.toml"
if (Test-Path $pyproject -PathType Leaf) {
    $versionLine = Select-String -Path $pyproject -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($versionLine) { $KazmaVersion = $versionLine.Matches[0].Groups[1].Value }
}
$commit = "$(Invoke-Native -StdoutOnly { git -C $PSScriptRoot rev-parse --short HEAD })".Trim()
if ($LASTEXITCODE -ne 0) { $commit = "" }
$versionText = $KazmaVersion
if ($versionText -and $commit) { $versionText += " (commit $commit)" }
if (-not $versionText) { $versionText = "(version unknown)" }

# What Kazma is, in its own words (docs/docs/guide/what-is-kazma.md).
Write-Host ""
Write-Kazma "  Kazma (كاظمه) $versionText — setup" brand -Bold
Write-Kazma "  The self-hosted AI agent that asks before it acts."
Write-Kazma "  It runs on this machine with the AI models you choose, remembers what you" dim
Write-Kazma "  tell it, and works in English and Arabic. Open source (MIT), built in Kuwait." dim
Write-Host ""
Write-Kazma "  This installs uv and a Python when they are missing, Kazma's packages, then" dim
Write-Kazma "  asks how to install Kazma: Express, Full or Custom." dim

if ($Port -and ($Port -lt 1024 -or $Port -gt 65535)) {
    Write-Kazma "  -Port is a port from 1024 to 65535 (not '$Port')" fail
    exit 2
}
if ($Mode -and @("express", "full", "custom") -notcontains $Mode.ToLower()) {
    Write-Kazma "  -Mode is express, full or custom (not '$Mode')" fail
    exit 2
}

# ── 0. An administrator window ──────────────────────────────────────────────
# System packages (winget) and the service need administrator rights. Asked
# in a plain terminal, the install used to stop half way and ask for an
# elevated one (the drill VM, 2026-11-06). In a terminal, setup opens that
# window for itself and goes on there; declined, it goes on here and lists
# what needs the rights.
$IsAdmin = $false
try {
    $IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
} catch {
    Write-Verbose "$_"
}
if ($Interactive -and -not $IsAdmin -and -not $NoElevate -and $env:OS -eq "Windows_NT") {
    $forward = @()
    if ($Mode) { $forward += "-Mode $Mode" }
    if ($Yes) { $forward += "-Yes" }
    if ($Port) { $forward += "-Port $Port" }
    if ($Debug) { $forward += "-Debug" }
    $here = (Get-Location).Path -replace "'", "''"
    $self = $PSCommandPath -replace "'", "''"
    $command = "Set-Location -LiteralPath '$here'; & '$self' $($forward -join ' ')"
    $shell = (Get-Process -Id $PID).Path
    try {
        Start-Process -FilePath $shell -Verb RunAs -ArgumentList "-NoExit -NoProfile -ExecutionPolicy Bypass -Command `"$command`""
        Log-Info "Setup goes on in the administrator window it opened."
        exit 0
    } catch {
        Log-Warn "No administrator window: system packages and the service are listed, not installed"
    }
}

# ── 1. Environmental Guardrails ──────────────────────────────────────────────

Log-Header "1. Environmental Guardrails"

# 1a. Python 3.11.5+ (pyproject's requires-python; soupsieve 4 needs 3.11.5)
$PythonCmd = $null
$PythonCandidates = @("python", "python3", "py")

foreach ($cmd in $PythonCandidates) {
    try {
        $versionOutput = Invoke-Native -StdoutOnly { & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" }
        if ($LASTEXITCODE -eq 0 -and $versionOutput) {
            $parts = "$versionOutput".Trim() -split '\.'
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            $micro = [int]$parts[2]
            if ($major -eq 3 -and ($minor -gt 11 -or ($minor -eq 11 -and $micro -ge 5))) {
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
    # uv downloads a Python for Kazma's environment (pyproject's
    # requires-python): a fresh Windows has none, and installing one by hand
    # was the first manual step of every install.
    Log-Warn "No Python 3.11.5+ here: uv will download one for Kazma"
}

# 1b. uv package manager
$UvInstalled = $false

if (Test-UvWorks) {
    $uvVersion = Invoke-Native -StdoutOnly { uv --version }
    Log-Ok "uv detected: $uvVersion"
    $UvInstalled = $true
} else {
    Log-Warn "uv not found — installing uv $UvVersion (checked against its recorded SHA-256)..."

    # Install-PinnedUv: the release archive, verified before it is unpacked.
    if (Install-PinnedUv) {
        if (Test-UvWorks) {
            $uvVersion = Invoke-Native -StdoutOnly { uv --version }
            Log-Ok "uv installed: $uvVersion"
            $UvInstalled = $true
        } else {
            Log-Fail "uv $UvVersion was installed but does not run"
        }
    }

    if (-not $UvInstalled) {
        Log-Fail "Could not install a working uv"
        Log-Info "Fix manually: download uv-$(Get-UvTarget).zip from"
        Log-Info "  https://github.com/astral-sh/uv/releases/tag/$UvVersion"
        Log-Info "check it against the .sha256 file beside it, put uv.exe on PATH,"
        Log-Info "then run setup.cmd again."
        exit 1
    }
}

# 1c. kazma.yaml presence
if (Test-Path "kazma.yaml") {
    Log-Ok "kazma.yaml found"
} else {
    Log-Fail "kazma.yaml not found in current directory"
    Log-Info "Run it from the Kazma project root: cd kazma; .\setup.cmd"
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

# Kazma's base packages here; the extras of the install chosen in step 5
# (Express, Full or Custom) come from `kazma setup install`, which reads them
# from one list of parts (kazma_core/install_components.py).
# There is no [cli] extra — kazma-cli is a first-class package in the wheel.
#
# --inexact, and every extra the install already has: a plain `uv sync`
# makes the environment exactly the extras it names and removes the rest, so
# a re-run of this script removed whatever was installed after the first run
# (the drill VM, 2026-11-06: the Postgres driver, with KAZMA_DATABASE_URL set).
$extras = [System.Collections.Generic.List[string]]::new()
$existingPython = @(".venv\Scripts\python.exe", ".venv\bin\python") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($existingPython) {
    $present = Invoke-Native -StdoutOnly { & $existingPython -I -c "from kazma_cli.update import extras_in_this_environment as e; print(' '.join(e()))" }
    if ($LASTEXITCODE -eq 0) {
        foreach ($x in ("$present" -split '\s+')) { if ($x -and -not $extras.Contains($x)) { $extras.Add($x) } }
    }
}
$syncArgs = @("--inexact")
foreach ($x in $extras) { $syncArgs += @("--extra", $x) }
Log-Info "Installing Kazma's base packages"
Invoke-Native { uv sync @syncArgs } | ForEach-Object { Write-Verbose $_ }
if ($LASTEXITCODE -eq 0) {
    $kept = if ($extras.Count) { " (kept extras: $($extras -join ', '))" } else { "" }
    Log-Ok "Environment synced from pyproject.toml$kept"
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

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Log-Ok ".env created from .env.example"
}

# ── 4. Install: Express, Full or Custom ─────────────────────────────────────
# One list of parts (kazma_core/install_components.py), three ways to take it:
# Express (the default: the everyday parts, with private web search and the
# memory graph on Kazma's container engine where this computer can run
# containers), Full (every part, PostgreSQL among them) or Custom (a
# checklist; each part says what Kazma lacks without it). It checks everything first, shows one plan,
# asks once, installs, and records what it did: run setup again and it
# continues where it stopped. Without a terminal it installs Kazma's own
# packages and shows the rest as the plan (-Yes installs everything).
Log-Header "4. Install (Express, Full or Custom)"
$installArgs = @("-m", "kazma_cli", "setup", "install")
if ($Mode) { $installArgs += @("--mode", $Mode.ToLower()) }
if ($Port) { $installArgs += @("--port", "$Port") }
if ($Yes) { $installArgs += "-y" }
elseif (-not $Interactive) { $installArgs += "--packages-only" }
& $VenvPython @installArgs
$installExit = $LASTEXITCODE
# 3: everything that could be done is done, and Windows must restart (WSL)
# before the container parts (kazma_cli.install EXIT_RESTART).
if ($installExit -eq 3) {
    Log-Warn "Restart Windows, then run .\setup.cmd again: it finishes the container parts (WSL needs the restart)"
    Log-Info "What happened is in .kazma\install.log"
}
elseif ($installExit -ne 0) {
    Log-Warn "Not everything was installed: run .\setup.cmd again to finish (it continues where it stopped)"
    Log-Info "What happened is in .kazma\install.log"
}

$null = Invoke-Native { & $VenvPython -c "import pytest" }
if ($LASTEXITCODE -eq 0) {
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
} else {
    Log-Info "Kazma's tests are not installed (the tests part was not chosen)"
}

# ── 5. Summary ───────────────────────────────────────────────────────────────

# The install's own exit code is setup's: on the clean VM (2026-11-08) seven
# parts were left out and setup still said "Setup Complete" and exited 0, so a
# script that ran it could not tell.
if ($installExit -eq 0) { Log-Header "5. Setup Complete" }
elseif ($installExit -eq 3) { Log-Header "5. Restart Windows, then run .\setup.cmd again to finish" }
else { Log-Header "5. Setup Stopped Short (run .\setup.cmd again to finish)" }
Write-Host ""
$ScriptsDir = Split-Path $VenvPython
Log-Info "First visit:    the chat page asks for a provider key (or edit .env)"
# The port the install kept (KAZMA_PORT in .env); 9090 when none is set.
$KazmaPort = "9090"
$PortLine = Select-String -Path ".env" -Pattern '^\s*KAZMA_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -Last 1
if ($PortLine) { $KazmaPort = $PortLine.Matches[0].Groups[1].Value }
Log-Info "Kazma:          runs as a service when that part is installed (http://127.0.0.1:$KazmaPort)"
Log-Info "Port:           $VenvPython -m kazma_cli setup port <N> changes it"
Log-Info "Without it:     & '$(Join-Path $ScriptsDir 'kazma.exe')' serve"
Log-Info "Run TUI:        & '$(Join-Path $ScriptsDir 'kazma-tui.exe')'"
Log-Info "Run tests:      $VenvPython scripts\fast_test.py"
Log-Info "Parts:          $VenvPython -m kazma_cli setup install --list"
Log-Info "Everything:     .\setup.cmd -Mode full"
Log-Info "Docs:           https://kazma.ai/docs/"
Write-Host ""
Write-Kazma "🇰🇼 كاظمه — Built to remember. Built to last." brand -Bold
Write-Host ""
exit $installExit
