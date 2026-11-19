"""Kazma's container engine reads Ubuntu's archive over HTTPS (2026-11-19).

The first fresh install after the owner's PC reset failed at "install Docker
Engine in it": ``apt-get update`` in the kazma-engine distro refused
``noble-updates/universe/binary-amd64/Packages`` with "Hash Sum mismatch".
archive.ubuntu.com is served through a CDN, and over plain HTTP the network
on the way handed back the same wrong 1,716,308 bytes on every try -- by
hash, by name and with a query string, cached or not -- while the same URL
over HTTPS gave the right file. The step's three tries, 15 s apart, could not
outlast it. apt checks every list against Ubuntu's signature, so nothing was
installed from the bad copy; it simply could not go on.

``kazma_https_sources`` turns every ``http://`` source in apt's lists into
``https://`` before the first ``apt-get update``, once the distro has CA
certificates (apt could not reach an https source without them). The
function runs here under bash on a copy of the image's own lists; the script
without it is the negative control.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from kazma_core import container_engine_setup as ces

from tests.test_engine_clean_shutdown import _bash, _posix

needs_bash = pytest.mark.skipif(_bash() is None, reason="bash is not on this machine")

# The Ubuntu 24.04 WSL image's own lists (deb822), as the drill VM's held them.
UBUNTU_SOURCES = """\
Types: deb
URIs: http://archive.ubuntu.com/ubuntu
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu
Suites: noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""

SOURCES_LIST = """\
# Ubuntu sources have moved to /etc/apt/sources.list.d/ubuntu.sources
# see http://help.ubuntu.com/community/UpgradeNotes
deb http://archive.ubuntu.com/ubuntu noble main
deb [arch=amd64 signed-by=/etc/apt/keyrings/x.asc] http://mirror.example/ubuntu noble main
deb-src http://archive.ubuntu.com/ubuntu noble main
"""

DOCKER_LIST = ("deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] "
               "https://download.docker.com/linux/ubuntu noble stable\n")


def _function(script: str) -> str:
    match = re.search(r"\nkazma_https_sources\(\) \{\n.*?\n\}\n", script, re.S)
    assert match, "kazma_https_sources is not in setup-engine.sh"
    return match.group(0)


def _http_sources_left(script: str) -> str | None:
    """Why ``script`` would reach Ubuntu's archive over plain HTTP, or None."""
    body = script.replace(_function(script), "") if "kazma_https_sources()" in script else script
    first_update = body.find("apt-get update")
    if first_update < 0:
        return "the script runs no apt-get update"
    call = body.find("kazma_https_sources /etc/apt\n")
    if call < 0:
        return "apt's sources are never switched to HTTPS"
    if call > first_update:
        return "apt-get update runs before apt's sources are switched to HTTPS"
    return None


def test_the_sources_are_switched_before_the_first_update() -> None:
    script = ces._asset("setup-engine.sh")
    assert _http_sources_left(script) is None
    assert "\r" not in script


def test_control_the_script_before_the_fix_is_caught() -> None:
    """The negative control: the script without the call (as it was), and
    with the call after the first update, both reach the archive over HTTP."""
    script = ces._asset("setup-engine.sh")
    without = script.replace("kazma_https_sources /etc/apt\n", "")
    assert _http_sources_left(without) == "apt's sources are never switched to HTTPS"
    late = without.replace("apt-get upgrade -y -q\n", "apt-get upgrade -y -q\nkazma_https_sources /etc/apt\n", 1)
    assert _http_sources_left(late) == "apt-get update runs before apt's sources are switched to HTTPS"


@pytest.fixture
def apt_dir(tmp_path: Path) -> Path:
    d = tmp_path / "apt"
    (d / "sources.list.d").mkdir(parents=True)
    (d / "sources.list.d" / "ubuntu.sources").write_text(UBUNTU_SOURCES, encoding="utf-8", newline="\n")
    (d / "sources.list").write_text(SOURCES_LIST, encoding="utf-8", newline="\n")
    (d / "sources.list.d" / "docker.list").write_text(DOCKER_LIST, encoding="utf-8", newline="\n")
    return d


def _run(apt: Path, ca: Path) -> subprocess.CompletedProcess:
    script = "set -euo pipefail\n" + _function(ces._asset("setup-engine.sh")) + \
        f'kazma_https_sources "{_posix(apt)}" "{_posix(ca)}"\n'
    return subprocess.run([_bash(), "-s"], input=script, capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


def _read(apt: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in [apt / "sources.list", *sorted((apt / "sources.list.d").iterdir())]}


@needs_bash
def test_every_source_becomes_https_and_nothing_else_changes(apt_dir: Path, tmp_path: Path) -> None:
    ca = tmp_path / "ca-certificates.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    done = _run(apt_dir, ca)
    assert done.returncode == 0, done.stderr
    after = _read(apt_dir)
    assert after["ubuntu.sources"] == UBUNTU_SOURCES.replace("URIs: http://", "URIs: https://")
    assert "https://archive.ubuntu.com/ubuntu" in after["ubuntu.sources"]
    assert "https://security.ubuntu.com/ubuntu" in after["ubuntu.sources"]
    assert after["sources.list"] == (
        "# Ubuntu sources have moved to /etc/apt/sources.list.d/ubuntu.sources\n"
        "# see http://help.ubuntu.com/community/UpgradeNotes\n"
        "deb https://archive.ubuntu.com/ubuntu noble main\n"
        "deb [arch=amd64 signed-by=/etc/apt/keyrings/x.asc] https://mirror.example/ubuntu noble main\n"
        "deb-src https://archive.ubuntu.com/ubuntu noble main\n"), "a comment is left alone"
    assert after["docker.list"] == DOCKER_LIST
    again = _run(apt_dir, ca)
    assert again.returncode == 0 and _read(apt_dir) == after, "a second run changes nothing"


@needs_bash
def test_without_ca_certificates_nothing_is_changed(apt_dir: Path, tmp_path: Path) -> None:
    before = _read(apt_dir)
    done = _run(apt_dir, tmp_path / "no-such-ca.crt")
    assert done.returncode == 0, done.stderr
    assert "no CA certificates" in done.stderr
    assert _read(apt_dir) == before
