#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Kazma (كاظمه) — Certified Bootstrap Script
# Deterministic, fail-fast initialization for agents and developers.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

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

echo -e "\n${GREEN}🇰🇼 Kazma (كاظمه) — Bootstrap${NC}"
echo -e "${BLUE}   Autonomous AI Agent Framework${NC}\n"

# ── 1. Environmental Guardrails ─────────────────────────────────────────────

log_header "1. Environmental Guardrails"

# 1a. Python 3.11+
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
    PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 11 ]]; then
        log_ok "Python $PY_VERSION detected"
    else
        log_fail "Python 3.11+ required, found $PY_VERSION"
        log_info "Install: https://www.python.org/downloads/"
        exit 1
    fi
else
    log_fail "Python 3 not found"
    log_info "Install: sudo apt install python3.11 (Ubuntu/Debian)"
    exit 1
fi

# 1b. uv package manager
if command -v uv &>/dev/null; then
    UV_VERSION=$(uv --version 2>/dev/null | head -1)
    log_ok "uv detected: $UV_VERSION"
else
    log_warn "uv not found — attempting install..."
    if command -v snap &>/dev/null; then
        sudo snap install astral-uv --classic 2>/dev/null && log_ok "uv installed via snap" || true
    fi
    if ! command -v uv &>/dev/null; then
        if command -v pip3 &>/dev/null; then
            pip3 install uv 2>/dev/null && log_ok "uv installed via pip" || true
        fi
    fi
    if ! command -v uv &>/dev/null; then
        log_fail "Could not install uv automatically"
        log_info "Install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
fi

# 1c. kazma.yaml presence
if [[ -f "kazma.yaml" ]]; then
    log_ok "kazma.yaml found"
else
    log_fail "kazma.yaml not found in current directory"
    log_info "Run this script from the Kazma project root"
    exit 1
fi

# ── 2. The Sync Handshake ───────────────────────────────────────────────────

log_header "2. Sync Handshake (uv sync)"

if uv sync 2>&1 | tail -3; then
    log_ok "Environment synced from pyproject.toml"
else
    log_fail "uv sync failed"
    log_info "Check pyproject.toml for dependency errors"
    exit 1
fi

# ── 3. Foundation Integrity Check ───────────────────────────────────────────

log_header "3. Foundation Integrity Check"

# Activate the venv for verification
VENV_PYTHON=".venv/bin/python"
if [[ ! -f "$VENV_PYTHON" ]]; then
    log_fail "Virtual environment not found at .venv/"
    log_info "uv sync may have failed silently. Try: uv sync --verbose"
    exit 1
fi

# 3a. sqlite-vec verification
INTEGRITY_RESULT=$($VENV_PYTHON -c "
import sys
try:
    import sqlite_vec
    print('OK:sqlite_vec loaded')
except ImportError as e:
    print(f'FAIL:sqlite_vec not importable — {e}', file=sys.stderr)
    sys.exit(1)
try:
    import aiosqlite
    print('OK:aiosqlite loaded')
except ImportError as e:
    print(f'FAIL:aiosqlite not importable — {e}', file=sys.stderr)
    sys.exit(1)
try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    print('OK:langgraph checkpoint loaded')
except ImportError as e:
    print(f'FAIL:langgraph checkpoint not importable — {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1) || {
    log_fail "Foundation integrity check failed"
    echo ""
    log_info "The error above means a core dependency is broken."
    log_info "Fix: uv sync --reinstall"
    log_info "If sqlite-vec fails, ensure SQLite 3.35+ is available:"
    log_info "  sqlite3 --version"
    log_info "  sudo apt install libsqlite3-dev (if needed)"
    exit 1
}

echo "$INTEGRITY_RESULT" | while IFS= read -r line; do
    if [[ "$line" == OK:* ]]; then
        log_ok "${line#OK:}"
    elif [[ "$line" == FAIL:* ]]; then
        log_fail "${line#FAIL:}"
    fi
done

# 3b. Test suite sanity check (fast — just collection)
TEST_COUNT=$($VENV_PYTHON -m pytest tests/ --co -q 2>/dev/null | tail -1 | grep -oP '\d+' | head -1)
if [[ -n "$TEST_COUNT" && "$TEST_COUNT" -gt 0 ]]; then
    log_ok "$TEST_COUNT tests collected"
else
    log_warn "Could not collect tests (pytest may need dependencies)"
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
