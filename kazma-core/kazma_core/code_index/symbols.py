"""Extract definitions from source. Tree-sitter when installed; regex otherwise."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kazma_core.code_index.walk import lang_for_path

__all__ = ["Symbol", "extract_symbols", "tree_sitter_available"]


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    kind: str  # function | class | method
    line: int
    signature: str = ""


_PY_DEF = re.compile(r"^(\s*)(async\s+)?def\s+(\w+)\s*\(([^)]*)\)", re.M)
_PY_CLASS = re.compile(r"^(\s*)class\s+(\w+)", re.M)
_JS_FN = re.compile(
    r"^(\s*)(export\s+)?(async\s+)?function\s+(\w+)\s*\(", re.M
)
_JS_CLASS = re.compile(r"^(\s*)(export\s+)?class\s+(\w+)", re.M)
_JS_ARROW = re.compile(
    r"^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s*)?\(", re.M
)
_GO_FN = re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(", re.M)
_RS_FN = re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.M)
_RS_STRUCT = re.compile(r"^(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)", re.M)


def tree_sitter_available() -> bool:
    try:
        from tree_sitter import Language, Parser  # noqa: F401

        import tree_sitter_python  # noqa: F401

        return True
    except ImportError:
        return False


def extract_symbols(path: Path, source: str | None = None) -> list[Symbol]:
    lang = lang_for_path(path)
    if source is None:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    ts = _tree_sitter_symbols(lang, source)
    if ts:
        return ts
    return _regex_symbols(lang, source)


def _regex_symbols(lang: str, source: str) -> list[Symbol]:
    out: list[Symbol] = []
    if lang == "python":
        for m in _PY_CLASS.finditer(source):
            indent, name = m.group(1), m.group(2)
            kind = "class"
            out.append(Symbol(name=name, kind=kind, line=_line_of(source, m.start()), signature=f"class {name}"))
            del indent
        for m in _PY_DEF.finditer(source):
            indent, _async, name, args = m.group(1), m.group(2), m.group(3), m.group(4)
            kind = "method" if len(indent or "") >= 4 else "function"
            sig = f"def {name}({args.strip()})"
            out.append(Symbol(name=name, kind=kind, line=_line_of(source, m.start()), signature=sig))
    elif lang in ("javascript", "typescript"):
        for m in _JS_CLASS.finditer(source):
            out.append(Symbol(name=m.group(3), kind="class", line=_line_of(source, m.start()), signature=f"class {m.group(3)}"))
        for m in _JS_FN.finditer(source):
            out.append(Symbol(name=m.group(4), kind="function", line=_line_of(source, m.start()), signature=f"function {m.group(4)}"))
        for m in _JS_ARROW.finditer(source):
            out.append(Symbol(name=m.group(4), kind="function", line=_line_of(source, m.start()), signature=f"const {m.group(4)} = ("))
    elif lang == "go":
        for m in _GO_FN.finditer(source):
            out.append(Symbol(name=m.group(1), kind="function", line=_line_of(source, m.start()), signature=m.group(0)[:80]))
    elif lang == "rust":
        for m in _RS_STRUCT.finditer(source):
            out.append(Symbol(name=m.group(1), kind="class", line=_line_of(source, m.start()), signature=m.group(0)[:80]))
        for m in _RS_FN.finditer(source):
            out.append(Symbol(name=m.group(1), kind="function", line=_line_of(source, m.start()), signature=m.group(0)[:80]))
    return out


def _line_of(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _tree_sitter_symbols(lang: str, source: str) -> list[Symbol]:
    parser = _parser_for(lang)
    query_src = _QUERIES.get(lang)
    if parser is None or not query_src:
        return []
    try:
        from tree_sitter import Query, QueryCursor
    except ImportError:
        try:
            from tree_sitter import Query
            QueryCursor = None  # type: ignore[assignment, misc]
        except ImportError:
            return []
    try:
        tree = parser.parse(source.encode("utf-8"))
        language = parser.language
        if language is None:
            return []
        query = Query(language, query_src)
        out: list[Symbol] = []
        captures: list[tuple[object, str]]
        if QueryCursor is not None:
            cursor = QueryCursor(query)
            raw = cursor.captures(tree.root_node)
            if isinstance(raw, dict):
                captures = [(node, name) for name, nodes in raw.items() for node in nodes]
            else:
                captures = list(raw)
        else:
            captures = list(query.captures(tree.root_node))
        for node, cap_name in captures:
            kind = "class" if "class" in cap_name else "function"
            name = getattr(node, "text", b"")
            if isinstance(name, bytes):
                name_s = name.decode("utf-8", errors="replace")
            else:
                name_s = source[node.start_byte : node.end_byte]
            line = int(getattr(node, "start_point", (0, 0))[0]) + 1
            out.append(Symbol(name=name_s, kind=kind, line=line, signature=f"{kind} {name_s}"))
        return out
    except Exception:
        return []


_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @function)
        (class_definition name: (identifier) @class)
    """,
    "javascript": """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (identifier) @class)
        (lexical_declaration (variable_declarator name: (identifier) @function))
    """,
    "typescript": """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (type_identifier) @class)
    """,
}


def _parser_for(lang: str):  # noqa: ANN202
    try:
        from tree_sitter import Language, Parser
    except ImportError:
        return None
    mod_name = {
        "python": "tree_sitter_python",
        "javascript": "tree_sitter_javascript",
        "typescript": "tree_sitter_javascript",
    }.get(lang)
    if not mod_name:
        return None
    try:
        import importlib

        mod = importlib.import_module(mod_name)
        blob = mod.language()
    except Exception:
        return None
    try:
        language = Language(blob)
    except Exception:
        return None
    try:
        return Parser(language)
    except TypeError:
        parser = Parser()
        parser.set_language(language)
        return parser
