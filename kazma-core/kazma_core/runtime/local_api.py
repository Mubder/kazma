"""How in-process mouths find the running Kazma HTTP API.

TUI / CLI are separate processes. They are mouths of the *same* brain
only if they talk to the server the operator already started — not a
second LLM loop.
"""

from __future__ import annotations

import os
from typing import Any
from kazma_core.http_tls import shared_ssl_context

__all__ = [
    "LocalApiRefused",
    "LocalApiUnreachable",
    "auth_headers",
    "candidate_api_bases",
    "request_json",
    "request_json_async",
]

#: Answers that mean the server read the request and refused it: not a
#: reason to try another address, and never "the server is unreachable".
_REFUSALS = frozenset({400, 409, 413, 422})


class LocalApiRefused(RuntimeError):
    """The running server answered and refused the request; ``detail`` is
    its reason (a value a setting cannot hold, for example)."""

    def __init__(self, detail: str, status: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class LocalApiUnreachable(RuntimeError):
    """Nothing listens at any candidate address: no server answered at all.

    The only failure after which a mouth may act on its own (the TUI's
    local settings write, §47). A 401/403, a 5xx, a slow server or another
    program's answer is a server that answered -- a plain ``RuntimeError``
    -- and the TUI wrote straight into the shared store behind the
    server's admin check, listeners and cache on each of them (audit
    2026-10-26 AUD-035).
    """


def _refusal(resp: Any) -> LocalApiRefused | None:
    if resp.status_code not in _REFUSALS:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail") or body.get("error")
    return LocalApiRefused(str(detail), resp.status_code) if detail else None


def _is_loopback(url: str) -> bool:
    low = (url or "").lower()
    return (
        "://127.0.0.1" in low
        or "://localhost" in low
        or "://[::1]" in low
    )


def candidate_api_bases() -> list[str]:
    """Deduped API roots for in-process mouths (TUI / CLI).

    Loopback first. ``KAZMA_BASE_URL`` is an explicit override (may be remote).
    ``KAZMA_PUBLIC_URL`` is *not* used — that host is for browsers/OAuth and
    typically 302s to a login HTML page.
    """
    found: list[str] = []
    port = (os.environ.get("KAZMA_PORT") or "").strip()
    if port:
        found.append(f"http://127.0.0.1:{port}")
    found.append("http://127.0.0.1:9090")
    found.append("http://127.0.0.1:8000")
    raw = (os.environ.get("KAZMA_BASE_URL") or "").strip().rstrip("/")
    if raw:
        found.append(raw)
    loopback = [u for u in found if _is_loopback(u)]
    remote = [u for u in found if not _is_loopback(u)]
    out: list[str] = []
    for u in loopback + remote:
        if u not in out:
            out.append(u)
    return out


def _secret_from_dotenv() -> str:
    """Read KAZMA_SECRET from cwd/.env when the process env is empty."""
    import sys

    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    try:
        from pathlib import Path

        env_path = Path.cwd() / ".env"
        if not env_path.is_file():
            return ""
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, val = raw.split("=", 1)
            if key.strip() == "KAZMA_SECRET":
                return val.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def auth_headers() -> dict[str, str]:
    secret = (os.environ.get("KAZMA_SECRET") or "").strip()
    if not secret:
        secret = _secret_from_dotenv()
    if not secret:
        return {}
    return {"X-Kazma-Secret": secret}


def _json_url(base: str, path: str) -> str:
    p = path if path.startswith("/") else f"/{path}"
    return f"{base.rstrip('/')}{p}"


def _failed(errors: list[str], answered: bool) -> RuntimeError:
    """The error after every candidate failed: ``LocalApiUnreachable`` only
    when none of them answered at all (nothing listening, or a connect that
    timed out); a plain ``RuntimeError`` when one did."""
    detail = "; ".join(errors) if errors else "no API candidates"
    tried = ", ".join(candidate_api_bases())
    if not answered:
        return LocalApiUnreachable(
            "Kazma server is not running on this machine "
            f"(tried {tried}). {detail} "
            "Start it yourself — the TUI is only a mouth."
        )
    return RuntimeError(f"The Kazma server answered but the request failed (tried {tried}). {detail}")


def request_json(
    method: str,
    path: str,
    *,
    payload: Any | None = None,
    timeout: float = 8.0,
) -> Any:
    """GET/POST JSON against the first reachable loopback API.

    Raises ``LocalApiUnreachable`` when nothing listens at any candidate,
    ``LocalApiRefused`` for an answered refusal, and ``RuntimeError`` when
    a server answered but no candidate gave a JSON response.
    """
    import httpx

    headers = {"Accept": "application/json", **auth_headers()}
    errors: list[str] = []
    answered = False
    for base in candidate_api_bases():
        url = _json_url(base, path)
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=2.0)) as client:
                resp = client.request(method.upper(), url, json=payload, headers=headers)
            answered = True
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"{url} returned {resp.status_code} — set KAZMA_SECRET "
                    "to the same secret the server is using (cwd .env)."
                )
            refused = _refusal(resp)
            if refused is not None:
                raise refused
            if resp.status_code >= 400:
                errors.append(f"{url} -> {resp.status_code}")
                continue
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                errors.append(f"{url} returned {resp.status_code} ({ctype or 'no content-type'})")
                continue
            return resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout):
            errors.append(f"nothing listening at {base}")
            continue
        except httpx.TimeoutException:
            # Connected, then slow: a server is there.
            answered = True
            errors.append(f"timeout talking to {base}")
            continue
    raise _failed(errors, answered)


async def request_json_async(
    method: str,
    path: str,
    *,
    payload: Any | None = None,
    timeout: float = 8.0,
) -> Any:
    """Async variant of :func:`request_json` for TUI inspector commands."""
    import httpx

    headers = {"Accept": "application/json", **auth_headers()}
    errors: list[str] = []
    answered = False
    for base in candidate_api_bases():
        url = _json_url(base, path)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=2.0), verify=shared_ssl_context()) as client:
                resp = await client.request(
                    method.upper(), url, json=payload, headers=headers
                )
            answered = True
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"{url} returned {resp.status_code} — set KAZMA_SECRET "
                    "to the same secret the server is using (cwd .env)."
                )
            refused = _refusal(resp)
            if refused is not None:
                raise refused
            if resp.status_code >= 400:
                snippet = ""
                try:
                    body = resp.json()
                    snippet = str(body.get("error") or body.get("detail") or "")[:160]
                except Exception:
                    snippet = (resp.text or "")[:160]
                errors.append(f"{url} -> {resp.status_code}" + (f" {snippet}" if snippet else ""))
                continue
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                errors.append(f"{url} returned {resp.status_code} ({ctype or 'no content-type'})")
                continue
            return resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout):
            errors.append(f"nothing listening at {base}")
            continue
        except httpx.TimeoutException:
            answered = True
            errors.append(f"timeout talking to {base}")
            continue
    raise _failed(errors, answered)
