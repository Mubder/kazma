"""A containerised ``pg_restore`` must be fed the archive, not a host path.

``resolve_pg_restore()`` returns ``docker exec -i <container> pg_restore`` on
a Docker-backed deployment. That binary runs INSIDE the container, where
``C:\\Users\\balfa\\kazma\\kazma-data\\backups\\pg\\pg_shared_*.dump`` does not
exist and never will, so the archive has to arrive on stdin.

The TOC check learned that on its first live run. The deep data check was
added a week later with ``[*prefix, "--file=-", str(dump)]`` and never got the
same treatment, so it could not verify a data section on a containerised
Postgres at all. Nothing caught it because the deep tier fires every 168
hours: it failed on its FIRST scheduled run, exactly seven days after it
landed (2026-09-21), with

    pg_restore: error: could not open input file "C:Users...pg_shared_*.dump"

and alerted as "a backup cannot be restored -- the drill failed". The dump was
1.9 GB and perfectly healthy. Only the drill was broken.

That is the worst way for a backup check to fail. A drill that cries wolf
teaches an operator to discount the one alert they cannot afford to discount,
and it does so a week at a time, which is long enough to forget.

Both tiers now go through ``_run_pg_restore``. These tests pin the behaviour
of that chokepoint from the outside — through the two checks that call it —
so deleting it and inlining ``subprocess.run`` again reddens them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kazma_core.backup import restore_drill as rd

DOCKER_PREFIX = ["C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.EXE",
                 "exec", "-i", "kazma-db-win", "pg_restore"]
PLAIN_PREFIX = ["pg_restore"]


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    """A file with the PGDMP magic — the shallow check refuses anything else."""
    p = tmp_path / "pg_shared_1789967500.dump"
    p.write_bytes(b"PGDMP" + b"\x00" * 64)
    return p


@pytest.fixture
def spy(monkeypatch):
    """Capture what would have been executed, and run nothing."""
    calls: list[dict] = []

    def fake_run(argv, **kw):
        calls.append({"argv": list(argv), "stdin": kw.get("stdin")})
        return subprocess.CompletedProcess(
            argv, 0, stdout="; one entry\ntable public.x\n", stderr=""
        )

    monkeypatch.setattr(rd.subprocess, "run", fake_run)
    return calls


def _use_prefix(monkeypatch, prefix: list[str]) -> None:
    import kazma_core.migration.pg_bridge as bridge

    monkeypatch.setattr(bridge, "resolve_pg_restore", lambda: list(prefix))


@pytest.mark.parametrize(
    "check",
    [rd._check_pg_dump, rd._check_pg_data_section],
    ids=["toc-check", "deep-data-check"],
)
def test_docker_prefix_never_receives_a_host_path(check, dump, spy, monkeypatch):
    """The failure that fired in production, for BOTH tiers."""
    _use_prefix(monkeypatch, DOCKER_PREFIX)

    check(dump, rd.DrillResult(backup_dir=str(dump.parent)))

    assert spy, f"{check.__name__} never invoked pg_restore"
    call = spy[-1]
    assert str(dump) not in call["argv"], (
        f"{check.__name__} passed the HOST path {str(dump)!r} to a pg_restore "
        "running inside a container. It cannot open that file, and the drill "
        "reports a healthy backup as unrestorable."
    )
    assert dump.name not in " ".join(call["argv"]), (
        "the archive filename still reaches the container's argv"
    )
    assert call["stdin"] is not None, (
        "no host path AND no stdin — the container would be handed nothing"
    )


@pytest.mark.parametrize(
    "check",
    [rd._check_pg_dump, rd._check_pg_data_section],
    ids=["toc-check", "deep-data-check"],
)
def test_local_prefix_still_gets_the_path(check, dump, spy, monkeypatch):
    """The counterweight: piping unconditionally would be its own bug.

    A native pg_restore on the host reads the file directly. Forcing every
    deployment through stdin would work but would stream gigabytes through a
    pipe for no reason, and would hide a genuinely unreadable file behind a
    pipe error.
    """
    _use_prefix(monkeypatch, PLAIN_PREFIX)

    check(dump, rd.DrillResult(backup_dir=str(dump.parent)))

    assert spy, f"{check.__name__} never invoked pg_restore"
    call = spy[-1]
    assert str(dump) in call["argv"], (
        f"{check.__name__} withheld the path from a LOCAL pg_restore, which "
        "can read it directly"
    )
    assert call["stdin"] is None


def test_the_deep_check_reports_success_over_stdin(dump, spy, monkeypatch):
    """End to end: a healthy archive must now PASS on a Docker deployment.

    The regression was not merely the wrong argv — it was a green backup
    reported red. Assert the verdict, not just the command line.
    """
    _use_prefix(monkeypatch, DOCKER_PREFIX)
    res = rd.DrillResult(backup_dir=str(dump.parent))

    rd._check_pg_data_section(dump, res)

    data = [c for c in res.checks if c.get("check") == "postgres:data"]
    assert data, f"no postgres:data check recorded: {res.checks}"
    assert data[-1].get("ok") is True, (
        f"a healthy 1.9GB-shaped archive was reported unrestorable: {data[-1]}"
    )
