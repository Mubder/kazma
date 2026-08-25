"""JSON Schema generation from Python callables (extracted from tool_registry).

Object schemas always carry ``additionalProperties: false`` so models cannot
invent extra arguments. OpenAI ``strict: true`` (all properties required,
optionals as ``T | null``) is opt-in via ``KAZMA_STRICT_TOOLS=1`` — local /
Anthropic / Gemini endpoints often 400 on that flag.

``response_format`` plumbing lives on ``LLMProvider.chat``; this module only
builds the JSON-Schema body helpers.
"""

from __future__ import annotations

import copy
import inspect
import logging
import os
import types as _types
import typing as _typing
from collections.abc import Callable
from typing import Any, get_type_hints

logger = logging.getLogger(__name__)

_PY_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

# Swarm/HITL inject these into execute() args; they are not LLM schema fields.
_KEEP_EXEC_KEYS = frozenset({"task_id", "worker_name"})


def strict_tools_enabled() -> bool:
    """True when OpenAI-style ``function.strict`` should be stamped.

    Default **off**: ``strict: true`` plus all-properties-required is an
    OpenAI Structured Outputs contract. Anthropic, Gemini, and many local
    servers reject it. ``additionalProperties: false`` is always on.
    """
    raw = (os.environ.get("KAZMA_STRICT_TOOLS") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _python_type_to_json_schema(tp: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment.

    Handles:
      - Primitives (str, int, float, bool)
      - list[T] → {"type": "array", "items": ...}
      - dict[K, V] → {"type": "object", "additionalProperties": V-schema}
      - Optional[T] → T (nullable handled at parameter / strict-convert level)
      - Union[str, None] → T (same as Optional)
      - Union[A, B] → {"anyOf": [...]}
    """
    # Handle None type
    if tp is type(None):
        return {"type": "null"}

    # Direct primitive mapping
    if tp in _PY_TO_JSON:
        return {"type": _PY_TO_JSON[tp]}

    # Generic types (list[T], dict[K, V])
    origin = getattr(tp, "__origin__", None)

    if origin is list:
        args = getattr(tp, "__args__", ())
        if args:
            return {"type": "array", "items": _python_type_to_json_schema(args[0])}
        return {"type": "array"}

    if origin is dict:
        args = getattr(tp, "__args__", ())
        node: dict[str, Any] = {"type": "object"}
        if len(args) >= 2:
            node["additionalProperties"] = _python_type_to_json_schema(args[1])
        return node

    # Optional[X] = Union[X, None] or X | None (Python 3.10+)
    if origin is _typing.Union or isinstance(tp, _types.UnionType):
        args = getattr(tp, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_json_schema(non_none[0])
        if len(non_none) > 1:
            return {"anyOf": [_python_type_to_json_schema(a) for a in non_none]}

    # Fallback
    return {"type": "string"}


def _stamp_closed_objects(node: Any) -> None:
    """Set ``additionalProperties: false`` on closed object schemas (in place).

    A closed object is one with a ``properties`` map (the function-parameter
    object, nested dataclasses, etc.). Free-form ``dict[K, V]`` nodes have no
    ``properties`` — they keep ``additionalProperties`` as a value schema so
    callers can pass arbitrary keys.
    """
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        node.setdefault("additionalProperties", False)
        for sub in props.values():
            _stamp_closed_objects(sub)
    items = node.get("items")
    if isinstance(items, dict):
        _stamp_closed_objects(items)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in node.get(key) or []:
            _stamp_closed_objects(sub)
    addl = node.get("additionalProperties")
    if isinstance(addl, dict):
        _stamp_closed_objects(addl)


def _generate_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate an OpenAI-compatible JSON schema from a function signature.

    Inspects:
      - Parameter names and type hints
      - Default values (optional parameters)
      - Docstring for parameter descriptions

    The root object always has ``additionalProperties: false``. ``required``
    lists parameters without defaults (provider-safe; not OpenAI-strict).
    """
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception as _e:
        logger.debug("get_type_hints failed for %s: %s", getattr(func, "__name__", func), _e)
        hints = {}

    # Parse docstring for param descriptions
    param_descriptions: dict[str, str] = {}
    doc = inspect.getdoc(func) or ""
    for line in doc.split("\n"):
        line = line.strip()
        if ":" in line:
            # Handle "param_name: description" or "param_name (type): description"
            parts = line.split(":", 1)
            candidate = parts[0].strip().split("(")[0].strip().split(" ")[0].strip()
            if candidate and candidate in sig.parameters:
                param_descriptions[candidate] = parts[1].strip()

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        param_type = hints.get(name, str)
        schema_fragment = _python_type_to_json_schema(param_type)

        # Add description if found
        if name in param_descriptions:
            schema_fragment["description"] = param_descriptions[name]

        # Handle defaults
        if param.default is not inspect.Parameter.empty:
            schema_fragment["default"] = param.default
        else:
            required.append(name)

        properties[name] = schema_fragment

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    _stamp_closed_objects(schema)
    return schema


def _already_nullable(schema: dict[str, Any]) -> bool:
    t = schema.get("type")
    if t == "null" or (isinstance(t, list) and "null" in t):
        return True
    if "anyOf" in schema:
        return any(
            isinstance(x, dict) and x.get("type") == "null" for x in (schema.get("anyOf") or [])
        )
    return False


def _as_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap a property schema so OpenAI strict mode may emit JSON null."""
    core = {k: v for k, v in schema.items() if k != "default"}
    if _already_nullable(core):
        return core
    return {"anyOf": [core, {"type": "null"}]}


def _strict_walk(node: Any) -> bool:
    """Mutate *node* into OpenAI-strict form. False = incompatible (open dict)."""
    if not isinstance(node, dict):
        return True

    if node.get("type") == "array" and isinstance(node.get("items"), dict):
        if not _strict_walk(node["items"]):
            return False

    for key in ("anyOf", "oneOf", "allOf"):
        for sub in node.get(key) or []:
            if isinstance(sub, dict) and not _strict_walk(sub):
                return False

    is_object = node.get("type") == "object" or "properties" in node
    if not is_object:
        return True

    props = node.get("properties")
    if not isinstance(props, dict):
        # Free-form object (dict[K, V] or bare dict). OpenAI strict forbids
        # additionalProperties as a schema / true.
        if node.get("additionalProperties") is False:
            node.setdefault("properties", {})
            node["required"] = []
            return True
        return False

    orig_required = set(node.get("required") or [])
    for name, sub in list(props.items()):
        if not isinstance(sub, dict):
            continue
        if not _strict_walk(sub):
            return False
        if name not in orig_required:
            props[name] = _as_nullable(sub)
        elif "default" in sub:
            sub = dict(sub)
            sub.pop("default", None)
            props[name] = sub
    node["required"] = list(props.keys())
    node["additionalProperties"] = False
    return True


def to_openai_strict_schema(schema: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return ``(converted, compatible)`` for OpenAI Structured Outputs tools.

    Compatible schemas get every property listed in ``required``, optionals
    as ``anyOf: [T, {type: null}]``, and ``additionalProperties: false`` on
    every closed object. Open ``dict`` parameters make the schema
    incompatible — callers should skip ``strict: true`` for that tool.
    """
    cloned = copy.deepcopy(schema) if isinstance(schema, dict) else {"type": "object", "properties": {}}
    ok = _strict_walk(cloned)
    return cloned, ok


def apply_openai_strict_tools(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp ``function.strict = true`` on OpenAI-format tool defs that qualify.

    Mutates *definitions* in place. Tools with free-form ``dict`` parameters
    are left unstrict so the request does not 400.
    """
    for item in definitions:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if not isinstance(fn, dict):
            continue
        params = fn.get("parameters")
        if not isinstance(params, dict):
            continue
        converted, ok = to_openai_strict_schema(params)
        if ok:
            fn["parameters"] = converted
            fn["strict"] = True
    return definitions


def json_schema_response_format(
    name: str,
    schema: dict[str, Any],
    *,
    strict: bool = True,
    description: str = "",
) -> dict[str, Any]:
    """Build an OpenAI ``response_format`` body for structured JSON.

    Pass the result to ``LLMProvider.chat(..., response_format=...)``.
    Do **not** attach this to every supervisor turn — tool calls and free
    text must stay allowed.
    """
    body: dict[str, Any] = {
        "name": (name or "result").replace(" ", "_")[:64],
        "strict": bool(strict),
        "schema": schema,
    }
    if description:
        body["description"] = description
    return {"type": "json_schema", "json_schema": body}


def filter_tool_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Drop undeclared keys; treat JSON null as omitted when a default exists.

    OpenAI strict mode emits ``null`` for unused optionals. Python defaults
    only apply when the key is missing, so we pop those nulls. Extra keys
    that are not in ``properties`` are dropped (swarm ``task_id`` /
    ``worker_name`` are preserved for HITL).
    """
    if not isinstance(arguments, dict):
        return {}
    out = dict(arguments)
    props: Any = None
    if isinstance(schema, dict):
        props = schema.get("properties")
    if isinstance(props, dict) and props:
        extra = [k for k in list(out) if k not in props and k not in _KEEP_EXEC_KEYS]
        for key in extra:
            out.pop(key, None)
        if extra:
            logger.debug("Dropped undeclared tool args: %s", extra)
        for name, spec in props.items():
            if (
                name in out
                and out[name] is None
                and isinstance(spec, dict)
                and "default" in spec
            ):
                out.pop(name, None)
    return out
