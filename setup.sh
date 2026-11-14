#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Kazma (كاظمه) — Certified Bootstrap Script v3
# Deterministic, fail-fast, idempotent initialization.
# Compatible with WSL2, Docker, bare-metal Linux.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

trap 'echo -e "\n\033[0;31m❌ Installation aborted due to error (exit code $?).\033[0m"; echo -e "\033[0;34mℹ️  Run with DEBUG=1 for verbose output: DEBUG=1 ./setup.sh\033[0m"' ERR

if [[ "${DEBUG:-0}" == "1" ]]; then
    set -x
fi

# Options: --mode express|full|custom, -y/--yes (install without the question),
# --port N (Kazma's page port; a first install asks, Enter keeps 9090).
MODE=""
YES=0
PORT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="${2:-}"; shift 2 ;;
        --mode=*) MODE="${1#*=}"; shift ;;
        -y|--yes) YES=1; shift ;;
        --port) PORT="${2:-}"; shift 2 ;;
        --port=*) PORT="${1#*=}"; shift ;;
        *) echo "unknown option: $1 (use --mode express|full|custom, -y, --port N)"; exit 2 ;;
    esac
done
case "$MODE" in ""|express|full|custom) ;; *) echo "--mode is express, full or custom (not '$MODE')"; exit 2 ;; esac
if [[ -n "$PORT" ]] && { [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); }; then
    echo "--port is a port from 1024 to 65535 (not '$PORT')"; exit 2
fi

# Kazma's colours, readable on a light terminal and a dark one: its royal blue
# (#3b82f6, the web app's identity colour) and darker green, amber and red
# than a terminal's bright defaults (bright yellow all but vanishes on white).
# 256-colour codes, which macOS's Terminal shows too (it has no 24-bit
# colour); none when the output is not a terminal or NO_COLOR is set.
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    BRAND='\033[1;38;5;33m'
    GREEN='\033[38;5;28m'
    YELLOW='\033[38;5;172m'
    RED='\033[38;5;160m'
    DIM='\033[38;5;66m'
    NC='\033[0m'
else
    BRAND='' GREEN='' YELLOW='' RED='' DIM='' NC=''
fi

log_ok()      { echo -e "  ${GREEN}✅ $*${NC}"; }
log_warn()    { echo -e "  ${YELLOW}⚠️  $*${NC}"; }
log_fail()    { echo -e "  ${RED}❌ $*${NC}"; }
log_info()    { echo -e "  ℹ️  $*"; }
log_header()  { echo -e "\n${BRAND}━━━ $* ━━━${NC}"; }

# ── Helper: test if uv actually works ───────────────────────────────────────
uv_works() {
    uv --version &>/dev/null
}

# ── Helper: install uv, verified ────────────────────────────────────────────
# One uv release, with the SHA-256 of each archive written here (2026-11,
# supply chain). Setup used to pipe https://astral.sh/uv/install.sh into sh:
# it ran whatever that address served that day. Now it downloads this
# release's archive from GitHub, checks it against the hash below before
# anything is unpacked or run, and stops on a mismatch. The hashes are uv's
# own .sha256 files of the release, which GitHub's asset digests matched.
# setup.ps1 does the same; tests/test_setup_downloads_verified.py holds both.
UV_RELEASE="0.13.6"

uv_archive_sha256() {
    case "$1" in
        x86_64-unknown-linux-gnu)   echo "f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645" ;;
        aarch64-unknown-linux-gnu)  echo "229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d" ;;
        x86_64-unknown-linux-musl)  echo "5d61f26b6c7213779eda90edc6971ea6b0b796bfb8d49a574c8ad69b59838548" ;;
        aarch64-unknown-linux-musl) echo "819692e404d23e5aae6bc44c0e260f1f5492fd1910ab4693b2c650f926dbe800" ;;
        x86_64-apple-darwin)        echo "63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02" ;;
        aarch64-apple-darwin)       echo "60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472" ;;
        *) echo "" ;;
    esac
}

uv_target() {
    local arch ldd_out
    case "$(uname -m)" in
        x86_64|amd64) arch="x86_64" ;;
        aarch64|arm64) arch="aarch64" ;;
        *) echo ""; return 0 ;;
    esac
    case "$(uname -s)" in
        Darwin) echo "$arch-apple-darwin" ;;
        Linux)
            ldd_out=$(ldd --version 2>&1 || true)
            if [[ "$ldd_out" == *musl* || -f /etc/alpine-release ]]; then
                echo "$arch-unknown-linux-musl"
            else
                echo "$arch-unknown-linux-gnu"
            fi ;;
        *) echo "" ;;
    esac
}

sha256_of() {
    if command -v sha256sum &>/dev/null; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum &>/dev/null; then
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

download_to() {
    if command -v curl &>/dev/null; then
        curl --proto '=https' --tlsv1.2 -fsSL "$1" -o "$2"
    elif command -v wget &>/dev/null; then
        wget -q --https-only -O "$2" "$1"
    else
        log_fail "Neither curl nor wget is here to download uv"
        return 1
    fi
}

install_pinned_uv() {
    local target expected url work archive actual found
    target=$(uv_target)
    expected=$(uv_archive_sha256 "$target")
    if [[ -z "$target" || -z "$expected" ]]; then
        log_fail "uv $UV_RELEASE has no build for this machine ($(uname -s) $(uname -m))"
        return 1
    fi
    url="https://github.com/astral-sh/uv/releases/download/$UV_RELEASE/uv-$target.tar.gz"
    work=$(mktemp -d)
    archive="$work/uv-$target.tar.gz"
    if ! download_to "$url" "$archive"; then
        log_fail "Could not download $url"
        rm -rf "$work"
        return 1
    fi
    actual=$(sha256_of "$archive")
    if [[ -z "$actual" ]]; then
        log_fail "No sha256sum or shasum here to check the uv download: nothing was installed"
        rm -rf "$work"
        return 1
    fi
    if [[ "$actual" != "$expected" ]]; then
        log_fail "The uv download does not match its recorded SHA-256: nothing was installed"
        log_info "  uv-$target.tar.gz"
        log_info "  expected $expected"
        log_info "  got      $actual"
        rm -rf "$work"
        return 1
    fi
    mkdir -p "$work/unpacked" "$HOME/.local/bin"
    tar -xzf "$archive" -C "$work/unpacked"
    for exe in uv uvx; do
        found=$(find "$work/unpacked" -type f -name "$exe" | head -n 1)
        if [[ -n "$found" ]]; then
            cp "$found" "$HOME/.local/bin/$exe"
            chmod 755 "$HOME/.local/bin/$exe"
        fi
    done
    rm -rf "$work"
    if [[ ! -x "$HOME/.local/bin/uv" ]]; then
        log_fail "The uv archive held no uv"
        return 1
    fi
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) log_info "uv is in ~/.local/bin: add it to PATH for later terminals (export PATH=\"\$HOME/.local/bin:\$PATH\")" ;;
    esac
    export PATH="$HOME/.local/bin:$PATH"
    hash -r
}

# ── 0. Permission Guard ─────────────────────────────────────────────────────
if [[ ! -x "$0" ]]; then
    log_warn "This script is not executable."
    log_info "Run:  chmod +x setup.sh && ./setup.sh"
    exit 1
fi

# Which Kazma this installs, read before anything is installed: the version
# pyproject.toml declares and, in a git checkout, the commit.
script_dir="$(cd "$(dirname "$0")" && pwd)"
kazma_version="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$script_dir/pyproject.toml" 2>/dev/null | head -n 1 || true)"
kazma_commit="$(git -C "$script_dir" rev-parse --short HEAD 2>/dev/null || true)"
version_text="${kazma_version:-(version unknown)}"
if [[ -n "$kazma_version" && -n "$kazma_commit" ]]; then
    version_text="$kazma_version (commit $kazma_commit)"
fi
# What Kazma is, in its own words (docs/docs/guide/what-is-kazma.md).
echo
echo -e "  ${BRAND}Kazma (كاظمه) ${version_text} — setup${NC}"
echo -e "  The self-hosted AI agent that asks before it acts."
echo -e "  ${DIM}It runs on this machine with the AI models you choose, remembers what you${NC}"
echo -e "  ${DIM}tell it, and works in English and Arabic. Open source (MIT), built in Kuwait.${NC}"
echo
echo -e "  ${DIM}This installs uv and a Python when they are missing, Kazma's packages, then${NC}"
echo -e "  ${DIM}asks how to install Kazma: Express, Full or Custom.${NC}"

# ── 1. Environmental Guardrails ─────────────────────────────────────────────

log_header "1. Environmental Guardrails"

# 1a. Python 3.11.5+ (pyproject's requires-python; soupsieve 4 needs 3.11.5)
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info[:3] >= (3, 11, 5) and sys.version_info[0] == 3 else 1)' 2>/dev/null; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -n "$PYTHON_CMD" ]]; then
    PY_VERSION=$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
    log_ok "Python $PY_VERSION detected ($PYTHON_CMD)"
else
    # uv downloads a Python for Kazma's environment (pyproject's requires-python).
    log_warn "No Python 3.11.5+ here: uv will download one for Kazma"
fi

# 1b. uv package manager (pip-first, snap-fallback)
UV_INSTALLED=false

# First: remove broken snap uv if present
if command -v uv &>/dev/null; then
    UV_PATH=$(command -v uv)
    if [[ "$UV_PATH" == *snap* ]]; then
        if ! uv_works; then
            log_warn "Broken snap uv detected at $UV_PATH — removing"
            sudo snap remove astral-uv 2>/dev/null || true
        fi
    fi
fi

# Check if uv is now available and working
if command -v uv &>/dev/null && uv_works; then
    UV_VERSION=$(uv --version 2>/dev/null || echo "unknown")
    log_ok "uv detected: $UV_VERSION"
    UV_INSTALLED=true
fi

# Install uv if needed
if [[ "$UV_INSTALLED" == "false" ]]; then
    log_warn "uv not found — installing uv $UV_RELEASE (checked against its recorded SHA-256)..."

    # install_pinned_uv: the release archive, verified before it is unpacked.
    if install_pinned_uv; then
        if uv_works; then
            UV_VERSION=$(uv --version 2>/dev/null || echo "installed")
            log_ok "uv installed: $UV_VERSION"
            UV_INSTALLED=true
        else
            log_fail "uv $UV_RELEASE was installed but does not run"
        fi
    fi

    if [[ "$UV_INSTALLED" == "false" ]]; then
        log_fail "Could not install a working uv"
        log_info "Fix manually: download uv-$(uv_target).tar.gz from"
        log_info "  https://github.com/astral-sh/uv/releases/tag/$UV_RELEASE"
        log_info "check it against the .sha256 file beside it, put uv on PATH,"
        log_info "then run ./setup.sh again."
        exit 1
    fi
fi

# 1c. kazma.yaml presence
if [[ -f "kazma.yaml" ]]; then
    log_ok "kazma.yaml found"
else
    log_fail "kazma.yaml not found in current directory"
    log_info "Run this script from the Kazma project root: cd kazma && ./setup.sh"
    exit 1
fi

# 1d. pyproject.toml readable
if [[ -r "pyproject.toml" ]]; then
    log_ok "pyproject.toml readable"
else
    log_fail "pyproject.toml not found or not readable"
    exit 1
fi

# ── 2. The Sync Handshake ───────────────────────────────────────────────────

log_header "2. Sync Handshake (uv sync)"

# Kazma's base packages here; the extras of the install chosen in step 4
# (Express, Full or Custom) come from `kazma setup install`, which reads them
# from one list of parts (kazma_core/install_components.py).
# There is no [cli] extra — kazma-cli is a first-class package in the wheel.
#
# --inexact, and every extra the install already has: a plain `uv sync`
# makes the environment exactly the extras it names and removes the rest, so
# a re-run of this script removed whatever was installed after the first run
# (the drill VM, 2026-11-06: the Postgres driver, with KAZMA_DATABASE_URL set).
EXTRAS=()
if [[ -x .venv/bin/python ]]; then
    PRESENT=$(.venv/bin/python -I -c "from kazma_cli.update import extras_in_this_environment as e; print(' '.join(e()))" 2>/dev/null || true)
    for x in $PRESENT; do
        [[ " ${EXTRAS[*]:-} " == *" $x "* ]] || EXTRAS+=("$x")
    done
fi
SYNC_ARGS=(--inexact)
for x in "${EXTRAS[@]:-}"; do [[ -n "$x" ]] && SYNC_ARGS+=(--extra "$x"); done
if uv sync "${SYNC_ARGS[@]}" 2>&1 | tail -5; then
    log_ok "Environment synced from pyproject.toml${EXTRAS[*]:+ (kept extras: ${EXTRAS[*]})}"
else
    SYNC_EXIT=$?
    log_fail "uv sync failed (exit code $SYNC_EXIT)"

    # Diagnostic block
    echo ""
    log_info "Running diagnostics..."

    AVAIL_KB=$(df -k . 2>/dev/null | tail -1 | awk '{print $4}')
    AVAIL_MB=$((AVAIL_KB / 1024))
    if [[ "$AVAIL_MB" -lt 100 ]]; then
        log_fail "Low disk space: ${AVAIL_MB}MB available"
    else
        log_ok "Disk space: ${AVAIL_MB}MB available"
    fi

    if "$PYTHON_CMD" -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))" 2>/dev/null; then
        log_ok "pyproject.toml syntax valid"
    else
        log_warn "Could not validate pyproject.toml syntax"
    fi

    if curl -s --max-time 5 https://pypi.org >/dev/null 2>&1; then
        log_ok "PyPI reachable"
    else
        log_fail "Cannot reach PyPI — check network/proxy"
    fi

    echo ""
    log_info "Fallback — install without uv:"
    log_info "  $PYTHON_CMD -m pip install -e '.[rag,dev,tui]'"
    exit 1
fi

# ── 3. Foundation Integrity Check ───────────────────────────────────────────

log_header "3. Foundation Integrity Check"

VENV_PYTHON=".venv/bin/python"
if [[ ! -f "$VENV_PYTHON" ]]; then
    log_fail "Virtual environment not found at .venv/"
    log_info "uv sync may have failed silently. Try: uv sync ${SYNC_ARGS[*]} --verbose"
    exit 1
fi

INTRO_ERRORS=0

check_import() {
    local module="$1"
    local label="$2"
    if $VENV_PYTHON -c "import $module" 2>/dev/null; then
        log_ok "$label loaded"
    else
        log_fail "$label not importable"
        INTRO_ERRORS=$((INTRO_ERRORS + 1))
    fi
}

check_import "aiosqlite"        "aiosqlite"
check_import "langgraph"        "LangGraph"
check_import "langgraph.checkpoint.sqlite.aio" "LangGraph SQLite checkpointer"
check_import "yaml"             "PyYAML"
check_import "httpx"            "httpx"
check_import "textual"          "textual (TUI)"

if [[ "$INTRO_ERRORS" -gt 0 ]]; then
    echo ""
    log_fail "$INTRO_ERRORS core import(s) failed"
    log_info "Fix: uv sync ${SYNC_ARGS[*]} --reinstall"
    exit 1
fi

if [[ ! -f .env && -f .env.example ]]; then
    cp .env.example .env
    log_ok ".env created from .env.example"
fi

# ── 4. Install: Express, Full or Custom ─────────────────────────────────────
# One list of parts (kazma_core/install_components.py), three ways to take it:
# Express (the default: everything that needs no Docker), Full (every part,
# Docker and PostgreSQL among them) or Custom (a checklist; each part says
# what Kazma lacks without it). It checks everything first, shows one plan,
# asks once, installs, and records what it did: run setup again and it
# continues where it stopped. Without a terminal it installs Kazma's own
# packages and shows the rest as the plan (-y installs everything).
log_header "4. Install (Express, Full or Custom)"
INSTALL_ARGS=(-m kazma_cli setup install)
[[ -n "$MODE" ]] && INSTALL_ARGS+=(--mode "$MODE")
[[ -n "$PORT" ]] && INSTALL_ARGS+=(--port "$PORT")
if [[ "$YES" == "1" ]]; then
    INSTALL_ARGS+=(-y)
elif [[ ! -t 0 ]]; then
    INSTALL_ARGS+=(--packages-only)
fi
INSTALL_EXIT=0
$VENV_PYTHON "${INSTALL_ARGS[@]}" || INSTALL_EXIT=$?
if [[ "$INSTALL_EXIT" -eq 3 ]]; then
    # kazma_cli.install EXIT_RESTART: the computer must restart before the rest.
    log_warn "Restart this computer, then run ./setup.sh again: it finishes what is left"
    log_info "What happened is in .kazma/install.log"
elif [[ "$INSTALL_EXIT" -ne 0 ]]; then
    log_warn "Not everything was installed: run ./setup.sh again to finish (it continues where it stopped)"
    log_info "What happened is in .kazma/install.log"
fi

if $VENV_PYTHON -c "import pytest" 2>/dev/null; then
    TEST_OUTPUT=$($VENV_PYTHON -m pytest tests/ --co -q 2>&1 || true)
    TEST_COUNT=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ tests? collected' | tail -1 | grep -oE '^[0-9]+' || true)
    if [[ -n "$TEST_COUNT" && "$TEST_COUNT" -gt 0 ]]; then
        log_ok "$TEST_COUNT tests collected"
    else
        # Say why: pytest's own last words (it refused to start on every fresh
        # install while the dev extra lacked pytest-timeout).
        log_warn "Could not collect tests (non-critical): $(echo "$TEST_OUTPUT" | grep -v '^[[:space:]]*$' | tail -1)"
    fi
else
    log_info "Kazma's tests are not installed (the tests part was not chosen)"
fi

# ── 5. Summary ──────────────────────────────────────────────────────────────

# The install's own exit code is setup's: on the clean Windows VM (2026-11-08)
# seven parts were left out and setup still said "Setup Complete" and exited 0.
if [[ "$INSTALL_EXIT" -eq 0 ]]; then
    log_header "5. Setup Complete"
elif [[ "$INSTALL_EXIT" -eq 3 ]]; then
    log_header "5. Restart this computer, then run ./setup.sh again to finish"
else
    log_header "5. Setup Stopped Short (run ./setup.sh again to finish)"
fi
echo ""
log_info "First visit:    the chat page asks for a provider key (or edit .env)"
# The port the install kept (KAZMA_PORT in .env); 9090 when none is set.
KAZMA_PORT_SHOWN="$(grep -E '^[[:space:]]*KAZMA_PORT[[:space:]]*=[[:space:]]*[0-9]+' .env 2>/dev/null | tail -n 1 | sed -E 's/[^0-9]*([0-9]+).*/\1/' || true)"
log_info "Run Web UI:     .venv/bin/kazma serve          # http://127.0.0.1:${KAZMA_PORT_SHOWN:-9090}"
log_info "Port:           .venv/bin/python -m kazma_cli setup port <N> changes it"
log_info "Run TUI:        .venv/bin/kazma-tui"
log_info "Run tests:      .venv/bin/python scripts/fast_test.py"
log_info "Parts:          .venv/bin/python -m kazma_cli setup install --list"
log_info "Everything:     ./setup.sh --mode full"
log_info "Docs:           https://kazma.ai/docs/"
echo ""
echo -e "${BRAND}🇰🇼 كاظمه — Built to remember. Built to last.${NC}"
echo ""
exit "$INSTALL_EXIT"
