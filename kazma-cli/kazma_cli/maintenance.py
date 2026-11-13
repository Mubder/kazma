"""``kazma maintenance begin|end`` -- an update window (resilience plan B2b).

On 4 November an update tool stopped Docker Desktop and the guard restarted
Kazma about every 20 minutes for eleven hours, paging each time. Planned work
belongs inside a window:

* ``begin`` waits until no chat turn runs, then pauses the guard under a
  maintenance reason (Kazma keeps running; the guard restarts nothing and
  pages nothing while the pause holds). The guard's heartbeat says
  "maintenance", so the outside observer stays quiet too. The pause expires
  by itself (``--hours``, 2 by default) so a forgotten window cannot become a
  silent outage.
* ``end`` lifts the pause, waits up to 5 minutes for ``/health/deep``, and
  pages the owner (the guard's ``--page``) if Kazma is not healthy by then.

Update tools call them from their own configuration, e.g. topgrade's
``pre_commands`` / ``post_commands`` (``docs/docs/ops/kazma-update.md``).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request


def _health_deep() -> str:
    """Kazma's deep health check, on the port this install uses."""
    from kazma_core.server_port import local_url

    return local_url("/health/deep")

END_WAIT_S = 300.0


def _guard(*args: str, timeout: float = 1200) -> tuple[int, str]:
    from kazma_core.paths import get_project_root

    script = get_project_root() / "scripts" / "service" / "kazma_guard.py"
    done = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                          timeout=timeout, check=False, encoding="utf-8", errors="replace")
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def _deep_ok(url: str | None = None) -> bool:
    url = url or _health_deep()
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 -- loopback, fixed scheme
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def begin(reason: str, hours: float, *, guard=_guard) -> int:
    code, out = guard("--pause", "--when-idle", "--reason", f"maintenance: {reason}",
                      "--ttl", str(int(hours * 3600)))
    print(out)
    if code != 0:
        print("The window did not open: nothing was paused.")
        return 1
    print(f"Maintenance window open for up to {hours:g} h: the guard restarts and pages nothing. "
          "End it with: kazma maintenance end")
    return 0


def end(*, guard=_guard, deep_ok=_deep_ok, wait_s: float = END_WAIT_S, sleep=time.sleep) -> int:
    code, out = guard("--resume")
    print(out)
    if code != 0:
        print("The pause could not be lifted.")
        return 1
    waited = 0.0
    while waited <= wait_s:
        if deep_ok():
            print("Kazma is healthy again.")
            return 0
        sleep(10.0)
        waited += 10.0
    from kazma_core.english_count import count_noun

    detail = f"/health/deep did not answer healthy within {count_noun(int(wait_s // 60), 'minute')} of the window's end."
    print(detail)
    guard("--page", "Kazma is not healthy after maintenance", "--detail", detail, timeout=120)
    return 1


def run(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kazma maintenance", description="An update window.")
    sub = parser.add_subparsers(dest="action", required=True)
    b = sub.add_parser("begin", help="pause the guard (when idle) for planned work")
    b.add_argument("--reason", default="updates", help="what the window is for")
    b.add_argument("--hours", type=float, default=2.0, help="the window expires by itself after this")
    sub.add_parser("end", help="resume, and page if Kazma is not healthy within 5 minutes")
    options = parser.parse_args(args)
    if options.action == "begin":
        return begin(options.reason, options.hours)
    return end()
