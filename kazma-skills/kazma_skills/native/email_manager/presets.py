"""IMAP/POP/SMTP host presets for Gmail and Microsoft (Outlook/M365)."""

from __future__ import annotations

from typing import Any

# Well-known server endpoints (SSL for inbound, STARTTLS for SMTP:587).
PROVIDER_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "gmail": {
        "imap": {
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_starttls": True,
        },
        "pop": {
            "pop_host": "pop.gmail.com",
            "pop_port": 995,
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_starttls": True,
        },
    },
    "microsoft": {
        "imap": {
            "imap_host": "outlook.office365.com",
            "imap_port": 993,
            "smtp_host": "smtp.office365.com",
            "smtp_port": 587,
            "smtp_starttls": True,
        },
        "pop": {
            "pop_host": "outlook.office365.com",
            "pop_port": 995,
            "smtp_host": "smtp.office365.com",
            "smtp_port": 587,
            "smtp_starttls": True,
        },
    },
}


def get_preset(provider: str, protocol: str) -> dict[str, Any]:
    """Return a copy of the preset for provider+protocol, or empty dict."""
    p = (provider or "").strip().lower()
    proto = (protocol or "").strip().lower()
    if p in ("microsoft_graph", "outlook", "m365", "office365"):
        p = "microsoft"
    if p in ("google", "workspace"):
        p = "gmail"
    if proto in ("app_password", "app-password"):
        proto = "imap"
    block = PROVIDER_PRESETS.get(p) or {}
    preset = block.get(proto)
    return dict(preset) if preset else {}


def list_presets() -> dict[str, Any]:
    """Non-secret preset map for Settings / docs."""
    return {
        k: {proto: dict(cfg) for proto, cfg in protos.items()}
        for k, protos in PROVIDER_PRESETS.items()
    }
