"""RBAC Engine — Role-Based Access Control for enterprise divisions.

Enforces division-level permissions, role hierarchies, and cross-division
access controls for the ALMuhalab Global ecosystem.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_DB = str(Path.cwd() / "kazma-data" / "rbac.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    division TEXT NOT NULL,
    role TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT DEFAULT 'system',
    expires_at TEXT DEFAULT '',
    PRIMARY KEY (user_id, division, role)
);

CREATE TABLE IF NOT EXISTS division_permissions (
    division TEXT NOT NULL,
    role TEXT NOT NULL,
    resource_pattern TEXT NOT NULL,
    actions TEXT NOT NULL,  -- JSON array of allowed actions
    PRIMARY KEY (division, role, resource_pattern)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_division ON user_roles(division);
"""

# ─── Division Definitions ─────────────────────────────────────────────

DIVISIONS: dict[str, dict[str, Any]] = {
    "gas_oil": {
        "name": "Gas & Oil Trading",
        "name_ar": "تتجارة الغاز والنفط",
        "roles": ["admin", "trader", "analyst", "viewer"],
        "sensitive_data": ["contracts", "pricing", "suppliers"],
    },
    "tourism": {
        "name": "Tourism",
        "name_ar": "السياحة",
        "roles": ["admin", "manager", "agent", "viewer"],
        "sensitive_data": ["bookings", "revenue", "clients"],
    },
    "general_trading": {
        "name": "General Trading",
        "name_ar": "التجارة العاملة",
        "roles": ["admin", "buyer", "seller", "viewer"],
        "sensitive_data": ["inventory", "suppliers", "pricing"],
    },
}

# ─── Role Hierarchy (higher index = more privilege) ───────────────────

_ROLE_HIERARCHY: dict[str, list[str]] = {
    "gas_oil": ["viewer", "analyst", "trader", "admin"],
    "tourism": ["viewer", "agent", "manager", "admin"],
    "general_trading": ["viewer", "seller", "buyer", "admin"],
}

# ─── Default permission matrix per role per division ──────────────────

_DEFAULT_PERMISSIONS: dict[str, dict[str, dict[str, list[str]]]] = {
    "gas_oil": {
        "viewer": {"*": ["read"]},
        "analyst": {"*": ["read"], "pricing": ["read", "analyze"]},
        "trader": {"*": ["read", "write"], "contracts": ["read", "write", "sign"]},
        "admin": {"*": ["read", "write", "delete", "admin"]},
    },
    "tourism": {
        "viewer": {"*": ["read"]},
        "agent": {"*": ["read"], "bookings": ["read", "write"]},
        "manager": {"*": ["read", "write"], "revenue": ["read"]},
        "admin": {"*": ["read", "write", "delete", "admin"]},
    },
    "general_trading": {
        "viewer": {"*": ["read"]},
        "seller": {"*": ["read"], "inventory": ["read", "write"]},
        "buyer": {"*": ["read"], "procurement": ["read", "write"]},
        "admin": {"*": ["read", "write", "delete", "admin"]},
    },
}

# ─── Sensitive data that requires approval for cross-division access ───

SENSITIVE_RESOURCES: dict[str, list[str]] = {
    "gas_oil": ["contracts", "pricing", "suppliers"],
    "tourism": ["bookings", "revenue", "clients"],
    "general_trading": ["inventory", "suppliers", "pricing"],
}


@dataclass
class PermissionResult:
    """Result of a permission check."""

    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    user_role: str = ""
    division: str = ""
    resource: str = ""
    action: str = ""


class RBACEngine:
    """Role-Based Access Control for enterprise divisions.

    Manages user roles, permission checks, and division boundaries.
    All mutations are auditable through the audit logger.

    Args:
        db_path: Path to the SQLite database file.
        divisions: Optional override for division definitions.
    """

    def __init__(
        self,
        db_path: str | None = None,
        divisions: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self.divisions = divisions or DIVISIONS
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self.db_path)
            from kazma_core.config_store import apply_sqlite_pragmas_async

            await apply_sqlite_pragmas_async(self._db)
            self._db.row_factory = aiosqlite.Row
            await self._db.executescript(_SCHEMA)
            await self._db.commit()
            await self._load_default_permissions()
        return self._db

    async def _load_default_permissions(self) -> None:
        """Seed division_permissions with defaults if empty."""
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM division_permissions")
        row = await cursor.fetchone()
        if row["cnt"] == 0:
            for div, roles in _DEFAULT_PERMISSIONS.items():
                for role, resources in roles.items():
                    for pattern, actions in resources.items():
                        await db.execute(
                            "INSERT OR IGNORE INTO division_permissions (division, role, resource_pattern, actions) VALUES (?, ?, ?, ?)",
                            (div, role, pattern, json.dumps(actions)),
                        )
            await db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # ─── Role Management ──────────────────────────────────────────────

    async def assign_role(self, user_id: str, division: str, role: str, granted_by: str = "system") -> bool:
        """Assign a role to a user within a division.

        Args:
            user_id: The user to assign the role to.
            division: The division (gas_oil, tourism, general_trading).
            role: The role to assign.
            granted_by: Who granted this role.

        Returns:
            True if role was assigned, False if invalid.
        """
        if division not in self.divisions:
            logger.warning("Unknown division: %s", division)
            return False

        valid_roles = self.divisions[division]["roles"]
        if role not in valid_roles:
            logger.warning("Invalid role '%s' for division '%s'. Valid: %s", role, division, valid_roles)
            return False

        db = await self._get_db()
        now = datetime.now(UTC).isoformat()

        await db.execute(
            """INSERT OR REPLACE INTO user_roles (user_id, division, role, granted_at, granted_by)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, division, role, now, granted_by),
        )
        await db.commit()
        logger.info("Assigned role '%s' to user '%s' in division '%s'", role, user_id, division)
        return True

    async def revoke_role(self, user_id: str, division: str, role: str) -> bool:
        """Revoke a role from a user within a division.

        Args:
            user_id: The user to revoke the role from.
            division: The division.
            role: The role to revoke.

        Returns:
            True if role was revoked, False if not found.
        """
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM user_roles WHERE user_id = ? AND division = ? AND role = ?",
            (user_id, division, role),
        )
        await db.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Revoked role '%s' from user '%s' in division '%s'", role, user_id, division)
        else:
            logger.warning("No role '%s' found for user '%s' in division '%s'", role, user_id, division)
        return deleted

    async def get_user_roles(self, user_id: str, division: str | None = None) -> list[dict[str, Any]]:
        """Get all roles for a user, optionally filtered by division.

        Returns:
            List of dicts with division, role, granted_at, granted_by.
        """
        db = await self._get_db()
        if division:
            cursor = await db.execute(
                "SELECT * FROM user_roles WHERE user_id = ? AND division = ?",
                (user_id, division),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM user_roles WHERE user_id = ?",
                (user_id,),
            )
        rows = await cursor.fetchall()
        return [
            {
                "division": row["division"],
                "role": row["role"],
                "granted_at": row["granted_at"],
                "granted_by": row["granted_by"],
            }
            for row in rows
        ]

    async def get_division_users(self, division: str) -> list[dict[str, Any]]:
        """Get all users in a division with their roles."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM user_roles WHERE division = ? ORDER BY user_id",
            (division,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "user_id": row["user_id"],
                "role": row["role"],
                "granted_at": row["granted_at"],
                "granted_by": row["granted_by"],
            }
            for row in rows
        ]

    # ─── Permission Checks ────────────────────────────────────────────

    async def _get_permissions_for_role(
        self, division: str, role: str,
    ) -> dict[str, list[str]]:
        """Get permissions for a role in a division from the DB.

        Falls back to the hardcoded ``_DEFAULT_PERMISSIONS`` matrix only
        when the ``division_permissions`` table has no rows for this
        division/role combination (e.g. first run before seeding).
        """
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT resource_pattern, actions FROM division_permissions "
            "WHERE division = ? AND role = ?",
            (division, role),
        )
        rows = await cursor.fetchall()
        if rows:
            return {row["resource_pattern"]: json.loads(row["actions"]) for row in rows}
        # Fallback only when DB table is truly empty for this combo
        return _DEFAULT_PERMISSIONS.get(division, {}).get(role, {})

    async def check_permission(
        self,
        user_id: str,
        division: str,
        resource: str,
        action: str,
    ) -> PermissionResult:
        """Check if user has permission for action on resource within division.

        Args:
            user_id: The user requesting access.
            division: The division context.
            resource: The resource pattern (e.g. "pricing", "contracts").
            action: The action (e.g. "read", "write", "delete").

        Returns:
            PermissionResult with allowed flag, reason, and details.
        """
        # 1. Validate division
        if division not in self.divisions:
            return PermissionResult(
                allowed=False,
                reason=f"Unknown division: {division}",
                user_role="",
                division=division,
                resource=resource,
                action=action,
            )

        # 2. Get user's highest role in this division
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT role FROM user_roles WHERE user_id = ? AND division = ?",
            (user_id, division),
        )
        rows = await cursor.fetchall()

        if not rows:
            return PermissionResult(
                allowed=False,
                reason=f"User '{user_id}' has no role in division '{division}'",
                user_role="",
                division=division,
                resource=resource,
                action=action,
            )

        # Find highest role in hierarchy
        user_roles = {row["role"] for row in rows}
        hierarchy: list[str] = _ROLE_HIERARCHY.get(division, self.divisions[division]["roles"])  # type: ignore[assignment]
        highest_role = "viewer"
        for role in reversed(hierarchy):
            if role in user_roles:
                highest_role = role
                break

        # 3. Check permission matrix (from DB, with hardcoded fallback)
        permissions = await self._get_permissions_for_role(division, highest_role)

        # Check exact resource match first, then wildcard
        allowed_actions: list[str] = []
        for pattern, actions in permissions.items():
            if pattern == resource or pattern == "*":
                allowed_actions.extend(actions)

        # Deduplicate
        allowed_actions = list(set(allowed_actions))

        if action in allowed_actions:
            # 4. Check if this is a sensitive resource requiring approval
            sensitive = SENSITIVE_RESOURCES.get(division, [])
            requires_approval = resource in sensitive and action in ("write", "delete", "sign")

            return PermissionResult(
                allowed=True,
                reason="",
                requires_approval=requires_approval,
                user_role=highest_role,
                division=division,
                resource=resource,
                action=action,
            )

        return PermissionResult(
            allowed=False,
            reason=f"Role '{highest_role}' does not have '{action}' permission on '{resource}' in '{division}'",
            user_role=highest_role,
            division=division,
            resource=resource,
            action=action,
        )

    async def is_user_in_division(self, user_id: str, division: str) -> bool:
        """Check if a user has any role in a division."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT 1 FROM user_roles WHERE user_id = ? AND division = ? LIMIT 1",
            (user_id, division),
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_user_divisions(self, user_id: str) -> list[str]:
        """Get all divisions a user belongs to."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT DISTINCT division FROM user_roles WHERE user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [row["division"] for row in rows]

    # ─── Cleanup ──────────────────────────────────────────────────────

    async def clear(self) -> int:
        """Clear all user roles. Returns count of deleted rows."""
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM user_roles")
        await db.commit()
        count = cursor.rowcount
        logger.warning("RBAC cleared: %d user roles deleted", count)
        return count
