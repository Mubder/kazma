"""Multi-agent pipeline topology engine.

Implements directed acyclic graph (DAG) task execution with specialized
worker stages: Researcher → Refiner → Builder → Validator.  Each stage
receives the previous stage's output as context.  The Refiner is a
middleman that captures collective output, synthesizes it, and delivers
a clean Markdown report card back to the active chat.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Refiner — synthesizes collective output ──────────────────────────────

async def _synthesize_refined_output(
    task: str,
    stages: list[Any],
    overall_status: str,
    total_ms: float,
) -> str:
    """Synthesize pipeline outputs via LLM Refiner.

    Sends aggregated worker outputs + REFINER_SYSTEM_PROMPT to the
    active model provider.  Returns the model's synthesized report.
    Falls back to formatted string if no provider is available.
    """
    completed_stages = [s for s in stages if getattr(s, "status", "") == "completed"]
    all_outputs = [getattr(s, "output", "") or "" for s in completed_stages]
    combined = "\n---\n".join(o.strip() for o in all_outputs if o.strip()) or "No output"

    # Build the Refiner prompt with context and raw outputs
    user_prompt = f"""Task: {task[:300]}\nStatus: {overall_status}\nDuration: {total_ms:.0f}ms\n\nRaw worker outputs:\n{combined[:4000]}"""

    try:
        from kazma_core.model_registry import get_model_registry
        registry = get_model_registry()
        provider = registry.get_client()
        if provider is not None:
            messages = [
                {"role": "system", "content": REFINER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            response = await provider.chat(messages)
            return response.content
    except Exception as exc:
        logger.warning("[Refiner] LLM call failed, using raw output: %s", exc)

    # Fallback: formatted raw output (no LLM available)
    lines: list[str] = []
    lines.append("## Swarm Report (raw)")
    lines.append("")
    lines.append(f"**Task:** {task[:200]}")
    lines.append(f"**Status:** {overall_status}")
    lines.append(f"**Duration:** {total_ms:.0f}ms")
    lines.append("")
    lines.append(combined[:2000])
    return "\n".join(lines)


# ── Pipeline Dataclasses ──────────────────────────────────────────────────

import asyncio
from enum import StrEnum


# ── Stage definitions ─────────────────────────────────────────────────────


class StageRole(StrEnum):
    """Semantic role of a pipeline stage."""

    RESEARCHER = "researcher"     # broad analysis, raw output
    REFINER = "refiner"            # normalize, condense, format
    BUILDER = "builder"            # implementation / execution
    VALIDATOR = "validator"        # quality check, reject/approve
    CUSTOM = "custom"              # user-defined stage


@dataclass(slots=True)
class PipelineStage:
    """A single stage in a multi-agent pipeline.

    Attributes:
        name:           Human-readable stage identifier.
        role:           Semantic role (researcher/refiner/builder/validator).
        worker_name:    Name of the worker from WorkerRegistry.
        system_prompt:  Instructions for the worker at this stage.
        depends_on:     Names of stages that must complete first.
    """

    name: str
    role: StageRole
    worker_name: str
    system_prompt: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"     # pending | running | completed | failed
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0

    def to_worker_entry(self) -> dict[str, Any]:
        """Return a dict suitable for WorkerRegistry registration."""
        expertise: list[str] = [str(self.role.value)]
        return {
            "name": self.name,
            "expertise": expertise,
            "roles": ["leaf"],
            "system_prompt": self.system_prompt,
        }


@dataclass
class PipelineResult:
    """Final output of a pipeline execution."""

    pipeline_id: str
    stages: list[PipelineStage]
    status: str            # "completed" | "failed" | "partial"
    final_output: str
    total_duration_ms: float = 0.0
    correlation_id: str = ""

    @property
    def succeeded_stages(self) -> list[PipelineStage]:
        return [s for s in self.stages if s.status == "completed"]

    @property
    def failed_stages(self) -> list[PipelineStage]:
        return [s for s in self.stages if s.status == "failed"]


# ── Refiner worker ─────────────────────────────────────────────────────────

REFINER_SYSTEM_PROMPT = """You are a Refiner — a middleman worker that intercepts raw,
verbose output from a Researcher and condenses it into a clean,
actionable payload for the Builder.

Your job:
1. Strip hallucinations, fluff, and redundant explanations.
2. Extract only the core facts, code snippets, and decisions.
3. Format the output as a structured payload:
   - Key findings (3-5 bullet points)
   - Code snippets (if any)
   - Recommended next action (1 sentence)
4. Never add new information.  Only distill what was provided.

Output format:
```
## Key Findings
- Point 1
- Point 2

## Code
```python
...
```

## Recommended Next Action
One sentence.
```
"""


class RefinerStage(PipelineStage):
    """Pre-built Refiner stage using the Refiner system prompt."""

    def __init__(
        self,
        name: str = "refiner",
        worker_name: str = "bridge",
        depends_on: list[str] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            role=StageRole.REFINER,
            worker_name=worker_name,
            system_prompt=REFINER_SYSTEM_PROMPT,
            depends_on=depends_on or [],
        )


# ── Standard pipeline topologies ───────────────────────────────────────────

# Research → Refine → Build → Validate
STANDARD_PIPELINE: list[dict[str, Any]] = [
    {
        "name": "researcher",
        "role": "researcher",
        "worker_name": "core",
        "system_prompt": "You are a Researcher. Analyze the task broadly and provide comprehensive findings.",
        "depends_on": [],
    },
    {
        "name": "refiner",
        "role": "refiner",
        "worker_name": "bridge",
        "system_prompt": REFINER_SYSTEM_PROMPT,
        "depends_on": ["researcher"],
    },
    {
        "name": "builder",
        "role": "builder",
        "worker_name": "core",
        "system_prompt": "You are a Builder. Implement the solution based on the refined research.",
        "depends_on": ["refiner"],
    },
    {
        "name": "validator",
        "role": "validator",
        "worker_name": "bridge",
        "system_prompt": (
            "You are a Validator. Check the Builder's output against the "
            "original task. Reject if wrong, approve if correct."
        ),
        "depends_on": ["builder"],
    },
]

# Quick pipeline — skip the refiner (two-stage)
QUICK_PIPELINE: list[dict[str, Any]] = [
    {
        "name": "researcher",
        "role": "researcher",
        "worker_name": "core",
        "system_prompt": "You are a Researcher. Provide a concise, actionable answer.",
        "depends_on": [],
    },
    {
        "name": "builder",
        "role": "builder",
        "worker_name": "core",
        "system_prompt": "You are a Builder. Implement based on the research above.",
        "depends_on": ["researcher"],
    },
]


# ── Pipeline execution engine ──────────────────────────────────────────────


class PipelineEngine:
    """Executes a DAG of pipeline stages via SwarmEngine.consult().

    Stages with satisfied dependencies run in parallel batches.
    Failed stages halt downstream stages by default.
    """

    def __init__(self, halt_on_failure: bool = True) -> None:
        self.halt_on_failure = halt_on_failure

    @staticmethod
    def from_standard(topo: list[dict[str, Any]] | None = None) -> list[PipelineStage]:
        """Build PipelineStage list from a topology definition."""
        stages: list[PipelineStage] = []
        for cfg in (topo or STANDARD_PIPELINE):
            stages.append(PipelineStage(
                name=cfg["name"],
                role=StageRole(cfg["role"]),
                worker_name=cfg["worker_name"],
                system_prompt=cfg.get("system_prompt", ""),
                depends_on=cfg.get("depends_on", []),
            ))
        return stages

    async def execute(
        self,
        stages: list[PipelineStage],
        task: str,
        correlation_id: str | None = None,
    ) -> PipelineResult:
        """Execute a pipeline of stages.

        Args:
            stages: Ordered pipeline stages.
            task: The initial task description.
            correlation_id: Trace ID for linking logs. Auto-generated if None.

        Returns:
            PipelineResult with all stage outputs and final synthesis.
        """
        cid = correlation_id or f"cid-{uuid.uuid4().hex[:12]}"
        started = __import__("time").perf_counter()
        from kazma_core.swarm.engine import get_swarm_engine
        from kazma_core.swarm.bus import get_message_bus

        engine = get_swarm_engine()
        bus = get_message_bus()
        stage_outputs: dict[str, str] = {}
        completed: set[str] = set()
        failed: set[str] = set()

        while len(completed | failed) < len(stages):
            # Find stages with all dependencies satisfied
            ready = [
                s for s in stages
                if s.status == "pending"
                and all(d in completed for d in s.depends_on)
                and (not self.halt_on_failure or not any(
                    d in failed for d in s.depends_on
                ))
            ]
            if not ready:
                # Check for deadlocked stages
                pending = [s for s in stages if s.status == "pending"]
                if pending and self.halt_on_failure:
                    for s in pending:
                        s.status = "failed"
                        s.error = "Upstream stage failed — halted"
                        failed.add(s.name)
                break

            # Build context from upstream outputs
            for stage in ready:
                context_parts: list[str] = [task]
                for dep in stage.depends_on:
                    if dep in stage_outputs:
                        context_parts.append(f"\n[Output from '{dep}']\n{stage_outputs[dep][:2000]}")
                context = "\n\n".join(context_parts)

                try:
                    stage_start = __import__("time").perf_counter()
                    stage.status = "running"

                    await bus.stream(
                        worker_name=stage.name,
                        worker_role=str(stage.role.value),
                        content=f"Pipeline stage starting: {stage.name}",
                        level="info",
                    )

                    if engine:
                        result = await engine.dispatch_by_name(stage.worker_name or str(stage.role.value), context)
                        stage.output = result.get("synthesis", "No output")
                    else:
                        # No engine available — try direct LLM call
                        try:
                            from kazma_core.model_registry import get_model_registry
                            provider = get_model_registry().get_client()
                            if provider:
                                messages = [{"role": "user", "content": str(context)}]
                                resp = await provider.chat(messages)
                                stage.output = resp.content
                            else:
                                stage.output = f"Error: No provider available — cannot process {stage.name}"
                        except Exception as exc:
                            stage.output = f"Error: {exc}"

                    stage.duration_ms = (__import__("time").perf_counter() - stage_start) * 1000
                    stage.status = "completed"
                    stage_outputs[stage.name] = stage.output
                    completed.add(stage.name)

                    await bus.report(
                        worker_name=stage.name,
                        worker_role=str(stage.role.value),
                        status="success",
                        output=stage.output,
                        duration_ms=stage.duration_ms,
                        task_id=f"{cid}:{stage.name}",
                    )

                except Exception as exc:
                    stage.status = "failed"
                    stage.error = str(exc)
                    failed.add(stage.name)
                    await bus.stream(
                        worker_name=stage.name,
                        worker_role=str(stage.role.value),
                        content=f"Pipeline stage FAILED: {exc}",
                        level="error",
                    )

        # Determine overall status
        if len(completed) == len(stages):
            overall_status = "completed"
        elif len(failed) == 0:
            overall_status = "partial"
        else:
            overall_status = "failed" if len(failed) >= len(stages) / 2 else "partial"

        # Build final output from the last completed stage
        final_output = ""
        for s in reversed(stages):
            if s.status == "completed":
                final_output = s.output
                break

        total_ms = (__import__("time").perf_counter() - started) * 1000

        # ── Self-improvement hook ───────────────────────────────────
        result = PipelineResult(
            pipeline_id=cid,
            stages=stages,
            status=overall_status,
            final_output=final_output or "No stages completed",
            total_duration_ms=total_ms,
            correlation_id=cid,
        )

        # After pipeline completes, trigger Soul evolution for each worker
        try:
            from kazma_core.skills.self_improvement import get_self_improvement

            si = get_self_improvement()
            for stage in stages:
                if stage.worker_name:
                    analysis = await si.analyze(
                        worker_name=stage.worker_name,
                        task=task,
                        stages=stages,
                        status=overall_status,
                    )
                    if analysis.get("action") == "mutate":
                        await si.apply_mutation(stage.worker_name, analysis["delta"])
        except Exception as exc:
            logger.debug("[PipelineEngine] Self-improvement hook failed: %s", exc)

        # ── Refiner — Synthesize collective output into Markdown card ──
        refined_output = await _synthesize_refined_output(task, stages, overall_status, total_ms)
        result.final_output = refined_output

        # ── Pipeline Logger — persist all stage outputs to SQLite ─────
        try:
            from kazma_core.swarm.memory.pipeline_logger import get_pipeline_logger
            plog = get_pipeline_logger()
            for stage in stages:
                plog.log_output(cid, stage.worker_name or stage.name, stage.name,
                                stage.output or "")
                plog.log_step(cid, stage.worker_name or stage.name, stage.name,
                              "info", "stage_" + (stage.status or "unknown"),
                              f"Stage {stage.name}: {stage.status} ({stage.duration_ms:.0f}ms)")
            plog.log_step(cid, "orchestrator", "pipeline", "info", "pipeline_complete",
                          f"Pipeline {cid}: {overall_status} in {total_ms:.0f}ms",
                          {"stages": len(stages), "completed": len([s for s in stages if s.status == "completed"])})
        except Exception as exc:
            logger.debug("[PipelineEngine] Logging hook failed: %s", exc)

        return result


# ── Convenience ────────────────────────────────────────────────────────────


async def run_standard_pipeline(
    task: str,
    worker_map: dict[str, str] | None = None,
) -> PipelineResult:
    """Run the standard 4-stage pipeline with optional worker overrides.

    Args:
        task: The task to execute.
        worker_map: Optional dict of stage_name → worker_name overrides.
                    e.g. {"builder": "ux"} to use UX worker for builder stage.

    Returns:
        PipelineResult.
    """
    stages = PipelineEngine.from_standard(STANDARD_PIPELINE)
    if worker_map:
        for s in stages:
            if s.name in worker_map:
                s.worker_name = worker_map[s.name]
    engine = PipelineEngine()
    return await engine.execute(stages, task)
