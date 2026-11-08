"""Provider list persistence helpers for ModelRegistry (S5 extract).

Pure read/normalize helpers — load never writes (protects against data loss).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from kazma_core.providers import PROVIDER_PRESETS

__all__ = ["default_provider_entries", "detect_gcp_project_id", "load_providers", "normalize_models", "normalize_provider_entry", "parse_providers_raw", "save_providers", "seed_missing_presets"]

logger = logging.getLogger(__name__)


def normalize_models(models: Any) -> list[str]:
    """Return a sorted unique list of non-empty model id strings."""
    if isinstance(models, list):
        return sorted({str(model).strip() for model in models if str(model).strip()})
    return []


#: Fields a provider row may carry beyond the common ones (providers stages
#: 2 and 4). Kept only when set, so a row without them reads as before.
#: Secrets among them are vaulted by name like ``api_key``
#: (``config_store.is_sensitive_config_key``): ``secret_headers`` (every
#: header value), ``aws_secret_access_key``, ``aws_session_token``, and each
#: extra key's ``api_key``.
OPTIONAL_FIELDS: tuple[str, ...] = (
    "preset",  # the catalog entry the row was made from (kazma_core.providers)
    "kind",  # "chat" | "speech"
    "url_as_typed",  # the address is used exactly as typed (no /v1 added)
    "secret_headers",  # {header: value}, sent on every request
    "api_version",  # Azure
    "region", "aws_access_key_id", "aws_secret_access_key", "aws_session_token",  # Bedrock
    "api_keys",  # [{"id", "label", "api_key"}]: keys tried after the first
)


def _optional_fields(provider: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in OPTIONAL_FIELDS:
        value = provider.get(field)
        if value in (None, "", [], {}, False):
            continue
        if field == "url_as_typed":
            out[field] = bool(value)
        elif field == "kind":
            kind = str(value).strip().lower()
            if kind in ("chat", "speech"):
                out[field] = kind
        elif field == "secret_headers":
            if isinstance(value, dict):
                headers = {str(k).strip(): str(v) for k, v in value.items() if str(k).strip()}
                if headers:
                    out[field] = headers
        elif field == "api_keys":
            if isinstance(value, list):
                keys = [
                    {"id": str(k.get("id") or "").strip(), "label": str(k.get("label") or ""),
                     "api_key": str(k.get("api_key") or "")}
                    for k in value if isinstance(k, dict) and str(k.get("id") or "").strip()
                ]
                if keys:
                    out[field] = keys
        else:
            out[field] = str(value)
    return out


def normalize_provider_entry(provider: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single provider dict to the canonical shape."""
    name = str(provider.get("name") or "").strip()
    return {
        "name": name,
        "display_name": str(provider.get("display_name") or name),
        "base_url": str(provider.get("base_url") or ""),
        "api_key": str(provider.get("api_key") or ""),
        "models": normalize_models(provider.get("models", [])),
        "enabled": bool(provider.get("enabled", True)),
        "health": str(provider.get("health") or "unknown"),
        "project_id": str(provider.get("project_id") or ""),
        "location": str(provider.get("location") or "us-central1"),
        "google_mode": str(provider.get("google_mode") or ""),
        **_optional_fields(provider),
    }


def parse_providers_raw(raw: Any) -> list[dict[str, Any]]:
    """Parse ConfigStore ``providers.list`` value into a list of dicts.

    Handles legacy double-encoded JSON strings. **Never writes.**
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "[ModelRegistry] providers.list is a string but could "
                "not be parsed — returning empty list (data NOT overwritten)"
            )
            return []
        if not isinstance(raw, list):
            return []
        logger.debug(
            "[ModelRegistry] Loaded providers from legacy double-encoded "
            "format (in-memory only; will migrate on next save)"
        )
    if not isinstance(raw, list):
        return []
    return [normalize_provider_entry(item) for item in raw if isinstance(item, dict)]


def load_providers(config_store: Any) -> list[dict[str, Any]]:
    """Load and normalize providers from ConfigStore (read-only)."""
    raw = config_store.get("providers.list", [])
    return parse_providers_raw(raw)


def load_providers_unresolved(config_store: Any) -> list[dict[str, Any]]:
    """The stored provider list WITHOUT resolving vault pointers.

    ``load_providers`` goes through ``config_store.get()``, which decrypts
    every ``vault://`` pointer -- and returns ``None`` for any it cannot
    decrypt. That is the right read for "give me a usable key" and the wrong
    one for "give me what is on disk so I can write it back".
    """
    import json

    try:
        for values in config_store.get_all().values():
            if "providers.list" in values:
                stored = values["providers.list"]
                if isinstance(stored, str):
                    stored = json.loads(stored)
                return stored if isinstance(stored, list) else []
    except Exception:
        logger.debug("[providers] unresolved read failed", exc_info=True)
    return []


def save_providers(config_store: Any, providers: list[dict[str, Any]]) -> None:
    """Persist providers list, refusing to blank an api_key that is on disk.

    **Why this guard exists.** Every provider mutation -- ``toggle_provider``,
    ``set_provider_health``, ``upsert_provider`` -- is a read-modify-write over
    the WHOLE list: read the resolved view, change one field, write all of it
    back. When the reading process cannot decrypt the vault, every key in that
    resolved view is ``None`` -> ``""``, and writing it back replaces each
    ``vault://`` pointer with an empty string.

    Reproduced: one ``set_provider_health("groq", "healthy")`` call -- which is
    what pressing **Test** does -- turned
    ``vault://cfg:providers.list.groq.api_key`` into ``""``. Permanently. The
    only symptom was a one-line warning, and afterwards the provider reported
    "no API key" while the operator knew they had saved one.

    So: an empty incoming key never overwrites a non-empty stored one. This is
    the single chokepoint every writer already goes through, which is why the
    guard lives here rather than in each caller. The cost is that clearing a
    key cannot be done by writing ``""`` -- delete the provider, or store a new
    key over it.
    """
    stored_keys: dict[str, Any] = {}
    for entry in load_providers_unresolved(config_store):
        if isinstance(entry, dict):
            name = str(entry.get("name", ""))
            if name and entry.get("api_key"):
                stored_keys[name] = entry["api_key"]

    protected: list[dict[str, Any]] = []
    for entry in providers:
        if not isinstance(entry, dict):
            protected.append(entry)
            continue
        name = str(entry.get("name", ""))
        if name in stored_keys and not str(entry.get("api_key") or "").strip():
            entry = dict(entry)
            entry["api_key"] = stored_keys[name]
            logger.warning(
                "[providers] refused to blank the stored api_key for %r -- the "
                "incoming value was empty, which is what an undecryptable "
                "vault read looks like. Keeping what is on disk.",
                name,
            )
        protected.append(entry)

    config_store.set("providers.list", protected, category="providers")


_GCP_PROJECT_TTL_S = 300.0
_gcp_project_cache: tuple[float, str] | None = None


def detect_gcp_project_id() -> str:
    """Best-effort GCP project id from ADC or gcloud config files.

    Cached for a few minutes: ``list_providers`` calls this on every provider
    lookup, on the event loop, and a loop-stall dump caught it reading these
    files (2026-09-22). A project id changes about never; a gcloud change is
    still picked up within the TTL.
    """
    global _gcp_project_cache
    now = time.monotonic()
    if _gcp_project_cache is not None and _gcp_project_cache[0] > now:
        return _gcp_project_cache[1]
    value = _read_gcp_project_id()
    _gcp_project_cache = (now + _GCP_PROJECT_TTL_S, value)
    return value


def _read_gcp_project_id() -> str:
    try:
        adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        if not adc_path.exists():
            adc_path = (
                Path(os.environ.get("APPDATA", ""))
                / "gcloud"
                / "application_default_credentials.json"
            )
        if adc_path.exists():
            data = json.loads(adc_path.read_text(encoding="utf-8"))
            project_id = data.get("quota_project_id") or data.get("project_id") or ""
            if project_id:
                return str(project_id)

        config_path = Path.home() / ".config" / "gcloud" / "configurations" / "config_default"
        if not config_path.exists():
            config_path = (
                Path(os.environ.get("APPDATA", ""))
                / "gcloud"
                / "configurations"
                / "config_default"
            )
        if config_path.exists():
            for line in config_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("project"):
                    return line.partition("=")[2].strip()
    except Exception as exc:
        logger.debug("gcloud project auto-detection failed: %s", exc)
    return ""


def default_provider_entries() -> list[dict[str, Any]]:
    """Build default provider rows from PROVIDER_PRESETS (no custom).

    All providers start **disabled** — the user enables the one they want
    via the Settings UI or by setting the corresponding env var.
    Google is auto-enabled if Application Default Credentials are detected.
    """
    import os

    providers: list[dict[str, Any]] = []
    for key, preset in PROVIDER_PRESETS.items():
        if key == "custom" or key not in SEEDED_PRESETS:
            continue
        models: list[str] = []
        project_id = ""
        location = "us-central1"
        if key == "google":
            from kazma_core.providers import GEMINI_MODELS

            models = list(GEMINI_MODELS)
            project_id = detect_gcp_project_id()
        # Auto-enable a provider if its API key is in the environment.
        env_key = f"{key.upper()}_API_KEY"
        api_key_val = os.environ.get(env_key, "")
        if key == "google" and not api_key_val:
            api_key_val = os.environ.get("GEMINI_API_KEY", "")
        has_key = bool(api_key_val.strip())
        # Google uses ADC or API key — enable if project_id or API key detected.
        has_adc = key == "google" and bool(project_id)
        enabled = has_key or has_adc
        google_mode = ""
        if key == "google":
            google_mode = "vertex_ai" if project_id else "ai_studio"
        providers.append(
            {
                "name": key,
                "display_name": preset.get("name", key),
                "base_url": preset.get("base_url", ""),
                "api_key": api_key_val if has_key else "",
                "models": models,
                "enabled": enabled,
                "health": "unknown",
                "project_id": project_id,
                "location": location,
                "google_mode": google_mode,
            }
        )
    return providers


#: The presets every install gets a (switched-off) row for: the ones it had
#: before providers stage 2. The catalog's later presets (Cerebras, vLLM,
#: Kimi...) are offered by Settings' Add dialog and the setup guide, and get
#: a row when the owner adds one: 35 rows of switched-off providers made the
#: list a chore to read.
SEEDED_PRESETS: frozenset[str] = frozenset({
    "openai", "anthropic", "groq", "deepseek", "google", "xai", "zai", "openrouter", "ollama",
    "lm-studio", "nvidia", "mistral", "together", "cohere", "fireworks", "perplexity", "ai21",
    "azure", "bedrock",
})


def seed_missing_presets(
    stored: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Append any missing preset providers to *stored*. Returns (list, changed)."""
    from kazma_core.providers import RETIRED_PRESET_URLS

    stored_by_name = {p.get("name", ""): p for p in stored} if stored else {}
    changed = False
    for key, preset in PROVIDER_PRESETS.items():
        if key not in SEEDED_PRESETS and key not in stored_by_name:
            continue
        row = stored_by_name.get(key)
        if row is not None:
            # A preset that moved: its row still on the old address it gave.
            old = {u.rstrip("/") for u in RETIRED_PRESET_URLS.get(key, ())}
            if str(row.get("base_url") or "").rstrip("/") in old:
                row["base_url"] = preset.get("base_url", "")
                changed = True
                logger.info("[ModelRegistry] Preset provider %s moved to %s", key, row["base_url"])
            continue
        if key == "custom":
            continue
        entry: dict[str, Any] = {
            "name": key,
            "display_name": preset.get("name", key),
            "base_url": preset.get("base_url", ""),
            "api_key": "",
            "models": [],
            "enabled": False,  # User enables via Settings UI or env var
            "health": "unknown",
        }
        if key == "google":
            from kazma_core.providers import GEMINI_MODELS

            entry["models"] = list(GEMINI_MODELS)
            pid = detect_gcp_project_id()
            entry["project_id"] = pid
            entry["location"] = "us-central1"
            entry["google_mode"] = "vertex_ai" if pid else "ai_studio"
        stored.append(entry)
        changed = True
        logger.info("[ModelRegistry] Seeded new preset provider: %s", key)
    return stored, changed
