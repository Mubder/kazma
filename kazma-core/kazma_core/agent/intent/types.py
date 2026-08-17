from __future__ import annotations

from enum import StrEnum
from dataclasses import dataclass, field
from typing import Any


class RouteKind(StrEnum):
    EXECUTE = "execute"
    CONSTRAIN = "constrain"
    LOOP = "loop"


class ActKind(StrEnum):
    DOCUMENT_GENERATE = "document_generate"
    DOCUMENT_INTEL = "document_intel"
    RESEARCH = "research"
    RESEARCH_DEEP = "research_deep"
    SWARM = "swarm"
    CODE_EXEC = "code_exec"
    FILE_MGMT = "file_mgmt"
    ANALYSIS = "analysis"
    REMIND = "remind"
    GENERAL = "general"


EXECUTE_MIN = 0.86
TIER2_LOW = 0.35
TIER2_HIGH = 0.80
WEAK_KEYWORD = 0.45
HIGH_PRECISION = 0.86
NO_MATCH = 0.35


@dataclass(frozen=True)
class IntentAct:
    kind: str
    confidence: float
    slots: dict[str, Any] = field(default_factory=dict)
    source: str = "heuristic"


@dataclass(frozen=True)
class ResolvedFile:
    path: str
    filename: str
    mime: str = ""
    source: str = "attachment"


@dataclass(frozen=True)
class EntitySet:
    files: tuple[ResolvedFile, ...] = ()
    unresolved: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnDecision:
    focus: str
    acts: tuple[IntentAct, ...]
    entities: EntitySet
    route: RouteKind
    handler: str | None
    reason: str
    plan_note: str = ""
    source: str = "heuristic"

    @property
    def primary(self) -> IntentAct | None:
        non_gen = [a for a in self.acts if a.kind != ActKind.GENERAL]
        if not non_gen:
            return None
        return max(non_gen, key=lambda a: a.confidence)


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    message: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    escalate: bool = False
