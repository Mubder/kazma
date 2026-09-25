"""Catastrophic calls in ``python_exec`` code, denied before the approval card.

The exec denylist (``authorize._EXEC_DENYLIST``) reads ``args["command"]``.
``python_exec`` carries its program in ``args["code"]``, so every Python call
reached the approval card unvetted — ``shutil.rmtree("/")`` and
``os.system("rm -rf /")`` alike. The card is still the guarantee; this gives
both exec tools the same deny-before-card floor for the same catastrophic
class.

AST, not regex. It resolves ``import shutil as s; s.rmtree(...)`` and
``from shutil import rmtree``, and it reads string constants as values, so a
comment or a ``print`` that merely mentions ``rm -rf /`` is not denied.

Deliberately narrow: only targets known statically. A path computed at run
time goes to the card, where a human reads the code. Target rules are not
re-derived here: a Python delete target is judged by the shell denylist's own
``rm -r`` pattern, a chmod target by its ``chmod`` pattern, and a command
handed to a shell by the whole list — so the two exec tools cannot drift.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence

__all__ = ["catastrophic_python"]

#: Recursive deletes: the first argument is the target.
_TREE_DELETES = frozenset({"shutil.rmtree", "os.removedirs"})
#: Calls that hand a string or an argv list to a shell or an exec.
_SHELL_CALLS = frozenset({
    "os.system", "os.popen",
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen", "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "os.execv", "os.execve", "os.execvp", "os.execvpe", "os.execl", "os.execle",
    "os.execlp", "os.execlpe", "os.spawnv", "os.spawnve", "os.spawnvp",
    "os.spawnvpe", "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
})
_RAW_DISK = re.compile(
    r"^(?:/dev/(?:sd|hd|nvme|disk|mmcblk|xvd|vd)|\\\\\.\\physicaldrive)", re.IGNORECASE,
)
_WRITE_MODE = re.compile(r"[wa+]")
_PATH_TYPES = frozenset({"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.WindowsPath"})


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> dotted origin, from every import in the module."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    names[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _dotted(node: ast.AST, aliases: dict[str, str]) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(aliases.get(node.id, node.id))
        return ".".join(reversed(parts))
    return ""


def _target(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    """A statically known filesystem target, spelled the way a shell would."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call):
        fn = _dotted(node.func, aliases)
        if fn in _PATH_TYPES and node.args:
            return _target(node.args[0], aliases)
        if fn == "os.path.expanduser" and node.args:
            return _target(node.args[0], aliases)
        if fn in {f"{t}.home" for t in _PATH_TYPES}:
            return "~"
        if fn in {"os.getcwd", *(f"{t}.cwd" for t in _PATH_TYPES)}:
            return "."
        if fn in ("os.getenv", "os.environ.get") and node.args:
            return "$HOME" if _target(node.args[0], aliases) in ("HOME", "USERPROFILE") else None
    if isinstance(node, ast.Subscript) and _dotted(node.value, aliases) == "os.environ":
        return "$HOME" if _target(node.slice, aliases) in ("HOME", "USERPROFILE") else None
    if isinstance(node, ast.Attribute) and _dotted(node, aliases) in ("os.sep", "os.path.sep"):
        return "/"
    return None


def _shell_text(args: list[ast.expr]) -> str | None:
    """The command line a shell-ish call would run, when it is all literal."""
    words: list[str] = []
    for arg in args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            words.append(arg.value)
        elif isinstance(arg, (ast.List, ast.Tuple)):
            if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in arg.elts):
                return None
            words.extend(e.value for e in arg.elts)  # type: ignore[union-attr]
        elif isinstance(arg, ast.Constant) and isinstance(arg.value, int):
            continue  # os.spawn* mode flag
        else:
            return None
    return " ".join(words) if words else None


def catastrophic_python(
    code: str,
    *,
    shell_denylist: Sequence[re.Pattern[str]],
    rm_target: re.Pattern[str],
    chmod_target: re.Pattern[str],
) -> str | None:
    """Why *code* is denied before the card, or ``None`` to let the card decide."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None  # python_exec fails on it anyway; the card still shows it
    aliases = _aliases(tree)

    for loop in ast.walk(tree):
        if isinstance(loop, ast.While):
            for inner in ast.walk(loop):
                if isinstance(inner, ast.Call) and _dotted(inner.func, aliases) == "os.fork":
                    return "os.fork() inside a loop is a fork bomb"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _dotted(node.func, aliases)

        if fn in _TREE_DELETES and node.args:
            target = _target(node.args[0], aliases)
            if target is not None and rm_target.search(f"rm -rf {target}"):
                return f"{fn}({target!r}) deletes a catastrophic location"

        if fn in _SHELL_CALLS:
            text = _shell_text(list(node.args))
            if text is not None and any(p.search(text) for p in shell_denylist):
                return f"{fn} runs a denylisted command: {text[:80]!r}"

        if fn == "os.kill" and node.args:
            pid = node.args[0]
            if (
                isinstance(pid, ast.UnaryOp) and isinstance(pid.op, ast.USub)
                and isinstance(pid.operand, ast.Constant) and pid.operand.value == 1
            ):
                return "os.kill(-1, ...) signals every process the user owns"

        if fn in ("os.chmod", "os.chown") and node.args:
            target = _target(node.args[0], aliases)
            if target is not None and chmod_target.search(f"chmod 777 {target}"):
                return f"{fn}({target!r}) changes a system location"

        if fn in ("open", "io.open", "os.open") and node.args:
            target = _target(node.args[0], aliases)
            mode = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg in ("mode", "flags")), None)
            writes = fn == "os.open" or (
                isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                and bool(_WRITE_MODE.search(mode.value))
            )
            if target is not None and writes and _RAW_DISK.match(target):
                return f"writes the raw disk device {target!r}"
    return None
