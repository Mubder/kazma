"""Two metric families say what their names say (2026-11, FA-257).

- ``kazma_commitment_decisions_total`` was a COUNT(*) of the commitments
  table, which the GC trims, typed as a counter: it fell. It is now counted
  when the gate decides.
- ``kazma_hitl_gates_total`` was recorded by the web path's bridge only, so
  the swarm bus and pipeline gates (which write the registry directly) never
  counted, and the bridge counted a re-register of an existing gate as a
  transition. It is now recorded by the registry, once per real transition.
"""

from __future__ import annotations

import pytest

prometheus_client = pytest.importorskip("prometheus_client")


def _sample(name: str, **labels) -> float:
    value = prometheus_client.REGISTRY.get_sample_value(name, labels)
    return float(value or 0.0)


@pytest.fixture
def gates(tmp_path):
    from kazma_core.safety import hitl_gates as hg

    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    yield hg
    hg.set_db_path_for_tests(None)


def test_a_bus_gate_counts_each_transition_once(gates):
    before = {s: _sample("kazma_hitl_gates_total", state=s, mechanism="swarm_bus")
              for s in ("pending", "claimed", "settled")}
    row = gates.register_gate(gates.GateRow(gate_id="bus-1", thread_id="t", tool="file_write",
                                            mechanism="swarm_bus"))
    gates.register_gate(gates.GateRow(gate_id="bus-1", thread_id="t", tool="file_write",
                                      mechanism="swarm_bus"))  # the same pause again: no transition
    gates.claim_gate(row.gate_id, "approve", "owner")
    gates.claim_gate(row.gate_id, "approve", "owner")  # a reclaim: no transition
    gates.settle_gate(row.gate_id, "done")
    after = {s: _sample("kazma_hitl_gates_total", state=s, mechanism="swarm_bus")
             for s in ("pending", "claimed", "settled")}
    assert {s: after[s] - before[s] for s in after} == {"pending": 1, "claimed": 1, "settled": 1}


def test_the_bridge_no_longer_counts_on_its_own():
    """Negative control on the source: the web bridge counting too would count
    every chat gate twice."""
    from pathlib import Path

    bridge = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "hitl_gate_bridge.py"
    assert "record_hitl_gate(" not in bridge.read_text(encoding="utf-8")


def test_a_protected_key_deny_is_counted_when_made():
    from kazma_core.safety.commitment import authorize_effect

    before = _sample("kazma_commitment_decisions_total", decision="deny")
    decision = authorize_effect("config_save", {"key": "safety.hitl.enabled", "value": False},
                                thread_id="t-metrics", context={"source": "test"})
    assert decision.decision == "deny"
    assert _sample("kazma_commitment_decisions_total", decision="deny") - before == 1


def test_a_plain_allow_is_not_a_policy_decision():
    from kazma_core.safety.commitment import authorize_effect

    before = _sample("kazma_commitment_decisions_total", decision="allow")
    decision = authorize_effect("file_read", {"path": "README.md"}, context={"source": "test"})
    assert decision.decision == "allow" and not decision.commitment_id
    assert _sample("kazma_commitment_decisions_total", decision="allow") == before


def test_metrics_names_the_decisions_family_once_as_a_counter():
    """Two families of one name make the page unreadable to Prometheus: the
    table-derived lines are gone, the counter comes from the registry."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.safety.commitment import authorize_effect
    from kazma_ui.metrics import create_metrics_router

    authorize_effect("config_save", {"key": "safety.hitl.enabled", "value": False},
                     context={"source": "test"})  # so the family has a sample
    app = FastAPI()
    gateway = SimpleNamespace(metrics=SimpleNamespace(inbound_total=0, outbound_total=0, errors_total=0),
                              adapters={}, queue=SimpleNamespace(qsize=lambda: 0))
    app.include_router(create_metrics_router(gateway))
    body = TestClient(app).get("/metrics").text
    types = [line for line in body.splitlines() if line.startswith("# TYPE kazma_commitment_decisions_total")]
    assert types == ["# TYPE kazma_commitment_decisions_total counter"], types
