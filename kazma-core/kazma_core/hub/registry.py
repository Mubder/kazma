"""Kazma Hub — SQLite-backed skill registry and agent registry."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import aiosqlite

from kazma_core.hub.manifest_schema import SkillManifest

# ─── Agent Info (for delegation discovery) ────────────────────────────


@dataclass
class AgentInfo:
    """Information about a registered agent (for delegation discovery)."""
    agent_id: str
    capabilities: list[str]
    endpoint: str = ""
    reputation: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


_SKILL_ID_RE = re.compile(r"^kazma-hub://([^/]+)/([^@]+)@(.+)$")

_CREATE_SKILLS = """\
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    author TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT,
    license TEXT,
    capabilities TEXT,   -- JSON array
    tags TEXT,           -- JSON array
    manifest_json TEXT,  -- full manifest serialized
    checksum TEXT,       -- SHA256 of manifest JSON
    installed_path TEXT, -- local install path if installed
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, author, version)
);
"""

_CREATE_DEPS = """\
CREATE TABLE IF NOT EXISTS skill_dependencies (
    skill_id INTEGER REFERENCES skills(id) ON DELETE CASCADE,
    dep_name TEXT NOT NULL,
    dep_version TEXT,
    is_optional BOOLEAN DEFAULT FALSE
);
"""

_CREATE_AGENTS = """\
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    capabilities TEXT NOT NULL,
    endpoint TEXT DEFAULT '',
    reputation REAL DEFAULT 1.0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _parse_skill_id(skill_id: str):
    """Return (author, name, version) or raise ValueError."""
    m = _SKILL_ID_RE.match(skill_id)
    if not m:
        raise ValueError(f"Invalid skill ID: {skill_id!r}")
    return m.group(1), m.group(2), m.group(3)


def _make_skill_id(author: str, name: str, version: str) -> str:
    return f"kazma-hub://{author}/{name}@{version}"


def _manifest_checksum(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_to_manifest(row: dict) -> SkillManifest:
    """Reconstruct a SkillManifest from a DB row dict."""
    return SkillManifest.from_dict(json.loads(row["manifest_json"]))


class KazmaHub:
    """Async SQLite-backed skill registry."""

    def __init__(self, registry_path: str = "~/.kazma/hub/registry.db"):
        self.db_path = Path(registry_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None
        self._initialized = False
        self._agents: dict[str, AgentInfo] = {}

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self.db_path))
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA foreign_keys = ON")
        if not self._initialized:
            await self._conn.executescript(_CREATE_SKILLS + _CREATE_DEPS + _CREATE_AGENTS)
            await self._conn.commit()
            self._initialized = True
        return self._conn

    async def _init_db(self) -> None:
        conn = await self._get_conn()
        await conn.executescript(_CREATE_SKILLS + _CREATE_DEPS)
        await conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Register / Unregister
    # ------------------------------------------------------------------

    async def register(self, manifest: SkillManifest) -> str:
        """Insert or update a skill manifest. Return its kazma-hub:// ID."""
        data = manifest.data
        author = data["author"]
        name = data["name"]
        version = data["version"]
        sid = _make_skill_id(author, name, version)
        checksum = _manifest_checksum(data)

        conn = await self._get_conn()
        await conn.execute(
            """\
            INSERT INTO skills
                (name, author, version, description, license,
                 capabilities, tags, manifest_json, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, author, version) DO UPDATE SET
                description = excluded.description,
                license      = excluded.license,
                capabilities = excluded.capabilities,
                tags         = excluded.tags,
                manifest_json = excluded.manifest_json,
                checksum      = excluded.checksum
            """,
            (
                name,
                author,
                version,
                data.get("description"),
                data.get("license"),
                json.dumps(data.get("capabilities")),
                json.dumps(data.get("tags")),
                json.dumps(data, ensure_ascii=False),
                checksum,
            ),
        )
        await conn.commit()

        # Insert dependencies (replace existing)
        cursor = await conn.execute(
            "SELECT id FROM skills WHERE name=? AND author=? AND version=?",
            (name, author, version),
        )
        skill_row = await cursor.fetchone()
        assert skill_row is not None  # we just inserted it
        skill_id_int = skill_row["id"]

        await conn.execute(
            "DELETE FROM skill_dependencies WHERE skill_id=?", (skill_id_int,)
        )
        for dep in data.get("dependencies", []):
            dep_name = dep.get("name", "") if isinstance(dep, dict) else str(dep)
            dep_version = dep.get("version") if isinstance(dep, dict) else None
            is_optional = dep.get("optional", False) if isinstance(dep, dict) else False
            await conn.execute(
                "INSERT INTO skill_dependencies (skill_id, dep_name, dep_version, is_optional) "
                "VALUES (?, ?, ?, ?)",
                (skill_id_int, dep_name, dep_version, is_optional),
            )
        await conn.commit()
        return sid

    async def unregister(self, skill_id: str) -> bool:
        """Remove a skill by ID. Returns True if a row was deleted."""
        author, name, version = _parse_skill_id(skill_id)
        conn = await self._get_conn()
        cursor = await conn.execute(
            "DELETE FROM skills WHERE name=? AND author=? AND version=?",
            (name, author, version),
        )
        await conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get(self, skill_id: str) -> Optional[SkillManifest]:
        """Fetch a single skill by kazma-hub:// ID."""
        author, name, version = _parse_skill_id(skill_id)
        conn = await self._get_conn()
        row = await conn.execute(
            "SELECT * FROM skills WHERE name=? AND author=? AND version=?",
            (name, author, version),
        )
        row = await row.fetchone()
        if row is None:
            return None
        return _row_to_manifest(dict(row))

    async def search(
        self,
        query: str | None = None,
        capabilities: list[str] | None = None,
        tags: list[str] | None = None,
        author: str | None = None,
    ) -> List[SkillManifest]:
        """Search skills by text query, capabilities, tags, or author."""
        clauses: list[str] = []
        params: list = []

        if query:
            like = f"%{query}%"
            clauses.append(
                "(name LIKE ? OR description LIKE ? OR manifest_json LIKE ?)"
            )
            params.extend([like, like, like])
        if capabilities:
            for cap in capabilities:
                clauses.append("capabilities LIKE ?")
                params.append(f"%{json.dumps(cap)}%")
        if tags:
            for tag in tags:
                clauses.append("tags LIKE ?")
                params.append(f"%{json.dumps(tag)}%")
        if author:
            clauses.append("author = ?")
            params.append(author)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM skills WHERE {where}"

        conn = await self._get_conn()
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_manifest(dict(r)) for r in rows]

    async def list_installed(self) -> List[SkillManifest]:
        """Return manifests for skills that have been installed locally."""
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT * FROM skills WHERE installed_path IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return [_row_to_manifest(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Install / Update (stubs)
    # ------------------------------------------------------------------

    async def install(self, skill_id: str) -> Path:
        """Stub: mark skill as installed and return install path."""
        author, name, version = _parse_skill_id(skill_id)
        install_path = Path("~/.kazma/skills").expanduser() / name
        install_path.mkdir(parents=True, exist_ok=True)

        conn = await self._get_conn()
        await conn.execute(
            "UPDATE skills SET installed_path=? "
            "WHERE name=? AND author=? AND version=?",
            (str(install_path), name, author, version),
        )
        await conn.commit()
        return install_path

    async def update(self, skill_id: str) -> Optional[SkillManifest]:
        """Stub: return latest version of the given skill."""
        # For now, just return the current version.
        return await self.get(skill_id)

    # ------------------------------------------------------------------
    # Agent Registry (for delegation discovery)
    # ------------------------------------------------------------------

    async def register_agent(self, agent: AgentInfo) -> None:
        """Register an agent with its capabilities for delegation discovery."""
        conn = await self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO agents (agent_id, capabilities, endpoint, reputation, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                agent.agent_id,
                json.dumps(agent.capabilities),
                agent.endpoint,
                agent.reputation,
                json.dumps(agent.metadata),
            ),
        )
        await conn.commit()

    async def unregister_agent(self, agent_id: str) -> bool:
        """Remove an agent from the registry."""
        conn = await self._get_conn()
        cursor = await conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        await conn.commit()
        return cursor.rowcount > 0

    async def find_agents_by_capabilities(
        self, required: list[str]
    ) -> list[AgentInfo]:
        """Find agents that have all required capabilities."""
        agents = await self.list_agents()
        results = []
        for agent in agents:
            if all(c in agent.capabilities for c in required):
                results.append(agent)
        results.sort(
            key=lambda a: (
                sum(1 for c in required if c in a.capabilities) / max(len(required), 1),
                a.reputation,
            ),
            reverse=True,
        )
        return results

    async def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """Get a registered agent by ID."""
        conn = await self._get_conn()
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return AgentInfo(
            agent_id=row["agent_id"],
            capabilities=json.loads(row["capabilities"]),
            endpoint=row["endpoint"],
            reputation=row["reputation"],
            metadata=json.loads(row["metadata"]),
        )

    async def list_agents(self) -> list[AgentInfo]:
        """List all registered agents."""
        conn = await self._get_conn()
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT * FROM agents")
        rows = await cursor.fetchall()
        return [
            AgentInfo(
                agent_id=r["agent_id"],
                capabilities=json.loads(r["capabilities"]),
                endpoint=r["endpoint"],
                reputation=r["reputation"],
                metadata=json.loads(r["metadata"]),
            )
            for r in rows
        ]

    async def update_agent_reputation(self, agent_id: str, score: float) -> None:
        """Update an agent's reputation score."""
        conn = await self._get_conn()
        await conn.execute(
            "UPDATE agents SET reputation = ? WHERE agent_id = ?",
            (score, agent_id),
        )
        await conn.commit()
