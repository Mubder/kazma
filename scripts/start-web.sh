#!/usr/bin/env bash
# Start Kazma Web UI for WSL → Windows access (0.0.0.0 bind).
#
# Usage (from WSL, repo root or any path):
#   ./scripts/start-web.sh          # port 9090
#   ./scripts/start-web.sh 9091     # custom port
#
# Recommended once: copy env into .env (repo root)
#   KAZMA_HOST=0.0.0.0
#   KAZMA_SECRET=your-strong-secret
#   KAZMA_TRUST_LAN=1
#
# Windows localhost:9090 also needs the pin script after reboot:
#   .\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090
# See docs/docs/ops/wsl-fixed-access.md

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${1:-9090}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

# Defaults tuned for WSL → Windows browser via portproxy / WSL eth IP
export KAZMA_HOST="${KAZMA_HOST:-0.0.0.0}"
export KAZMA_TRUST_LAN="${KAZMA_TRUST_LAN:-1}"

if [[ -x "$ROOT/.venv/bin/kazma" ]]; then
  KAZMA_BIN="$ROOT/.venv/bin/kazma"
elif command -v kazma >/dev/null 2>&1; then
  KAZMA_BIN="$(command -v kazma)"
else
  echo "error: kazma not found. Create a venv and install: pip install -e '.[rag,dev]'" >&2
  exit 1
fi

if [[ -z "${KAZMA_SECRET:-}" && "$KAZMA_HOST" != "127.0.0.1" && "$KAZMA_HOST" != "localhost" && "$KAZMA_HOST" != "::1" ]]; then
  echo "warning: KAZMA_HOST=$KAZMA_HOST with empty KAZMA_SECRET — CLI will require a secret or refuse non-loopback." >&2
  echo "         Set KAZMA_SECRET in .env (recommended) or export it before starting." >&2
fi

echo "Starting Kazma Web UI"
echo "  host=$KAZMA_HOST  port=$PORT  trust_lan=${KAZMA_TRUST_LAN}"
echo "  bin=$KAZMA_BIN"
echo "  After reboot (Windows Admin once per boot):"
echo "    .\\scripts\\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port $PORT"
echo "  Then open: http://127.0.0.1:${PORT}/"
echo ""

exec "$KAZMA_BIN" serve "$PORT"
