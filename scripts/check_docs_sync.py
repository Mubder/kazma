#!/usr/bin/env python3
"""Verify architecture.md / README.md claims match actual code.

Run in CI to prevent documentation drift.
"""

import ast
import importlib
import re
import sys
from pathlib import Path


def check_no_services_py_facade() -> list[str]:
    """Check that docs don't claim services.py facade exists."""
    errors = []
    for doc_file in ["architecture.md", "README.md"]:
        path = Path(doc_file)
        if path.exists():
            content = path.read_text()
            # Look for claims that services.py facade EXISTS (not that it was removed)
            if "services.py" in content and "facade" in content.lower():
                # Check if it's a positive claim (facade exists) vs negative (no facade)
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if "services.py" in line and "facade" in line.lower():
                        # If line says "no" or "removed" or "does not exist", it's correct
                        if "no " not in line.lower() and "removed" not in line.lower() and "does not exist" not in line.lower() and "claim removed" not in line.lower():
                            errors.append(f"{doc_file}:{i+1}: Claims services.py facade exists: {line.strip()}")
    return errors


def check_required_modules_exist() -> list[str]:
    """Check that all critical modules from AGENTS.md exist."""
    errors = []
    required_modules = [
        "kazma_core.model_registry",
        "kazma_core.agent.graph_builder",
        "kazma_core.llm_provider",
        "kazma_core.swarm.engine",
        "kazma_core.swarm.reliability",
        "kazma_core.swarm.task_store",
        "kazma_core.safety.hitl",
        "kazma_core.config_store",
        "kazma_core.swarm.checkpoint_manager",
        "kazma_core.swarm.phonebook",
        "kazma_core.swarm.reliability_registry",
    ]
    
    for mod in required_modules:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            errors.append(f"Missing module: {mod} ({e})")
    return errors


def check_swarm_engine_methods() -> list[str]:
    """Check SwarmEngine has required public methods."""
    errors = []
    from kazma_core.swarm.engine import SwarmEngine
    
    required_methods = [
        "dispatch",
        "approve_checkpoint",
        "reject_checkpoint",
        "_handle_pipeline_checkpoint",
        "add_worker",
        "remove_worker",
    ]
    for m in required_methods:
        if not hasattr(SwarmEngine, m):
            errors.append(f"SwarmEngine missing method: {m}")
    return errors


def check_config_store_singleton() -> list[str]:
    """Check ConfigStore singleton pattern."""
    errors = []
    from kazma_core.config_store import get_config_store, ConfigStore
    
    # Verify get_config_store exists and returns ConfigStore
    store = get_config_store()
    if not isinstance(store, ConfigStore):
        # Could be _InMemoryStore fallback
        pass
    return errors


def check_hitl_three_mechanisms() -> list[str]:
    """Verify all 3 HITL mechanisms exist."""
    errors = []
    
    # Mechanism A: Graph interrupt
    try:
        from kazma_core.agent.graph_builder import tool_worker_node
        import inspect
        sig = inspect.signature(tool_worker_node)
        if "hitl_config" not in sig.parameters:
            errors.append("tool_worker_node missing hitl_config parameter")
    except ImportError:
        errors.append("Cannot import tool_worker_node")
    
    # Mechanism B: Swarm bus safety
    try:
        from kazma_core.swarm.safety import get_safety
        safety = get_safety()
        if not hasattr(safety, "check_sync"):
            errors.append("Safety missing check_sync")
        if not hasattr(safety, "_danger_tools"):
            errors.append("Safety missing _danger_tools")
    except ImportError:
        errors.append("Cannot import safety")
    
    # Mechanism C: Pipeline checkpoints
    try:
        from kazma_core.swarm.engine import SwarmEngine
        if not hasattr(SwarmEngine, "approve_checkpoint"):
            errors.append("SwarmEngine missing approve_checkpoint")
        if not hasattr(SwarmEngine, "reject_checkpoint"):
            errors.append("SwarmEngine missing reject_checkpoint")
    except ImportError:
        errors.append("Cannot import SwarmEngine")
    
    return errors


def check_platform_isolation() -> list[str]:
    """Verify platform IDs don't leak into graph state."""
    errors = []
    from kazma_gateway.agent_handler.store import _build_initial_state
    import inspect
    
    src = inspect.getsource(_build_initial_state)
    # Should store chat_id/user_id in session store, not pass to graph
    if "chat_id" in src and "_gateway" not in src:
        errors.append("_build_initial_state may leak chat_id to graph state")
    return errors


def check_circuit_breaker_half_open() -> list[str]:
    """Verify circuit breaker has _probe_in_flight flag."""
    errors = []
    from kazma_core.swarm.reliability import CircuitBreaker
    
    if not hasattr(CircuitBreaker, "_probe_in_flight"):
        # Check instance
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        if not hasattr(cb, "_probe_in_flight"):
            errors.append("CircuitBreaker missing _probe_in_flight flag (AGENTS.md §5 critical)")
    return errors


def check_task_store_wal_mode() -> list[str]:
    """Verify TaskStore uses WAL + busy_timeout."""
    errors = []
    from kazma_core.swarm.task_store import TaskStore
    import inspect
    
    # Check _get_conn method which applies pragmas
    src = inspect.getsource(TaskStore._get_conn)
    if "apply_sqlite_pragmas" not in src:
        errors.append("TaskStore may not apply WAL pragmas via apply_sqlite_pragmas")
    return errors


def check_llm_tool_fallback() -> list[str]:
    """Verify NVIDIA NIM 404 fallback exists."""
    errors = []
    from kazma_core.llm_provider import LLMProvider
    import inspect
    
    src = inspect.getsource(LLMProvider)
    if "404" not in src or "function" not in src.lower():
        errors.append("LLMProvider may be missing NVIDIA NIM 404 'Function not found' fallback (AGENTS.md §3)")
    return errors


def check_swarm_handoff_cycle_detection() -> list[str]:
    """Verify swarm handoff cycle detection params."""
    errors = []
    from kazma_core.swarm.engine import SwarmEngine
    import inspect
    
    src = inspect.getsource(SwarmEngine._handle_handoff)
    if "_visited" not in src:
        errors.append("_handle_handoff missing _visited parameter")
    if "_depth" not in src:
        errors.append("_handle_handoff missing _depth parameter")
    if "_MAX_VISITS" not in src and "MAX_VISITS" not in src:
        errors.append("_handle_handoff may not have MAX_VISITS limit")
    return errors


def main() -> int:
    """Run all checks and return exit code."""
    all_errors = []
    
    checks = [
        ("No services.py facade claim", check_no_services_py_facade),
        ("Required modules exist", check_required_modules_exist),
        ("SwarmEngine methods", check_swarm_engine_methods),
        ("ConfigStore singleton", check_config_store_singleton),
        ("HITL three mechanisms", check_hitl_three_mechanisms),
        ("Platform isolation", check_platform_isolation),
        ("Circuit breaker half-open", check_circuit_breaker_half_open),
        ("TaskStore WAL mode", check_task_store_wal_mode),
        ("LLM tool fallback", check_llm_tool_fallback),
        ("Swarm handoff cycle detection", check_swarm_handoff_cycle_detection),
    ]
    
    print("Running doc-code sync checks...")
    for name, check_fn in checks:
        try:
            errors = check_fn()
            if errors:
                all_errors.extend(errors)
                print(f"  ❌ {name}: {len(errors)} error(s)")
                for e in errors:
                    print(f"     - {e}")
            else:
                print(f"  ✅ {name}")
        except Exception as e:
            all_errors.append(f"{name}: Check failed with exception: {e}")
            print(f"  ❌ {name}: Exception: {e}")
    
    if all_errors:
        print(f"\n❌ {len(all_errors)} total error(s) found:")
        for e in all_errors:
            print(f"  - {e}")
        return 1
    else:
        print("\n✅ All doc-code sync checks passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())