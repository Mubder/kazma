"""A tool's structured arguments name their keys in the schema the model reads.

The schema of every tool is generated from its Python signature
(``kazma_core.agent.tool_schema``). ``list[dict]`` became "objects of any
keys": the model guessed the keys, and the engines dropped every key they did
not read -- live 2026-11-12, every slide title of a deck, sent as ``"title"``
to an engine reading ``"heading"``. Twelve parameters of ten tools had that
shape. A list of records is now a TypedDict (keys, types, descriptions), and
a mapping whose keys are data (a form's fields) says what its keys and
values are.

The gate walks every registered tool's schema: an object with no keys inside
a list is refused, and any other object with no keys needs a description.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

import pytest

from kazma_core.agent.tool_schema import _generate_schema, to_openai_strict_schema


def record_problems(schema: dict[str, Any]) -> list[str]:
    """Where a tool's schema leaves the model guessing keys."""
    problems: list[str] = []

    def walk(node: Any, where: str, in_list: bool) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and not node.get("properties"):
            if in_list:
                problems.append(f"{where}: a list of records that names no key (type it with a TypedDict)")
            elif not str(node.get("description") or "").strip():
                problems.append(f"{where}: a mapping that says nothing of its keys (describe it)")
        for key, sub in (node.get("properties") or {}).items():
            walk(sub, f"{where}.{key}", False)
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{where}[]", True)
        for sub in node.get("anyOf") or []:
            walk(sub, where, in_list)

    for name, sub in (schema.get("properties") or {}).items():
        walk(sub, name, False)
    return problems


def test_every_registered_tool_names_the_keys_it_takes():
    pytest.importorskip("kazma_core.agent.tool_builtins")
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    assert len(registry._tools) > 100, "the gate read almost no tools; it would be decoration"
    found = [f"{name}: {problem}"
             for name, tool in sorted(registry._tools.items())
             for problem in record_problems(tool.input_schema or {})]
    assert not found, "\n".join(found)


async def _old_generate_pptx(title: str, slides: list[dict[str, Any]]) -> str:
    """Generate a verified PPTX through the isolated renderer worker."""
    return title


async def _old_pdf_fill_form(file_path: str, fields: dict[str, str]) -> str:
    """Fill validated AcroForm fields through the isolated mutation worker."""
    return file_path


def test_negative_control_the_old_signatures_fail():
    assert record_problems(_generate_schema(_old_generate_pptx)) == [
        "slides[]: a list of records that names no key (type it with a TypedDict)"]
    assert record_problems(_generate_schema(_old_pdf_fill_form)) == [
        "fields: a mapping that says nothing of its keys (describe it)"]


class _Point(TypedDict):
    """A point on the page."""

    x: Annotated[int, "across, from the left"]
    y: int


class _Shape(TypedDict, total=False):
    label: Annotated[str, "what the shape says"]
    points: list[_Point]
    weight: float | list[float]


async def _draw(shapes: list[_Shape], scale: float = 1.0) -> str:
    """Draw shapes.

    shapes: the shapes, in drawing order
    """
    return ""


def test_a_typeddict_becomes_a_closed_object_naming_its_keys():
    schema = _generate_schema(_draw)
    shape = schema["properties"]["shapes"]["items"]
    assert schema["properties"]["shapes"]["description"] == "the shapes, in drawing order"
    assert shape["type"] == "object" and shape["additionalProperties"] is False
    assert set(shape["properties"]) == {"label", "points", "weight"}
    assert "required" not in shape, "total=False: every key is optional"
    assert shape["properties"]["label"] == {"type": "string", "description": "what the shape says"}
    assert shape["properties"]["weight"] == {
        "anyOf": [{"type": "number"}, {"type": "array", "items": {"type": "number"}}]}
    point = shape["properties"]["points"]["items"]
    assert point["required"] == ["x", "y"] and point["description"] == "A point on the page."
    assert point["properties"]["x"] == {"type": "integer", "description": "across, from the left"}
    assert record_problems(schema) == []
    strict, ok = to_openai_strict_schema(schema)
    assert ok, "a record is OpenAI-strict compatible"
    assert strict["properties"]["shapes"]["items"]["required"] == ["label", "points", "weight"]
