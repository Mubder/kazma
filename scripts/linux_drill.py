"""Kazma on a clean Linux machine: install, reboot, update, uninstall (2026-11-14,
REMAINING 2.3). Run from Windows, on a WSL distro made for the drill.

1. A fresh Ubuntu 24.04 from Ubuntu's own WSL image (its SHA-256 checked
   against the release's ``SHA256SUMS``), imported as its own distro
   (``kazma-linux-drill``): never the owner's distros, never Kazma's engine.
   systemd on, an account ``kazma`` with sudo, lingering on (its user services
   run without a login, as on a server).
2. Kazma cloned from GitHub at ``--from`` (a commit of ``main``), installed the
   way a person installs it: ``./setup.sh --mode express -y --port 9393``,
   without the two model downloads and without the container parts (every
   WSL distro shares one network: a SearXNG or Neo4j here would take the ports
   Kazma's engine serves to the live install). The service must run and Kazma
   must answer on 9393.
3. A reboot (``wsl --terminate``, then boot): Kazma must start by itself.
4. ``python -m kazma_cli update -y``: to ``origin/main``; Kazma must answer on
   the new commit.
5. ``python -m kazma_cli uninstall -y``: the service and ``.venv`` gone, the
   port closed, the data kept.

The distro is removed at the end (``--keep`` keeps it). The report is one
line per check; exit code 0 means every check passed.

    .venv/Scripts/python.exe scripts/linux_drill.py --from <commit> [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DISTRO = "kazma-linux-drill"
IMAGES = "https://cloud-images.ubuntu.com/wsl/releases/24.04/current/"
ROOTFS = "ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz"
REPO = "https://github.com/Mubder/kazma.git"
PORT = 9393
WITHOUT = ("meaning_search", "neural_ocr", "docker", "private_search", "graph_neo4j")


def say(text: str) -> None:
    print(text, flush=True)


def wsl(*args: str, user: str = "", timeout: float = 600, check: bool = True,
        input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """``wsl.exe`` with UTF-8 output; with *user*, a bash login shell command."""
    env = {**os.environ, "WSL_UTF8": "1"}
    argv = ["wsl.exe", *args]
    if user:
        # wsl -u gives no login session: systemctl --user needs the runtime folder named.
        command = ("export XDG_RUNTIME_DIR=/run/user/$(id -u); " if user != "root" else "") + args[0]
        argv = ["wsl.exe", "-d", DISTRO, "-u", user, "--", "bash", "-lc", command]
    done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                          timeout=timeout, input=input_text)
    if check and done.returncode != 0:
        raise SystemExit(f"{' '.join(argv[:6])} ... failed ({done.returncode}):\n{(done.stdout + done.stderr)[-3000:]}")
    return done


def distros() -> list[str]:
    out = wsl("-l", "-q", check=False).stdout
    return [line.strip().strip("\x00") for line in out.splitlines() if line.strip().strip("\x00")]


def fetch_rootfs(folder: Path) -> Path:
    sums = urllib.request.urlopen(IMAGES + "SHA256SUMS", timeout=60).read().decode()  # noqa: S310 - Ubuntu's site
    expected = next(line.split()[0] for line in sums.splitlines() if line.endswith(ROOTFS))
    path = folder / ROOTFS
    sha = hashlib.sha256()
    with urllib.request.urlopen(IMAGES + ROOTFS, timeout=600) as resp, path.open("wb") as fh:  # noqa: S310
        while chunk := resp.read(1 << 20):
            sha.update(chunk)
            fh.write(chunk)
    if sha.hexdigest() != expected:
        raise SystemExit(f"{ROOTFS} does not match Ubuntu's SHA256SUMS")
    say(f"  {ROOTFS}: SHA-256 matches Ubuntu's ({expected[:16]}...)")
    return path


def make_machine(folder: Path) -> None:
    if DISTRO in distros():
        raise SystemExit(f"{DISTRO} already exists: remove it first (wsl --unregister {DISTRO})")
    rootfs = fetch_rootfs(folder)
    wsl("--import", DISTRO, str(folder / "disk"), str(rootfs), "--version", "2", timeout=900)
    setup = (
        "set -e\n"
        "printf '[boot]\\nsystemd=true\\n[user]\\ndefault=kazma\\n[interop]\\nappendWindowsPath=false\\n' > /etc/wsl.conf\n"
        "id kazma >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo kazma\n"
        "echo 'kazma ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/kazma && chmod 440 /etc/sudoers.d/kazma\n"
        "mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/kazma\n"
    )
    wsl(setup, user="root")
    reboot()
    wsl("export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq git curl ca-certificates "
        ">/dev/null", user="root", timeout=1200)
    say(f"  {DISTRO}: Ubuntu 24.04, systemd, account kazma (sudo, lingering)")


def reboot() -> None:
    wsl("--terminate", DISTRO, check=False)
    time.sleep(3)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = wsl("systemctl is-system-running 2>/dev/null || true", user="root", check=False).stdout.strip()
        if state in ("running", "degraded"):
            return
        time.sleep(3)
    raise SystemExit(f"{DISTRO} did not finish booting (systemd: {state!r})")


def answers(path: str = "/health/live", timeout: float = 300.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = wsl(f"curl -fsS --max-time 5 http://127.0.0.1:{PORT}{path}", user="kazma", check=False)
        if out.returncode == 0:
            try:
                return json.loads(out.stdout)
            except ValueError:
                return {}
        time.sleep(5)
    return None


def commit_after(head: str, timeout: float = 600.0) -> str:
    """The commit Kazma answers with, once it is *head* (or the last seen)."""
    deadline = time.monotonic() + timeout
    commit = ""
    while time.monotonic() < deadline:
        commit = ((answers(timeout=30) or {}).get("build") or {}).get("commit", "")
        if commit and head.startswith(commit[:7]):
            return commit
        time.sleep(10)
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="from_ref", required=True, help="the commit of main to install first")
    parser.add_argument("--keep", action="store_true", help="keep the distro afterwards")
    args = parser.parse_args(argv)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        say(f"  {'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="kazma-linux-drill-", dir=os.environ.get("KAZMA_DRILL_DIR"),
                                     ignore_cleanup_errors=True) as tmp:
        folder = Path(tmp)
        try:
            say("1. A clean Linux machine")
            make_machine(folder)
            say(f"2. Install Kazma at {args.from_ref}")
            wsl(f"git clone -q {REPO} ~/kazma && cd ~/kazma && git reset -q --hard {args.from_ref}", user="kazma",
                timeout=900)
            without = " ".join(f"--without {p}" for p in WITHOUT)
            install = wsl(f"cd ~/kazma && ./setup.sh --mode express -y --port {PORT} -- {without} --no-browser",
                          user="kazma", timeout=5400, check=False)
            check("setup.sh finished", install.returncode == 0,
                  f"exit {install.returncode}" + ("" if install.returncode == 0 else ": " + install.stdout[-1500:]))
            check("every part it chose is in place",
                  wsl("cd ~/kazma && .venv/bin/python -m kazma_cli setup install --check", user="kazma",
                      check=False).returncode == 0)
            active = wsl("systemctl --user is-active kazma", user="kazma", check=False).stdout.strip()
            check("the service runs", active == "active", active)
            live = answers()
            check("Kazma answers on its port", live is not None)
            first = (live or {}).get("build", {}).get("commit", "")

            say("3. Reboot")
            reboot()
            check("Kazma starts by itself after a reboot", answers() is not None)

            say("4. kazma update")
            head = wsl("cd ~/kazma && git ls-remote origin refs/heads/main | cut -c1-9", user="kazma").stdout.strip()
            update = wsl("cd ~/kazma && .venv/bin/python -m kazma_cli update -y", user="kazma", timeout=3600,
                         check=False)
            check("kazma update finished", update.returncode == 0, update.stdout[-800:] if update.returncode else "")
            commit = commit_after(head)
            check("Kazma answers on main after the update", bool(commit) and head.startswith(commit[:7]),
                  f"{first} -> {commit} (main {head})")

            say("5. kazma uninstall")
            gone = wsl("cd ~/kazma && .venv/bin/python -m kazma_cli uninstall -y", user="kazma", timeout=900,
                       check=False)
            check("kazma uninstall finished", gone.returncode == 0, gone.stdout[-800:] if gone.returncode else "")
            time.sleep(10)
            state = wsl("systemctl --user is-enabled kazma 2>&1 || true", user="kazma", check=False).stdout.strip()
            check("the service is gone", "not-found" in state or "No such file" in state or state == "", state)
            check("the port is closed", answers(timeout=15) is None)
            check("the Python environment is gone",
                  wsl("test ! -e ~/kazma/.venv", user="kazma", check=False).returncode == 0)
            check("the data is kept",
                  wsl("test -d ~/kazma/kazma-data || test -d ~/.local/share/kazma", user="kazma",
                      check=False).returncode == 0)
        finally:
            if not args.keep and DISTRO in distros():
                wsl("--unregister", DISTRO, check=False, timeout=300)
    failed = [c for c in checks if not c[1]]
    say("PASS: install, reboot, update, uninstall" if not failed else f"{len(failed)} check(s) failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
