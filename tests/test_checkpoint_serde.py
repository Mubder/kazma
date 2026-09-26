"""Every checkpointer uses Kazma's one strict serializer.

LangGraph's JsonPlusSerializer rebuilds typed values from a checkpoint by
calling the class the checkpoint names; its default is permissive (any
class). Kazma built savers in six places and passed its allowlist in two, so
the shared SQLite saver's posture depended on which component opened it first
(2026-09-26), and the list itself missed TaskStatus and RouteKind. See
kazma_core/checkpoint_serde.py.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import importlib
from pathlib import Path

import pytest

pytest.importorskip("langgraph.checkpoint.serde.jsonplus")

from kazma_core.checkpoint_serde import (  # noqa: E402
    KAZMA_MSGPACK_TYPES,
    kazma_checkpoint_serde,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_DIRS = [
    REPO_ROOT / "kazma-core" / "kazma_core",
    REPO_ROOT / "kazma-ui" / "kazma_ui",
    REPO_ROOT / "kazma-gateway" / "kazma_gateway",
    REPO_ROOT / "kazma-cli" / "kazma_cli",
    REPO_ROOT / "kazma-tui" / "kazma_tui",
    REPO_ROOT / "kazma-skills" / "kazma_skills",
]
_HOME = REPO_ROOT / "kazma-core" / "kazma_core" / "checkpoint_serde.py"

#: LangGraph's checkpoint savers. Each deserializes what it stored.
SAVERS = {
    "AsyncSqliteSaver", "SqliteSaver", "AsyncPostgresSaver", "PostgresSaver",
    "MemorySaver", "InMemorySaver",
}

#: The modules that define the types the supervisor writes into graph state.
STATE_TYPE_MODULES = ("kazma_core.agent.state", "kazma_core.agent.intent.types")


def _product_files() -> list[Path]:
    out: list[Path] = []
    for base in PRODUCT_DIRS:
        if base.is_dir():
            out.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return out


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def saver_calls_without_kazma_serde(source: str) -> list[int]:
    """Lines where a saver is built without serde=kazma_checkpoint_serde()."""
    bad: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _name(node.func) in SAVERS:
            serde = next((k.value for k in node.keywords if k.arg == "serde"), None)
            if not (isinstance(serde, ast.Call) and _name(serde.func) == "kazma_checkpoint_serde"):
                bad.append(node.lineno)
    return bad


def serializer_constructions(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _name(node.func) == "JsonPlusSerializer"
    ]


def test_every_saver_takes_the_kazma_serializer() -> None:
    offenders = []
    for path in _product_files():
        for line in saver_calls_without_kazma_serde(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}")
    assert not offenders, (
        "a checkpoint saver built without serde=kazma_checkpoint_serde() "
        f"deserializes with LangGraph's permissive default: {offenders}"
    )


def test_the_serializer_is_built_in_one_place() -> None:
    elsewhere = []
    for path in _product_files():
        if path == _HOME:
            continue
        for line in serializer_constructions(path.read_text(encoding="utf-8")):
            elsewhere.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}")
    assert not elsewhere, f"build it with kazma_checkpoint_serde(): {elsewhere}"
    assert serializer_constructions(_HOME.read_text(encoding="utf-8")), "the home lost it"


def test_negative_control_the_scan_sees_a_bare_saver() -> None:
    bare = "saver = AsyncSqliteSaver(conn)\n"
    other = "saver = AsyncPostgresSaver(conn=pool, serde=JsonPlusSerializer())\n"
    good = "saver = AsyncSqliteSaver(conn, serde=kazma_checkpoint_serde())\n"
    assert saver_calls_without_kazma_serde(bare) == [1]
    assert saver_calls_without_kazma_serde(other) == [1]
    assert saver_calls_without_kazma_serde(good) == []
    assert serializer_constructions(other) == [1]


def _state_enums() -> list[type[enum.Enum]]:
    found = []
    for name in STATE_TYPE_MODULES:
        module = importlib.import_module(name)
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, enum.Enum) and obj.__module__ == name:
                found.append(obj)
    return found


def test_every_state_enum_is_on_the_list() -> None:
    enums = _state_enums()
    assert len(enums) >= 4, enums  # the walk itself works
    missing = [(e.__module__, e.__name__) for e in enums
               if (e.__module__, e.__name__) not in KAZMA_MSGPACK_TYPES]
    assert not missing, f"add to KAZMA_MSGPACK_TYPES: {missing}"


@pytest.mark.parametrize("cls", _state_enums(), ids=lambda c: c.__name__)
def test_a_state_enum_comes_back_as_itself(cls) -> None:
    serde = kazma_checkpoint_serde()
    for member in cls:
        out = serde.loads_typed(serde.dumps_typed({"v": member}))["v"]
        assert type(out) is cls and out == member, (cls, member, type(out))


@dataclasses.dataclass
class _Probe:
    """A class no allowlist names. Building it counts."""

    note: str = ""
    built = 0

    def __post_init__(self) -> None:
        type(self).built += 1


def test_a_class_off_the_list_is_never_constructed() -> None:
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    blob = kazma_checkpoint_serde().dumps_typed({"v": _Probe("x")})
    before = _Probe.built
    out = kazma_checkpoint_serde().loads_typed(blob)["v"]
    assert _Probe.built == before and not isinstance(out, _Probe), out
    # Negative control: LangGraph's permissive mode (its default unless
    # LANGGRAPH_STRICT_MSGPACK is set) builds whatever the blob names.
    rebuilt = JsonPlusSerializer(allowed_msgpack_modules=True).loads_typed(blob)["v"]
    assert isinstance(rebuilt, _Probe) and _Probe.built == before + 1
