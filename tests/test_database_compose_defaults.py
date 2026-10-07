"""Compile deployment templates; never start a container or use install secrets."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kazma_core.security.child_env import tool_child_env

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = [
    ("docker-compose.postgres.yml", "POSTGRES_PASSWORD", "db"),
    ("deploy/docker-compose.neo4j.yml", "KAZMA_NEO4J_PASSWORD", "kazma-neo4j"),
]


@pytest.mark.parametrize("template,variable,service", TEMPLATES)
def test_database_templates_require_secret_and_bind_loopback(tmp_path, template, variable, service):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI unavailable")
    version = subprocess.run([docker, "compose", "version"], capture_output=True, timeout=10)
    if version.returncode:
        pytest.skip("Docker Compose CLI unavailable")
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    command = [docker, "compose", "--env-file", str(env_file), "-f", str(ROOT / template),
               "config", "--format", "json", "--no-env-resolution"]
    environment = tool_child_env()
    environment.pop(variable, None)
    refused = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert refused.returncode != 0
    assert variable in refused.stderr
    environment[variable] = "synthetic-strong-password"
    accepted = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert accepted.returncode == 0, accepted.stderr
    config = json.loads(accepted.stdout)
    assert all(p["host_ip"] == "127.0.0.1" for p in config["services"][service]["ports"])
    if service == "db":
        assert config["services"]["db"]["environment"]["POSTGRES_PASSWORD"] == environment[variable]
        assert environment[variable] in config["services"]["kazma"]["environment"]["KAZMA_DATABASE_URL"]
