"""Every maintenance sweep does the same armed as not (Spaces S4-01 step 5).

The design's proof that the second lock blinds nothing (a lock that hides too
much is the likelier fault): one database is seeded with rows of three spaces
in the stores the sweeps keep on Postgres -- chats, swarm tasks, checkpoints,
memory -- and the rules are put in place. It is copied (``CREATE DATABASE ...
TEMPLATE``) with its data folder, and each copy runs one pass of every sweep
on the maintenance cadence (``worker_bootstrap._MAINTENANCE_SWEEPS``), the
way the server does, in a process of its own (``tests/_sweeps_armed_child.py``),
Spaces enforced: one copy armed, one not. Each sweep ends the same, the same
warnings are said, and every Kazma table holds the same rows (times aside).

Equal is not empty: the sweeps change rows of every space -- checkpoints
pruned to an idle chat's keep, old swarm tasks pruned, the memory mirror
filled -- and that is asserted too. The control: armed, with the swarm task
retention's install declaration taken away, the comparison sees it (the sweep
is refused, and the old tasks stay).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

try:
    import psycopg
except ImportError:  # CI's light job has no psycopg: collected and skipped, so the counts agree
    psycopg = None

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="psycopg is not installed")]

ROOT = Path(__file__).resolve().parents[1]


def _with(dsn: str, name: str) -> str:
    base, _, _ = dsn.rpartition("/")
    return f"{base}/{name}"


def _env(dsn: str, data: Path) -> dict[str, str]:
    """The child's: this copy's database and folder, no alerts, no Docker."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KAZMA_TEST_SECOND_LOCK", "DOCKER"))}
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if "docker" not in p.lower())
    env.update(KAZMA_DATABASE_URL=dsn, KAZMA_DB_BACKEND="postgres", KAZMA_DATA_DIR=str(data),
               KAZMA_OPS_ALERTS="0", KAZMA_TEST_ALLOW_REAL_DB="1", PYTHONIOENCODING="utf-8")
    return env


def _child(*args: str, dsn: str, data: Path) -> None:
    run = subprocess.run([sys.executable, "-m", "tests._sweeps_armed_child", *args], cwd=ROOT,
                         env=_env(dsn, data), capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=900, check=False)
    assert run.returncode == 0, f"{args[0]}: {run.stdout[-2000:]}\n{run.stderr[-4000:]}"


@pytest.fixture(scope="module")
def passes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """The seed's tables, and what one pass did on each copy."""
    admin = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not admin or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    with psycopg.connect(admin, autocommit=True) as conn:
        if not conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
            pytest.skip("copying a database and making the space role need a superuser DSN")
    from tests._sweeps_armed_child import tables

    root = tmp_path_factory.mktemp("sweeps")
    tag = uuid.uuid4().hex[:8]
    names = {kind: f"kazma_sweeps_{kind}_{tag}" for kind in ("seed", "unarmed", "armed", "blinded")}
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{names["seed"]}"')
        seed_dsn, seed_data = _with(admin, names["seed"]), root / "seed"
        seed_data.mkdir()
        _child("seed", seed_dsn, str(seed_data), dsn=seed_dsn, data=seed_data)
        out: dict[str, dict[str, Any]] = {"seed": {"tables": tables(seed_dsn)}}
        with psycopg.connect(admin, autocommit=True) as conn:
            for kind in ("unarmed", "armed", "blinded"):
                conn.execute(f'CREATE DATABASE "{names[kind]}" TEMPLATE "{names["seed"]}"')
        for kind in ("unarmed", "armed", "blinded"):
            dsn, data = _with(admin, names[kind]), root / kind
            shutil.copytree(seed_data, data)
            result = root / f"{kind}.json"
            _child("sweep", dsn, str(data), kind, str(result), dsn=dsn, data=data)
            out[kind] = json.loads(result.read_text(encoding="utf-8"))
        return out
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            for name in names.values():
                conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_every_sweep_ends_the_same_armed(passes: dict[str, dict[str, Any]]) -> None:
    unarmed, armed = passes["unarmed"]["outcomes"], passes["armed"]["outcomes"]
    assert len(unarmed) >= 40, "every sweep of the cadence ran"
    assert armed == unarmed
    assert [label for label, o in armed.items() if o["failures"] or o["waiting"]] == []


def test_the_same_warnings_are_said_armed(passes: dict[str, dict[str, Any]]) -> None:
    assert passes["armed"]["warnings"] == passes["unarmed"]["warnings"]


def test_every_table_holds_the_same_rows_armed(passes: dict[str, dict[str, Any]]) -> None:
    assert passes["armed"]["tables"] == passes["unarmed"]["tables"]


def test_the_sweeps_changed_rows_of_every_space(passes: dict[str, dict[str, Any]]) -> None:
    """Equal is not empty: three spaces' old checkpoints and swarm tasks went,
    and their memories reached the mirror."""
    seed, after = passes["seed"]["tables"], passes["unarmed"]["tables"]
    assert (seed["checkpoints"]["rows"], after["checkpoints"]["rows"]) == (54, 30), "18 steps each, an idle chat keeps 10"
    assert seed["kazma_swarm_tasks"]["rows"] - after["kazma_swarm_tasks"]["rows"] == 3, "one past its keep in each"
    assert after["kazma_episodes"]["rows"] - seed["kazma_episodes"]["rows"] == 9, "three memories of each space"


def test_the_control_a_sweep_with_no_door_is_seen(passes: dict[str, dict[str, Any]]) -> None:
    """Armed, the swarm task retention with its install declaration taken
    away is refused, and the old tasks stay: the comparison sees a sweep the
    lock stops."""
    blinded, unarmed = passes["blinded"], passes["unarmed"]
    assert blinded["outcomes"]["swarm task retention"]["error"] == "NoSpaceBound"
    assert blinded["outcomes"] != unarmed["outcomes"]
    assert blinded["tables"]["kazma_swarm_tasks"] != unarmed["tables"]["kazma_swarm_tasks"]
