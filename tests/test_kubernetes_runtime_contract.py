"""The deployment keeps one runtime and its full mutable state together."""
from __future__ import annotations

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
    assert app["volumeMounts"] == [{"name": "state", "mountPath": "/state"}]
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
