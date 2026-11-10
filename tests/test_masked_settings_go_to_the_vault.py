"""Every setting the Settings API masks as a secret is also stored in the vault.

Audit 2026-11-10: ``notifications.push.vapid_private_key`` (the Web Push
signing key, written by ``kazma_ui.push.ensure_vapid_keys``) was masked by the
API masker (``security.config_mask.key_is_secret``: the ``private_key``
fragment) but ``config_store.is_sensitive_config_key`` -- what sends a value to
the vault -- matched no rule for it, so the PEM sat in plaintext in
settings.db, in every dump, backup and export. ``*_access_key_id``,
``*_passphrase``, ``authorization``, ``bearer`` and ``pat`` were the same shape.

The gate: over the shipped settings, the validated keys and a corpus of
credential names, a key the masker hides is a key the vault takes, unless it is
declared below with why (a name the masker over-matches that is no secret).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kazma_core import config_store as cs_mod
from kazma_core.config_store import ConfigStore, is_sensitive_config_key, is_vault_ref
from kazma_core.security import vault as vault_mod
from kazma_core.security.config_mask import key_is_secret

REPO = Path(__file__).resolve().parents[1]
VAPID = "notifications.push.vapid_private_key"
PEM = "-----BEGIN PRIVATE KEY-----\nMIGHdemo-not-a-real-key\n-----END PRIVATE KEY-----\n"

#: Credential-shaped names (the finding's and their siblings).
CREDENTIAL_KEYS = (
    VAPID,
    "connectors.github.app_private_key",
    "integrations.ssh.deploy_private_key",
    "backups.offsite.s3.access_key",
    "backups.offsite.s3.access_key_id",
    "backups.offsite.s3.aws_access_key_id",
    "backups.offsite.s3.aws_secret_access_key",
    "backups.restic.passphrase",
    "backups.restic.repo_passphrase",
    "connectors.http.authorization",
    "connectors.http.bearer",
    "connectors.github.pat",
    "connectors.slack.bot_token",
    "connectors.x.client_secret",
    "llm.api_key",
    "email.imap.password",
    "integrations.oauth.credential",
)

#: The masker hides these; they are not secrets, so they stay plaintext.
MASKED_NOT_SECRET = {
    "memory.embedding.api_key_env": "the NAME of an environment variable, not its value",
    "embedding.api_key_env": "the NAME of an environment variable, not its value",
    "agent.turn_token_budget": "a number of model tokens (a budget), not a credential",
}


def _yaml_keys() -> list[str]:
    data = yaml.safe_load((REPO / "kazma.yaml").read_text(encoding="utf-8")) or {}
    out: list[str] = []

    def walk(node, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}.{k}" if prefix else str(k))
        else:
            out.append(prefix)

    walk(data, "")
    return out


def _corpus() -> set[str]:
    from kazma_core.settings_validation import _rules

    return set(_yaml_keys()) | {str(k) for k in _rules()} | set(CREDENTIAL_KEYS)


def test_every_masked_setting_goes_to_the_vault():
    unrouted = sorted(k for k in _corpus()
                      if key_is_secret(k) and not is_sensitive_config_key(k)
                      and k not in MASKED_NOT_SECRET)
    assert not unrouted, f"masked by the API but stored in plaintext: {unrouted}"


def test_the_declared_exceptions_are_still_needed():
    stale = [k for k in MASKED_NOT_SECRET if not key_is_secret(k) or is_sensitive_config_key(k)]
    assert not stale, f"no longer an exception: {stale}"


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_each_credential_name_is_routed(key):
    assert is_sensitive_config_key(key)


@pytest.mark.parametrize("key", ["agent.trim.token_budget", "llm.max_tokens", "workspace.selected_path",
                                 "notifications.push.vapid_public_key", "ui.patterns", "memory.tokenizer"])
def test_ordinary_settings_stay_plaintext(key):
    assert not is_sensitive_config_key(key)


def test_negative_control_the_old_predicate_left_these_in_plaintext(monkeypatch):
    """The rule before 2026-11-10: its last-segment set and suffixes."""
    old_last = cs_mod._SENSITIVE_LAST_SEGMENTS

    def old(key: str) -> bool:
        lowered = key.lower().replace("-", "_")
        last = lowered.rsplit(".", 1)[-1]
        if last in old_last or last.endswith(("_token", "_secret", "_password", "_api_key",
                                               "_encryption_key", "_signing_key", "_webhook")):
            return True
        segments = [s for p in lowered.split(".") for s in p.split("_") if s]
        return any(s in {"password", "passwd", "secret", "credential", "credentials", "apikey"}
                   for s in segments)

    missed = [k for k in CREDENTIAL_KEYS if not old(k)]
    assert VAPID in missed and "backups.offsite.s3.aws_access_key_id" in missed
    assert "backups.restic.passphrase" in missed and "connectors.github.pat" in missed


# -- the stored row ----------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "unit-test-vault-key-32bytes-long!!")
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    vault_mod.reset_vault()
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", v)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    s = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    yield s
    s.close()
    vault_mod.reset_vault()


def _raw(store: ConfigStore, key: str):
    with store._lock:
        row = store._get_conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"])


def test_the_push_key_is_stored_as_a_pointer_and_reads_back(store):
    store.set(VAPID, PEM)  # as push.ensure_vapid_keys writes it
    assert is_vault_ref(_raw(store, VAPID))
    assert PEM not in json.dumps(_raw(store, VAPID))
    store._invalidate_key(VAPID)
    assert store.get(VAPID) == PEM


def test_a_plaintext_push_key_already_stored_is_moved(store):
    with store._lock:
        conn = store._get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key, value, category, updated_at) "
                     "VALUES (?, ?, ?, ?)", (VAPID, json.dumps(PEM), "notifications",
                                             "2026-11-01T00:00:00+00:00"))
        conn.commit()
        store._invalidate_key(VAPID)
    moved, left = store.move_plaintext_secrets()
    assert VAPID in moved and left == []
    assert is_vault_ref(_raw(store, VAPID))
    assert store.get(VAPID) == PEM
