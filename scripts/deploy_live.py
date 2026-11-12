#!/usr/bin/env python3
"""Deploy to the live install, check it, and undo it when it fails (resilience plan C2).

Run from the dev checkout, after pushing ``main``::

    .venv/Scripts/python.exe scripts/deploy_live.py --live C:/Users/balfa/kazma

It uses only what the live install allows: a plain ``git pull`` (the checkout
must have no modified tracked files), the live guard's ``--reload
--when-idle``, ``kazma update --reinstall -y`` when ``pyproject.toml``
changed, and the document worker image build when Kazma's core changed.
Then it checks, in order:

1. the server answers ``/health/ready`` and ``/health/deep`` with the new
   commit (``/health/live``'s build);
2. one test turn in a private chat kept out of memory ("Reply with the word
   OK"), answered OK, then the chat deleted;
3. no ERROR or CRITICAL line in the server's log since the new boot.

When a check fails it undoes the deploy the only way the live install allows:
it reverts the deployed commits on ``main`` here, pushes, pulls the revert
into the live install and reloads; then it pages the owner (the live guard's
``--page``) with what failed. ``--no-revert`` stops after the page instead.
Every step is printed; nothing secret is.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEV = Path(__file__).resolve().parents[1]
HEALTH = "http://127.0.0.1:9090"
# The document worker image's tag hashes every file of kazma_core the build
# sends, its Dockerfile and requirements (kazma_core.documents.worker_image):
# any change there needs the image built for the new code, BEFORE the reload.
DOCUMENT_PATHS = ("kazma-core/kazma_core/", "deploy/document-worker/")


def say(text: str) -> None:
    print(text, flush=True)


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout.strip()


def live_python(live: Path) -> str:
    for candidate in (live / ".venv" / "Scripts" / "python.exe", live / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(f"no virtual environment in {live}")


def run_live(live: Path, *args: str, timeout: float = 1800) -> tuple[int, str]:
    done = subprocess.run([live_python(live), *args], cwd=str(live), capture_output=True, text=True,
                          timeout=timeout, check=False, encoding="utf-8", errors="replace")
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def env_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        name, sep, rest = line.partition("=")
        if sep and name.strip() == key and not line.lstrip().startswith("#"):
            value = rest.strip().strip('"').strip("'")
    return value


class Server:
    """The live server's HTTP API, signed in with the install's secret."""

    def __init__(self, live: Path, base: str = HEALTH) -> None:
        self.base = base
        self.secret = env_value(live / ".env", "KAZMA_SECRET")

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 30.0):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.secret:
            req.add_header("X-Kazma-Secret", self.secret)
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 -- loopback, fixed scheme

    def json(self, method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
        try:
            with self.request(method, path, body, timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
            except ValueError:
                return exc.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            return 0, {}


def wait_for_build(server: Server, commit: str, timeout: float = 900.0) -> str:
    """Wait until the live server answers with *commit*. Returns the problem or ""."""
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        status, data = server.json("GET", "/health/live", timeout=10)
        build = (data.get("build") or {}) if isinstance(data, dict) else {}
        seen = str(build.get("commit") or "")
        if status == 200 and seen and commit.startswith(seen[:7]):
            return ""
        time.sleep(5)
    return f"the server did not come back on {commit[:12]} within {int(timeout)} s (it answers {seen or 'nothing'})"


def check_health(server: Server) -> str:
    for path in ("/health/ready", "/health/deep"):
        deadline = time.monotonic() + 300
        status = 0
        while time.monotonic() < deadline:
            status, _data = server.json("GET", path, timeout=30)
            if status == 200:
                break
            time.sleep(10)
        if status != 200:
            return f"{path} answered {status or 'nothing'}"
    return ""


def test_turn(server: Server) -> str:
    """One turn in a private chat kept out of memory; the chat deleted after."""
    session = f"deploy-check-{uuid.uuid4().hex[:12]}"
    status, _ = server.json("PUT", f"/api/memory/v2/chats/{session}/memory", {"remember": False})
    if status not in (200, 404):
        return f"the test chat could not be kept out of memory (HTTP {status})"
    answer, done = "", False
    try:
        with server.request("POST", "/api/chat/stream",
                            {"message": "Reply with the word OK and nothing else.", "session_id": session},
                            timeout=300) as resp:
            event = ""
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event in ("done", "turn_complete"):
                    try:
                        payload = json.loads(line[5:].strip() or "{}")
                    except ValueError:
                        payload = {}
                    answer = str(payload.get("content") or answer)
                    done = True
                    if event == "done":
                        break
    except (urllib.error.URLError, OSError) as exc:
        return f"the test turn failed: {type(exc).__name__}"
    finally:
        server.json("DELETE", f"/api/chat/sessions/{session}", {})
    if not done:
        return "the test turn never finished"
    if "OK" not in answer.upper():
        return f"the test turn answered {answer[:80]!r}, not OK"
    return ""


def new_boot(live: Path, after_epoch: float) -> float:
    """When the server the reload started was spawned: the guard's last
    ``child.spawned`` event at or after *after_epoch*, else *after_epoch*.

    The log check reads from there. Read from the reload request, it took the
    old server's last seconds as the new build's: on 2026-11-12 the guard's
    stop also stopped the old server's restic check, which logged "restic
    check FAILED" 4 s before the new server existed, and the deploy reverted
    a good commit."""
    from datetime import datetime

    try:
        lines = (live / ".kazma" / "guard.log").read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
    except OSError:
        return after_epoch
    found = after_epoch
    for line in lines:
        try:
            rec = json.loads(line)
            if rec.get("event") != "child.spawned":
                continue
            when = datetime.fromisoformat(str(rec.get("ts"))).timestamp()
        except (ValueError, AttributeError):
            continue
        if when >= after_epoch:
            found = when
    return found


def log_errors(live: Path, since_epoch: float) -> list[str]:
    """ERROR and CRITICAL lines of the server's log written since *since_epoch*."""
    found = []
    log = live / ".kazma" / "kazma.log"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-20000:]
    except OSError:
        return []
    from datetime import datetime

    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("level") not in ("ERROR", "CRITICAL"):
            continue
        try:
            when = datetime.fromisoformat(str(rec.get("timestamp"))).timestamp()
        except ValueError:
            continue
        if when >= since_epoch:
            found.append(f"{rec.get('level')} {rec.get('logger')}: {str(rec.get('message'))[:160]}")
    return found


def page(live: Path, title: str, detail: str) -> None:
    code, out = run_live(live, "scripts/service/kazma_guard.py", "--page", title, "--detail", detail, timeout=120)
    say(f"   paged the owner ({'sent' if code == 0 else 'NOT sent: ' + out[-200:]})")


def deploy(live: Path, *, dev: Path = DEV, revert: bool = True, skip_turn: bool = False,
           server: Server | None = None) -> int:
    if git(live, "status", "--porcelain", "--untracked-files=no"):
        say("The live checkout has modified tracked files: a pull would refuse. Nothing was done.")
        return 2
    before = git(live, "rev-parse", "HEAD")
    # The live checkout keeps a local commit (its kazma.yaml), so its pull is
    # a merge: what was deployed is what origin/main gained.
    shipped_from = git(live, "rev-parse", "origin/main")
    say(f"live is at {before[:12]}")
    say("-> pull")
    # A merge, named: with no `pull.rebase` set (a fresh machine) git refuses
    # to pick for a checkout with its own commit.
    git(live, "pull", "--no-rebase", "--no-edit")
    after = git(live, "rev-parse", "HEAD")
    shipped = (shipped_from, git(live, "rev-parse", "origin/main"))
    if after == before:
        say("Nothing new to deploy.")
        return 0
    changed = git(live, "diff", "--name-only", before, after).splitlines()
    say(f"-> deployed {before[:12]}..{after[:12]} ({len(changed)} files)")
    if "pyproject.toml" in changed:
        say("-> pyproject.toml changed: kazma update --reinstall -y")
        code, out = run_live(live, "-m", "kazma_cli", "update", "--reinstall", "-y", timeout=3600)
        if code != 0:
            return fail(live, dev, shipped, after, f"the package update failed: {out[-300:]}", revert)
    if any(path.startswith(DOCUMENT_PATHS) for path in changed):
        say("-> Kazma's core changed: building the document worker image for it")
        code, out = run_live(live, "scripts/document_worker_image.py", "build", timeout=3600)
        if code != 0:
            return fail(live, dev, shipped, after, f"the worker image build failed: {out[-300:]}", revert)
    boot_from = time.time()
    say("-> reload when idle")
    code, out = run_live(live, "scripts/service/kazma_guard.py", "--reload", "--when-idle", timeout=1200)
    if code != 0:
        return fail(live, dev, shipped, after, f"the reload did not happen: {out[-300:]}", revert)
    server = server or Server(live)
    for check in (lambda: wait_for_build(server, after), lambda: check_health(server),
                  (lambda: "") if skip_turn else (lambda: test_turn(server))):
        problem = check()
        if problem:
            return fail(live, dev, shipped, after, problem, revert)
    errors = log_errors(live, new_boot(live, boot_from))
    if errors:
        say("   errors in the log since the boot:")
        for line in errors[:10]:
            say(f"     {line}")
        return fail(live, dev, shipped, after, f"{len(errors)} ERROR/CRITICAL line(s) in the log since the boot",
                    revert)
    say(f"Deployed {after[:12]}: healthy, the test turn answered, the log is clean.")
    return 0


def fail(live: Path, dev: Path, shipped: tuple[str, str], after: str, problem: str, revert: bool) -> int:
    say(f"FAILED: {problem}")
    if not revert:
        page(live, "A deploy failed its checks", f"{after[:12]}: {problem}. Not reverted (--no-revert).")
        return 1
    first, last = shipped
    say(f"-> reverting {first[:12]}..{last[:12]} on main")
    try:
        git(dev, "fetch", "origin")
        git(dev, "revert", "--no-edit", f"{first}..{last}")
        git(dev, "push", "origin", "HEAD:main")
        git(live, "pull", "--no-rebase", "--no-edit")
        code, out = run_live(live, "scripts/service/kazma_guard.py", "--reload", "--when-idle", timeout=1200)
        reverted = code == 0
    except RuntimeError as exc:
        say(f"   the revert did not complete: {exc}")
        reverted = False
    page(live, "A deploy failed and was undone" if reverted else "A deploy failed; undoing it did not finish",
         f"{after[:12]}: {problem}.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", required=True, type=Path, help="the live install's folder")
    ap.add_argument("--no-revert", action="store_true", help="page on failure, but do not revert")
    ap.add_argument("--skip-turn", action="store_true", help="skip the test turn (no model calls)")
    options = ap.parse_args()
    return deploy(options.live.resolve(), revert=not options.no_revert, skip_turn=options.skip_turn)


if __name__ == "__main__":
    sys.exit(main())
