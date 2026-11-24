"""A write that changes the whole install is the admin's (2026-11-15).

An operator session (or token) reached routes outside
``platform_rbac._ADMIN_PREFIXES`` that write a credential every chat uses,
the active workspace or a process-wide switch: the GitHub token and App key
(``/api/github/token``, ``/api/github/app/config``, the OAuth sign-in and its
revoke), a clone that becomes the active workspace, creating, switching and
deleting workspaces and the folders outside them every file tool may use,
the chat apps' stop, a division request's decision and a model profile's
key. They are admin now (``_ADMIN_WRITE_PREFIXES``: reads stay open). Once
Spaces is enforced the GitHub token, sign-in and revoke are a space's own and
its members' (the review of S1-17c, ``platform_rbac._connects_a_spaces_own_account``,
held in ``tests/test_github_account_spaces.py``); this gate runs on an install
that does not enforce Spaces, where they stay the admin's.

The gate, both ways against the built app: every route in
:data:`INSTALL_WIDE_WRITES` is served and admin-only; every write route whose
code reaches an install-wide sink is in that table, or in
:data:`NOT_INSTALL_WIDE` with the reason it is not. Behaviour through
``create_app()`` with an operator session and an operator token; the rule
without the new prefixes is the negative control. With it, the model profile
rule: a key posted masked beside a changed address is refused, never sent to
the new host (providers too).
"""

from __future__ import annotations

import json
import re

import pytest
from kazma_core.security import platform_rbac
from kazma_core.security.web_sessions import SESSION_COOKIE, create_session

from tests._route_reach import reach
from tests._secured_app import SECRET, call, secured_app

#: (METHOD, route) -> what it changes for the whole install.
INSTALL_WIDE_WRITES: dict[tuple[str, str], str] = {
    ("POST", "/api/github/token"): "the GitHub token every chat and tool uses",
    ("POST", "/api/github/app/config"): "the GitHub App id and private key",
    ("POST", "/api/github/oauth/start"): "a GitHub sign-in whose token becomes the install's",
    ("POST", "/api/github/oauth/revoke"): "the install's GitHub sign-in",
    ("POST", "/api/github/repos/clone"): "a clone that becomes the active workspace",
    ("POST", "/api/workspaces/create"): "a workspace every tool may be pointed at",
    ("POST", "/api/workspaces/switch"): "the active workspace, process-wide",
    ("POST", "/api/workspaces/delete"): "a workspace, and the active one when it goes",
    ("POST", "/api/workspace/select"): "the active workspace, process-wide",
    ("PUT", "/api/workspace/extra-roots"): "folders outside the workspace every file tool may use",
    ("POST", "/api/gateway/stop"): "every chat app at once",
    ("POST", "/api/gateway/start"): "every chat app at once",
    ("POST", "/api/gateway/refresh-adapters"): "every chat app's connection",
    ("POST", "/api/divisions/requests/{request_id}/approve"): "a division's authorization",
    ("POST", "/api/divisions/requests/{request_id}/deny"): "a division's authorization",
    ("POST", "/api/models/profiles"): "a model profile's key and address",
    ("DELETE", "/api/models/profiles/{name}"): "a saved model profile",
    ("POST", "/api/models/saved"): "a model profile's key and address",
    ("DELETE", "/api/models/saved/{name}"): "a saved model profile",
    ("POST", "/api/swarm/stop"): "every swarm worker",
    ("POST", "/api/swarm/workers"): "a worker and its provider key",
    ("POST", "/api/saas/users"): "a platform user and their password",
    ("PATCH", "/api/saas/users/{username}"): "a platform user's role and password",
}

#: Write routes whose code reaches a sink below, and why each is not an
#: install-wide write.
NOT_INSTALL_WIDE: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): "reads a password to sign in; changes nothing install-wide",
    ("POST", "/api/chat/stream"): "names a provider key only to resolve the chat's model",
    ("POST", "/api/voice/stt"): "names the speech provider's key to call it",
    ("POST", "/api/webhooks/telegram"): "Telegram's update, read by the adapter",
    ("POST", "/api/spaces/{space_id}/export"): "reads the password the export is sealed with; "
    "one zip of the caller's own space (its owner only) is written to a temporary folder and "
    "sent back: nothing install-wide changes",
    ("POST", "/api/spaces/{space_id}/move"): "reads the password the bundle is sealed with; one sealed "
    "file of one shared space, made for an owner of that space only, as the identity store records it "
    "(space_move._must_own, and spaces_api._refusal before it: the admin who is no owner and an API token "
    "below admin are refused), and sent back. It changes that space's own key and its record; nothing of "
    "the install's",
    ("PUT", "/api/account/model-keys/{provider}"): "a member's own model key (Spaces S2-04): kept in "
    "their personal space, used only for their own calls; the install's provider rows and keys are "
    "not touched",
}

_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: What in a route's code writes a credential every chat uses, the active
#: workspace or a process-wide switch.
_SINKS = re.compile(
    r"set_active_workspace|set_durable_roots|configure_workspace|notify_root_changed|"
    r"\.create_workspace\(|save_model_profile|bind_github_token|app_private_key|"
    r"os\.environ\[[^\]]+\]\s*=|os\.environ\.(?:pop|update|setdefault)\(|"
    r"vault\w*\.(?:store|delete)\(|store_install_scoped|persist_\w*tokens|"
    r"gateway\.(?:stop|start)\(|stop_all\(|refresh_adapters|"
    r"switch_active_model|switch_active_provider|"
    r"create_local_user|set_local_password|create_api_token|"
    r"\bapi_key\b|\bpassword\b"
)


def _all_routes(routes) -> list:
    out = []
    for route in routes:
        effective = getattr(route, "effective_candidates", None)
        if callable(effective):
            out.extend(_all_routes(effective()))
            continue
        inner = getattr(route, "original_router", None)
        out.extend(_all_routes(inner.routes) if inner is not None else [route])
    return out


def _reach(endpoint) -> str:
    """The endpoint's source and every function of its module it calls
    (``tests/_route_reach.reach``: each module parsed once)."""
    return reach(endpoint)


def _concrete(template: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", template)


def _admin_only(route, method: str) -> bool:
    """Whether an operator is refused *method route*: the gate's role rule,
    the swarm's role table, or the route's own admin check."""
    if not platform_rbac.role_allows("operator", _concrete(route.path), method):
        return True
    from kazma_ui.swarm_panel.route_roles import SWARM_ROUTE_ROLES

    if SWARM_ROUTE_ROLES.get((method, route.path)) == "admin":
        return True
    return bool(re.search(r"\b_?require_admin\b|\badmin_decision\b", _reach(route.endpoint)))


@pytest.fixture(scope="module")
def write_routes():
    with pytest.MonkeyPatch.context() as mp:
        app = secured_app(mp)
        yield [
            (route, method)
            for route in _all_routes(app.routes)
            for method in sorted((getattr(route, "methods", None) or set()) & _BODY_METHODS)
            if getattr(route, "endpoint", None) is not None
        ]


def test_every_declared_route_is_served(write_routes):
    served = {(m, r.path) for r, m in write_routes}
    for table in (INSTALL_WIDE_WRITES, NOT_INSTALL_WIDE):
        stale = sorted(key for key in table if key not in served)
        assert not stale, f"declared routes the app does not serve: {stale}"


def test_every_install_wide_write_is_the_admins(write_routes):
    by_key = {(m, r.path): r for r, m in write_routes}
    open_to_operators = sorted(
        key for key in INSTALL_WIDE_WRITES if not _admin_only(by_key[key], key[0])
    )
    assert not open_to_operators, f"an operator reaches these install-wide writes: {open_to_operators}"
    for method, path in INSTALL_WIDE_WRITES:
        assert platform_rbac.role_allows("admin", _concrete(path), method)


def test_every_write_reaching_an_install_wide_sink_is_the_admins(write_routes):
    """The class, read from the code: a write route whose code reaches a
    sink is admin-only, or says in NOT_INSTALL_WIDE why it need not be."""
    found = {(m, r.path): r for r, m in write_routes if _SINKS.search(_reach(r.endpoint))}
    open_to_operators = sorted(
        key for key, route in found.items()
        if not _admin_only(route, key[0]) and key not in NOT_INSTALL_WIDE
    )
    assert not open_to_operators, (
        "an operator reaches a write route that changes the whole install: make it "
        "admin (platform_rbac) and add it to INSTALL_WIDE_WRITES, or say why not in "
        f"NOT_INSTALL_WIDE: {open_to_operators}"
    )
    stale = sorted(key for key in NOT_INSTALL_WIDE if key not in found or _admin_only(found[key], key[0]))
    assert not stale, f"NOT_INSTALL_WIDE names routes that reach no sink or are admin anyway: {stale}"


def test_negative_control_the_gate_flags_the_reported_routes_on_the_old_rule(write_routes, monkeypatch):
    """Without the new write prefixes the scan finds the routes the review
    reported open to an operator."""
    monkeypatch.setattr(platform_rbac, "_ADMIN_WRITE_PREFIXES", ())
    found = {(m, r.path): r for r, m in write_routes if _SINKS.search(_reach(r.endpoint))}
    flagged = {key for key, route in found.items()
               if not _admin_only(route, key[0]) and key not in NOT_INSTALL_WIDE}
    assert {("POST", "/api/github/token"), ("POST", "/api/workspaces/switch"),
            ("POST", "/api/models/profiles"), ("PUT", "/api/workspace/extra-roots")} <= flagged


def test_the_scan_finds_a_planted_sink():
    """Negative control for the scan: a route that switches the workspace
    through a helper of its module is found."""

    def _switch(store, ws):
        store.set_active_workspace(ws)

    def planted(store, ws):
        _switch(store, ws)

    planted.__module__ = __name__
    assert _SINKS.search(_reach(planted))


# -- behaviour through the real app --------------------------------------------

REPORTED = [
    ("POST", "/api/github/token"),
    ("POST", "/api/github/app/config"),
    ("POST", "/api/github/oauth/start"),
    ("POST", "/api/github/oauth/revoke"),
    ("POST", "/api/workspaces/create"),
    ("POST", "/api/workspaces/switch"),
    ("POST", "/api/gateway/stop"),
    ("POST", "/api/divisions/requests/r1/approve"),
    ("POST", "/api/models/profiles"),
]

JSON = {"content-type": "application/json"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(platform_rbac, "multi_user_enabled", lambda: False)
    return secured_app(monkeypatch)


def _operator_session_headers() -> dict[str, str]:
    sid = create_session(actor="test", role="operator", username="operator@example.com")
    return {**JSON, "cookie": f"{SESSION_COOKIE}={sid}"}


@pytest.mark.parametrize("method,path", REPORTED)
def test_an_operator_session_is_refused(app, method, path):
    assert call(app, method, path, body=b"{}", headers=_operator_session_headers()).status == 403


def test_an_operator_token_is_refused(app):
    made = call(app, "POST", "/api/settings/account/tokens", body=b'{"name": "agent"}',
                headers={**JSON, "x-kazma-secret": SECRET}).json()
    for method, path in REPORTED:
        answer = call(app, method, path, body=b"{}", headers={**JSON, "x-api-token": made["token"]})
        assert answer.status == 403, (method, path)


def test_reads_stay_open_to_an_operator(app):
    headers = _operator_session_headers()
    assert call(app, "GET", "/api/models/profiles", headers=headers).status == 200
    assert call(app, "GET", "/api/workspaces", headers=headers).status == 200


def test_negative_control_without_the_write_prefixes_an_operator_got_in(app, monkeypatch):
    monkeypatch.setattr(platform_rbac, "_ADMIN_WRITE_PREFIXES", ())
    answer = call(app, "POST", "/api/models/profiles", body=b"{}", headers=_operator_session_headers())
    assert answer.status != 403


# -- a masked key stays with its address ----------------------------------------

ADMIN = {**JSON, "x-kazma-secret": SECRET}


def _post(app, path, body):
    return call(app, "POST", path, body=json.dumps(body).encode(), headers=ADMIN)


def test_a_profile_key_posted_masked_with_a_new_address_is_refused(app):
    from kazma_core.model_registry import get_model_registry

    saved = {"name": "work", "provider": "custom", "base_url": "https://api.example.com/v1",
             "api_key": "sk-real-profile-key-123456", "model": "m"}
    assert _post(app, "/api/models/profiles", saved).status == 200
    moved = {**saved, "api_key": "***", "base_url": "https://collector.attacker.example/v1"}
    answer = _post(app, "/api/models/profiles", moved)
    assert answer.status == 400
    stored = get_model_registry().get_model_profile("work")
    assert stored["api_key"] == "sk-real-profile-key-123456"
    assert stored["base_url"] == "https://api.example.com/v1"
    # Posted back unchanged (a trailing slash and the host's case aside), the
    # masked key keeps the stored one.
    same = {**saved, "api_key": "***", "base_url": "https://API.example.com/v1/"}
    assert _post(app, "/api/models/profiles", same).status == 200
    assert get_model_registry().get_model_profile("work")["api_key"] == "sk-real-profile-key-123456"


def test_negative_control_without_the_address_rule_the_key_moved(app, monkeypatch):
    from kazma_core.model_registry import get_model_registry
    from kazma_core.security import url_credentials

    saved = {"name": "w2", "provider": "custom", "base_url": "https://api.example.com/v1",
             "api_key": "sk-real-profile-key-654321", "model": "m"}
    assert _post(app, "/api/models/profiles", saved).status == 200
    monkeypatch.setattr(url_credentials, "same_address", lambda a, b: True)
    moved = {**saved, "api_key": "***", "base_url": "https://collector.attacker.example/v1"}
    assert _post(app, "/api/models/profiles", moved).status == 200
    stored = get_model_registry().get_model_profile("w2")
    assert stored["base_url"] == "https://collector.attacker.example/v1"
    assert stored["api_key"] == "sk-real-profile-key-654321"


def test_a_provider_key_posted_masked_with_a_new_address_is_refused(app):
    from kazma_core.model_registry import get_model_registry

    row = {"name": "mine", "base_url": "https://api.example.com/v1", "api_key": "sk-provider-key-abcdef"}
    assert _post(app, "/api/providers", row).status == 200
    moved = {**row, "api_key": "****cdef", "base_url": "https://collector.attacker.example/v1"}
    assert _post(app, "/api/providers", moved).status == 400
    registry = get_model_registry()
    assert registry.get_provider("mine")["base_url"] == "https://api.example.com/v1"
    _url, key = registry.resolve_provider_credentials("mine")
    assert key == "sk-provider-key-abcdef"


def test_a_check_against_a_new_address_never_takes_the_stored_key(app, monkeypatch):
    from kazma_core import provider_check
    from kazma_core.model_registry import get_model_registry

    registry = get_model_registry()
    assert _post(app, "/api/providers", {"name": "chk", "base_url": "https://api.example.com/v1",
                                         "api_key": "sk-check-key-778899"}).status == 200
    with pytest.raises(provider_check.CandidateRefused) as refused:
        provider_check.candidate_entry(registry, "chk", base_url="https://collector.attacker.example/v1")
    assert refused.value.code == "api_key_required"
    same = provider_check.candidate_entry(registry, "chk", base_url="https://api.example.com/v1/")
    assert same["api_key"] == "sk-check-key-778899"
    # Negative control: with every address "the same", the stored key went to
    # the typed host.
    monkeypatch.setattr(provider_check, "address_moved", lambda typed, stored: False)
    leaked = provider_check.candidate_entry(registry, "chk", base_url="https://collector.attacker.example/v1")
    assert leaked["api_key"] == "sk-check-key-778899"
    assert leaked["base_url"] == "https://collector.attacker.example/v1"
