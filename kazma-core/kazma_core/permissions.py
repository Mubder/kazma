"""Permission Manager — Manages per-user tool permission allowlists via YAML.

Permissions are persisted in a YAML file. Each user has an independent
allow-list (or the shared ``default`` user is used when no user is
specified).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

__all__ = ["PermissionManager"]

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent.parent / "kazma-permissions.yaml"


class PermissionManager:
    """Manages tool permission allowlists backed by a YAML config file.

    File format::

        users:
          default:
            allowed:
              - filesystem_read
              - web_search
            denied:
              - shell_exec
          admin:
            allowed:
              - "*"
            denied: []

    Args:
        config_path: Path to the YAML permissions file.  If the file does
            not exist it will be created with a safe default.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else _DEFAULT_CONFIG
        self._data: dict[str, Any] | None = None

    # -- public API --------------------------------------------------------

    def load_permissions(self) -> dict[str, Any]:
        """Load (or reload) the permissions from the YAML file.

        Returns:
            The full permissions dict with ``users`` key.
        """
        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as fh:
                self._data = yaml.safe_load(fh) or {}
            logger.debug("Loaded permissions from %s", self.config_path)
        else:
            self._data = {"users": {"default": {"allowed": [], "denied": []}}}
            self._save()
            logger.info("Created default permissions file at %s", self.config_path)

        # Ensure structure
        if "users" not in self._data:
            self._data["users"] = {}
        return self._data

    def grant(self, tool_name: str, user: str = "default") -> None:
        """Grant permission for a tool.

        Adds *tool_name* to the user's allowed list (if not already present
        or wildcarded) and removes it from the denied list.
        """
        self._ensure_loaded()
        user_perms = self._get_user(user)

        denied: list[str] = user_perms.get("denied", [])
        if tool_name in denied:
            denied.remove(tool_name)
            user_perms["denied"] = denied

        allowed: list[str] = user_perms.get("allowed", [])
        if tool_name not in allowed and "*" not in allowed:
            allowed.append(tool_name)
            user_perms["allowed"] = allowed

        self._save()
        logger.info("Granted tool '%s' to user '%s'", tool_name, user)

    def revoke(self, tool_name: str, user: str = "default") -> None:
        """Revoke permission for a tool.

        Removes *tool_name* from the user's allowed list.  Does NOT
        add it to the denied list — revoked tools are simply not permitted
        unless re-granted.
        """
        self._ensure_loaded()
        user_perms = self._get_user(user)

        allowed: list[str] = user_perms.get("allowed", [])
        if tool_name in allowed:
            allowed.remove(tool_name)
            user_perms["allowed"] = allowed
        # If wildcard is set, add to denied to override it
        if "*" in allowed:
            denied: list[str] = user_perms.get("denied", [])
            if tool_name not in denied:
                denied.append(tool_name)
                user_perms["denied"] = denied

        self._save()
        logger.info("Revoked tool '%s' from user '%s'", tool_name, user)

    def deny(self, tool_name: str, user: str = "default") -> None:
        """Explicitly deny a tool for a user (adds to deny list).

        Denied tools are always blocked even if the wildcard ``*`` is in the
        allowed list.
        """
        self._ensure_loaded()
        user_perms = self._get_user(user)

        denied: list[str] = user_perms.get("denied", [])
        if tool_name not in denied:
            denied.append(tool_name)
            user_perms["denied"] = denied

        # Also remove from allowed if present
        allowed: list[str] = user_perms.get("allowed", [])
        if tool_name in allowed:
            allowed.remove(tool_name)
            user_perms["allowed"] = allowed

        self._save()
        logger.info("Denied tool '%s' for user '%s'", tool_name, user)

    def is_allowed(self, tool_name: str, user: str = "default") -> bool:
        """Check if a tool is allowed for a user.

        Args:
            tool_name: The tool to check.
            user: The user context (default: ``"default"``).

        Returns:
            ``True`` if the tool is permitted.
        """
        self._ensure_loaded()
        user_perms = self._get_user(user)

        denied: list[str] = user_perms.get("denied", [])
        if tool_name in denied:
            return False

        allowed: list[str] = user_perms.get("allowed", [])
        return tool_name in allowed or "*" in allowed

    def list_allowed(self, user: str = "default") -> list[str]:
        """Return the list of allowed tool names for a user."""
        self._ensure_loaded()
        user_perms = self._get_user(user)
        return list(user_perms.get("allowed", []))

    def list_denied(self, user: str = "default") -> list[str]:
        """Return the list of explicitly denied tool names for a user."""
        self._ensure_loaded()
        user_perms = self._get_user(user)
        return list(user_perms.get("denied", []))

    def users(self) -> list[str]:
        """Return all user names in the config."""
        self._ensure_loaded()
        return list(self._data.get("users", {}).keys())  # type: ignore[union-attr]

    # -- internal ----------------------------------------------------------

    def _ensure_loaded(self) -> dict[str, Any]:
        if self._data is None:
            self.load_permissions()
        if self._data is None:  # load_permissions failed
            self._data = {}
        return self._data

    def _get_user(self, user: str) -> dict[str, Any]:
        data = self._ensure_loaded()
        users: dict[str, Any] = data.get("users", {})
        if user not in users:
            users[user] = {"allowed": [], "denied": []}
        return users[user]

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as fh:
            yaml.dump(self._data, fh, default_flow_style=False, allow_unicode=True)
