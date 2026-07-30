#!/usr/bin/env bash
# Refresh METRICS.md and open a PR to deploy it to kazma.ai.
#
# One-command manual fallback for the Sync Metrics GitHub Actions workflow.
# Use this when you want to update the metrics locally (e.g. before the secret
# is set up, or on demand) instead of waiting for the auto-trigger.
#
# What it does:
#   1. Regenerates METRICS.md in THIS repo (scripts/generate_metrics.py --write).
#   2. Commits it here (on a branch) — keeps the framework source of truth honest.
#   3. Copies the refreshed file into the website repo (Mubder/KazmaAI).
#   4. Commits + pushes a branch and opens a PR there.
#      Merging that PR triggers Cloudflare Pages' production deploy.
#
# Usage:
#   scripts/refresh-and-deploy-metrics.sh                # full flow
#   scripts/refresh-and-deploy-metrics.sh --site-only    # skip framework commit
#   scripts/refresh-and-deploy-metrics.sh --dry-run      # show what would happen
#
# Requires: the gh CLI authenticated, and the website repo cloned locally.
set -euo pipefail

# --- config (override via env if your layout differs) ---
FRAMEWORK_DIR="${FRAMEWORK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SITE_DIR="${SITE_DIR:-$FRAMEWORK_DIR/../KazmaAI}"
SITE_REPO="${SITE_REPO:-Mubder/KazmaAI}"
SITE_TARGET="${SITE_TARGET:-src/data/METRICS.md}"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
BRANCH="${BRANCH:-chore/refresh-metrics}"

# --- flags ---
SITE_ONLY=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --site-only) SITE_ONLY=1 ;;
    --dry-run)   DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

run() { echo "❯ $*"; [ "$DRY_RUN" = "1" ] || "$@"; }

# --- sanity checks ---
command -v gh >/dev/null 2>&1 || { echo "✗ gh CLI not found. Install: https://cli.github.com" >&2; exit 1; }
[ -f "$FRAMEWORK_DIR/scripts/generate_metrics.py" ] || { echo "✗ Not run from the framework repo (no scripts/generate_metrics.py)." >&2; exit 1; }

echo "▸ Framework repo : $FRAMEWORK_DIR"
echo "▸ Website repo   : $SITE_DIR  (git remote: $SITE_REPO)"
echo "▸ Website target : $SITE_TARGET"
[ "$DRY_RUN" = "1" ] && echo "▸ DRY RUN — no changes will be made."
echo

cd "$FRAMEWORK_DIR"

# --- 1. regenerate ---
echo "STEP 1/4 — regenerate METRICS.md"
run "$PYBIN" scripts/generate_metrics.py --write
if git diff --quiet METRICS.md; then
  echo "  METRICS.md already current — nothing to do."
  [ "$SITE_ONLY" = "0" ] && { echo "  (use --site-only to skip this check anyway)"; exit 0; }
fi

# --- 2. commit to framework (unless --site-only) ---
if [ "$SITE_ONLY" = "0" ]; then
  echo; echo "STEP 2/4 — commit in framework repo"
  run git checkout -B "$BRANCH"
  run git add METRICS.md
  run git commit -m "chore(metrics): regenerate METRICS.md"
  run git push -u origin "$BRANCH"
  echo "  Framework PR: open one with:"
  echo "    gh pr create --title 'Regenerate METRICS.md' --body 'Auto-refresh via refresh-and-deploy-metrics.sh'"
fi

# --- 3. copy into site repo ---
echo; echo "STEP 3/4 — copy into website repo"
[ -d "$SITE_DIR/.git" ] || { echo "✗ Website repo not found at $SITE_DIR (set SITE_DIR=<path>)." >&2; exit 1; }
run cp "$FRAMEWORK_DIR/METRICS.md" "$SITE_DIR/$SITE_TARGET"
echo "  Copied → $SITE_DIR/$SITE_TARGET"

# --- 4. PR in site repo ---
echo; echo "STEP 4/4 — open PR in website repo"
cd "$SITE_DIR"
run git checkout -B "$BRANCH"
run git add "$SITE_TARGET"
run git commit -m "chore(metrics): refresh METRICS.md from framework"
run git push -u origin "$BRANCH"
if [ "$DRY_RUN" = "0" ]; then
  PR_URL=$(gh pr create --base main --head "$BRANCH" \
    --title "Refresh METRICS.md from framework" \
    --body "Auto-refreshed via \`scripts/refresh-and-deploy-metrics.sh\` in Mubder/kazma. Merging triggers the Cloudflare Pages deploy to kazma.ai." 2>&1 | tail -1)
  echo; echo "✓ Site PR opened: $PR_URL"
  echo "  Merge it to deploy → kazma.ai (CF Pages, ~2-3 min)."
else
  echo "  (dry-run: would run gh pr create)"
fi
echo
echo "Done."
