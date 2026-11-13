"""A test that runs real containers keeps the developer's Docker settings; every
other test gets an empty docker config (2026-11-13).

``tests/conftest.py``'s ``_isolated_docker_config`` points ``DOCKER_CONFIG`` at
an empty folder so which engine a developer's machine uses never decides a
test. Its ``default`` context is the platform's own Docker address: on a
machine whose engine is Kazma's (no Docker Desktop) it answers nothing, and
every container test failed there ("could not be removed; slot quarantined":
no ``docker`` call reached an engine) while the sandbox's real run skipped
without a word. A test marked ``real_docker`` keeps the developer's settings.
The two tests are each other's control.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_an_unmarked_test_gets_an_empty_docker_config(tmp_path):
    assert Path(os.environ["DOCKER_CONFIG"]) == tmp_path / "docker-config"
    assert "DOCKER_CONTEXT" not in os.environ


@pytest.mark.real_docker
def test_a_real_docker_test_keeps_the_developers_settings(tmp_path):
    config = os.environ.get("DOCKER_CONFIG")
    assert config is None or tmp_path not in Path(config).parents
