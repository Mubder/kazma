"""Opt-in isolated runtimes (E2B Firecracker). Docker/local stay the default."""

from __future__ import annotations

from kazma_core.sandbox.e2b import e2b_available, e2b_enabled, run_python

__all__ = ["e2b_available", "e2b_enabled", "run_python"]
