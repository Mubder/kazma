"""What Kazma claims to recover from, and what proves each claim.

The audit's thesis was that existence is not execution: three shipped
mechanisms had never run, and a fourth reported success while delivering
nothing. Phase 4 turned that from a thesis into a count -- the repetition
breaker had been incapable of firing since the day it was written, and a
green unit test on its detector said nothing, because the detector was
never the broken part.

This manifest exists so that cannot recur quietly. Every entry names a
fault, the code that recovers from it, and the test file that drives the
LIVE path rather than a helper in isolation. ``tests/test_resilience_
manifest.py`` fails the build when an entry's code or its proof goes
missing, so deleting or renaming the test that keeps a mechanism honest is
a red build rather than a silent loss of coverage.

``proven_in_production`` is deliberately conservative: it means the
mechanism has been observed firing on a real install, not that it is
believed to work. Most entries are False. That is the honest state, and
the number is only useful while it stays honest.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mechanism:
    """One fault, one recovery, one proof."""

    name: str
    fault: str
    module: str
    symbol: str
    proof: str
    proven_in_production: bool
    note: str = ""


MECHANISMS: tuple[Mechanism, ...] = (
    Mechanism(
        name="turn-keyed reply persistence",
        fault="a reply is written to the wrong row, or to none, and vanishes on reload",
        module="kazma_ui.reply_sink",
        symbol="upsert_reply",
        proof="tests/test_reply_persistence_contract.py",
        proven_in_production=True,
        note="Replaced five positional writers that reconciled by message index.",
    ),
    Mechanism(
        name="process supervision (health-gated restart)",
        fault="Kazma exits, or stays alive but wedged, and nobody notices",
        module="scripts/service/kazma_guard.py",
        symbol="Guard",
        proof="tests/test_guard_integration.py",
        proven_in_production=True,
        note="OS supervisors restart on exit; none restart 'alive but not serving'.",
    ),
    Mechanism(
        name="foreign-server detection",
        fault="an orphan holds the port; the guard adopts it and reports healthy",
        module="scripts/service/kazma_guard.py",
        symbol="server_started_at",
        proof="tests/test_service_supervision.py",
        proven_in_production=True,
        note="Fired live 2026-08-28: caught a grandchild serving a stale build.",
    ),
    Mechanism(
        name="pre-spawn port clearance",
        fault="a surviving grandchild holds the port after its parent is killed",
        module="scripts/service/kazma_guard.py",
        symbol="clear_stale_port",
        proof="tests/test_service_supervision.py",
        proven_in_production=False,
        note="reap_orphan only knows the PID it recorded; a dead parent hid the child.",
    ),
    Mechanism(
        name="MCP reconnect",
        fault="an MCP server drops and its tools disappear until a restart",
        module="kazma_core.mcp.reconnect",
        symbol="MCPReconnector",
        proof="tests/test_mcp_reconnect.py",
        proven_in_production=True,
        note="60 connection failures in 8 days, 0 reconnects, before this existed.",
    ),
    Mechanism(
        name="operator alerting (deduplicated)",
        fault="a silent failure that only a human scrolling logs would find",
        module="kazma_core.observability.ops_alerts",
        symbol="alert",
        proof="tests/test_ops_alerts.py",
        proven_in_production=True,
        note="First version reported success while delivering nothing.",
    ),
    Mechanism(
        name="daily digest",
        fault="slow degradation that never crosses an alert threshold",
        module="kazma_core.observability.daily_digest",
        symbol="build_digest",
        proof="tests/test_daily_digest.py",
        proven_in_production=True,
    ),
    Mechanism(
        name="repetition loop breaker",
        fault="the model locks onto a paging pattern and burns the whole budget",
        module="kazma_core.agent.graph_supervisor",
        symbol="supervisor_node",
        proof="tests/test_loop_breaker_live_path.py",
        proven_in_production=False,
        note=(
            "Could not fire at all until 2026-08-28: PendingToolCall is a "
            "TypedDict and three sites used attribute access. Its unit test "
            "was green throughout, which is why the proof must drive "
            "supervisor_node and not detect_tool_loop."
        ),
    ),
    Mechanism(
        name="detached-pump watchdog",
        fault="the client disconnects, astream_events wedges, the thread is held",
        module="kazma_ui.active_turns",
        symbol="pump_is_stalled",
        proof="tests/test_active_turns.py",
        proven_in_production=False,
        note="0 engagements in 367 disconnects; sound, and now assertable.",
    ),
    Mechanism(
        name="chaos injection points",
        fault="resilience claims that cannot be tested because faults cannot be caused",
        module="kazma_core.chaos",
        symbol="chaos_injection",
        proof="tests/test_chaos_injection.py",
        proven_in_production=False,
        note="544 lines with zero call sites until Phase 4.",
    ),
    Mechanism(
        name="restic snapshots and restore",
        fault="backups that cannot be restored, are unencrypted, and never dedupe",
        module="kazma_core.backup.restic_repo",
        symbol="restore",
        proof="tests/test_restic_repo.py",
        proven_in_production=False,
        note=(
            "The restore half that never existed. Tests run against a real "
            "restic binary: byte-identical round trip, secrets unreadable in "
            "the repository, dedup across snapshots, and --read-data catching "
            "a deliberately corrupted pack."
        ),
    ),
    Mechanism(
        name="graph memory backup",
        fault="Neo4j lives in a Docker volume the backup sweep never walked",
        module="kazma_core.backup.neo4j_backup",
        symbol="export_graph",
        proof="tests/test_neo4j_backup.py",
        proven_in_production=True,
        note=(
            "323 nodes were in no backup at all until 2026-08-29. Community "
            "edition has no online neo4j-admin backup and no APOC here, so "
            "this is a driver-level JSONL export. Round-trip verified against "
            "a scratch database: identical content hashes."
        ),
    ),
    Mechanism(
        name="install restore",
        fault="a backup nobody can turn back into a working install",
        module="kazma_core.backup.restore",
        symbol="restore_files",
        proof="tests/test_restore.py",
        proven_in_production=True,
        note=(
            "Selects by GENERATION, never restic's 'latest' -- which on this "
            "install resolves to a bulk-ingested legacy generation with the "
            "newest snapshot time and the oldest content. Rehearsed against "
            "the live repository: 9/9 steps, 25 databases integrity-checked."
        ),
    ),
    Mechanism(
        name="restore drill",
        fault="a backup that cannot actually be restored",
        module="kazma_core.backup.restore_drill",
        symbol="verify_backup",
        proof="tests/test_restore_drill.py",
        proven_in_production=True,
        note=(
            "Non-destructive: integrity-checks every SQLite file in scratch "
            "and parses the Postgres TOC via pg_restore --list. Kazma has no "
            "restore path for universal backups, so verification is the half "
            "that can be rehearsed safely."
        ),
    ),
    Mechanism(
        name="connector credential health",
        fault="a connector's sign-in expires and nothing says so",
        module="kazma_core.observability.connector_health",
        symbol="check_google",
        proof="tests/test_connector_health.py",
        proven_in_production=False,
        note=(
            "The Google grant died on 2026-08-27 and was found by looking, "
            "not by being told. OAuth status stays on Testing by choice, so "
            "Google expires it every 7 days -- this warns a day ahead rather "
            "than reporting a break already tripped over."
        ),
    ),
    Mechanism(
        name="backup-gap alerting",
        fault="backups keep succeeding while silently protecting nothing",
        module="kazma_core.backup.universal",
        symbol="_alert_on_backup_gaps",
        proof="tests/test_backup_alerting.py",
        proven_in_production=False,
        note=(
            "Found live 2026-08-28: 29 of 29 backups had a failing offsite "
            "copy for over a day, recorded only in a JSON file nobody reads."
        ),
    ),
    Mechanism(
        name="read-only offsite detection",
        fault=(
            "the offsite repository accepts reads and refuses every write, "
            "so nothing is stored and nothing complains"
        ),
        module="kazma_core.backup.restic_repo",
        symbol="remote_writable",
        proof="tests/test_backup_silent_failures.py",
        proven_in_production=True,
        note=(
            "Found live 2026-08-29: a Google service account listed the "
            "shared folder in milliseconds and failed every upload with 403 "
            "storageQuotaExceeded. restic locks before it will even LIST, so "
            "each command retried for 15 minutes and read as a hang."
        ),
    ),
    Mechanism(
        name="missing-passphrase alerting",
        fault=(
            "the restic passphrase is gone, every snapshot is skipped, and "
            "the backup still reports 'complete' because the local dump ran"
        ),
        module="kazma_core.backup.restic_repo",
        symbol="alert_missing_password",
        proof="tests/test_backup_silent_failures.py",
        proven_in_production=True,
        note=(
            "Found live 2026-08-29: four hours of skipped snapshots behind a "
            "single INFO line. Silent on a fresh install, where no repository "
            "means nothing is yet at stake."
        ),
    ),
    Mechanism(
        name="firing ledger",
        fault=(
            "a recovery stops running and the only evidence is a log line "
            "that stops appearing, which nobody is counting"
        ),
        module="kazma_core.observability.firing_ledger",
        symbol="run_weekly_sweep",
        proof="tests/test_backup_silent_failures.py",
        proven_in_production=True,
        note=(
            "Its own first version read one log file and reported zero guard "
            "restarts on an evening they fired -- a false silence in the "
            "report whose job is to find silence."
        ),
    ),
    Mechanism(
        name="scheduled-post recurrence refusal",
        fault="a recurring time is accepted and the post fires exactly once",
        module="kazma_core.x_api.booking",
        symbol="_parse_when",
        proof="tests/test_x_scheduled.py",
        proven_in_production=False,
    ),
)


def by_name(name: str) -> Mechanism | None:
    for m in MECHANISMS:
        if m.name == name:
            return m
    return None


def unproven() -> tuple[Mechanism, ...]:
    """Mechanisms that have never been observed firing on a real install."""
    return tuple(m for m in MECHANISMS if not m.proven_in_production)
