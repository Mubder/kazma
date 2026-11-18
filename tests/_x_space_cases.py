"""The matrix cases of Spaces S1-17, part b: X (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``, through the product's own X routers (X Studio, the
whole X API, the auto-reply, the datasets, the Scheduled page's X booking and
the X Studio page) behind the real tenant middleware, or through the product's
own tools, on a private data folder and a real vault of the case's own.

Both spaces connect their own X account and use it the way X Studio does, each
through its own requests and the stores' own writers: the four OAuth 1.0a keys
and a handle, the app's Bearer token and OAuth 2.0 client, the composer, a
saved draft, a booked post, an audit row, a staged thread, a dataset, the
auto-reply settings and a summons. Every one names its space's marker. A
request or tool call of A's never shows B's marker, never changes what B's
space shows itself (B's own read routes, before and after), and every call to
X it makes carries A's keys, never B's. Nothing reaches the network: X is
stood in for, and the stand-in records whose keys each call carried; the X
model's drafting is stood in for too.
"""

from __future__ import annotations

import json
import secrets
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S1-17"
_ORIGIN = "http://testserver"
_TEMPLATES = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates"

#: The environment X reads: cleared for each case (it is the owner's).
_X_ENV = ("X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "X_BEARER_TOKEN",
          "KAZMA_X_POST", "KAZMA_X_SCHEDULE")

#: B's own read routes: what B's space shows itself. A request of A's must
#: leave every one of them as it was.
_VIEWS = ("/api/x/status", "/api/x/composer", "/api/x/drafts", "/api/x/queue?include_finished=true",
          "/api/x/operations", "/api/x/audit", "/api/x/api-access", "/api/x/threads", "/api/x/datasets",
          "/api/x/reply", "/api/x/reply/recent", "/api/x/reply/conversations", "/api/x/reply/qualification")


class _PageData:
    """Stands in for the page templates: the answer is the context the
    router hands its template (the page draws X Studio from /api/x)."""

    def TemplateResponse(self, request: Any, name: str, context: dict[str, Any]) -> Any:  # noqa: N802
        from fastapi.responses import JSONResponse

        return JSONResponse({"template": name, "config": context.get("config")})


class World:
    """The X routers in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient, marks: Any, x_calls: list[Any]) -> None:
        self.matrix = matrix
        self.client = client
        self.marks = marks
        self.x_calls = x_calls
        #: Each space's planted ids: {"a": {...}, "b": {...}}.
        self.ids: dict[str, dict[str, Any]] = {"a": {}, "b": {}}

    def as_who(self, who: str) -> Any:
        return {"a": self.matrix.as_a, "b": self.matrix.as_b}[who]()

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        space = {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: space, "Origin": _ORIGIN,
                   "X-Requested-With": "XMLHttpRequest"}
        return self.client.request(method, path, headers=headers, **kwargs)

    def views(self, who: str) -> dict[str, str]:
        """What *who*'s space shows itself, through its own read routes."""
        out = {}
        for path in _VIEWS:
            answer = self.call(who, "GET", path)
            assert answer.status_code == 200, (path, answer.text[:300])
            out[path] = _steady(answer.text)
        return out

    def tool(self, who: str, name: str, **kwargs: Any) -> str:
        from tests._chat_space_cases import _registry

        tool = _registry().get_tool(name)
        assert tool is not None, name

        async def as_them() -> str:
            from kazma_core.agent.tool_refusal import RefusedBeforeEffect

            with self.as_who(who):
                try:
                    return str(await tool.func(**kwargs))
                except RefusedBeforeEffect as refused:  # A's own refusal is A's answer
                    return str(refused)

        return self.client.portal.call(as_them)


#: Keys of a view whose values move by themselves (the clock).
_MOVING_KEYS = ("_at", "_age", "_ago", "_seconds", "_s", "_until")


def _steady(text: str) -> str:
    """A view without the values that move by themselves (the clock)."""
    try:
        data = json.loads(text)
    except ValueError:
        return text

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()
                    if not (k.endswith(_MOVING_KEYS) or k in ("now", "age", "expires_in"))}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return json.dumps(walk(data), sort_keys=True)


def _plant(w: World, who: str, mark: str) -> None:
    """*who*'s space connects its own X account and uses it: through its own
    requests where X Studio has one, else the stores' own writers."""
    from kazma_core.agent.artifacts import get_artifact_store
    from kazma_core.x_api.audit import get_x_audit
    from kazma_core.x_api.ownership import x_tenant_id
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.reply_store import get_reply_store

    def ok(answer: Any) -> Any:
        assert answer.status_code == 200, answer.text[:300]
        return answer.json()

    ok(w.call(who, "POST", "/api/x/credentials", json={
        "api_key": f"{mark}key1", "api_key_secret": f"{mark}key2", "access_token": f"{mark}key3",
        "access_token_secret": f"{mark}key4", "handle": f"@{mark}", "enabled": True}))
    ok(w.call(who, "POST", "/api/x/api-access", json={"bearer_token": f"{mark}bearer", "client_id": f"{mark}client"}))
    ok(w.call(who, "PUT", "/api/x/composer", json={"expected_revision": 0, "text": f"compose {mark}"}))
    dataset = ok(w.call(who, "POST", "/api/x/datasets", json={"name": f"set {mark}"}))
    w.ids[who]["dataset"] = dataset["dataset"]["id"]
    ok(w.call(who, "PUT", "/api/x/reply", json={"trigger": mark, "summoners": [mark]}))
    staged = w.call(who, "POST", "/api/x/threads", json={"segments": [f"one {mark}", f"two {mark}"],
                                                        "intent_key": f"i{mark}"})
    if staged.status_code == 200 and staged.json().get("thread"):
        w.ids[who]["thread"] = staged.json()["thread"].get("id") or ""
    with w.as_who(who):
        tenant = x_tenant_id()
        saved = get_artifact_store().save_proposal(tenant, "", "x_posts", [f"draft {mark}"])
        w.ids[who]["draft"] = saved["items"][0]["id"]
        get_x_audit().log(action="post", method="POST", endpoint="/2/tweets",
                          request_body={"text": f"audit {mark}"}, response_body={"data": {"id": "1"}})
        op = get_publication_store().reserve(
            tenant_id=tenant, account_id=f"9{len(mark)}", credential_revision=f"rev{mark}",
            idempotency_key=f"k{mark}", text=f"booked {mark}", reply_to_id="", due_at=time.time() + 86400,
            max_day=50, max_month=500, duplicate_days=1, origin="schedule")
        w.ids[who]["operation"] = op["id"]
        w.ids[who]["version"] = op["version"]
        # A summons' id is X's post id: one carrying no marker, so a route
        # that echoes the id A named shows no text of B's.
        summon = f"{secrets.randbelow(10**12):012d}"
        get_reply_store().claim(summon_id=summon, parent_id="7", target_handle=mark, summoner=mark,
                                parent_text=f"parent {mark}", summon_text=f"summon {mark}", tenant_id=tenant)
        w.ids[who]["summon"] = summon


def _forget_x_settings() -> None:
    """The two spaces' X settings, removed (the settings store is the session's)."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    for keys in store.get_all().values():
        for key in list(keys):
            if key.startswith((f"tenant.{Matrix.SPACE_A}.", f"tenant.{Matrix.SPACE_B}.")):
                store.delete(key)


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    """Spaces enforced; the X routers behind the real tenant middleware, a
    private data folder and vault, X and the X model stood in for, both
    spaces' X accounts planted."""
    import kazma_core.agent.artifacts as artifacts
    import kazma_core.x_api.audit as audit
    import kazma_core.x_api.ledger as ledger
    import kazma_core.x_api.post_drafting as post_drafting
    import kazma_core.x_api.reply_store as reply_store
    import kazma_core.x_api.schedule as schedule
    from fastapi.templating import Jinja2Templates
    from kazma_core.security import vault as vault_mod
    from kazma_core.x_api import auth_tokens
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.post_drafting import PostDraftResult

    from kazma_ui import x_api, x_dataset_api, x_reply_api

    from tests._no_models import keep_models_out
    from kazma_ui.routes_direct.misc import register_misc_routes
    from kazma_ui.scheduled_api import create_scheduled_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    x_calls: list[Any] = []
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setenv("KAZMA_DATA_DIR", tmp)
        patch.setenv("KAZMA_VAULT_KEY", "k" * 64)
        patch.setenv("KAZMA_PUBLIC_URL", _ORIGIN)
        for name in _X_ENV:
            patch.delenv(name, raising=False)
        for module, name in ((artifacts, "_store"), (audit, "_audit"), (ledger, "_ledger"),
                             (reply_store, "_store"), (schedule, "_store")):
            patch.setattr(module, name, None)
        v = vault_mod.SecretVault(db_path=str(Path(tmp) / "vault.db"))
        patch.setattr(vault_mod, "get_vault", lambda: v)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))

        async def no_network(self: Any, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            bound = scope.current_scope()
            x_calls.append((bound.space_id if bound else None, self._creds.api_key, method, path))
            raise XApiError("stand-in: no network", status=503, outcome="not_sent")

        async def drafted(brief: str, *, count: int = 1, subject_id: str = "") -> Any:
            return PostDraftResult(drafts=(f"drafted for {brief}",), reviews=({"checks": []},))

        patch.setattr(XClient, "_request", no_network)
        patch.setattr(post_drafting, "draft_posts", drafted)
        # Sign in with X rebuilds its verifier from Kazma's secret (unset here).
        patch.setattr(auth_tokens, "pkce_verifier", lambda state: "v" * 43)
        keep_models_out(patch)
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        for router in (x_api.router, x_api.protected_router, x_reply_api.router, x_reply_api.protected_router,
                       x_dataset_api.router):
            app.include_router(router)

        class _Agent:
            config: dict[str, Any] = {}

        app.include_router(create_scheduled_router(_Agent(), Jinja2Templates(directory=str(_TEMPLATES))))
        register_misc_routes(SimpleNamespace(app=app, templates=_PageData(), agent=_Agent()))
        try:
            with TestClient(app, base_url=_ORIGIN) as client:
                made = World(matrix, client, matrix.plant("x"), x_calls)
                _plant(made, "b", made.marks.b)
                _plant(made, "a", made.marks.a)
                made.x_calls.clear()
                yield made
        finally:
            v.close()
            _forget_x_settings()
            for name in _X_ENV:
                os.environ.pop(name, None)


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def _only_own_keys(w: World) -> None:
    """Every call to X carried A's keys, in A's space."""
    for space, key, method, path in w.x_calls:
        assert space == Matrix.SPACE_A and w.marks.b not in str(key), (space, key, method, path)


def _fill(w: World, text: str) -> str:
    """*text* with B's planted ids in it: A names B's."""
    ids = w.ids["b"]
    return (text.replace("{operation_id}", str(ids.get("operation", "")))
            .replace("{ident}", str(ids.get("dataset" if "datasets" in text else "thread", "") or "none"))
            .replace("{summon_id}", str(ids.get("summon", "")))
            .replace("{draft}", str(ids.get("draft", "")))
            .replace("{action}", "cancel"))


def _route(target: str, body: Any = None, *, check: Callable[[World, Any], None] | None = None) -> None:
    """A case for *target*: as A -- naming B's ids where it takes one -- B's
    space shows itself as before and B's marker is never shown; *check* the
    positive for A."""
    method, path = target.split(" ", 1)

    def run(w: World) -> None:
        before = w.views("b")
        payload = json.loads(_fill(w, json.dumps(body))) if body is not None else None
        answer = w.call("a", method, _fill(w, path), **({"json": payload} if payload is not None else {}))
        assert answer.status_code < 500, answer.text[:300]
        assert w.marks.b not in answer.text, answer.text[:300]
        assert w.views("b") == before, "A's request changed what B's space shows itself"
        _only_own_keys(w)
        if check is not None:
            check(w, answer)

    run.__name__ = "x_" + path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    case(target, ITEM)(_in_world(run))


def _shows_mine(w: World, answer: Any) -> None:
    assert w.marks.a in answer.text, answer.text[:300]


def _started_in_a(w: World, answer: Any) -> None:
    """Sign in with X started in A's space, as A, with A's own app."""
    from kazma_core.security import oauth_attempts
    from kazma_core.security.oauth_attempts import binding_cookie_name

    assert answer.status_code == 200 and answer.json().get("authorize_url"), answer.text[:300]
    assert f"{w.marks.a}client" in answer.json()["authorize_url"]
    state = next(c.value for c in w.client.cookies.jar if c.value and binding_cookie_name(c.value) == c.name)
    attempt = oauth_attempts.take(state, bound_state=state, providers=("x",))
    assert (attempt["space"], attempt["asker"]) == (Matrix.SPACE_A, w.matrix.account_a), attempt


def _not_found(w: World, answer: Any) -> None:
    """Refused, or answered with nothing: another space's id reads as unknown."""
    if answer.status_code in (400, 404, 409, 422):
        return
    body = answer.json()
    assert not body.get("ok", True) or body.get("rows") == [] or body.get("items") == [], answer.text[:300]


# ── X Studio ─────────────────────────────────────────────────────────────

_route("GET /api/x/status", check=_shows_mine)
_route("GET /api/x/composer", check=_shows_mine)
_route("GET /api/x/drafts", check=_shows_mine)
_route("GET /api/x/queue", check=_shows_mine)
_route("GET /api/x/operations", check=_shows_mine)
_route("GET /api/x/audit", check=_shows_mine)
_route("GET /api/x/api-access", check=_shows_mine)
_route("GET /api/x/threads")
_route("GET /api/x/health")
_route("GET /api/x/operations/{operation_id}", check=_not_found)
_route("POST /api/x/operations/{operation_id}/check", {"expected_version": 1}, check=_not_found)
_route("POST /api/x/queue/{operation_id}/{action}", {"expected_version": 1}, check=_not_found)
_route("POST /api/x/preview", {"text": "a preview"})
_route("POST /api/x/resume")
_route("PUT /api/x/composer", {"expected_revision": 1, "text": "A's new words"})
_route("POST /api/x/generate", {"brief": "a brief"})
_route("POST /api/x/drafts/discard", {"id": "{draft}"})
_route("POST /api/x/post", {"text": "a post of A's"})
_route("POST /api/x/delete", {"tweet_id": "12345"})
_route("POST /api/x/credentials", {"handle": "@renamed"})
_route("POST /api/x/test")
_route("POST /api/x/disconnect")
_route("POST /api/x/api-access", {"client_id": "anotherclient1"})
_route("POST /api/x/oauth2/start", {}, check=_started_in_a)
_route("POST /api/x/oauth2/sign-out")
_route("POST /api/x/threads", {"segments": ["one of A's", "two of A's"], "intent_key": "a-thread"})
_route("POST /api/x/threads/{ident}/{action}", {"expected_revision": 1}, check=_not_found)
_route("GET /x")


def _draft_of_b_not_bound(w: World) -> None:
    """A booking that names B's draft is refused: the draft is not A's."""
    before = w.views("b")
    answer = w.call("a", "POST", "/api/scheduled/x",
                    json={"text": "x", "when": "2h", "proposal_id": w.ids["b"]["draft"]})
    assert answer.status_code == 400 and answer.json().get("code") == "proposal_not_found", answer.text[:300]
    assert w.views("b") == before and w.marks.b not in answer.text


case("POST /api/scheduled/x", ITEM)(_in_world(_draft_of_b_not_bound))


# ── the auto-reply ───────────────────────────────────────────────────────

_route("GET /api/x/reply", check=_shows_mine)
_route("GET /api/x/reply/recent", check=_shows_mine)
_route("GET /api/x/reply/conversations", check=_shows_mine)
_route("GET /api/x/reply/history/{summon_id}", check=_not_found)
_route("GET /api/x/reply/qualification")
_route("PUT /api/x/reply", {"trigger": "a-trigger"})
_route("PUT /api/x/reply/qualification", {"report": {}})
_route("POST /api/x/reply/policy/validate", {"subjects": []})
_route("POST /api/x/reply/poll")
_route("POST /api/x/reply/preview", {"parent_text": "a post", "parent_handle": "someone"})
_route("POST /api/x/reply/model-test", {"parent_text": "a post"})
for _verb in ("approve", "delete", "deny", "retry"):
    _route(f"POST /api/x/reply/{_verb}", {"summon_id": "{summon_id}"}, check=_not_found)


# ── datasets ─────────────────────────────────────────────────────────────

_route("GET /api/x/datasets", check=_shows_mine)
_route("GET /api/x/datasets/{ident}", check=_not_found)
_route("GET /api/x/datasets/{ident}/export", check=_not_found)
_route("POST /api/x/datasets", {"name": "a set of A's"})
_route("PUT /api/x/datasets/{ident}/case", {"expected_revision": 1, "case": {"text": "x"}}, check=_not_found)
_route("POST /api/x/datasets/{ident}/collect", {"expected_revision": 1}, check=_not_found)


# ── the tools ────────────────────────────────────────────────────────────


_TOOL_CALLS: dict[str, list[dict[str, Any]]] = {
    "x_status": [{}, {"metrics": True}],
    "x_account": [{}, {"followers": True}],
    "x_post": [{"text": "", "proposal_id": "{draft}"}],
    "x_delete_post": [{"tweet_id": "12345"}],
    "x_draft_posts": [{"brief": "a brief"}],
    "x_schedule_post": [{"text": "", "when": "2h", "proposal_id": "{draft}"}],
    "x_list_scheduled": [{}],
    "x_cancel_scheduled_post": [{"post_id": 1}],
    "x_download_media": [{"post": "12345"}],
    "x_upload_media": [{"file_path": "missing.png"}],
    "x_api_operations": [{"search": "followers"}],
    "x_api_read": [{"operation": "getUsersMe"}],
    "x_api_act": [{"operation": "createPosts", "body": {"text": "x"}}],
}


def _tool_case(name: str) -> Callable[[Matrix], None]:
    def run(w: World) -> None:
        before = w.views("b")
        for arguments in _TOOL_CALLS[name]:
            filled = {k: (w.ids["b"]["draft"] if v == "{draft}" else v) for k, v in arguments.items()}
            answer = w.tool("a", name, **filled)
            assert w.marks.b not in answer, (name, arguments, answer[:300])
        assert w.views("b") == before, f"{name} changed what B's space shows itself"
        _only_own_keys(w)

    run.__name__ = f"tool_{name}"
    return run


for _name in _TOOL_CALLS:
    case(f"tool:{_name}", ITEM)(_in_world(_tool_case(_name)))
