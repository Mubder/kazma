"""Lightweight LaTeX → Unicode for document bodies.

We do not ship a TeX engine. Display and inline math become readable
Unicode (``R = P · I``, ``S(x) = 1⁄(1 + e⁻ˣ)``) and are isolated LTR
so Word/LibreOffice do not reverse them inside Arabic paragraphs.

Bare ``$0.0035$`` wrappers are currency, not math.
"""

from __future__ import annotations

import re
from typing import Literal

__all__ = [
    "looks_like_currency",
    "latex_to_unicode",
    "split_inline_math",
    "split_display_math",
]

Kind = Literal["text", "math", "money"]

_CURRENCY = re.compile(r"^\s*\d[\d,.]*(?:\s*[A-Za-z]{3})?\s*$")
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.S)
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)

_COMMANDS: dict[str, str] = {
    "cdot": "·",
    "times": "×",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "equiv": "≡",
    "infty": "∞",
    "rightarrow": "→",
    "to": "→",
    "leftarrow": "←",
    "Rightarrow": "⇒",
    "ldots": "…",
    "cdots": "⋯",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "pi": "π",
    "sigma": "σ",
    "phi": "φ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Sigma": "Σ",
    "Omega": "Ω",
    "sum": "∑",
    "prod": "∏",
    "int": "∫",
    "partial": "∂",
}

_SUP = str.maketrans(
    "0123456789+-=nijkxyabceh()/",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼ⁿⁱʲᵏˣʸᵃᵇᶜᵉʰ⁽⁾ᐟ",
)
_SUB = str.maketrans(
    "0123456789+-=nijkxyaeh()",
    "₀₁₂₃₄₅₆₇₈₉₊₋₌ₙᵢⱼₖₓᵧₐₑₕ₍₎",
)


def looks_like_currency(inner: str) -> bool:
    """True for ``$0.0035$`` / ``$12.00 USD$`` — not real math."""
    return bool(_CURRENCY.fullmatch((inner or "").strip()))


def _consume_brace(s: str, start: int) -> tuple[str, int] | None:
    """Return (inner, index_after) for a ``{...}`` at ``start``, or None."""
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    i = start + 1
    while i < len(s):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                return s[start + 1 : i], i + 1
            depth -= 1
        i += 1
    return None


def _replace_cmd_braces(s: str, cmd: str, n_args: int, fn) -> str:
    needle = "\\" + cmd
    out: list[str] = []
    i = 0
    while i < len(s):
        j = s.find(needle, i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        k = j + len(needle)
        args: list[str] = []
        ok = True
        for _ in range(n_args):
            while k < len(s) and s[k].isspace():
                k += 1
            got = _consume_brace(s, k)
            if got is None:
                ok = False
                break
            arg, k = got
            args.append(arg)
        if not ok:
            out.append(s[j:j + len(needle)])
            i = j + len(needle)
            continue
        out.append(fn(*args))
        i = k
    return "".join(out)


def _wrap_if_sum(expr: str) -> str:
    if any(c in expr for c in "+-"):
        return f"({expr})"
    return expr


def latex_to_unicode(tex: str) -> str:
    """Best-effort readable Unicode for a TeX fragment (no engine)."""
    s = (tex or "").strip()
    if not s:
        return ""
    s = s.replace("\\\\", "\n")
    s = re.sub(r"\\left|\\right", "", s)
    s = re.sub(
        r"\\(?:mathrm|mathbf|boldsymbol|mathit|text|operatorname)\s*\{([^{}]*)\}",
        r"\1",
        s,
    )

    for _ in range(8):
        nxt = _replace_cmd_braces(
            s,
            "frac",
            2,
            lambda a, b: (
                f"{_wrap_if_sum(latex_to_unicode(a))}⁄"
                f"{_wrap_if_sum(latex_to_unicode(b))}"
            ),
        )
        if nxt == s:
            break
        s = nxt

    s = _replace_cmd_braces(
        s, "sqrt", 1, lambda a: f"√({latex_to_unicode(a)})"
    )

    def _script(m: re.Match[str], table: dict[int, int]) -> str:
        inner = latex_to_unicode(m.group(1))
        mapped = inner.translate(table)
        if mapped and mapped != inner:
            return mapped
        return ("^" if table is _SUP else "_") + inner

    s = re.sub(
        r"\^\{([^{}]+)\}",
        lambda m: _script(m, _SUP),
        s,
    )
    s = re.sub(
        r"_\{([^{}]+)\}",
        lambda m: _script(m, _SUB),
        s,
    )
    s = re.sub(r"\^([A-Za-z0-9+\-])", lambda m: m.group(1).translate(_SUP), s)
    s = re.sub(r"_([A-Za-z0-9+\-])", lambda m: m.group(1).translate(_SUB), s)

    s = re.sub(
        r"\\([A-Za-z]+)",
        lambda m: _COMMANDS.get(m.group(1), m.group(1)),
        s,
    )
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def split_inline_math(text: str) -> list[tuple[Kind, str]]:
    """Split a paragraph into text / math / money runs (``$...$`` only)."""
    if not text or "$" not in text:
        return [("text", text or "")]
    out: list[tuple[Kind, str]] = []
    pos = 0
    for m in _INLINE_MATH.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        inner = m.group(1)
        kind: Kind = "money" if looks_like_currency(inner) else "math"
        out.append((kind, inner))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out or [("text", text)]


def split_display_math(text: str) -> list[tuple[Kind, str]]:
    """Split ``$$...$$`` display fragments out of a paragraph."""
    if not text or "$$" not in text:
        return [("text", text or "")]
    parts = _DISPLAY_MATH.split(text)
    out: list[tuple[Kind, str]] = []
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        out.append(("math" if i % 2 else "text", part.strip() if i % 2 else part))
    return out or [("text", text)]
