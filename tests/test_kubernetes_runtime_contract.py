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
    assert env["KAZMA_ALLOW_HOST_SHELL"] == "0"
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
