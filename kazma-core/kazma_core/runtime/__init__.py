"""Runtime orchestration helpers (model rebind, process-wide live state).

Import from the module that defines a name -- ``kazma_core.runtime.model_switch``,
``.live_llm``, ``.turn_model`` -- never from this package: a re-export is a
second copy of the value, which a test's patch of the module does not reach
(AGENTS.md section 45). The package re-exported all of them until 2026-10-01;
nothing imported them from here.
"""
