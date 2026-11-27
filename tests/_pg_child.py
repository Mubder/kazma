"""A child process of a Postgres test, switched to one database (S4-01 step 5 review).

Several Postgres tests run each database's work in a child process, since the
shared pool and the settings store are the process's. A child that switched
its database by the environment alone kept the pool and settings store it had
already opened on the one before: on the ``docker exec`` route, finding
``pg_restore`` reads the stored container-engine choice once
(``docker_cli._apply_stored_choice_once``), which opens both on the process's
database, so the restore-order drill then read the base database instead of
the restored one -- "Document is unavailable" whenever pg_dump ran through
docker (``tests/test_document_pg_restore_order.py``; with a local pg_dump
nothing opened them first, so it passed). A child switches through
:func:`use_database`, which lets go of both first
(``tests/test_pg_child_switches_database.py`` refuses a switch by hand).
"""

from __future__ import annotations

import os


def use_database(dsn: str, metadata: str = "postgres") -> None:
    """This process's Kazma database is *dsn* from here on: the shared pool,
    the settings store and the database's remembered state let go, the
    environment pointed at *dsn*, documents switched on in its settings."""
    from kazma_core.config_store import get_config_store, reset_config_store
    from kazma_core.db import availability
    from kazma_core.db.postgres_pool import reset_postgres_pool

    reset_config_store()
    reset_postgres_pool()
    availability.reset()
    os.environ["KAZMA_DB_BACKEND"] = "postgres"
    os.environ["KAZMA_DATABASE_URL"] = dsn
    os.environ["KAZMA_DOCUMENTS_METADATA_BACKEND"] = metadata
    os.environ["KAZMA_DOCUMENTS_JOBS_BACKEND"] = "postgres"
    get_config_store().set("documents.enabled", True, category="documents")
