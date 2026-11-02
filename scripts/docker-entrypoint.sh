#!/bin/sh
# Optional: run migrate then start uvicorn
# KAZMA_AUTO_MIGRATE=1 migrates SQLite → Postgres when both present
set -e
if [ "${KAZMA_AUTO_MIGRATE:-0}" = "1" ] && [ -n "${KAZMA_DATABASE_URL:-}" ]; then
  if [ -f /app/kazma-data/settings.db ] || [ -f /app/kazma-data/chat_sessions.db ]; then
    echo "[entrypoint] Running SQLite → Postgres migrate..."
    python /app/scripts/migrate_sqlite_to_postgres.py --data-dir /app/kazma-data
  fi
fi

# Keep ClamAV's signatures current. The image fetches them when it is built,
# and nothing refreshed them after: a container scanned with the signatures
# of its build day, and readiness reports them stale past 7 days (2026-11,
# FA-232). freshclam's own daemon checks 12 times a day and runs beside Kazma
# as the same user (the image hands it the database and log folders). A
# freshclam that cannot start is said and Kazma starts anyway: the scanner
# still scans, and readiness names the age of what it scans with.
if command -v freshclam >/dev/null 2>&1; then
  if ! freshclam --daemon --checks=12 --quiet; then
    echo "[entrypoint] freshclam did not start; ClamAV signatures will not refresh in this container" >&2
  fi
fi
exec "$@"
