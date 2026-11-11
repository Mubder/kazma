"""The deployment keeps one runtime and its full mutable state together."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_stateful_runtime_ownership_storage_and_probe_contract():
    resources = list(yaml.safe_load_all((ROOT / "deploy/kubernetes/runtime.yaml").read_text()))
    sts = next(item for item in resources if item["kind"] == "StatefulSet")
    assert sts["spec"]["replicas"] == 1
    assert not any(item["kind"] == "HorizontalPodAutoscaler" for item in resources)
    claim = sts["spec"]["volumeClaimTemplates"][0]["spec"]
    assert claim["accessModes"] == ["ReadWriteOncePod"]
    assert "REPLACE" in claim["storageClassName"]
    pod = sts["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    app = pod["containers"][0]
    env = {item["name"]: item.get("value") for item in app["env"]}
    assert env["KAZMA_RUNTIME_HA"] == "1"
    assert env["KAZMA_HOST_SHELL"] == "0"
    assert env["KAZMA_AUTO_MIGRATE"] == "0"
    for name in ("KAZMA_DATA_DIR", "KAZMA_VECTOR_PATH", "KAZMA_USER_HOME", "KAZMA_SKILLS_HOME"):
        assert env[name].startswith("/state/")
    assert {"name": "state", "mountPath": "/state"} in app["volumeMounts"]
    # Everything else it mounts is a scratch emptyDir: no second claim, no host path.
    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    for mount in app["volumeMounts"]:
        if mount["name"] != "state":
            assert set(volumes[mount["name"]]) == {"name", "emptyDir"}, mount
    assert app["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert app["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert app["readinessProbe"]["timeoutSeconds"] >= 6
    from kazma_ui.app import _SHUTDOWN_CEILING_SECONDS

    args = app["args"]
    budget = int(args[args.index("--timeout-graceful-shutdown") + 1])
    assert _SHUTDOWN_CEILING_SECONDS < budget < pod["terminationGracePeriodSeconds"]
    assert pod["securityContext"]["runAsUser"] == pod["securityContext"]["fsGroup"] == 10001
    assert "useradd -r -u 10001" in (ROOT / "Dockerfile").read_text()


def _deployment_env_names() -> dict[str, list[str]]:
    import re

    files = [*(ROOT / "deploy/kubernetes").glob("*.yaml"), *ROOT.glob("docker-compose*.yml"), *ROOT.glob("Dockerfile*")]
    found: dict[str, list[str]] = {}
    for path in files:
        for name in sorted(set(re.findall(r"\bKAZMA_[A-Z0-9_]+\b", path.read_text(encoding="utf-8")))):
            found.setdefault(name, []).append(path.name)
    return found


def _product_source() -> str:
    roots = [*ROOT.glob("kazma-*/kazma_*"), ROOT / "scripts"]
    chunks = [(ROOT / "serve.py").read_text(encoding="utf-8", errors="ignore")]
    for base in roots:
        for path in base.rglob("*.py"):
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    # The container entrypoint reads some of them (KAZMA_AUTO_MIGRATE).
    chunks += [path.read_text(encoding="utf-8", errors="ignore") for path in (ROOT / "scripts").glob("*.sh")]
    return "\n".join(chunks)


def _unread(names, source: str) -> list[str]:
    return sorted(name for name in names if name not in source)


def test_every_kazma_variable_a_deployment_sets_is_read_by_the_product():
    """KAZMA_ALLOW_HOST_SHELL sat in the manifest for months and nothing read it:
    the host-shell switch is KAZMA_HOST_SHELL (safety/post_hitl.py)."""
    names = _deployment_env_names()
    assert "KAZMA_HOST_SHELL" in names, "the instrument found no variables"
    unread = _unread(names, _product_source())
    assert not unread, {name: names[name] for name in unread}


def test_negative_control_the_dead_name_is_unread():
    assert _unread(["KAZMA_ALLOW_HOST_SHELL"], _product_source()) == ["KAZMA_ALLOW_HOST_SHELL"]


# -- a read-only root filesystem (2026-11-11) ---------------------------------------------
#
# readOnlyRootFilesystem is set on every container, and every place Kazma
# writes resolves inside a declared writable mount (the state volume or an
# emptyDir). The write locations are resolved by Kazma's own code, run in a
# child process under the container's environment (the manifest's env, its
# working directory /app), with each absolute path mapped under a private
# folder: every zero-argument path function of ``kazma_core.paths`` (a new one
# is covered the day it is added), the temp folder (``tempfile``, which
# SQLite's and LibreOffice's scratch follow), the home and the caches under it,
# the Hugging Face cache, the env file a generated key is kept in, and a shell
# tool's private home. What the image's entrypoint writes for ClamAV
# (freshclam) is declared below. Not verified here: a cluster (no kubectl run
# in CI); a write the product builds from the working directory by hand.

#: Path functions of kazma_core.paths that are not places Kazma writes.
_NOT_WRITE_LOCATIONS = {
    "get_project_root": "where the code is: read-only in a container",
    "installed_project_root": "where the code is: read-only in a container",
    "legacy_user_home": "a read fallback for ~/.kazma; new code never writes there",
    "pin_project_root": "takes a path",
    "chat_spool_db": "takes a path (beside the chat store, which is under data)",
    "migrate_legacy_user_home": "moves a folder, never at a container's boot (KAZMA_USER_HOME is set)",
    "merge_legacy_hub_if_empty": "copies from the legacy home, same",
}

#: Written by the image's entrypoint (freshclam --daemon), not by Kazma's code.
_IMAGE_WRITES = {
    "/var/lib/clamav": "freshclam's signature database (DatabaseDirectory)",
    "/var/log/clamav": "freshclam's log (UpdateLogFile)",
    "/run/clamav": "freshclam's pid file",
}

_RESOLVE = textwrap.dedent('''
    import inspect, json, os, sys, tempfile
    from pathlib import Path
    root = Path(sys.argv[1]).resolve()
    skip = set(json.loads(sys.argv[2]))
    if os.name == "nt":  # Path.home() reads USERPROFILE there, HOME elsewhere
        os.environ["USERPROFILE"] = os.environ["HOME"]
    from kazma_core import paths
    found = {}
    for name, fn in inspect.getmembers(paths, inspect.isfunction):
        if name.startswith("_") or fn.__module__ != paths.__name__ or name in skip:
            continue
        if any(p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
               for p in inspect.signature(fn).parameters.values()):
            found[name] = "TAKES-ARGUMENTS"
            continue
        found[name] = str(fn())
    found["tempfile.gettempdir"] = tempfile.gettempdir()
    found["home"] = str(Path.home())
    found["home/.cache"] = str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"))
    found["home/.config"] = str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"))
    found["huggingface"] = os.path.expanduser(os.environ.get("HF_HOME") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or "~/.cache", "huggingface"))
    from kazma_core.env_files import _install_env_file
    found["env_files._install_env_file"] = str(_install_env_file())
    from kazma_core.safety.post_hitl import _tool_home_for
    found["post_hitl._tool_home_for"] = str(_tool_home_for(str(paths.data_dir() / "workspace")))
    from kazma_core.agent_skills.discovery import skills_home
    found["agent_skills.skills_home"] = str(skills_home())
    from kazma_core.documents.ocr.neural_models import model_directory
    found["neural_models.model_directory"] = str(model_directory())
    print(json.dumps({"root": str(root), "found": found}))
''')


def _runtime() -> tuple[dict, dict]:
    resources = list(yaml.safe_load_all((ROOT / "deploy/kubernetes/runtime.yaml").read_text()))
    sts = next(item for item in resources if item["kind"] == "StatefulSet")
    pod = sts["spec"]["template"]["spec"]
    return pod, pod["containers"][0]


def _container_writes(app: dict, tmp_path: Path) -> dict[str, str]:
    """Kazma's write locations under *app*'s environment, as container paths."""
    root = tmp_path / "container"
    (root / "app").mkdir(parents=True)
    (root / "app" / "pyproject.toml").write_text("[project]\nname = 'stand-in'\n", encoding="utf-8")
    literal = {item["name"]: str(item["value"]) for item in app["env"] if "value" in item}

    def mapped(value: str) -> str:
        return str(root / value.lstrip("/")) if value.startswith("/") else value

    child = {k: v for k, v in os.environ.items()
             if not k.startswith(("KAZMA_", "HF_", "XDG_")) and k not in ("HOME", "TMPDIR", "TEMP", "TMP")}
    child.update({name: mapped(value) for name, value in literal.items()})
    for name in ("TMPDIR", "HOME"):  # gettempdir needs the folder to be there
        if name in literal:
            Path(mapped(literal[name])).mkdir(parents=True, exist_ok=True)
    child["TEMP"] = child["TMP"] = child.get("TMPDIR", "")
    child["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    done = subprocess.run([sys.executable, "-c", _RESOLVE, str(root), json.dumps(sorted(_NOT_WRITE_LOCATIONS))],
                          cwd=root / "app", env=child, capture_output=True, text=True, timeout=180, check=False)
    assert done.returncode == 0, done.stderr[-3000:]
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    prefix = os.path.normcase(answer["root"])
    out = {}
    for name, where in answer["found"].items():
        assert where != "TAKES-ARGUMENTS", f"paths.{name} takes arguments: declare it in _NOT_WRITE_LOCATIONS"
        norm = os.path.normcase(os.path.abspath(where))
        assert norm.startswith(prefix), f"{name} resolved outside the container: {where}"
        out[name] = "/" + where[len(answer["root"]):].replace("\\", "/").strip("/")
    return out


def _writable(app: dict) -> list[str]:
    return [m["mountPath"].rstrip("/") for m in app["volumeMounts"] if not m.get("readOnly")]


def _outside(locations: dict[str, str], mounts: list[str]) -> dict[str, str]:
    def inside(path: str) -> bool:
        return any(path == m or path.startswith(m + "/") for m in mounts)

    return {name: path for name, path in locations.items() if not inside(path)}


def test_the_root_filesystem_is_read_only_in_every_container():
    pod, _ = _runtime()
    for container in [*pod.get("initContainers", []), *pod["containers"]]:
        ctx = container.get("securityContext") or {}
        assert ctx.get("readOnlyRootFilesystem") is True, container["name"]
        assert ctx.get("allowPrivilegeEscalation") is False, container["name"]


def test_every_place_kazma_writes_is_a_writable_mount(tmp_path):
    _, app = _runtime()
    locations = _container_writes(app, tmp_path)
    assert "data_dir" in locations and "tempfile.gettempdir" in locations, "the instrument found nothing"
    locations.update({f"image:{path}": path for path in _IMAGE_WRITES})
    assert not _outside(locations, _writable(app)), _outside(locations, _writable(app))
    # The meaning-search model and the env file outlive the pod: on the volume.
    assert locations["huggingface"].startswith("/state/")
    assert locations["env_files._install_env_file"].startswith("/state/")


def test_negative_control_a_write_outside_every_mount_is_found(tmp_path):
    _, app = _runtime()
    locations = _container_writes(app, tmp_path)
    planted = dict(locations, planted="/app/kazma-data/settings.db")
    assert _outside(planted, _writable(app)) == {"planted": "/app/kazma-data/settings.db"}
    # And the manifest without its emptyDirs leaves the temp folder and the
    # home on the read-only root.
    bare = dict(app, volumeMounts=[{"name": "state", "mountPath": "/state"}])
    missed = _outside(locations, _writable(bare))
    assert {"tempfile.gettempdir", "home"} <= set(missed), missed
    # Without KAZMA_ENV_FILE, a generated key would go to /app/.env.
    without = dict(app, env=[e for e in app["env"] if e["name"] != "KAZMA_ENV_FILE"])
    assert _container_writes(without, tmp_path / "bare")["env_files._install_env_file"] == "/app/.env"