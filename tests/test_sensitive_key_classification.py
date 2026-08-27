"""Sensitive-key classifier coverage (audit M3): vault-gaps enumeration.

Pins the extended ``is_sensitive_config_key`` rules — webhook URLs (which
carry bearer-ish paths), database/dsn locator URLs (user:password@host) and
key-material file endings — plus the false-positive guards that must stay
plaintext. Any change to the classifier should update BOTH lists here.
"""

from __future__ import annotations

import pytest

from kazma_core.config_store import is_sensitive_config_key


@pytest.mark.parametrize(
    "key",
    [
        # Pre-existing core names (regression guard).
        "llm.api_key",
        "connectors.telegram.token",
        "email.smtp.password",
        "connectors.github.app_private_key",
        # Webhook URLs carry embedded credentials/bearer paths.
        "notifications.webhook_url",
        "slack.webhook",
        "hooks.incoming_webhook",
        # Locator URLs of database-ish subsystems embed user:password@host.
        "database.url",
        "kazma_database_url",
        "db.dsn",
        "memory.backends.redis.url",
        "state.postgres.connection_string",
        "graph.backends.dsn",
        # Key-material endings / variants.
        "llm.encryption_key",
        "vault.encryption_key",
        "jwt.signing_key",
        "sso.jwt_signing_key",
        "tls.private_key_path",
        "connectors.github.app_private_key_path",
    ],
)
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_config_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        # The original guards must stay green.
        "agent.language",
        "token_count",  # 'token' word but a counter, not a credential
        "tokenizer.vocab_size",
        # Database WORD alone is not enough — only locator-shaped values count.
        "metrics.database_query_count",
        "swarm.max_visits",
        "backup.pg.retention",
        "documents.limits.max_pages",
        "models.defaults.coding",
        "api.rate_limit.chat_per_minute",
    ],
)
def test_nonsensitive_keys(key: str) -> None:
    assert is_sensitive_config_key(key) is False


def test_empty_key() -> None:
    assert is_sensitive_config_key("") is False


def test_uppercase_and_dash_normalisation() -> None:
    assert is_sensitive_config_key("Webhook-URL") is True
    assert is_sensitive_config_key("STATE.POSTGRES.CONNECTION_STRING") is True
