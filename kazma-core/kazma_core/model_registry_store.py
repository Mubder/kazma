"""Provider list persistence helpers for ModelRegistry (S5 extract).

Pure read/normalize helpers — load never writes (protects against data loss).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from kazma_core.providers import PROVIDER_PRESETS

logger = logging.getLogger(__name__)


def normalize_models(models: Any) -> list[str]:
    """Return a sorted unique list of non-empty model id strings."""
    if isinstance(models, list):
        return sorted({str(model).strip() for model in models if str(model).strip()})
    return []


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


def save_providers(config_store: Any, providers: list[dict[str, Any]]) -> None:
    """Persist providers list (ConfigStore serializes; do not pre-json.dumps)."""
    config_store.set("providers.list", providers, category="providers")


def detect_gcp_project_id() -> str:
    """Best-effort GCP project id from ADC or gcloud config files."""
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
        if key == "custom":
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


def seed_missing_presets(
    stored: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Append any missing preset providers to *stored*. Returns (list, changed)."""
    stored_by_name = {p.get("name", ""): p for p in stored} if stored else {}
    changed = False
    for key, preset in PROVIDER_PRESETS.items():
        if key == "custom" or key in stored_by_name:
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
