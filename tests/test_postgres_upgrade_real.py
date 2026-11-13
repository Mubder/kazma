"""The upgrade's copy on real servers: Postgres 16 into Postgres 18
(2026-11-13, ``postgres_upgrade.copy_database``).

Two throwaway containers (``postgres:16.16-alpine``, ``postgres:18.7-alpine``,
pinned as the compose files pin them) on loopback ports; the source holds
Kazma-shaped data -- a settings table with the boot stamp, a table with a
sequence, a trigger and its function. The copy runs the real ``pg_dump`` and
``pg_restore`` of ``KAZMA_TEST_PG_UPGRADE_BIN`` (a Postgres ``bin`` folder,
16 or newer), compares every table and sequence, and replaces an earlier
copy only through its ``empty_target``. The negative control restores one
table only: the comparison refuses it.

Opt-in: ``KAZMA_TEST_PG_UPGRADE_BIN`` and a Docker engine; skipped without.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from kazma_core import postgres_upgrade as pgu

pytestmark = [pytest.mark.postgres, pytest.mark.real_docker, pytest.mark.timeout(600)]

BIN = (os.environ.get("KAZMA_TEST_PG_UPGRADE_BIN") or "").strip()
OLD_IMAGE = "postgres:16.16-alpine@sha256:0adbf25a9e96d38823bb813f3ac0ed41fadb665c88f1df7fe6dbfb060cbdc106"
NEW_IMAGE = "postgres:18.7-alpine@sha256:75dcac3dfb1cd01ca3536910bbc84128759362f8b118bf8c55b38a94f68f3b73"
SEED = """
CREATE TABLE kazma_settings (key text PRIMARY KEY, value text);
INSERT INTO kazma_settings VALUES ('system.lifecycle.last_boot_epoch', '1790000000.5'),
                                  ('agent.language', '"ar"');
CREATE TABLE kazma_chat_sessions (id serial PRIMARY KEY, title text, created timestamptz DEFAULT now());
INSERT INTO kazma_chat_sessions (title) SELECT 'chat ' || g FROM generate_series(1, 250) g;
INSERT INTO kazma_chat_sessions (title) VALUES ('محادثة عربية');
CREATE TABLE document_audit_events (id bigserial PRIMARY KEY, note text);
CREATE FUNCTION kazma_document_audit_guard() RETURNS trigger LANGUAGE plpgsql AS
  $$ BEGIN RAISE EXCEPTION 'append only'; END $$;
CREATE TRIGGER document_audit_guard BEFORE UPDATE ON document_audit_events
  FOR EACH ROW EXECUTE FUNCTION kazma_document_audit_guard();
INSERT INTO document_audit_events (note) SELECT 'event ' || g FROM generate_series(1, 40) g;
"""


def _docker() -> str | None:
    from kazma_core.docker_cli import find_docker_cli

    return find_docker_cli()


@pytest.fixture(scope="module")
def servers():
    if not BIN or not Path(BIN).is_dir():
        pytest.skip("set KAZMA_TEST_PG_UPGRADE_BIN to a Postgres bin folder (16 or newer)")
    docker = _docker()
    if not docker:
        pytest.skip("no docker CLI")
    password = secrets.token_urlsafe(16)
    names, urls = [], {}
    try:
        for major, image in ((16, OLD_IMAGE), (18, NEW_IMAGE)):
            port = pgu._free_port(56000 + major * 10)
            name = f"kazma-pgupgrade-test-{major}-{uuid.uuid4().hex[:6]}"
            subprocess.run([docker, "run", "-d", "--name", name, "-e", "POSTGRES_USER=kazma_app",
                            "-e", f"POSTGRES_PASSWORD={password}", "-e", "POSTGRES_DB=kazma",
                            "-p", f"127.0.0.1:{port}:5432", image], check=True, capture_output=True, timeout=300)
            names.append(name)
            urls[major] = f"postgresql://kazma_app:{password}@127.0.0.1:{port}/kazma"
        import psycopg

        for url in urls.values():
            deadline = time.monotonic() + 120
            while True:
                try:
                    with psycopg.connect(url, connect_timeout=3):
                        break
                except psycopg.Error:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
        with psycopg.connect(urls[16], autocommit=True) as conn:
            conn.execute(SEED)
        yield urls
    finally:
        for name in names:
            subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=120)


def _empty(url: str):
    """Drop and make Kazma's database again on the destination, as the
    upgrade's ``empty_database_steps`` do (here as the container's superuser)."""
    import psycopg

    def empty() -> bool:
        with psycopg.connect(url.rsplit("/", 1)[0] + "/postgres", autocommit=True) as conn:
            conn.execute("DROP DATABASE kazma")
            conn.execute("CREATE DATABASE kazma")
        return True
    return empty


def test_a_real_16_database_is_copied_whole_into_18(servers):
    said: list[str] = []
    assert pgu.server_major(servers[16]) == 16 and pgu.server_major(servers[18]) == 18
    assert pgu.copy_database(servers[16], servers[18], bin_dir=BIN, say=said.append), "\n".join(said)
    assert any("251 rows" in line or "rows) and" in line for line in said), said
    import psycopg

    with psycopg.connect(servers[18]) as conn:
        assert conn.execute("SELECT title FROM kazma_chat_sessions WHERE id = 251").fetchone()[0] == "محادثة عربية"
        assert conn.execute("SELECT nextval('kazma_chat_sessions_id_seq')").fetchone()[0] == 252
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE document_audit_events SET note = 'x' WHERE id = 1")


def test_a_second_copy_replaces_the_first_only_through_empty_target(servers):
    said: list[str] = []
    assert not pgu.copy_database(servers[16], servers[18], bin_dir=BIN, say=said.append)
    assert any("nothing was given to empty it" in line for line in said)
    said.clear()
    assert pgu.copy_database(servers[16], servers[18], bin_dir=BIN, say=said.append,
                             empty_target=_empty(servers[18])), "\n".join(said)


def test_negative_control_a_partial_restore_is_refused(servers):
    """pg_restore of one table only: the comparison says the copy is not whole."""
    tool = Path(BIN) / ("pg_restore.exe" if os.name == "nt" else "pg_restore")
    dump = Path(BIN) / ("pg_dump.exe" if os.name == "nt" else "pg_dump")

    def tools(name: str) -> list[str]:
        return [str(tool), "--table", "kazma_settings"] if name == "pg_restore" else [str(dump)]

    said: list[str] = []
    ok = pgu.copy_database(servers[16], servers[18], bin_dir=BIN, say=said.append, tools=tools,
                           empty_target=_empty(servers[18]))
    assert not ok and any("DIFFERS" in line for line in said), said
