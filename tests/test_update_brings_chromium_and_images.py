"""What new packages need outside the environment reaches the install.

Two gaps of ``kazma update`` (2026-11-13):

* A Playwright release launches one Chromium build. The update installed a
  new Playwright and never downloaded its Chromium, and the check that said
  "installed" (``browser_isolation.chromium_installed``) took any
  ``chromium-*`` folder, so the old build's folder kept saying so while every
  launch failed. Playwright was held at 1.64 for it.
* The compose files pin each container image by version and digest, and a
  new pin reached the file and never the running container: the owner's
  SearXNG still ran ``searxng:latest``. And compose names a project after its
  file's folder, so a checkout and an install both in a folder called
  ``kazma`` share one: a refresh by project alone would have recreated the
  live install's container from the checkout's file.

Each case drives the real code with fakes for the programs; each negative
control is the old rule.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core import container_services as cs
from kazma_core.web_acquire import browser_isolation as bi

# -- which Chromium build is installed ---------------------------------------------------


def _browsers(tmp_path: Path, monkeypatch, *folders: str) -> Path:
    root = tmp_path / "ms-playwright"
    root.mkdir()
    for name in folders:
        (root / name).mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(root))
    return root


def test_only_the_build_the_installed_playwright_launches_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "_chromium_revision", lambda: "1260")
    root = _browsers(tmp_path, monkeypatch, "chromium-1248", "chromium_headless_shell-1248")
    assert bi.chromium_was_installed() is True
    assert bi.chromium_installed() is False, "the previous Playwright's build is not this one's"
    (root / "chromium-1260").mkdir()
    assert bi.chromium_installed() is False, "headless runs need the headless shell of the same build"
    (root / "chromium_headless_shell-1260").mkdir()
    assert bi.chromium_installed() is True


def test_negative_control_any_build_counted_as_installed(tmp_path, monkeypatch):
    _browsers(tmp_path, monkeypatch, "chromium-1248", "chromium_headless_shell-1248")
    monkeypatch.setattr(bi, "_chromium_revision", lambda: "1260")
    old_rule = any(any(root.glob("chromium-*")) for root in bi._browser_roots() if root.is_dir())
    assert old_rule is True and bi.chromium_installed() is False


def test_the_revision_is_read_from_playwright_s_own_browsers_json(tmp_path, monkeypatch):
    package = tmp_path / "site" / "playwright"
    (package / "driver" / "package").mkdir(parents=True)
    (package / "driver" / "package" / "browsers.json").write_text(json.dumps({"browsers": [
        {"name": "chromium", "revision": "1260", "installByDefault": True},
        {"name": "chromium-headless-shell", "revision": "1260", "installByDefault": True},
        {"name": "firefox", "revision": "1555", "installByDefault": True},
    ]}), encoding="utf-8")
    import importlib.util

    real = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "playwright":
            return SimpleNamespace(submodule_search_locations=[str(package)])
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    assert bi._chromium_revision() == "1260"
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == "playwright" else real(name, *a, **k))
    assert bi._chromium_revision() is None


def test_with_no_revision_to_read_any_build_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "_chromium_revision", lambda: None)
    _browsers(tmp_path, monkeypatch, "chromium-1248")
    assert bi.chromium_installed() is True


# -- kazma update downloads it -----------------------------------------------------------


class _Runs:
    """subprocess.run as the update calls it, answering by what was asked."""

    def __init__(self, probe: str, install_code: int = 0, images: str = "") -> None:
        self.probe, self.install_code, self.images = probe, install_code, images
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        assert kwargs.get("stdin") is subprocess.DEVNULL, "every program the update starts gets no input"
        if argv[1:3] == ["-c", argv[2]] and "chromium_was_installed" in argv[2]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{self.probe}\n", stderr="")
        if argv[1:3] == ["-c", argv[2]] and "refresh_images" in argv[2]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.images, stderr="")
        if argv[1:5] == ["-m", "playwright", "install", "chromium"]:
            return subprocess.CompletedProcess(argv, self.install_code, stdout="", stderr="download failed")
        raise AssertionError(f"unexpected program: {argv}")


@pytest.fixture()
def update(monkeypatch):
    from kazma_cli import update as module

    return module


def test_a_missing_build_is_downloaded_with_the_install_s_own_python(update, monkeypatch):
    runs = _Runs("missing")
    monkeypatch.setattr(update.subprocess, "run", runs)
    assert update._bring_chromium_along() is True
    assert runs.calls[-1] == [sys.executable, "-m", "playwright", "install", "chromium"]


@pytest.mark.parametrize("probe", ["current", "absent"])
def test_nothing_is_downloaded_when_current_or_never_set_up(update, monkeypatch, probe):
    runs = _Runs(probe)
    monkeypatch.setattr(update.subprocess, "run", runs)
    assert update._bring_chromium_along() is True
    assert len(runs.calls) == 1, "only the check ran"


def test_a_failed_download_is_said_with_the_command(update, monkeypatch, capsys):
    runs = _Runs("missing", install_code=1)
    monkeypatch.setattr(update.subprocess, "run", runs)
    assert update._bring_chromium_along() is False
    out = capsys.readouterr().out
    assert "download failed" in out and "-m playwright install chromium" in out


def test_the_probe_asks_the_one_rule(update):
    """The child asks browser_isolation, the rule the install and the canary ask."""
    assert "b.chromium_was_installed()" in update._CHROMIUM_PROBE
    assert "b.chromium_installed()" in update._CHROMIUM_PROBE


def test_the_update_brings_them_along_after_a_successful_install_only(update, monkeypatch):
    calls: list[str] = []

    class Hold:
        held = False

        def __init__(self, command): ...

        def stop_for_update(self):
            return True

        def resume_after_update(self, ok):
            calls.append("resumed")
            return True

    monkeypatch.setattr(update, "_ServerHold", Hold)
    monkeypatch.setattr(update, "_bring_along", lambda: calls.append("brought") or True)
    assert update._install_held("kazma update", lambda: True) == "done"
    assert calls == ["resumed", "brought"], "after Kazma serves again"
    calls.clear()
    assert update._install_held("kazma update", lambda: False) == "failed"
    assert calls == ["resumed"]


def test_container_outcomes_are_said(update, monkeypatch, capsys):
    runs = _Runs("current", images="SearXNG\tupdated\nNeo4j\tfailed: pull access denied\n")
    monkeypatch.setattr(update.subprocess, "run", runs)
    assert update._refresh_container_images() is False
    out = capsys.readouterr().out
    assert "SearXNG now runs the image its compose file pins" in out
    assert "Neo4j could not be moved to its pinned image: pull access denied" in out


# -- containers onto their pinned image ------------------------------------------------


def _install(tmp_path: Path, name: str = "kazma") -> Path:
    root = tmp_path / name
    (root / "deploy").mkdir(parents=True)
    (root / "docker-compose.yml").write_text(
        "services:\n  searxng:\n    image: searxng/searxng:2026.11.12@sha256:" + "a" * 64 + "\n", encoding="utf-8")
    (root / "deploy" / "docker-compose.neo4j.yml").write_text(
        "services:\n  kazma-neo4j:\n    image: neo4j:5.26.32@sha256:" + "b" * 64 + "\n", encoding="utf-8")
    return root


class _Docker:
    """docker inspect / compose up, answering from *containers*."""

    def __init__(self, containers: dict[str, dict]) -> None:
        self.containers = containers
        self.ups: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        assert kwargs.get("stdin") is subprocess.DEVNULL
        if argv[1] == "inspect":
            row = self.containers.get(argv[2])
            if row is None:
                return subprocess.CompletedProcess(argv, 1, stdout="[]", stderr=f"Error: No such object: {argv[2]}")
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([row]), stderr="")
        if argv[1] == "compose" and "up" in argv:
            self.ups.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(argv)


def _container(image: str, project: str, service: str, files: Path) -> dict:
    return {"Config": {"Image": image, "Labels": {
        "com.docker.compose.project": project, "com.docker.compose.service": service,
        "com.docker.compose.project.config_files": str(files)}}}


def test_a_set_up_container_on_an_old_image_is_recreated_through_compose(tmp_path):
    root = _install(tmp_path)
    docker = _Docker({"kazma-searxng": _container("docker.io/searxng/searxng:latest", "kazma", "searxng",
                                                  root / "docker-compose.yml")})
    outcomes = {state.svc.title: outcome for state, outcome in cs.refresh_images(root, root / ".env", "docker",
                                                                                run=docker)}
    assert outcomes == {"SearXNG": "updated", "Neo4j": "absent"}
    (up,) = docker.ups
    assert up[:2] == ["docker", "compose"] and up[-3:] == ["up", "-d", "searxng"]
    assert str(root / ".env") in up and "--profile" in up


def test_a_container_on_its_pinned_image_is_left(tmp_path):
    root = _install(tmp_path)
    pinned = cs._pinned_image(cs.by_part("private_search"), root)
    docker = _Docker({"kazma-searxng": _container(pinned, "kazma", "searxng", root / "docker-compose.yml")})
    assert [o for _s, o in cs.refresh_images(root, root / ".env", "docker", run=docker)] == ["current", "absent"]
    assert docker.ups == []


def test_another_install_s_container_of_the_same_project_is_never_touched(tmp_path):
    """The dev checkout and the live install are both in a folder called
    "kazma": the same compose project. The container was made from the other
    install's file."""
    checkout = _install(tmp_path / "dev")
    live = _install(tmp_path / "live")
    docker = _Docker({"kazma-searxng": _container("docker.io/searxng/searxng:latest", "kazma", "searxng",
                                                  live / "docker-compose.yml")})
    states = {s.svc.title: o for s, o in cs.refresh_images(checkout, checkout / ".env", "docker", run=docker)}
    assert states["SearXNG"] == "not_ours" and docker.ups == []


def test_negative_control_the_project_alone_takes_the_other_install_s_container(tmp_path, monkeypatch):
    checkout = _install(tmp_path / "dev")
    live = _install(tmp_path / "live")
    docker = _Docker({"kazma-searxng": _container("docker.io/searxng/searxng:latest", "kazma", "searxng",
                                                  live / "docker-compose.yml")})
    monkeypatch.setattr(cs, "_made_from", lambda files, svc, root: True)
    states = {s.svc.title: o for s, o in cs.refresh_images(checkout, checkout / ".env", "docker", run=docker)}
    assert states["SearXNG"] == "updated" and len(docker.ups) == 1


def test_an_owner_s_own_container_under_kazma_s_name_is_left(tmp_path):
    root = _install(tmp_path)
    docker = _Docker({"kazma-neo4j": {"Config": {"Image": "neo4j:5", "Labels": {}}}})
    states = {s.svc.title: o for s, o in cs.refresh_images(root, root / ".env", "docker", run=docker)}
    assert states["Neo4j"] == "not_ours" and docker.ups == []


def test_the_shipped_compose_files_pin_what_the_table_names():
    root = Path(__file__).resolve().parents[1]
    for svc in cs.SERVICES:
        image = cs._pinned_image(svc, root)
        assert image and "@sha256:" in image, svc.title
