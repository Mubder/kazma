"""Kazma's container engine trusts Docker's apt signing key by its fingerprint
(2026-11-15).

``setup-engine.sh`` (``container_engine_assets``) fetched
``download.docker.com/linux/ubuntu/gpg`` straight into ``/etc/apt/keyrings``
and pointed apt at it: whatever that download held -- a changed file, a
proxy's answer -- became the key every later Docker package was checked
with. Now ``kazma_trust_docker_key`` keeps the key only when its one primary
key has the fingerprint Docker publishes (``9DC8 5822 9FC7 DD38 854A E2D8 8D81
803C 0EBF CD88``), checks a key a second run finds, and refuses with a line
setup shows.

The script's own functions run here under bash with stand-in ``curl`` and
``gpg`` (the stand-in gpg lists a key file that already holds gpg's colon
listing), as ``tests/test_engine_clean_shutdown.py`` runs the engine's other
scripts. The old lines are the negative control: they keep any key.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from kazma_core import container_engine_setup as ces

from tests.test_engine_clean_shutdown import _bash, _posix

DOCKER = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
OTHER = "0123456789ABCDEF0123456789ABCDEF01234567"
needs_bash = pytest.mark.skipif(_bash() is None, reason="bash is not on this machine")


def test_docker_s_published_fingerprint_is_the_one_trusted() -> None:
    script = ces._asset("setup-engine.sh")
    assert f"DOCKER_KEY_FINGERPRINT={DOCKER}\n" in script
    assert DOCKER == "9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88".replace(" ", "")
    assert "\r" not in script


def test_every_apt_key_the_script_takes_goes_through_the_check() -> None:
    """Nothing else in the script writes into the keyrings folder, and apt is
    pointed at Docker's repository only after the key was checked."""
    script = ces._asset("setup-engine.sh")
    body = script.replace(_functions(script), "")
    assert "kazma_trust_docker_key /etc/apt/keyrings\n" in body
    assert body.index("kazma_trust_docker_key /etc/apt/keyrings") < body.index("signed-by=/etc/apt/keyrings/docker.asc")
    assert not re.search(r"-o\s+/etc/apt/keyrings", body), "a key downloaded straight into the keyrings"
    assert "gnupg" in body, "gpg reads the key's fingerprint"


def _functions(script: str) -> str:
    match = re.search(r"DOCKER_KEY_FINGERPRINT=.*?\nkazma_trust_docker_key\(\) \{\n.*?\n\}\n", script, re.S)
    assert match, "the key check is not in setup-engine.sh"
    return match.group(0)


def _listing(*fingerprints: str) -> str:
    """A key file as the stand-in gpg lists it: gpg's colon format."""
    lines = []
    for fpr in fingerprints:
        lines += ["pub:-:4096:1:8D81803C0EBFCD88:1487788586:::-:::scESA::::::23::0:",
                  f"fpr:::::::::{fpr}:",
                  "sub:-:4096:1:7EA0A9C3F273FCD8:1487792064::::::s::::::23:",
                  "fpr:::::::::D3306A018370199E527AE7997EA0A9C3F273FCD8:"]
    return "\n".join(lines) + "\n"


@pytest.fixture
def engine(tmp_path):
    """The script's key check, stand-in curl and gpg, a keyrings folder."""
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir()
    served = tmp_path / "served.key"
    (bin_dir / "curl").write_text(
        "#!/bin/bash\n"
        f'echo "curl $*" >> "{_posix(log)}"\n'
        f'[ -f "{_posix(served)}" ] || exit 22\n'
        'while [ "$#" -gt 0 ]; do if [ "$1" = "-o" ]; then out="$2"; fi; shift; done\n'
        f'cp "{_posix(served)}" "$out"\n', encoding="utf-8", newline="\n")
    (bin_dir / "gpg").write_text('#!/bin/bash\nfor last; do :; done\ncat "$last"\n', encoding="utf-8", newline="\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    keyrings = tmp_path / "keyrings"

    def run(old: bool = False) -> subprocess.CompletedProcess:
        script = tmp_path / "check.sh"
        if old:
            # The lines as they were.
            body = (f'install -m 0755 -d "{_posix(keyrings)}"\n'
                    f'curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "{_posix(keyrings)}/docker.asc"\n')
        else:
            body = _functions(ces._asset("setup-engine.sh")) + f'kazma_trust_docker_key "{_posix(keyrings)}"\n'
        script.write_text(f'set -euo pipefail\nexport PATH="{_posix(bin_dir)}:$PATH"\n' + body,
                          encoding="utf-8", newline="\n")
        return subprocess.run([_bash(), _posix(script)], capture_output=True, text=True, timeout=60, check=False)

    def calls() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    return Path(keyrings), served, run, calls


@needs_bash
def test_docker_s_key_is_kept(engine) -> None:
    keyrings, served, run, _calls = engine
    served.write_text(_listing(DOCKER), encoding="utf-8")
    done = run()
    assert done.returncode == 0, done.stderr
    assert (keyrings / "docker.asc").read_text(encoding="utf-8") == _listing(DOCKER)


@needs_bash
@pytest.mark.parametrize("fingerprints", [(OTHER,), (DOCKER, OTHER), (OTHER, DOCKER), ()])
def test_any_other_key_is_refused_and_nothing_is_kept(engine, fingerprints) -> None:
    keyrings, served, run, _calls = engine
    served.write_text(_listing(*fingerprints) if fingerprints else "not a key\n", encoding="utf-8")
    done = run()
    assert done.returncode != 0
    assert "KAZMA_ENGINE_ERROR" in done.stderr and DOCKER in done.stderr
    assert not (keyrings / "docker.asc").exists()


@needs_bash
def test_a_key_a_second_run_finds_is_checked_and_a_good_one_downloads_nothing(engine) -> None:
    keyrings, served, run, calls = engine
    keyrings.mkdir()
    (keyrings / "docker.asc").write_text(_listing(DOCKER), encoding="utf-8")
    assert run().returncode == 0 and calls() == []
    # A key replaced since: checked, fetched again, refused when still not Docker's.
    (keyrings / "docker.asc").write_text(_listing(OTHER), encoding="utf-8")
    served.write_text(_listing(OTHER), encoding="utf-8")
    assert run().returncode != 0 and not (keyrings / "docker.asc").exists()
    served.write_text(_listing(DOCKER), encoding="utf-8")
    assert run().returncode == 0 and (keyrings / "docker.asc").read_text(encoding="utf-8") == _listing(DOCKER)


@needs_bash
def test_a_download_that_fails_is_refused(engine) -> None:
    keyrings, _served, run, _calls = engine
    done = run()
    assert done.returncode != 0 and "could not be downloaded" in done.stderr
    assert not (keyrings / "docker.asc").exists()


@needs_bash
def test_negative_control_the_old_lines_trust_any_key(engine) -> None:
    keyrings, served, run, _calls = engine
    served.write_text(_listing(OTHER), encoding="utf-8")
    assert run(old=True).returncode == 0
    assert (keyrings / "docker.asc").read_text(encoding="utf-8") == _listing(OTHER)
