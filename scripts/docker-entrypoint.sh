#!/bin/sh
# Optional: run migrate then start uvicorn
# KAZMA_AUTO_MIGRATE=1 migrates SQLite → Postgres when both present
set -e
if [ "${KAZMA_AUTO_MIGRATE:-0}" = "1" ] && [ -n "${KAZMA_DATABASE_URL:-}" ]; then
  if [ -f /app/kazma-data/settings.db ] || [ -f /app/kazma-data/chat_sessions.db ]; then
    echo "[entrypoint] Running SQLite → Postgres migrate..."
    python /app/scripts/migrate_sqlite_to_postgres.py --data-dir /app/kazma-data || true
  fi
fi
exec "$@"
