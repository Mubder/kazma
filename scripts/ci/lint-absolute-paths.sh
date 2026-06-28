#!/usr/bin/env bash
# Fail if any non-test, non-config .py file contains hardcoded absolute user paths.
set -euo pipefail

PATTERN='/(home|Users|root|mnt/c/Users)/'
EXCLUDE_DIRS="tests/,.venv/,__pycache__/,kazma-ui/,kazma-skills/"

echo "=== Linting for hardcoded absolute paths ==="
echo ""

# build exclude args
EXCLUDE_ARGS=""
for d in $(echo "$EXCLUDE_DIRS" | tr ',' ' '); do
    EXCLUDE_ARGS="$EXCLUDE_ARGS --exclude-dir=$d"
done

VIOLATIONS=$(grep -rEn "$PATTERN" kazma-core/ kazma-gateway/ $EXCLUDE_ARGS 2>/dev/null || true)

if [ -n "$VIOLATIONS" ]; then
    echo "❌ Hardcoded absolute user paths found:"
    echo "$VIOLATIONS"
    echo ""
    echo "Fix: use Path.home() or os.path.expanduser('~') instead."
    echo "See docs/portability.md for the full policy."
    exit 1
fi

echo "✅ No hardcoded absolute paths detected."
echo ""
echo "Portability invariants:"
echo "  - All user state: ~/.kazma/"
echo "  - All data paths: relative to repo root"
echo "  - No /home/, /Users/, /root/, or /mnt/c/Users/ in shipped code"
echo ""
echo "See docs/portability.md for the full policy."
