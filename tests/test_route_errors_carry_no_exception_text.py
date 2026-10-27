"""AUD-102: a route's broad ``except`` answers with a code and a reference.

AGENTS.md §26F: a server fault returns ``safe_error(exc)`` (a stable code and
a correlation id, the exception logged under it); a 4xx validation failure
returns ``validation_error(exc)`` (the caller's answer, redacted). The audit
found routes returning the exception's own text -- paths, store messages,
provider endpoints -- in the documents upload, the platform-user create, the
chat's provider switch, Settings (restart, dependency scan, model test), the
provider test and the MCP OAuth start.
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_UI = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"

#: The modules the audit named. A broad handler in them may not build its
#: answer from the exception's text.
_MODULES = (
    "documents_api.py",
    "saas_api.py",
    "sse_chat/__init__.py",
    "settings.py",
    "providers.py",
    "mcp_ui.py",
)

_WRAPPERS = {"safe_error", "validation_error", "error_payload"}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    names = [getattr(n, "id", getattr(n, "attr", "")) for n in
             (t.elts if isinstance(t, ast.Tuple) else [t])]
    return "Exception" in names or "BaseException" in names


def _raw_text_returns(source: str) -> list[int]:
    """Lines where a broad handler returns ``str(exc)``, ``f"{exc}"`` or ``exc``."""
    found: list[int] = []
    for handler in ast.walk(ast.parse(source)):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name and _is_broad(handler)):
            continue
        var = handler.name

        def is_var(n: ast.AST) -> bool:
            return isinstance(n, ast.Name) and n.id == var

        def walk(n: ast.AST) -> None:
            if isinstance(n, ast.Call):
                fname = getattr(n.func, "id", getattr(n.func, "attr", ""))
                if fname in _WRAPPERS:
                    return
                if fname in ("str", "repr") and n.args and is_var(n.args[0]):
                    found.append(n.lineno)
                    return
            if isinstance(n, ast.FormattedValue) and is_var(n.value):
                found.append(n.lineno)
                return
            if isinstance(n, ast.Dict):
                found.extend(v.lineno for v in n.values if is_var(v))
            for child in ast.iter_child_nodes(n):
                walk(child)

        for node in handler.body:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    walk(sub.value)
    return sorted(set(found))


def test_the_audited_routes_return_no_exception_text() -> None:
    offenders = {
        name: lines
        for name in _MODULES
        if (lines := _raw_text_returns((_UI / name).read_text(encoding="utf-8")))
    }
    assert not offenders, (
        "a broad except returns the exception's text; use safe_error(exc) "
        f"(server fault) or validation_error(exc) (4xx): {offenders}"
    )


def test_negative_control_the_gate_sees_each_old_shape() -> None:
    old = (
        "def a():\n"
        "    try:\n        x()\n"
        "    except Exception as exc:\n"
        "        return JSONResponse({'error': str(exc)}, status_code=500)\n"
        "def b():\n"
        "    try:\n        x()\n"
        "    except Exception as e:\n"
        "        return {'success': False, 'error': f'Google Provider test failed: {e}'}\n"
        "def c():\n"
        "    try:\n        x()\n"
        "    except Exception as exc:\n"
        "        return {'error': safe_error(exc)}\n"
    )
    assert _raw_text_returns(old) == [5, 10]


def test_a_failed_user_create_answers_a_reference_not_the_store_error(monkeypatch) -> None:
    from kazma_core.security import platform_rbac
    from kazma_ui import auth
    from kazma_ui.saas_api import create_saas_router

    def boom(*_a, **_k):
        raise RuntimeError("could not write /srv/private/users.db: disk full")

    monkeypatch.setattr(auth, "admin_decision", lambda request: "ok")
    monkeypatch.setattr(platform_rbac, "create_local_user", boom)
    app = FastAPI()
    app.include_router(create_saas_router())
    with TestClient(app) as client:
        r = client.post(
            "/api/saas/users", json={"username": "lee", "password": "long-enough"}
        )
    assert r.status_code == 500
    text = r.text
    assert "/srv/private" not in text and "disk full" not in text
    assert "ref:" in r.json()["error"]
