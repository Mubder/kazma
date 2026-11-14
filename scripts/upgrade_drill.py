"""Every release is tested as an upgrade from the one before (2026-11-14, REMAINING 2.6).

An install made from the previous release, holding its own data, is upgraded
to the new wheel the way ``kazma update`` upgrades a wheel install, and must
still run on that data:

1. A fresh virtual environment and a private Kazma home and data folder
   (``KAZMA_USER_HOME``, ``KAZMA_DATA_DIR``), started in an empty folder with
   every ``KAZMA_*`` and credential variable of this machine left out: nothing
   of an install here is read or written.
2. The previous release's wheel from its GitHub release, checked as the
   updater checks one (``SHA256SUMS``, GitHub's digest, its Sigstore bundle
   when it has one), installed with the versions it was tested with: the
   locked versions inside the wheel, else its tag's ``uv.lock``.
3. It starts (``kazma serve`` on a free loopback port, SQLite), answers
   ``/health/live``, stops, and a marker setting is written into its data.
4. The new wheel is installed over it with the locked versions inside it
   (``kazma_cli.update._locked_versions_in_wheel``), as ``kazma update`` does.
5. It starts on the same data. It must report the new version, answer
   ``/health/live`` and ``/health/ready`` (ready, or not ready only because no
   model is set up yet), keep the marker and every database the old release
   made, and write no ERROR line or traceback.

    python scripts/upgrade_drill.py --wheel dist/kazma-0.22.0-py3-none-any.whl
    python scripts/upgrade_drill.py --wheel dist/kazma-*.whl --from v0.21.2

The release workflow runs it before it publishes; a release that cannot be
upgraded to is never published. Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "kazma-cli"), str(ROOT / "kazma-core")]

PYTHON = "3.12"
START_TIMEOUT_S = 180.0
STOP_TIMEOUT_S = 60.0
MARKER_KEY = "drill.upgrade_marker"
#: Variables an install here may have that the drill's installs must not see.
_SCRUB = re.compile(r"^(KAZMA_|OPENAI_|ANTHROPIC_|DEEPSEEK_|GROQ_|GEMINI_|GOOGLE_|AZURE_|AWS_|TELEGRAM_|"
                    r"DISCORD_|SLACK_|DATABASE_URL|PG|HF_TOKEN|GITHUB_TOKEN|GH_TOKEN)", re.I)
_ERROR_LINE = re.compile(r'"level":\s*"(ERROR|CRITICAL)"|\b(ERROR|CRITICAL)\b|Traceback \(most recent call last\)')


def say(text: str) -> None:
    print(text, flush=True)


def scrubbed_env(home: Path, data: Path, port: int) -> dict[str, str]:
    """The drill's environment: this machine's minus anything of a Kazma install
    or a credential, plus the drill's own home, data folder and port."""
    env = {k: v for k, v in os.environ.items() if not _SCRUB.match(k)}
    env.update({
        "KAZMA_USER_HOME": str(home),
        "KAZMA_DATA_DIR": str(data),
        "KAZMA_PORT": str(port),
        "KAZMA_HOST": "127.0.0.1",
        "KAZMA_DB_BACKEND": "sqlite",
        "KAZMA_EMBEDDER_PREWARM": "0",
        "KAZMA_PROCESS_PRIORITY": "keep",
        "HF_HUB_OFFLINE": "1",
        "PYTHONIOENCODING": "utf-8",
        "NO_COLOR": "1",
    })
    return env


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None,
        timeout: float = 1800.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def wheel_version(wheel: Path) -> str:
    match = re.match(r"kazma-([^-]+)-", wheel.name)
    if not match:
        raise SystemExit(f"not a Kazma wheel: {wheel.name}")
    return match.group(1)


# ── the previous release ─────────────────────────────────────────────────


def previous_release(tag: str | None, new_version: str, *, any_version: bool = False) -> Any:
    """The release to upgrade from: *tag*, else the newest release, which must
    be older than the new wheel unless *any_version* (CI builds ``main``,
    whose version moves only when a release is made). Read as ``kazma
    update`` reads one."""
    from kazma_cli import update

    if tag:
        release = update._get_release(tag.removeprefix("v"))
    else:
        release = update.get_latest_release()
        if release is not None and not any_version and not update.is_newer(release.version, new_version):
            # The newest published release is this one (a re-run): go one back.
            raise SystemExit(f"the newest release is v{release.version}; name the one to upgrade from with --from")
    if release is None:
        raise SystemExit("GitHub could not be read for the previous release")
    if release.problem:
        raise SystemExit(f"v{release.version} cannot be installed: {release.problem}")
    return release


def download(url: str, dest: Path) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "kazma-upgrade-drill"})
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 - a github.com release URL
        data = resp.read()
    dest.write_bytes(data)
    return data


def fetch_previous_wheel(release: Any, folder: Path) -> Path:
    """The previous release's wheel, checked: SHA-256 and, when it has one, its signature."""
    from kazma_cli.release_signature import check_bundle

    wheel = folder / release.wheel_name
    data = download(release.wheel_url, wheel)
    if hashlib.sha256(data).hexdigest() != release.wheel_sha256:
        raise SystemExit(f"v{release.version}'s wheel does not match its SHA-256")
    if release.bundle_url:
        bundle = download(release.bundle_url, folder / (release.wheel_name + ".sigstore.json"))
        say(f"  previous wheel signed by {check_bundle(hashlib.sha256(data).digest(), bundle)}")
    return wheel


def locked_for(wheel: Path, tag: str, folder: Path) -> Path | None:
    """The versions *wheel* was tested with: inside it, else its tag's uv.lock."""
    from kazma_cli import update

    locked, problem = update._locked_versions_in_wheel(wheel, folder)
    if problem:
        raise SystemExit(f"{wheel.name}: {problem}")
    if locked is not None:
        return locked
    project = folder / f"lock-{tag}"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shown = run(["git", "show", f"{tag}:{name}"], cwd=ROOT)
        if shown.returncode != 0:
            say(f"  {tag} has no {name} in this checkout: installing it unpinned")
            return None
        (project / name).write_text(shown.stdout, encoding="utf-8")
    out = folder / f"locked-{tag}.txt"
    exported = run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "--no-header",
                    "--all-extras", "--format", "requirements-txt", "-o", str(out)], cwd=project)
    if exported.returncode != 0 or update._locked_text_problem(out.read_text(encoding="utf-8")):
        say(f"  {tag}'s uv.lock could not be exported: installing it unpinned")
        return None
    return out


def install(python: Path, wheel: Path, locked: Path | None, env: dict[str, str]) -> None:
    cmd = ["uv", "pip", "install", "--python", str(python), str(wheel)]
    if locked is not None:
        cmd += ["-c", str(locked)]
    result = run(cmd, env=env)
    if result.returncode != 0:
        raise SystemExit(f"installing {wheel.name} failed:\n{result.stderr[-3000:]}")


# ── running it ───────────────────────────────────────────────────────────


class Server:
    """``kazma serve`` from the drill's environment, its output in a file."""

    def __init__(self, python: Path, env: dict[str, str], cwd: Path, log: Path, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.log = log
        flags: dict[str, Any] = {}
        if os.name == "nt":
            flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            flags["start_new_session"] = True
        self._out = log.open("w", encoding="utf-8")
        self.proc = subprocess.Popen([str(python), "-m", "kazma_cli", "serve", str(port)], env=env, cwd=cwd,
                                     stdout=self._out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **flags)

    def get(self, path: str) -> tuple[int, Any]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as resp:  # noqa: S310 - loopback
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return exc.code, json.loads(body or b"null")
            except ValueError:
                return exc.code, None
        except (OSError, ValueError):
            return 0, None

    def wait_live(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit(f"the server exited ({self.proc.returncode}) before it answered:\n{self.tail()}")
            status, _body = self.get("/health/live")
            if status == 200:
                return
            time.sleep(1.0)
        raise SystemExit(f"the server did not answer /health/live in {START_TIMEOUT_S:.0f} s:\n{self.tail()}")

    def stop(self) -> None:
        if self.proc.poll() is None:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
                raise SystemExit(f"the server did not stop in {STOP_TIMEOUT_S:.0f} s:\n{self.tail()}") from None
        self._out.close()

    def tail(self, lines: int = 40) -> str:
        self._out.flush()
        return "\n".join(self.log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def ready_is_fine(status: int, body: Any) -> bool:
    """Ready, or not ready only because no model is set up yet (a fresh data
    folder has none): the guard counts that as up, never a restart."""
    if status == 200:
        return True
    return status == 503 and isinstance(body, dict) and bool(body.get("needs_setup"))


def error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if _ERROR_LINE.search(line)]


def read_marker(python: Path, env: dict[str, str], cwd: Path, value: str | None = None) -> str:
    code = ("import sys\nfrom kazma_core.config_store import get_config_store\ns = get_config_store()\n"
            + (f"s.set({MARKER_KEY!r}, {value!r}, category='system')\n" if value is not None else "")
            + f"print(s.get({MARKER_KEY!r}, ''))\ns.close()\n")
    result = run([str(python), "-c", code], env=env, cwd=cwd, timeout=300)
    if result.returncode != 0:
        raise SystemExit(f"the settings store could not be read:\n{result.stderr[-2000:]}")
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


def databases(data: Path) -> set[str]:
    return {str(p.relative_to(data)) for p in data.rglob("*.db")}


def drill(new_wheel: Path, from_tag: str | None, *, any_version: bool = False) -> list[str]:
    """Run the drill; the failed checks (empty: the upgrade holds)."""
    from kazma_cli import update

    new_version = wheel_version(new_wheel)
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kazma-upgrade-drill-") as tmp:
        root = Path(tmp)
        home, data, work, downloads = root / "home", root / "data", root / "work", root / "downloads"
        for folder in (home, data, work, downloads):
            folder.mkdir()
        port = free_port()
        env = scrubbed_env(home, data, port)

        release = previous_release(from_tag, new_version, any_version=any_version)
        tag = f"v{release.version}"
        say(f"Upgrade drill: v{release.version} -> v{new_version}")
        old_wheel = fetch_previous_wheel(release, downloads)
        made = run(["uv", "venv", "--python", PYTHON, str(root / "venv")])
        if made.returncode != 0:
            raise SystemExit(f"uv venv failed:\n{made.stderr}")
        python = venv_python(root / "venv")
        install(python, old_wheel, locked_for(old_wheel, tag, downloads), env)
        say(f"  installed {old_wheel.name}")

        old = Server(python, env, work, root / "old.log", port)
        try:
            old.wait_live()
        finally:
            old.stop()
        say("  the previous release ran on the drill's data")
        if read_marker(python, env, work, value=f"made by {tag}") != f"made by {tag}":
            raise SystemExit("the marker could not be written with the previous release")
        before = databases(data)

        new_locked, problem = update._locked_versions_in_wheel(new_wheel, downloads)
        if problem or new_locked is None:
            failures.append(f"the new wheel carries no usable locked versions ({problem or 'none'})")
        install(python, new_wheel, new_locked, env)
        say(f"  upgraded to {new_wheel.name}")

        shown = run([str(python), "-m", "kazma_cli", "--version"], env=env, cwd=work, timeout=120)
        if new_version not in shown.stdout:
            failures.append(f"kazma --version says {shown.stdout.strip()!r}, not {new_version}")
        new = Server(python, env, work, root / "new.log", port)
        try:
            new.wait_live()
            status, body = new.get("/health/ready")
            if not ready_is_fine(status, body):
                failures.append(f"/health/ready answered {status}: {json.dumps(body)[:400]}")
        finally:
            new.stop()
        marker = read_marker(python, env, work)
        if marker != f"made by {tag}":
            failures.append(f"the setting the previous release kept reads {marker!r} after the upgrade")
        lost = sorted(before - databases(data))
        if lost:
            failures.append(f"databases the previous release made are gone: {', '.join(lost)}")
        errors = error_lines((root / "new.log").read_text(encoding="utf-8", errors="replace"))
        if errors:
            failures.append("the upgraded server logged errors:\n" + "\n".join(errors[:20]))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", required=True, help="the new release's wheel (a glob is expanded)")
    parser.add_argument("--from", dest="from_tag", help="the release to upgrade from (default: the newest)")
    parser.add_argument("--any-version", action="store_true",
                        help="upgrade from the newest release even when it is not older (CI on main)")
    args = parser.parse_args(argv)
    matches = sorted(glob.glob(args.wheel))
    if len(matches) != 1:
        raise SystemExit(f"--wheel names {len(matches)} files: {matches}")
    failures = drill(Path(matches[0]).resolve(), args.from_tag, any_version=args.any_version)
    for failure in failures:
        say(f"FAIL: {failure}")
    say("PASS: the upgrade holds" if not failures else f"{len(failures)} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
