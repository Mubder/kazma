#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Kazma (كاظمه) — Certified Bootstrap Script v2
# Deterministic, fail-fast, idempotent initialization.
# Compatible with WSL2, Docker, bare-metal Linux.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Trap: print diagnostic on any failure ────────────────────────────────────
trap 'echo -e "\n\033[0;31m❌ Installation aborted due to error (exit code $?).\033[0m"; echo -e "\033[0;34mℹ️  Run with DEBUG=1 for verbose output: DEBUG=1 ./setup.sh\033[0m"' ERR

# Debug mode
if [[ "${DEBUG:-0}" == "1" ]]; then
    set -x
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_ok()      { echo -e "  ${GREEN}✅ $*${NC}"; }
log_warn()    { echo -e "  ${YELLOW}⚠️  $*${NC}"; }
log_fail()    { echo -e "  ${RED}❌ $*${NC}"; }
log_info()    { echo -e "  ${BLUE}ℹ️  $*${NC}"; }
log_header()  { echo -e "\n${BLUE}━━━ $* ━━━${NC}"; }

# ── 0. Permission Guard ─────────────────────────────────────────────────────
if [[ ! -x "$0" ]]; then
    log_warn "This script is not executable."
    log_info "Run:  chmod +x setup.sh && ./setup.sh"
    exit 1
fi

echo -e "\n${GREEN}🇰🇼 Kazma (كاظمه) — Bootstrap v2${NC}"
echo -e "${BLUE}   Autonomous AI Agent Framework${NC}\n"

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
    log_fail "Python 3.11+ required but not found"
    log_info "Install: sudo apt install python3.11 (Ubuntu/Debian/WSL)"
    log_info "Or:     https://www.python.org/downloads/"
    exit 1
fi

# 1b. uv package manager (pip-first, snap-fallback)
UV_INSTALLED=false

if command -v uv &>/dev/null; then
    UV_VERSION=$(uv --version 2>/dev/null || echo "unknown")
    UV_PATH=$(command -v uv)

    # Detect broken snap uv (systemd scope error)
    if [[ "$UV_PATH" == *snap* ]]; then
        log_warn "uv installed via snap — known systemd scope issues"
        log_info "Replacing with pip-installed uv..."
        sudo snap remove astral-uv 2>/dev/null || true
        "$PYTHON_CMD" -m pip install uv 2>/dev/null || true
        export PATH="$HOME/.local/bin:$PATH"
        hash -r
    fi

    if command -v uv &>/dev/null; then
        UV_VERSION=$(uv --version 2>/dev/null || echo "unknown")
        log_ok "uv detected: $UV_VERSION"
        UV_INSTALLED=true
    fi
fi

if [[ "$UV_INSTALLED" == "false" ]]; then
    log_warn "uv not found — installing..."

    # Priority 1: pip (most universal, works in WSL/Docker/bare-metal)
    if "$PYTHON_CMD" -m pip install uv 2>/dev/null; then
        # Ensure uv is on PATH (pip may install to ~/.local/bin)
        export PATH="$HOME/.local/bin:$PATH"
        hash -r  # refresh command cache
        if command -v uv &>/dev/null; then
            UV_VERSION=$(uv --version 2>/dev/null || echo "installed")
            log_ok "uv installed via pip: $UV_VERSION"
            UV_INSTALLED=true
        fi
    fi

    # Priority 2: snap (bare-metal Ubuntu, not available in Docker/WSL)
    if [[ "$UV_INSTALLED" == "false" ]] && command -v snap &>/dev/null; then
        log_info "pip install failed or uv not on PATH, trying snap..."
        if sudo snap install astral-uv --classic 2>/dev/null; then
            hash -r
            UV_VERSION=$(uv --version 2>/dev/null || echo "installed")
            log_ok "uv installed via snap: $UV_VERSION"
            UV_INSTALLED=true
        fi
    fi

    # Priority 3: official installer (curl)
    if [[ "$UV_INSTALLED" == "false" ]] && command -v curl &>/dev/null; then
        log_info "snap unavailable, trying official installer..."
        if curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null; then
            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
            hash -r
            if command -v uv &>/dev/null; then
                UV_VERSION=$(uv --version 2>/dev/null || echo "installed")
                log_ok "uv installed via official installer: $UV_VERSION"
                UV_INSTALLED=true
            fi
        fi
    fi

    if [[ "$UV_INSTALLED" == "false" ]]; then
        log_fail "Could not install uv automatically"
        log_info "Install manually:"
        log_info "  curl -LsSf https://astral.sh/uv/install.sh | sh"
        log_info "  # or: pip install uv"
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

# Idempotent: uv sync is safe to re-run (only installs missing/changed deps)
if uv sync 2>&1 | tail -5; then
    log_ok "Environment synced from pyproject.toml"
else
    SYNC_EXIT=$?
    log_fail "uv sync failed (exit code $SYNC_EXIT)"

    # Diagnostic block
    echo ""
    log_info "Running diagnostics..."

    # Check disk space
    AVAIL_KB=$(df -k . 2>/dev/null | tail -1 | awk '{print $4}')
    AVAIL_MB=$((AVAIL_KB / 1024))
    if [[ "$AVAIL_MB" -lt 100 ]]; then
        log_fail "Low disk space: ${AVAIL_MB}MB available"
    else
        log_ok "Disk space: ${AVAIL_MB}MB available"
    fi

    # Check pyproject.toml syntax
    if "$PYTHON_CMD" -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))" 2>/dev/null; then
        log_ok "pyproject.toml syntax valid"
    elif "$PYTHON_CMD" -c "import tomli; tomli.load(open('pyproject.toml', 'rb'))" 2>/dev/null; then
        log_ok "pyproject.toml syntax valid"
    else
        log_warn "Could not validate pyproject.toml syntax"
    fi

    # Check network
    if curl -s --max-time 5 https://pypi.org >/dev/null 2>&1; then
        log_ok "PyPI reachable"
    else
        log_fail "Cannot reach PyPI — check network/proxy"
    fi

    echo ""
    log_info "Fallback: try installing without uv:"
    log_info "  $PYTHON_CMD -m pip install -e '.[dev,cli]'"
    exit 1
fi

# ── 3. Foundation Integrity Check ───────────────────────────────────────────

log_header "3. Foundation Integrity Check"

VENV_PYTHON=".venv/bin/python"
if [[ ! -f "$VENV_PYTHON" ]]; then
    log_fail "Virtual environment not found at .venv/"
    log_info "uv sync may have failed silently. Try: uv sync --verbose"
    exit 1
fi

# Verify core imports
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

check_import "sqlite_vec"       "sqlite-vec"
check_import "aiosqlite"        "aiosqlite"
check_import "langgraph"        "LangGraph"
check_import "langgraph.checkpoint.sqlite.aio" "LangGraph SQLite checkpointer"
check_import "yaml"             "PyYAML"
check_import "httpx"            "httpx"

if [[ "$INTRO_ERRORS" -gt 0 ]]; then
    echo ""
    log_fail "$INTRO_ERRORS core import(s) failed"
    log_info "Fix: uv sync --reinstall"
    log_info "If sqlite-vec fails: ensure SQLite 3.35+ (sqlite3 --version)"
    log_info "If langgraph fails:  uv lock --upgrade && uv sync"
    exit 1
fi

# Test collection (fast, no execution)
TEST_COUNT=$($VENV_PYTHON -m pytest tests/ --co -q 2>/dev/null | tail -1 | grep -oP '\d+' | head -1 || echo "0")
if [[ -n "$TEST_COUNT" && "$TEST_COUNT" -gt 0 ]]; then
    log_ok "$TEST_COUNT tests collected"
else
    log_warn "Could not collect tests (non-critical)"
fi

# ── 4. Summary ──────────────────────────────────────────────────────────────

log_header "4. Setup Complete"

echo ""
log_ok "Kazma is ready"
echo ""
log_info "Run tests:      .venv/bin/python -m pytest tests/ -q"
log_info "Run agent:      .venv/bin/python -m kazma_core.agent"
log_info "Configuration:  kazma.yaml"
log_info "Documentation:  https://github.com/Mubder/kazma"
echo ""
echo -e "${GREEN}🇰🇼 كاظمه — Built to remember. Built to last.${NC}"
echo ""
