"""ROOT conftest — applies to EVERY testpath (tests/ + all package suites).

Production-database shield (2026-08-14 incident): a dev-repo .env pointing
at the PRODUCTION Postgres + an app-building test let the suite write test
providers/settings into the live kazma_settings table (566 → 127 rows,
sk-test keys). This root-level conftest guarantees no test process — in
any suite — can reach a real database, regardless of .env files, shell
profiles, or loader tricks.

Loaded before any suite conftest (pytest walks from the root down).
"""

from __future__ import annotations

import os

os.environ.pop("KAZMA_SECRET", None)

# Force the sqlite backend and strip every DSN variant BEFORE any kazma
# module can read them.
os.environ["KAZMA_DB_BACKEND"] = "sqlite"
for _dsn_key in (
    "KAZMA_DATABASE_URL",
    "DATABASE_URL",
    "KAZMA_DOCUMENTS_METADATA_BACKEND",
    "E2B_API_KEY",
    "KAZMA_E2B_API_KEY",
    "KAZMA_TEMPORAL_HOST",
    "TEMPORAL_ADDRESS",
):
    os.environ.pop(_dsn_key, None)

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: None
dotenv.dotenv_values = lambda *args, **kwargs: {}

# Re-introduction guard: if anything loads the DSN back into the environ
# during the session (real load_dotenv reference, manual .env parser,
# C-level putenv through a reloaded module), remove it again immediately.
_real_setitem = os.environ.__class__.__setitem__


def _shielded_setitem(self, key, value):
    _real_setitem(self, key, value)
    if key in ("KAZMA_DATABASE_URL", "DATABASE_URL"):
        import warnings

        warnings.warn(
            f"tests must not use a real database — {key} was re-introduced "
            "into the environment and has been removed again",
            stacklevel=2,
        )
        _real_setitem(self, "KAZMA_DB_BACKEND", "sqlite")
        del self[key]


os.environ.__class__.__setitem__ = _shielded_setitem


# ── Global shutdown-event reset ─────────────────────────────────────────────
# Tests that call create_app() + uvicorn (e2e, pipeline_sandbox, …) fire the
# app's lifespan shutdown → signal_shutdown(), setting the GLOBAL event for
# the rest of the process. Every later SSE/WS/stream test then sees
# is_shutting_down()==True and immediately breaks. Reset before each test.
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _reset_shutdown_event():
    try:
        from kazma_core.shutdown import reset_shutdown

        reset_shutdown()
    except Exception:
        pass
    yield
