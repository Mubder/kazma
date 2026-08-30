"""Backup frequency and retention are one decision, not two.

The loop ran every 24h and KEEP_POLICY was ``--keep-daily 7 --keep-weekly 8
--keep-monthly 12``. Raising the frequency alone would have been silently
useless: with no ``--keep-hourly``, ``forget --prune`` keeps ONE snapshot per
day, so every intra-day run is deleted at the next maintenance pass. You would
get a backup schedule whose interval does not describe what it retains.

These tests hold the two together, so the next person to change one is made to
look at the other.
"""

from __future__ import annotations

from kazma_core.backup.restic_repo import KEEP_POLICY
from kazma_core.db.pg_backup import _DEFAULT_RETENTION
from kazma_core.memory.worker_bootstrap import _BACKUP_EXPORT_INTERVAL_HOURS


def _policy() -> dict[str, int]:
    return {
        KEEP_POLICY[i].removeprefix("--keep-"): int(KEEP_POLICY[i + 1])
        for i in range(0, len(KEEP_POLICY), 2)
    }


def test_sub_daily_backups_require_sub_daily_retention():
    """The invariant this file exists for."""
    policy = _policy()
    if _BACKUP_EXPORT_INTERVAL_HOURS < 24:
        assert "hourly" in policy, (
            f"the backup loop runs every {_BACKUP_EXPORT_INTERVAL_HOURS}h but "
            "KEEP_POLICY has no --keep-hourly, so forget --prune will collapse "
            "every run to one snapshot per day and the extra frequency buys "
            "nothing that survives maintenance"
        )
        runs_per_day = 24 // _BACKUP_EXPORT_INTERVAL_HOURS
        assert policy["hourly"] >= runs_per_day, (
            f"--keep-hourly {policy['hourly']} keeps fewer points than the "
            f"{runs_per_day} runs a day this cadence produces"
        )


def test_the_recovery_point_objective_is_at_most_six_hours():
    assert _BACKUP_EXPORT_INTERVAL_HOURS <= 6, (
        "the main database would be recoverable only to within "
        f"{_BACKUP_EXPORT_INTERVAL_HOURS}h"
    )
    assert _BACKUP_EXPORT_INTERVAL_HOURS >= 1, "sub-hourly would thrash a 1.67 GB dump"


def test_retention_still_spans_hours_to_a_year():
    """Grandfather-father-son: each tier answers a different question."""
    policy = _policy()
    for tier in ("hourly", "daily", "weekly", "monthly"):
        assert tier in policy, f"KEEP_POLICY lost its {tier} tier"
        assert policy[tier] > 0
    # "I broke it an hour ago" through "this rotted over months".
    assert policy["hourly"] >= 24
    assert policy["daily"] >= 7
    assert policy["weekly"] >= 4
    assert policy["monthly"] >= 12


def test_retention_is_by_age_not_by_count():
    """Thirty backups is thirty days or thirty hours, depending on the loop."""
    assert "--keep-last" not in KEEP_POLICY, (
        "count-based retention gives no guarantee about the age of the oldest "
        "recoverable state"
    )


def test_local_raw_dumps_stay_staging_not_an_archive():
    """restic holds the history; raw .dump files are 1.67 GB apiece."""
    assert _DEFAULT_RETENTION <= 3, (
        f"{_DEFAULT_RETENTION} raw Postgres dumps is "
        f"~{_DEFAULT_RETENTION * 1.67:.0f} GB of near-identical copies that "
        "restic already deduplicates"
    )
    assert _DEFAULT_RETENTION >= 2, (
        "keep at least one older dump so a corrupt newest one is not the only "
        "local copy"
    )
