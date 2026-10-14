"""What every soak harness writes, and how.

A soak runs for hours or days, and a reboot, an update or a kill can end it
early. Each harness therefore rewrites its whole report as it goes
(``"status": "running"``), and once more at the end (``passed`` or
``failed``): the file on disk is always a complete report of the run so far.
The memory, document, swarm, scheduled, time-travel and knowledge soaks
share these helpers, so a reader finds the same fields in every report.

Standard library only: a harness imports it before it imports Kazma.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

#: The states a report's ``status`` can hold.
RUNNING, PASSED, FAILED = "running", "passed", "failed"


def utc_stamp() -> str:
    """Now, as the report's ``updated_at``."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


#: How long a rename refused by a reader is retried (on Windows a reader that
#: holds the file open -- an editor, a script reading it -- refuses it).
_REPLACE_RETRY_S = 2.0


def write_report(path: str | os.PathLike[str] | None, report: dict[str, Any]) -> bool:
    """Replace the report file whole (a reader never sees half of one).

    Written to a temporary name beside it, then renamed over it. No path,
    no file: a harness run without ``--report`` prints its report only.

    Never raises: a report write must not end a multi-day soak. A rename a
    reader refuses is retried for ``_REPLACE_RETRY_S``; a write that still
    fails says so on stderr, leaves the previous report in place, and
    answers False, and the next write catches up.
    """
    if not path:
        return True
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    deadline = time.monotonic() + _REPLACE_RETRY_S
    while True:
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
            return True
        except OSError as exc:
            if time.monotonic() >= deadline:
                print(f"soak_report: could not write {target}: {exc}; the next write will try "
                      "again", file=sys.stderr, flush=True)
                return False
            time.sleep(0.05)


def finished(report: dict[str, Any], healthy: bool) -> dict[str, Any]:
    """Stamp a report as ended: ``passed`` when healthy, else ``failed``."""
    report["healthy"] = healthy
    report["status"] = PASSED if healthy else FAILED
    report["updated_at"] = utc_stamp()
    return report


def percentile(values: list[float], q: float) -> float | None:
    """The ``q`` quantile (0-1) of ``values`` by nearest rank; None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 3)


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    """p50, p95, p99, max and the sample count, the same in every report."""
    return {
        "p50": percentile(values, 0.50), "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 3) if values else None, "n": len(values),
    }


def process_tree_sample() -> dict[str, float | int]:
    """Resident memory, handles (or open files) and process count of this
    process and every child it started."""
    import psutil

    me = psutil.Process()
    procs = [me, *me.children(recursive=True)]
    rss = handles = 0
    for proc in procs:
        try:
            rss += proc.memory_info().rss
            handles += proc.num_handles() if os.name == "nt" else proc.num_fds()
        except psutil.Error:
            continue
    return {"rss_mib": round(rss / 2**20, 1), "handles": handles, "processes": len(procs)}
