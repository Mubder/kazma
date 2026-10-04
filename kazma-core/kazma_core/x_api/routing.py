"""Explainable subject candidates; card order never resolves multiple targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kazma_core.x_api.subject_policy import literal_match, literal_spans

if TYPE_CHECKING:
    from kazma_core.x_api.stance import Subject


@dataclass(frozen=True)
class RoutingDecision:
    state: str
    selected: Subject | None = None
    candidates: tuple[dict[str, Any], ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "subject_id": self.selected.id if self.selected else "",
                "target": (self.selected.target or self.selected.id) if self.selected else "",
                "candidates": list(self.candidates), "reason": self.reason}


class AmbiguousSubjectError(ValueError):
    """An actual policy conflict, never a miss that permits voice fallback."""

    def __init__(self, decision: RoutingDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


def route_subject(text: str, subjects: tuple[Subject, ...]) -> RoutingDecision:
    specific, fallbacks, evidence, excluded = [], [], [], []
    for subject in subjects:
        hits = [alias for alias in (*subject.match, *subject.aliases) if literal_match(text, alias)]
        exclusion_hits = [alias for alias in subject.exclusions if literal_match(text, alias)]
        if exclusion_hits and (hits or subject.is_catch_all()):
            excluded.append({"subject_id": subject.id, "aliases": hits, "exclusions": exclusion_hits})
            continue
        if subject.is_catch_all():
            fallbacks.append(subject)
            continue
        if hits:
            specific.append(subject)
            evidence.append({"subject_id": subject.id, "target": subject.target or subject.id,
                             "aliases": hits, "spans": [{"alias": alias, **span} for alias in hits for span in literal_spans(text, alias)],
                             "side": subject.side, "revision": subject.revision})
    if excluded:
        return RoutingDecision("needs_review", candidates=tuple(excluded),
                               reason="A matching subject excludes this context; review it before drafting.")
    candidates = specific or fallbacks
    if len(candidates) > 1:
        return RoutingDecision("ambiguous", candidates=tuple(evidence),
                               reason="Multiple subject cards match; identify the primary target and review their positions.")
    if not candidates:
        return RoutingDecision("no_match", reason="No declared subject matched.")
    selected = candidates[0]
    if selected.scope or selected.exceptions:
        return RoutingDecision("needs_review", candidates=tuple(evidence),
                               reason="This subject has contextual scope or exceptions; verify applicability before selecting it.")
    if not selected.allow_draft:
        return RoutingDecision("rejected", candidates=tuple(evidence), reason="Drafting is disabled for this subject.")
    return RoutingDecision("ready", selected, tuple(evidence), "One unambiguous literal subject match.")
