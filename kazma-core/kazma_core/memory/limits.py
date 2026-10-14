"""One set of length limits for what is written into memory (Memory audit M36).

The edit route capped a fact at 200/120/4000 characters while
``mutate_belief`` -- the one writer every other path uses (the link route,
the agent's tools, extraction) -- capped nothing, and graph-group names were
unbounded. A value past a limit is refused, never cut: a cut value is a
different fact.
"""

from __future__ import annotations

__all__ = ["SUBJECT_MAX", "PREDICATE_MAX", "OBJECT_MAX", "NAME_MAX", "too_long"]

SUBJECT_MAX = 200
PREDICATE_MAX = 120
OBJECT_MAX = 4000
#: An entity's or a graph group's display name.
NAME_MAX = 120


def too_long(*, subject: str = "", predicate: str = "", obj: str = "", name: str = "") -> str | None:
    """The first field past its limit (``"subject"``, ...), or None."""
    for field, value, limit in (("subject", subject, SUBJECT_MAX), ("predicate", predicate, PREDICATE_MAX),
                                ("object", obj, OBJECT_MAX), ("name", name, NAME_MAX)):
        if len(value or "") > limit:
            return field
    return None
