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
it reverts the deployed commits on ``origin/main`` (in a worktree of its own,
never on a checkout's HEAD), pushes, pulls the revert
into the live install and reloads; then it pages the owner (the live guard's
``--page``) with what failed. ``--no-revert`` stops after the page instead.
Every step is printed; nothing secret is.

Each deploy keeps the whole output of every step it ran on the live install
in ``.kazma/deploy/<time>.log`` of the dev checkout, and a failure names the
lines that say what went wrong, never the last 300 characters (2026-11-13: a
package update's error was lost behind a settings warning). When the deploy
had changed ``pyproject.toml``, the revert puts the reverted code's packages
back -- ``kazma update --reinstall -y``, else every package rewritten with
uv while the guard holds Kazma (:func:`repair_packages`) -- instead of a
reload: that day the failed update had removed Kazma itself, the revert's
reload lifted the update's hold, and the server crash-looped for 18 minutes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEV = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 9090  # kazma_core.server_port.DEFAULT_PORT; this script runs before the live install's code


def live_base(live: Path) -> str:
    """The live install's page: its ``KAZMA_PORT`` (its .env), else 9090."""
    raw = env_value(live / ".env", "KAZMA_PORT").strip()
    port = int(raw) if raw.isdigit() and 1 <= int(raw) <= 65535 else DEFAULT_PORT
    return f"http://127.0.0.1:{port}"
# The document worker image's tag hashes every file of kazma_core the build
# sends, its Dockerfile and requirements (kazma_core.documents.worker_image):
# any change there needs the image built for the new code, BEFORE the reload.
DOCUMENT_PATHS = ("kazma-core/kazma_core/", "deploy/document-worker/")


def say(text: str) -> None:
    print(text, flush=True)


#: This deploy's log: the whole output of every step run on the live install.
_LOG: list[Path] = []


def _start_log(dev: Path) -> Path:
    folder = dev / ".kazma" / "deploy"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("%Y%m%d-%H%M%S.log")
    _LOG[:] = [path]
    return path


def _record(argv: list[str], code: int, out: str) -> None:
    if not _LOG:
        return
    try:
        with _LOG[0].open("a", encoding="utf-8") as handle:
            handle.write(f"\n$ {' '.join(argv)}\n[exit {code}]\n{out}\n")
    except OSError:
        pass


#: Words of a line that says what went wrong.
_PROBLEM_WORDS = ("error", "failed", "denied", "traceback", "exception", "cannot", "could not", "refused",
                  "not found", "no module")


def essential(out: str, limit: int = 6) -> str:
    """The lines of *out* that say what went wrong (the last *limit*), else
    its last 300 characters. Kazma's settings-store notice is never one."""
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    picked = [ln for ln in lines
              if any(w in ln.lower() for w in _PROBLEM_WORDS) and not ln.startswith("[ConfigStore]")]
    text = " | ".join(picked[-limit:]) if picked else (out or "")[-300:]
    where = f" (whole output: {_LOG[0]})" if _LOG else ""
    return text[-1200:] + where


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
    argv = [live_python(live), *args]
    done = subprocess.run(argv, cwd=str(live), capture_output=True, text=True,
                          timeout=timeout, check=False, encoding="utf-8", errors="replace")
    out = ((done.stdout or "") + (done.stderr or "")).strip()
    _record(argv, done.returncode, out)
    return done.returncode, out


def run_cmd(argv: list[str], cwd: Path, timeout: float = 3600) -> tuple[int, str]:
    """A program other than the live Python (uv), logged like :func:`run_live`."""
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        _record(argv, -1, str(exc))
        return -1, str(exc)
    out = ((done.stdout or "") + (done.stderr or "")).strip()
    _record(argv, done.returncode, out)
    return done.returncode, out


#: Imported in a child of the live Python: what a broken environment loses
#: first (Kazma itself, its server, its settings loader).
_WHOLE = "import kazma_core, kazma_cli, kazma_gateway, kazma_ui.app, dotenv"


def environment_problem(live: Path) -> str:
    """Why the live environment cannot run Kazma, or "" when it can."""
    code, out = run_live(live, "-c", _WHOLE, timeout=600)
    return "" if code == 0 else essential(out)


def recorded_extras(live: Path) -> list[str]:
    """The optional extras the live install records (``.kazma/installed_extras.json``)."""
    try:
        data = json.loads((live / ".kazma" / "installed_extras.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    extras = data.get("extras") if isinstance(data, dict) else None
    return [str(e) for e in extras or () if str(e).replace("-", "").isalnum()]


def repair_packages(live: Path, attempts: int = 2, pause_s: float = 15.0) -> str:
    """Every package of the live environment rewritten for the checkout as it
    is now, with Kazma held stopped by its guard; "" when Kazma can run again
    (then resumed), else the problem (Kazma stays held: resumed broken, the
    guard would only restart it and page).

    ``--reinstall``: a failed install can leave a package's record with its
    files gone, and a plain install takes it as installed (2026-11-13:
    ``dotenv``)."""
    uv = shutil.which("uv")
    if not uv:
        return "uv is not on PATH: the packages cannot be put back"
    extras = recorded_extras(live)
    spec = ".[" + ",".join(extras) + "]" if extras else "."
    code, out = run_live(live, "scripts/service/kazma_guard.py", "--pause", "--stop", "--reason",
                         "deploy: putting Kazma's packages back", timeout=300)
    if code != 0:
        return f"the guard did not hold Kazma: {essential(out)}"
    problem = ""
    for attempt in range(attempts):
        if attempt:
            time.sleep(pause_s)
        code, out = run_cmd([uv, "pip", "install", "--reinstall", "--python", live_python(live), "-e", spec], live)
        problem = essential(out) if code != 0 else environment_problem(live)
        if not problem:
            run_live(live, "scripts/service/kazma_guard.py", "--resume", timeout=120)
            return ""
    return f"the packages could not be put back; Kazma stays held: {problem}"


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

    def __init__(self, live: Path, base: str | None = None) -> None:
        self.base = base or live_base(live)
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
    packages = "pyproject.toml" in changed
    if packages:
        say("-> pyproject.toml changed: kazma update --reinstall -y")
        code, out = run_live(live, "-m", "kazma_cli", "update", "--reinstall", "-y", timeout=3600)
        if code != 0:
            return fail(live, dev, shipped, after, f"the package update failed: {essential(out)}", revert,
                        packages=True)
    if any(path.startswith(DOCUMENT_PATHS) for path in changed):
        say("-> Kazma's core changed: building the document worker image for it")
        code, out = run_live(live, "scripts/document_worker_image.py", "build", timeout=3600)
        if code != 0:
            return fail(live, dev, shipped, after, f"the worker image build failed: {essential(out)}", revert,
                        packages=packages)
    boot_from = time.time()
    say("-> reload when idle")
    code, out = run_live(live, "scripts/service/kazma_guard.py", "--reload", "--when-idle", timeout=1200)
    if code != 0:
        return fail(live, dev, shipped, after, f"the reload did not happen: {essential(out)}", revert,
                    packages=packages)
    server = server or Server(live)
    for check in (lambda: wait_for_build(server, after), lambda: check_health(server),
                  (lambda: "") if skip_turn else (lambda: test_turn(server))):
        problem = check()
        if problem:
            return fail(live, dev, shipped, after, problem, revert, packages=packages)
    errors = log_errors(live, new_boot(live, boot_from))
    if errors:
        say("   errors in the log since the boot:")
        for line in errors[:10]:
            say(f"     {line}")
        return fail(live, dev, shipped, after, f"{len(errors)} ERROR/CRITICAL line(s) in the log since the boot",
                    revert, packages=packages)
    say(f"Deployed {after[:12]}: healthy, the test turn answered, the log is clean.")
    return 0


def revert_on_origin(repo: Path, first: str, last: str) -> None:
    """Revert ``first..last`` on top of ``origin/main`` and push that, built
    in a worktree of its own: never on a checkout's HEAD.

    On 2026-11-12 this script ran from the live install's copy, so ``dev`` was
    the live checkout: the revert was made on its HEAD and ``HEAD:main``
    pushed the live install's own commits (its local kazma.yaml since
    2026-10-03) to the public repository, and main shipped that kazma.yaml.
    A revert built on origin/main pushes the revert and nothing else, from
    whichever checkout this runs in."""
    import tempfile

    git(repo, "fetch", "origin")
    where = Path(tempfile.mkdtemp(prefix="kazma-revert-"))
    git(repo, "worktree", "add", "--detach", str(where), "origin/main")
    try:
        git(where, "revert", "--no-edit", f"{first}..{last}")
        git(where, "push", "origin", "HEAD:main")
    finally:
        try:
            git(repo, "worktree", "remove", "--force", str(where))
        except RuntimeError:
            say(f"   the revert's worktree {where} was left behind; remove it with git worktree prune")


def put_packages_back(live: Path) -> str:
    """After a revert of a ``pyproject.toml`` change: the reverted code's
    packages, through the update (it holds and resumes Kazma itself), else
    every package rewritten (:func:`repair_packages`). "" or the problem."""
    say("-> putting back the reverted code's packages: kazma update --reinstall -y")
    code, out = run_live(live, "-m", "kazma_cli", "update", "--reinstall", "-y", timeout=3600)
    problem = (essential(out) if code != 0 else "") or environment_problem(live)
    if not problem:
        return ""
    say(f"   the update could not ({problem}); rewriting every package")
    return repair_packages(live)


def fail(live: Path, dev: Path, shipped: tuple[str, str], after: str, problem: str, revert: bool,
         *, packages: bool = False) -> int:
    say(f"FAILED: {problem}")
    if not revert:
        page(live, "A deploy failed its checks", f"{after[:12]}: {problem}. Not reverted (--no-revert).")
        return 1
    first, last = shipped
    say(f"-> reverting {first[:12]}..{last[:12]} on main")
    try:
        revert_on_origin(dev, first, last)
        git(live, "pull", "--no-rebase", "--no-edit")
        if packages:
            # A reload cannot bring back packages a failed update removed,
            # and it lifts the update's hold on Kazma (2026-11-13).
            trouble = put_packages_back(live)
            if trouble:
                say(f"   {trouble}")
            reverted = not trouble
        else:
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
    say(f"(the whole output of each step: {_start_log(DEV)})")
    return deploy(options.live.resolve(), revert=not options.no_revert, skip_turn=options.skip_turn)


if __name__ == "__main__":
    sys.exit(main())
