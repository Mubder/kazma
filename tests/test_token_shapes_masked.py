"""A token is masked by its shape, by every redactor and masker found in the source.

Measured 2026-11-15: the shared redactor (``errors.redact_secrets``, used by
the logs' readers, the problem report and ``read_system_logs``) knew five
shapes and missed Slack ``xapp-``, ``Authorization: Bot`` (Discord),
Google ``ya29.``/``1//`` tokens, Meta ``EAA...``, Kazma's own ``kazma_...``
API tokens, JWTs, ``hf_``, ``glpat-`` and AWS access keys; the Settings and
``config_read`` masker (``config_mask.mask_deep``) masked a value only when it
was itself a URL (``{'post': 'curl https://bob:PW@h/x'}`` came back as it
was) and no token at all; an approval card showed a ``Bearer`` token inside
a ``shell_exec`` command and ``config_save(key="llm.api_key", value=...)``'s
value, on every chat platform and in ``hitl_gates.db``.

Now one table (``security.token_shapes.TOKEN_SHAPES``) is read by every one
of them. The census below finds the maskers from the product source (named
mask*/redact*/scrub*/sanitize*, deciding by a key's name or calling a value
masker); each is fed every shape. Planted fake tokens are built at run time
from pieces, so no file holds one whole.
"""

from __future__ import annotations

import ast
import json
import re

import pytest

from kazma_core.security import token_shapes
from tests.test_url_credentials import _decides_by_key_name, _product_sources

_PAD = "Ab3x" * 6


def _tokens() -> dict[str, tuple[str, str]]:
    """shape -> (the text a masker sees, the secret part that must go)."""
    discord = "M" + "Tk4" * 8 + ".GhIjKl." + "x9Y" * 11
    jwt = "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "SflKx" * 6
    pieces = {
        "authorization": "Bea" + "rer ",
        "openai": "",
        "slack_app": "",
        "slack_bot": "",
        "github": "",
        "gitlab": "",
        "huggingface": "",
        "google_api": "",
        "google_oauth": "",
        "google_refresh": "",
        "meta": "",
        "aws": "",
        "kazma": "",
        "jwt": "",
        "telegram": "",
        "discord_bot": "Bo" + "t ",
        "discord": "",
    }
    secrets_ = {
        "authorization": "a1B2" * 6,
        "openai": "s" + "k-proj-" + _PAD,
        "slack_app": "xa" + "pp-1-A0" + "12345678-" + "9" * 13 + "-" + "abc123" * 8,
        "slack_bot": "xo" + "xb-1234567890-" + "abc" * 8,
        "github": "gh" + "p_" + _PAD + "1234",
        "gitlab": "gl" + "pat-" + _PAD,
        "huggingface": "h" + "f_" + _PAD,
        "google_api": "AI" + "za" + "Sy" + _PAD + "123456",
        "google_oauth": "ya" + "29." + "a0Ab" * 10,
        "google_refresh": "1/" + "/0" + "gAbC" * 10,
        "meta": "EA" + "AB" + "zZ9" * 12,
        "aws": "AK" + "IA" + "QWERTYUIOP123456",
        "kazma": "kaz" + "ma_" + "0123456789abcdef" * 4,
        "jwt": jwt,
        "telegram": "1234567" + "89:AA" + "Bb" * 16,
        "discord_bot": discord,
        "discord": discord,
    }
    return {name: (f"call this: {pieces[name]}{secret} please", secret) for name, secret in secrets_.items()}


TOKENS = _tokens()


@pytest.mark.parametrize("name", sorted(TOKENS))
def test_each_shape_is_masked(name):
    text, secret = TOKENS[name]
    out = token_shapes.mask_tokens(text)
    assert secret not in out, out
    assert out.startswith("call this:") and out.endswith("please")  # the rest stays
    assert token_shapes.has_token(text)


def test_an_authorization_scheme_word_stays():
    text, secret = TOKENS["authorization"]
    assert "Bearer ****" in token_shapes.mask_tokens(text)
    text, secret = TOKENS["discord_bot"]
    assert "Bot ****" in token_shapes.mask_tokens(text)


@pytest.mark.parametrize("prose", [
    "the basic configuration of the bearer of good news",
    "kazma_core.security.token_shapes",
    "sort_key and public_key are settings",
    "token 12 of 40",
    "C:\\Users\\me\\Documents\\notes.md",
    "https://api.example.com/v1/models?page=2",
    "version 1.2.3 build 20261115",
    "the bot answered in 3 seconds",
])
def test_ordinary_text_is_left_alone(prose):
    assert token_shapes.mask_tokens(prose) == prose
    assert not token_shapes.has_token(prose)


# ── every masker in the source ───────────────────────────────────────────

_VALUE_MASKERS = re.compile(
    r"^(redact_secrets|mask_urls_in_text|mask_url_credentials_deep|mask_deep|mask_tokens|"
    r"redact_arguments|redact_text|redacted_gate_payload)$")


def _calls_a_value_masker(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
            if _VALUE_MASKERS.match(name or ""):
                return True
    return False


def maskers(sources: dict[str, str]) -> set[str]:
    """``path::qualname`` of every function named mask*/redact*/scrub*/sanitize*
    that decides by a key's name or hands a value to a value masker."""
    found: set[str] = set()

    def visit(node: ast.AST, path: str, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                if re.search(r"mask|redact|scrub|sanitiz", child.name, re.I) and (
                        _decides_by_key_name(child) or _calls_a_value_masker(child)):
                    found.add(f"{path}::{qual}")
                visit(child, path, qual)
            elif isinstance(child, ast.ClassDef):
                visit(child, path, f"{prefix}.{child.name}" if prefix else child.name)
            else:
                visit(child, path, prefix)

    for path, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        visit(tree, path, "")
    return found


def _p_redact_secrets(text):
    from kazma_core.errors import redact_secrets

    return redact_secrets(text)


def _p_mask_secret_text(text):
    from kazma_core.errors import _REDACT_SECRETS, _mask_secret_text

    return _mask_secret_text(text, _REDACT_SECRETS, "<redacted>")


def _p_problem_report(text):
    from kazma_core.support.problem_report import redacted

    return redacted(text)


def _p_system_logs(text):
    from kazma_skills.native.system_health_monitor.tools import _sanitize_log_text

    return _sanitize_log_text(text)


def _p_urls_in_text(text):
    from kazma_core.security.url_credentials import mask_urls_in_text

    return mask_urls_in_text(text)


def _p_url_deep(text):
    from kazma_core.security.url_credentials import mask_url_credentials_deep

    return mask_url_credentials_deep({"a": [text], "b": (text,)})


def _p_mask_deep(text):
    from kazma_core.security.config_mask import mask_deep

    return mask_deep({"note": text, "cmd": [text], "as_json": json.dumps({"x": text})})


def _p_config_read(text):
    """The agent's config_read reads through mask_deep."""
    from kazma_core.security.config_mask import mask_deep

    return mask_deep(text)


def _p_redact_arguments(text):
    from kazma_core.security.approval_args import redact_arguments

    return redact_arguments("shell_exec", {"command": text, "nested": {"x": [text]}})


def _p_redact_text(text):
    from kazma_core.security.approval_args import redact_text

    return redact_text(text)


def _p_arguments_json(text):
    from kazma_core.security.approval_args import redacted_arguments_json

    return [redacted_arguments_json("shell_exec", json.dumps({"command": text})),
            redacted_arguments_json("shell_exec", text)]


def _p_gate_payload(text):
    from kazma_core.security.approval_args import _redact_call, redacted_gate_payload

    payload = {"tool": "shell_exec", "args": {"command": text}, "message": text,
               "tools": [{"tool": "x", "args": {"y": text}}], "other": text}
    return [redacted_gate_payload(json.dumps(payload)), _redact_call(payload, "shell_exec")]


def _p_gate_message(text):
    from kazma_core.safety.hitl_gates import _masked_message

    return _masked_message(text)


def _p_redact_for_log(text):
    """It cuts the line at 80 characters AFTER masking: the leak check reads
    a secret's first characters, which come before the cut."""
    from kazma_core.config_store import _redact_for_log

    return [_redact_for_log("x.note", text), _redact_for_log("x.cmd", {"a": text})]


def _p_config_export(text):
    from kazma_gateway.slash_commands import _redact_secrets

    return _redact_secrets({"note": text, "cmd": [text]})


def _p_settings_backup(text):
    from kazma_core.settings_restore import _mask_backup_secrets

    return [_mask_backup_secrets("x.note", text), _mask_backup_secrets("x.list", [text])]


def _p_topic_summary(text):
    from kazma_core.memory.topic_summaries import _scrub

    return _scrub(text)


def _p_settings_page(text):
    from kazma_ui.settings import _mask_sensitive_values

    data = {"general": {"note": text}}
    _mask_sensitive_values(data)
    return data


def _p_pg_argv(text):
    """The sentence as one argument, and each token-shaped word as its own."""
    from kazma_core.migration.pg_bridge import _redact_cmd

    words = [w for w in text.split(" ") if token_shapes.has_token(w)]
    return _redact_cmd(["pg_dump", text, *words])


def _p_self_check_mask(text):
    from kazma_cli.self_check import _mask

    return _mask(text)


PROBES = {
    "kazma-core/kazma_core/errors.py::redact_secrets": _p_redact_secrets,
    "kazma-core/kazma_core/errors.py::_mask_secret_text": _p_mask_secret_text,
    "kazma-core/kazma_core/support/problem_report.py::redacted": _p_problem_report,
    "kazma-skills/kazma_skills/native/system_health_monitor/tools.py::_sanitize_log_text": _p_system_logs,
    "kazma-core/kazma_core/security/url_credentials.py::mask_urls_in_text": _p_urls_in_text,
    "kazma-core/kazma_core/security/url_credentials.py::mask_url_credentials_deep": _p_url_deep,
    "kazma-core/kazma_core/security/config_mask.py::mask_deep": _p_mask_deep,
    "kazma-core/kazma_core/security/approval_args.py::redact_arguments": _p_redact_arguments,
    "kazma-core/kazma_core/security/approval_args.py::redact_text": _p_redact_text,
    "kazma-core/kazma_core/security/approval_args.py::redacted_arguments_json": _p_arguments_json,
    "kazma-core/kazma_core/security/approval_args.py::redacted_gate_payload": _p_gate_payload,
    "kazma-core/kazma_core/security/approval_args.py::_redact_call": _p_gate_payload,
    "kazma-core/kazma_core/safety/hitl_gates.py::_masked_message": _p_gate_message,
    "kazma-core/kazma_core/config_store.py::_redact_for_log": _p_redact_for_log,
    "kazma-gateway/kazma_gateway/slash_commands.py::_redact_secrets": _p_config_export,
    "kazma-core/kazma_core/settings_restore.py::_mask_backup_secrets": _p_settings_backup,
    "kazma-core/kazma_core/memory/topic_summaries.py::_scrub": _p_topic_summary,
    "kazma-ui/kazma_ui/settings.py::_mask_sensitive_values": _p_settings_page,
    "kazma-core/kazma_core/migration/pg_bridge.py::_redact_cmd": _p_pg_argv,
    "kazma-core/kazma_core/migration/pg_bridge.py::_redact": _p_pg_argv,
    "kazma-cli/kazma_cli/self_check.py::_mask": _p_self_check_mask,
}

#: Found by the census and not fed free text -- each with its reason.
NOT_PROBED: dict[str, str] = {
    "kazma-core/kazma_core/documents/sandbox.py::_sanitized_environment": (
        "builds a document worker's environment from the variables it names; "
        "it shows nothing to anyone"
    ),
    "kazma-core/kazma_core/model_registry.py::ModelRegistry._restore_masked_profile_secrets": (
        "the reverse of a masker: it puts stored secrets back over a form's stars"
    ),
    # Form round trips: each shows a stored entry that the page posts back,
    # and a masked password is restored only as a URL password
    # (url_credentials.restore_masked_url). A token masked in a field that is
    # not a credential's (a base_url's path) would be saved back as stars. A
    # credential-NAMED field is masked whole; a token in an MCP server's
    # command, env or header is a secret by its shape (mcp.secrets).
    "kazma-core/kazma_core/memory/backends.py::mask_backends_cfg": (
        "form round trip: Settings -> Memory backends posts the masked view back"
    ),
    "kazma-core/kazma_core/security/provider_mask.py::mask_provider_entry": (
        "form round trip: the provider dialog posts the masked entry back"
    ),
    "kazma-ui/kazma_ui/providers.py::_mask_connector_entry": (
        "form round trip: the connector card posts the masked entry back"
    ),
    "kazma-core/kazma_core/model_registry.py::ModelRegistry._mask_profile": (
        "form round trip: a saved profile's form posts the masked profile back"
    ),
}


def test_every_masker_masks_every_token_shape():
    found = maskers(_product_sources())
    unlisted = sorted(found - PROBES.keys() - NOT_PROBED.keys())
    assert not unlisted, (
        "A function masks secrets and has no token probe. Mask values with "
        "kazma_core.security.url_credentials.mask_urls_in_text (or token_shapes."
        "mask_tokens) and add a probe to PROBES:\n  " + "\n  ".join(unlisted))
    stale = sorted((PROBES.keys() | NOT_PROBED.keys()) - found)
    assert not stale, f"listed but no longer found (renamed?): {stale}"
    leaks = []
    for masker, probe in PROBES.items():
        for name, (text, secret) in TOKENS.items():
            # Its first characters: a masker that cuts its output must not
            # pass for one that masks.
            if secret[:12] in str(probe(text)):
                leaks.append(f"{masker} <- {name}")
    assert not leaks, "these still show a token:\n  " + "\n  ".join(leaks)


def test_the_census_sees_a_planted_masker():
    """Negative control: a new masker of either kind is found."""
    planted = {
        "kazma-x/kazma_x/m.py": (
            "def mask_things(d):\n"
            "    return {k: '***' if k == 'api_key' else v for k, v in d.items()}\n"
            "def scrub_line(t):\n"
            "    return redact_secrets(t)\n"
            "def unrelated_mask(d):\n"
            "    return d\n"
        )
    }
    assert maskers(planted) == {"kazma-x/kazma_x/m.py::mask_things", "kazma-x/kazma_x/m.py::scrub_line"}


#: The shared redactor's value shapes before 2026-11-15 (errors._SECRET_FRAGMENTS).
_OLD_SHAPES = re.compile(
    r"\b(?:bearer|basic)\s+(?:(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{16,}|[A-Za-z0-9._~+/=-]{24,})"
    r"|\b(?:sk|xox[baprs]|ghp|gho|ghu|ghs|github_pat)[-_][A-Za-z0-9_-]{8,}"
    r"|\bAIza[A-Za-z0-9_-]{30,}"
    r"|\b(?:bot)?\d{6,}:[A-Za-z0-9_-]{30,}",
    re.IGNORECASE,
)


def test_control_the_old_redactor_left_most_shapes():
    leaked = sorted(name for name, (text, secret) in TOKENS.items()
                    if secret in _OLD_SHAPES.sub("<redacted>", text))
    assert {"slack_app", "discord_bot", "google_oauth", "google_refresh", "meta", "kazma",
            "jwt", "huggingface", "gitlab", "aws"} <= set(leaked), leaked


def test_control_the_old_settings_masker_left_text_and_tokens():
    """The string leaf of mask_deep before 2026-11-15: a value masked only
    when it was itself a URL."""
    from kazma_core.security.url_credentials import mask_url_credentials

    post = "curl https://bob:" + "PW-x9" + "@h/x"
    assert "PW-x9" in mask_url_credentials(post)
    from kazma_core.security.config_mask import mask_deep

    assert "PW-x9" not in str(mask_deep({"post": post}))
    text, secret = TOKENS["authorization"]
    assert secret in mask_url_credentials(text)


# ── config_save's value on the card ──────────────────────────────────────

def test_config_save_hides_the_value_of_a_secret_setting():
    from kazma_core.security.approval_args import HIDDEN, redact_arguments
    from kazma_core.swarm.approval_card import render_arguments
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt

    value = "plain" + "-provider-key-" + "9" * 12  # no token shape: hidden by its key
    args = {"key": "llm.api_key", "value": value}
    assert redact_arguments("config_save", args) == {"key": "llm.api_key", "value": HIDDEN}
    card = _build_approval_prompt({"tool": "config_save", "args": args}, "th-1", platform="telegram")["text"]
    assert value not in card and "llm.api_key" in card
    assert value not in render_arguments(json.dumps(args), tool="config_save").text
    from kazma_core.security.approval_args import redacted_gate_payload

    assert value not in redacted_gate_payload(json.dumps({"tool": "config_save", "args": args}))
    # An ordinary setting's value is shown.
    plain = {"key": "agent.max_iterations", "value": "60"}
    assert redact_arguments("config_save", plain) == plain


def test_control_without_the_rule_the_card_showed_the_value(monkeypatch):
    from kazma_core.security import approval_args

    monkeypatch.setattr(approval_args, "SECRET_WHEN_NAMED", {})
    value = "plain" + "-provider-key-" + "9" * 12
    assert approval_args.redact_arguments("config_save", {"key": "llm.api_key", "value": value})["value"] == value


def test_a_bearer_token_in_a_shell_command_is_hidden_on_every_card():
    from kazma_core.swarm.approval_card import render_arguments
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt

    text, secret = TOKENS["authorization"]
    args = {"command": f'curl -H "Authorization: {text.split(": ", 1)[1]}" https://api.example/x'}
    for platform in ("telegram", "discord", "slack"):
        card = _build_approval_prompt({"tool": "shell_exec", "args": args}, "th-1", platform=platform)["text"]
        assert secret not in card, platform
    assert secret not in render_arguments(json.dumps(args), tool="shell_exec").text
