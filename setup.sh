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

# Options: --mode express|full|custom, -y/--yes (install without the question).
MODE=""
YES=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="${2:-}"; shift 2 ;;
        --mode=*) MODE="${1#*=}"; shift ;;
        -y|--yes) YES=1; shift ;;
        *) echo "unknown option: $1 (use --mode express|full|custom, -y)"; exit 2 ;;
    esac
done
case "$MODE" in ""|express|full|custom) ;; *) echo "--mode is express, full or custom (not '$MODE')"; exit 2 ;; esac

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

# 1a. Python 3.11+
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        PY_MAJOR=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || true)
        PY_MINOR=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || true)
        if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 11 ]]; then
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
    log_warn "No Python 3.11+ here: uv will download one for Kazma"
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
    log_warn "uv not found — installing via pip..."

    # Priority 1: pip (works everywhere — WSL, Docker, bare metal), when there is a Python
    if [[ -n "$PYTHON_CMD" ]] && "$PYTHON_CMD" -m pip install --user uv 2>&1 | tail -1; then
        export PATH="$HOME/.local/bin:$PATH"
        hash -r
        if command -v uv &>/dev/null && uv_works; then
            UV_VERSION=$(uv --version 2>/dev/null || echo "installed")
            log_ok "uv installed via pip: $UV_VERSION"
            UV_INSTALLED=true
        else
            log_warn "pip install succeeded but uv not on PATH"
            log_info "Trying: export PATH=\$HOME/.local/bin:\$PATH"
            # One more check with explicit path
            if [[ -x "$HOME/.local/bin/uv" ]]; then
                export PATH="$HOME/.local/bin:$PATH"
                UV_VERSION=$("$HOME/.local/bin/uv" --version 2>/dev/null || echo "installed")
                log_ok "uv found at ~/.local/bin/uv: $UV_VERSION"
                UV_INSTALLED=true
            fi
        fi
    fi

    # Priority 2: official curl installer (works in Docker/bare metal)
    if [[ "$UV_INSTALLED" == "false" ]] && command -v curl &>/dev/null; then
        log_info "pip failed, trying official installer..."
        if curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -1; then
            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
            hash -r
            if command -v uv &>/dev/null && uv_works; then
                UV_VERSION=$(uv --version 2>/dev/null || echo "installed")
                log_ok "uv installed via curl: $UV_VERSION"
                UV_INSTALLED=true
            fi
        fi
    fi

    # Priority 3: snap (last resort — known systemd issues in WSL2)
    if [[ "$UV_INSTALLED" == "false" ]] && command -v snap &>/dev/null; then
        log_warn "Trying snap as last resort (may fail in WSL2)..."
        if sudo snap install astral-uv --classic 2>&1 | tail -1; then
            hash -r
            if uv_works; then
                UV_VERSION=$(uv --version 2>/dev/null || echo "installed")
                log_ok "uv installed via snap: $UV_VERSION"
                UV_INSTALLED=true
            else
                log_fail "snap uv installed but broken (systemd scope error)"
                sudo snap remove astral-uv 2>/dev/null || true
            fi
        fi
    fi

    if [[ "$UV_INSTALLED" == "false" ]]; then
        log_fail "Could not install a working uv"
        log_info "Fix manually:"
        log_info "  pip install uv"
        log_info "  # or: curl -LsSf https://astral.sh/uv/install.sh | sh"
        log_info ""
        log_info "Or skip uv entirely:"
        log_info "  $PYTHON_CMD -m pip install -e '.[rag,dev,tui]'"
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
if [[ "$YES" == "1" ]]; then
    INSTALL_ARGS+=(-y)
elif [[ ! -t 0 ]]; then
    INSTALL_ARGS+=(--packages-only)
fi
if ! $VENV_PYTHON "${INSTALL_ARGS[@]}"; then
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

log_header "5. Setup Complete"
echo ""
log_info "First visit:    the chat page asks for a provider key (or edit .env)"
log_info "Run Web UI:     .venv/bin/kazma serve          # http://127.0.0.1:9090"
log_info "Run TUI:        .venv/bin/kazma-tui"
log_info "Run tests:      .venv/bin/python scripts/fast_test.py"
log_info "Parts:          .venv/bin/python -m kazma_cli setup install --list"
log_info "Everything:     ./setup.sh --mode full"
log_info "Docs:           https://kazma.ai/docs/"
echo ""
echo -e "${BRAND}🇰🇼 كاظمه — Built to remember. Built to last.${NC}"
echo ""
