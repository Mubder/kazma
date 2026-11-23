"""Move a space to another install (Spaces S4-06).

A shared space's owner downloads a sealed bundle ("Move this space"), and
the admin of the other install takes it in ("Bring in a space"), choosing the
person who will own it there. The space keeps its id, its rows, its
settings, its secrets and its files; on the new install it is a shared space
with that person as its owner and only member.

**The bundle** (:func:`export_bundle`) is a zip sealed with a password the
person chooses (``security.sealed_zip``, decision N10). It holds what S1-27's
export holds -- every row store's rows of the space, found by
``space_data``'s walk; its settings, each as stored (a secret is a
``vault://`` pointer, never its value); its files -- and, unlike that
export, its secrets and their keys:

* ``secrets.jsonl``: each of the space's secrets as the vault seals it with
  the space's key (bytes, never a value; a secret still under the install's
  vault key is sealed with the space's key first,
  ``SecretVault.export_space_sealed``);
* ``keys.json``: the space's keys, each sealed under a key derived from the
  password (scrypt, AES-256-GCM, bound to the format, the space and the
  key's id). **The install's vault key never leaves it**: on the other
  install the keys are wrapped with that install's own.

**The import** (:func:`import_bundle`) refuses before it writes anything
(:class:`SpaceMoveRefused`, each with a code the pages word):

* the install does not enforce Spaces (``spaces_off``), or is on Postgres
  (``postgres_not_yet``, as S1-27);
* the password does not open the bundle (``wrong_password``), a byte was
  changed (``changed``), it is no space bundle (``not_a_bundle``) or too big
  (``too_big``);
* **anything of the space is here already** (``space_exists``: the identity
  store, a row of any store, a setting, a secret or a folder): a moved space
  is never merged into another;
* **a row that is not the space's** (``foreign_row``): every row is checked
  by the rule the store finds a space's rows by, against the space and the
  ids of the bundle's own rows (its threads, libraries, jobs...); a setting
  outside the space's own (``foreign_setting``), a pointer to a secret that
  is not the space's (``foreign_secret``);
* **an id that is here already** (``id_collides``): a row's primary key, a
  chat's thread, a setting, a secret's name or a key's id. Integer row ids
  (log lines) are given new numbers instead; nothing else is renumbered;
* a table or a column this install does not have (``unknown_table``,
  ``unknown_column``): never a schema from the bundle.

Before the checks every row store is made by its own code
(``spaces.row_stores``; a fresh install has few): that is not taken back, as
each would be made at its first use. Then it writes, recording the space in
an import journal first
(:data:`JOURNAL_KEY`): the keys and secrets (one vault transaction, every
secret opened with its key before it commits), every store's rows (one
transaction per file; the chats' thread records first), the settings, the
files, and last the space in the identity store. **A half-done import leaves
the install as it was**: a failure takes back everything of the space
written so far, by the same walk an erase uses; an import a stop cut off is
taken back by the next one (:func:`settle_unfinished_imports`). Until the
last step nobody is a member of the space, so nothing can reach it.

**Accounts.** The person who made the bundle is the new owner on this install:
their account id, where a row names it as an account (:data:`ACCOUNT_FIELDS`)
or as their memory entry (``member_<id>``), is the new owner's. Other people's
ids are kept as written: they name the old install's people.

**Not moved:** a personal space (``personal_space_stays``: its memory is
filed under the one ``user`` entry, a shared space's by member); a project
folder (a space with one arrives as a plain shared space, its files with it);
the Knowledge Library's vectors (rebuilt by its repair sweep) and the
keyword index (written by Knowledge's own code from the chunks).

Blocking: call it off the event loop.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets as _secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from kazma_core.spaces import space_data
from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced, valid_space_id

__all__ = [
    "ACCOUNT_FIELDS",
    "FORMAT",
    "JOURNAL_KEY",
    "MAX_BUNDLE_BYTES",
    "SpaceMoveRefused",
    "export_bundle",
    "import_bundle",
    "settle_unfinished_imports",
]

logger = logging.getLogger(__name__)

#: The bundle's format; an import refuses any other.
FORMAT = "kazma-space-bundle/1"
#: The largest bundle the import route takes (its body limit): the largest
#: body any route takes (``kazma_ui.body_limits.DOCUMENT_UPLOAD_CEILING``).
MAX_BUNDLE_BYTES = 2 * 1024**3
#: The most a bundle may unpack to (a sealed zip is untrusted input).
MAX_UNPACKED_BYTES = 16 * 1024**3
#: The import journal: the spaces whose import began and has not finished.
JOURNAL_KEY = "system.spaces.importing"
#: The columns that hold a person's account id (remapped to the new owner).
ACCOUNT_FIELDS = frozenset({
    "asker", "author", "creator", "created_by", "owner_id", "actor_id", "said_by", "opener",
    "account", "account_id", "granted_by", "booker", "principal_id", "requested_by", "decided_by",
})

_KEY_AAD = "kazma-space-bundle-key/v1"
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
#: Stores written first: other stores' rows are found by their ids (a chat's
#: thread, a swarm task), so an undo after a stop finds them too.
_FIRST = ("thread_scopes.db", "swarm_tasks.db")
_IMPORT_LOCK = threading.Lock()


class SpaceMoveRefused(ValueError):
    """Nothing was moved: ``code`` says why, ``status`` the HTTP answer."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ── the password's seal of the keys ─────────────────────────────────────


def _password_key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(password.encode("utf-8"))


def _aad(space: str, key_id: str) -> bytes:
    return "\x00".join((_KEY_AAD, FORMAT, space, key_id)).encode("utf-8")


def _seal_keys(space: str, keys: list[dict[str, Any]], password: str) -> dict[str, Any]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(16)
    sealer = AESGCM(_password_key(password, salt))
    sealed = []
    for key in keys:
        nonce = os.urandom(12)
        sealed.append({
            "key_id": key["key_id"], "state": key["state"], "created_at": key["created_at"],
            "retired_at": key.get("retired_at"), "nonce": _b64(nonce),
            "sealed": _b64(sealer.encrypt(nonce, key["key"], _aad(space, key["key_id"]))),
        })
    return {"kdf": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P, "salt": _b64(salt), "keys": sealed}


def _open_keys(space: str, held: dict[str, Any], password: str) -> list[dict[str, Any]]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if (held.get("kdf"), held.get("n"), held.get("r"), held.get("p")) != ("scrypt", _SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
        raise SpaceMoveRefused("not_a_bundle", 400, "The bundle's keys are sealed in a way this build does not know.")
    opener = AESGCM(_password_key(password, _unb64(held.get("salt"))))
    keys = []
    for item in held.get("keys") or ():
        key_id = str(item.get("key_id") or "")
        try:
            key = opener.decrypt(_unb64(item.get("nonce")), _unb64(item.get("sealed")), _aad(space, key_id))
        except InvalidTag:
            raise SpaceMoveRefused("changed", 400, "The bundle's keys do not open with it.") from None
        keys.append({"key_id": key_id, "state": item.get("state"), "created_at": item.get("created_at"),
                     "retired_at": item.get("retired_at"), "key": key})
    return keys


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any) -> bytes:
    try:
        return base64.b64decode(str(text or ""), validate=True)
    except ValueError:
        raise SpaceMoveRefused("not_a_bundle", 400, "The bundle holds a value that is not base64.") from None


# ── export ──────────────────────────────────────────────────────────────


def _space_settings(space: str, threads: list[str]) -> dict[str, dict[str, Any]]:
    """The space's settings as stored (``vault://`` pointers, never a
    secret's value), each with its category: its own keys, its chats' state,
    and its entry of the mappings kept by space."""
    from kazma_core.config_store import get_config_store

    wanted = set(space_data._settings_keys(space, threads))
    mappings = dict(space_data._BY_SPACE_MAPPINGS)
    found: dict[str, dict[str, Any]] = {}
    for category, group in get_config_store().get_all().items():
        for key, value in group.items():
            if key in wanted:
                found[key] = {"category": category, "value": value}
            elif key in mappings:
                holder = value
                for step in mappings[key]:
                    holder = holder.get(step) if isinstance(holder, dict) else None
                if isinstance(holder, dict) and space in holder:
                    found[key] = {"category": category, "value": {space: holder[space]}}
    return found


def _space_folders(space: str) -> dict[str, Path]:
    """``{"space": its own folder, "documents": its document files}``."""
    from kazma_core.documents import storage
    from kazma_core.documents.config import get_document_storage_root
    from kazma_core.spaces import files as space_files

    return {"space": space_files.space_dir(space),
            "documents": Path(get_document_storage_root()) / storage.space_folder(space)}


def export_bundle(space: str, target: Path, password: str, *, by: str) -> dict[str, Any]:
    """Write the space's bundle to *target*, sealed with *password*. *by* is
    the person moving it (its owner on the other install). Returns the
    bundle's manifest (counts only)."""
    from kazma_core.security.sealed_zip import MIN_PASSWORD, seal

    found = space_data._checked(space)
    if found.get("personal_of"):
        raise SpaceMoveRefused(
            "personal_space_stays", 409,
            "A personal space stays with its person; export it to keep a copy. A shared space can move.")
    if len(password or "") < MIN_PASSWORD:
        raise SpaceMoveRefused("password_too_short", 400, f"The password is at least {MIN_PASSWORD} characters.")
    keys, rows = _space_secrets(space)
    ctx, tables = space_data._walk(space)
    folder = Path(tempfile.mkdtemp(prefix="kazma-space-move-"))
    try:
        plain = folder / "bundle.zip"
        manifest: dict[str, Any] = {
            "format": FORMAT, "space": space, "name": str(found.get("name") or ""), "exported_by": by,
            "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tables": {}, "files": 0,
            "secrets": len(rows), "keys": len(keys), "settings": 0,
        }
        with zipfile.ZipFile(plain, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for item in tables:
                if not item.rows:
                    continue
                store = item.path.name if "*" in item.store else item.store
                lines = [json.dumps(row, ensure_ascii=False, default=str) for row in space_data._rows(item, ctx)]
                zipped.writestr(f"stores/{store}/{item.table}.jsonl", "\n".join(lines) + "\n")
                manifest["tables"][f"{store}:{item.table}"] = len(lines)
            settings = _space_settings(space, ctx.ids["threads"])
            manifest["settings"] = len(settings)
            zipped.writestr("settings.json", json.dumps(settings, ensure_ascii=False, default=str))
            zipped.writestr("secrets.jsonl", "".join(
                json.dumps({**row, "encrypted_value": _b64(bytes(row["encrypted_value"])),
                            "nonce": _b64(bytes(row["nonce"]))}) + "\n" for row in rows))
            zipped.writestr("keys.json", json.dumps(_seal_keys(space, keys, password)))
            for part, where in _space_folders(space).items():
                if not where.is_dir():
                    continue
                for path in sorted(where.rglob("*")):
                    if path.is_file():
                        zipped.write(path, f"files/{part}/{path.relative_to(where).as_posix()}")
                        manifest["files"] += 1
            zipped.writestr("bundle.json", json.dumps(manifest))
        seal(plain, target, password)
    finally:
        for key in keys:
            key["key"] = b""
        shutil.rmtree(folder, ignore_errors=True)
    logger.info("[spaces] space bundle made space=%s by=%s tables=%d files=%d secrets=%d", space, by,
                len(manifest["tables"]), manifest["files"], manifest["secrets"])
    return manifest


def _space_secrets(space: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from kazma_core.security.vault import get_vault

    if not space_data._vault_rows(space) and not space_data._vault_keys(space):
        return [], []
    vault = get_vault()
    if vault is None:
        raise SpaceMoveRefused("vault_unavailable", 503, "The vault cannot be opened; nothing was made.")
    return vault.export_space_sealed(space)


# ── reading a bundle ────────────────────────────────────────────────────


@dataclass
class _Bundle:
    folder: Path
    zipped: zipfile.ZipFile
    manifest: dict[str, Any]
    space: str
    #: ``{store: [table, ...]}`` from the entries' names.
    tables: dict[str, list[str]] = field(default_factory=dict)

    def lines(self, store: str, table: str) -> Iterator[dict[str, Any]]:
        with self.zipped.open(f"stores/{store}/{table}.jsonl") as handle:
            for raw in handle:
                text = raw.decode("utf-8").strip()
                if not text:
                    continue
                row = json.loads(text)
                if not isinstance(row, dict):
                    raise SpaceMoveRefused("not_a_bundle", 400, "A row of the bundle is not an object.")
                yield row

    def json(self, name: str, default: Any) -> Any:
        try:
            return json.loads(self.zipped.read(name).decode("utf-8"))
        except KeyError:
            return default


def _open_bundle(source: Path, password: str, folder: Path) -> _Bundle:
    from kazma_core.security.sealed_zip import SealedZipRefused, unseal

    plain = folder / "bundle.zip"
    try:
        unseal(source, plain, password, max_bytes=MAX_UNPACKED_BYTES)
    except SealedZipRefused as exc:
        status = 413 if exc.code == "too_big" else 400
        code = "not_a_bundle" if exc.code == "not_sealed" else exc.code
        raise SpaceMoveRefused(code, status, "The bundle was not opened.") from exc
    zipped = zipfile.ZipFile(plain)
    try:
        manifest = json.loads(zipped.read("bundle.json").decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        zipped.close()
        raise SpaceMoveRefused("not_a_bundle", 400, "This is not a space bundle.") from None
    space = str(manifest.get("space") or "") if isinstance(manifest, dict) else ""
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not valid_space_id(space) \
            or space == OWNER_SPACE_ID:
        zipped.close()
        raise SpaceMoveRefused("not_a_bundle", 400, "This is not a space bundle this build reads.")
    bundle = _Bundle(folder=folder, zipped=zipped, manifest=manifest, space=space)
    for name in zipped.namelist():
        parts = PurePosixPath(name).parts
        if name.startswith("stores/"):
            if len(parts) != 3 or not parts[2].endswith(".jsonl") or any(p in ("", ".", "..") for p in parts):
                zipped.close()
                raise SpaceMoveRefused("not_a_bundle", 400, "The bundle names a store it should not.")
            bundle.tables.setdefault(parts[1], []).append(parts[2][: -len(".jsonl")])
    return bundle


# ── the checks ──────────────────────────────────────────────────────────


def _decoded(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            if set(value) != {"base64"}:
                raise SpaceMoveRefused("not_a_bundle", 400, "A row holds a value this build does not read.")
            out[key] = _unb64(value["base64"])
        elif isinstance(value, list):
            raise SpaceMoveRefused("not_a_bundle", 400, "A row holds a value this build does not read.")
        else:
            out[key] = value
    return out


def _bundle_context(bundle: _Bundle) -> space_data._Context:
    """The ids rows of other tables are found by, read from the bundle's own
    rows (as ``space_data._context`` reads them from the stores)."""
    space = bundle.space
    ctx = space_data._Context(space=space)
    for key in ("threads", "summaries", "memory_rows", "commitments", "jobs", "swarm_tasks", "libraries"):
        ctx.ids[key] = []
    picks = (
        ("threads", "thread_scopes.db", "thread_scopes", "thread_id", "space_id"),
        ("summaries", "memory_state.db", "memory_summaries", "id", "tenant_id"),
        ("memory_rows", "memory_state.db", "episodes", "id", "tenant_id"),
        ("memory_rows", "memory_state.db", "beliefs", "id", "tenant_id"),
        ("commitments", "memory_ops.db", "commitments", "commitment_id", "tenant_id"),
        ("jobs", "cron.db", "cron_jobs", "job_id", "tenant_id"),
        ("libraries", "knowledge.db", "knowledge_libraries", "id", "tenant_id"),
    )
    for key, store, table, column, owner in picks:
        if table in bundle.tables.get(store, ()):
            ctx.ids[key] += [str(r[column]) for r in bundle.lines(store, table) if r.get(owner) == space]
    if "swarm_tasks" in bundle.tables.get("swarm_tasks.db", ()):
        for row in bundle.lines("swarm_tasks.db", "swarm_tasks"):
            if _json_path(row.get("metadata"), "$.tenant_id") == space:
                ctx.ids["swarm_tasks"].append(str(row.get("id")))
    return ctx


def _json_path(raw: Any, path: str) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    for step in path.removeprefix("$.").split("."):
        value = value.get(step) if isinstance(value, dict) else None
    return value


def _belongs(row: dict[str, Any], rule: space_data.TableRule, ctx: space_data._Context) -> bool:
    """Whether *row* is the space's by the rule its store finds them by."""
    if rule.how == "column":
        return row.get(rule.column) == ctx.space
    if rule.how == "json":
        return _json_path(row.get(rule.column), rule.path) == ctx.space
    if rule.how == "thread":
        return str(row.get("thread_id") or "") in set(ctx.ids["threads"])
    if rule.how == "ids":
        return str(row.get(rule.column) or "") in set(ctx.ids[rule.ids])
    return False  # keep, follows: no space's rows


@dataclass
class _Target:
    """One store table of this install a bundle table lands in."""

    store: str
    path: Path
    table: str
    rule: space_data.TableRule
    columns: dict[str, str]
    #: Primary key columns; empty for a table whose key is its row id.
    key: tuple[str, ...]
    #: The integer row id column, given a new number on insert.
    renumbered: str = ""
    virtual: bool = False


def _targets(bundle: _Bundle) -> dict[tuple[str, str], _Target]:
    from kazma_core.spaces import row_stores

    row_stores.make_row_stores()
    paths = {(path.name if "*" in name else name): (name, path) for name, path in space_data._store_paths()}
    found: dict[tuple[str, str], _Target] = {}
    for store, tables in bundle.tables.items():
        if store not in paths:
            raise SpaceMoveRefused("unknown_table", 409, f"This install keeps no store {store}.")
        name, path = paths[store]
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            present = dict(space_data._tables(conn))
            for table in tables:
                if table not in present:
                    raise SpaceMoveRefused("unknown_table", 409, f"This install has no table {table} in {store}.")
                info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                columns = {str(r[1]): str(r[2] or "") for r in info}
                rule = space_data._table_rule(name, table, set(columns), virtual_fts=present[table])
                if rule is None:
                    raise SpaceMoveRefused("unknown_table", 409, f"The table {table} of {store} has no rule.")
                keys = [(r[5], str(r[1]), str(r[2] or "")) for r in info if r[5]]
                keys.sort()
                renumbered = ""
                if len(keys) == 1 and "INT" in keys[0][2].upper():
                    renumbered = keys[0][1]
                found[(store, table)] = _Target(
                    store=store, path=path, table=table, rule=rule, columns=columns,
                    key=() if renumbered else tuple(k[1] for k in keys), renumbered=renumbered,
                    virtual=present[table])
    return found


def _remap(value: Any, column: str, exporter: str, owner: str) -> Any:
    """The new owner's id where *value* names the exporter as an account."""
    if exporter == owner or not exporter:
        return value
    entry, new_entry = f"member_{exporter}", f"member_{owner}"
    if isinstance(value, str):
        if column in ACCOUNT_FIELDS and value == exporter:
            return owner
        if value == entry:
            return new_entry
        if value[:1] in "{[":
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            changed, walked = _walk_json(parsed, exporter, owner)
            return json.dumps(walked, ensure_ascii=False) if changed else value
    return value


def _walk_json(value: Any, exporter: str, owner: str, key: str = "") -> tuple[bool, Any]:
    if isinstance(value, dict):
        changed = False
        out = {}
        for k, v in value.items():
            c, out[k] = _walk_json(v, exporter, owner, str(k))
            changed = changed or c
        return changed, out
    if isinstance(value, list):
        results = [_walk_json(v, exporter, owner, key) for v in value]
        return any(c for c, _ in results), [v for _, v in results]
    if isinstance(value, str):
        if key in ACCOUNT_FIELDS and value == exporter:
            return True, owner
        if value == f"member_{exporter}":
            return True, f"member_{owner}"
    return False, value


def _check_rows(bundle: _Bundle, targets: dict[tuple[str, str], _Target], ctx: space_data._Context) -> None:
    """Every row the space's, every column known, no key here already."""
    for (store, table), target in targets.items():
        if target.rule.how in ("keep", "follows"):
            raise SpaceMoveRefused("foreign_row", 409, f"The bundle carries rows of {table}, which holds no space's.")
        with closing(sqlite3.connect(f"file:{target.path}?mode=ro", uri=True)) as conn:
            for raw in bundle.lines(store, table):
                row = _decoded(raw)
                if not _belongs(row, target.rule, ctx):
                    raise SpaceMoveRefused("foreign_row", 409,
                                           f"The bundle carries a row of {table} that is not the space's.")
                unknown = set(row) - set(target.columns)
                if unknown:
                    raise SpaceMoveRefused("unknown_column", 409,
                                           f"This install's {table} has no column {sorted(unknown)[0]}.")
                if target.key and not target.virtual:
                    where = " AND ".join(f'"{c}" IS ?' for c in target.key)
                    if conn.execute(f'SELECT 1 FROM "{table}" WHERE {where} LIMIT 1',
                                    tuple(row.get(c) for c in target.key)).fetchone():
                        raise SpaceMoveRefused("id_collides", 409, f"A row of {table} is here already.")


def _nothing_of_the_space_here(space: str) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import identity

    reason = ""
    if space in identity.read_registry()["spaces"]:
        reason = "the identity store"
    elif sum(item.rows for item in space_data._walk(space)[1]):
        reason = "a store's rows"
    elif any(key.startswith(f"tenant.{space}.") for group in get_config_store().get_all().values() for key in group):
        reason = "a setting"
    elif space_data._vault_rows(space) or space_data._vault_keys(space):
        reason = "a secret"
    elif any(path.exists() for path in _space_folders(space).values()):
        reason = "a folder"
    if reason:
        raise SpaceMoveRefused("space_exists", 409, f"This install holds the space already ({reason}).")


_MAPPINGS = dict(space_data._BY_SPACE_MAPPINGS)


def _check_settings(space: str, settings: dict[str, Any], threads: set[str]) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.safety.grant_holder import thread_of

    store = get_config_store()
    for key, held in settings.items():
        if not isinstance(held, dict) or "value" not in held:
            raise SpaceMoveRefused("not_a_bundle", 400, "A setting of the bundle is not as written.")
        value = held["value"]
        if key in _MAPPINGS:
            if not isinstance(value, dict) or set(value) != {space}:
                raise SpaceMoveRefused("foreign_setting", 409, f"The bundle's {key} names another space.")
            current = store.get(key)
            for step in _MAPPINGS[key]:
                current = current.get(step) if isinstance(current, dict) else None
            if isinstance(current, dict) and space in current:
                raise SpaceMoveRefused("id_collides", 409, f"The space's entry of {key} is here already.")
        else:
            ours = key.startswith(f"tenant.{space}.") or (
                key.startswith(space_data._CHAT_STATE_PREFIXES)
                and any(thread_of(part) in threads for part in key.split(".")))
            if key.startswith(space_data._SPACE_RECORDING_PREFIXES):
                ours = isinstance(value, dict) and str(value.get("space") or "") == space
            if not ours:
                raise SpaceMoveRefused("foreign_setting", 409, f"The bundle carries a setting that is not the space's: {key}.")
            if store.get(key) is not None:
                raise SpaceMoveRefused("id_collides", 409, f"The setting {key} is here already.")
        for pointer in _pointers(value):
            from kazma_core.security.vault import sealing_space

            if sealing_space(pointer, space) != space:
                raise SpaceMoveRefused("foreign_secret", 409, "A setting points at a secret that is not the space's.")


def _pointers(value: Any) -> Iterator[str]:
    from kazma_core.config_store import _VAULT_REF_PREFIX

    if isinstance(value, str) and value.startswith(_VAULT_REF_PREFIX):
        yield value[len(_VAULT_REF_PREFIX):]
    elif isinstance(value, dict):
        for item in value.values():
            yield from _pointers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _pointers(item)


def _file_entries(bundle: _Bundle) -> list[tuple[str, PurePosixPath, str]]:
    """``(part, path inside the part's folder, entry name)``, each checked."""
    out = []
    for name in bundle.zipped.namelist():
        if not name.startswith("files/"):
            continue
        parts = PurePosixPath(name).parts
        if len(parts) < 3 or parts[1] not in ("space", "documents") or any(
                p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
            raise SpaceMoveRefused("not_a_bundle", 400, "The bundle names a file it should not.")
        out.append((parts[1], PurePosixPath(*parts[2:]), name))
    return out


def _check_threads(threads: list[str]) -> None:
    from kazma_core.spaces import threads as thread_store

    for thread in threads:
        if thread_store.record_of(thread) is not None:
            raise SpaceMoveRefused("id_collides", 409, "A chat of the bundle is here already.")


# ── writing ─────────────────────────────────────────────────────────────


def _write_rows(bundle: _Bundle, targets: dict[tuple[str, str], _Target], exporter: str, owner: str) -> int:
    by_store: dict[str, list[_Target]] = {}
    for target in targets.values():
        by_store.setdefault(target.store, []).append(target)
    order = [s for s in _FIRST if s in by_store] + [s for s in by_store if s not in _FIRST]
    written = 0
    for store in order:
        items = by_store[store]
        with closing(sqlite3.connect(str(items[0].path), timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                for target in items:
                    if target.virtual:
                        continue  # an index: written by its store's own code below
                    for raw in bundle.lines(store, target.table):
                        row = {k: _remap(v, k, exporter, owner) for k, v in _decoded(raw).items()}
                        if target.renumbered:
                            row.pop(target.renumbered, None)
                        columns = ", ".join(f'"{c}"' for c in row)
                        cur = conn.execute(
                            f'INSERT INTO "{target.table}" ({columns}) VALUES ({", ".join("?" * len(row))})',
                            tuple(row.values()))
                        written += 1
                        if store == "knowledge.db" and target.table == "knowledge_chunks":
                            _index_chunk(conn, cur.lastrowid, row)
                conn.execute("COMMIT")
                committed = True
            except sqlite3.IntegrityError as exc:
                raise SpaceMoveRefused("id_collides", 409, f"A row of {store} is here already.") from exc
            finally:
                if not committed:
                    conn.execute("ROLLBACK")
    return written


def _index_chunk(conn: sqlite3.Connection, rowid: int | None, row: dict[str, Any]) -> None:
    """A moved chunk's keyword index row, as Knowledge writes it (keyed by the
    chunk's own new row id)."""
    if rowid is None or not row.get("active", 1) or row.get("tombstoned", 0):
        return
    from kazma_core.stores.knowledge import KnowledgeStore

    KnowledgeStore._fts_insert(conn, int(rowid), str(row.get("content") or ""), str(row.get("library_id") or ""),
                               str(row.get("source_url") or ""), str(row.get("id") or ""))


def _write_settings(space: str, settings: dict[str, Any], exporter: str, owner: str) -> int:
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    for key, held in settings.items():
        category = str(held.get("category") or "general")
        _changed, value = _walk_json(held["value"], exporter, owner)
        if key in _MAPPINGS:
            inside = _MAPPINGS[key]

            def _add(current: Any, inside: tuple[str, ...] = inside, value: Any = value) -> Any:
                current = current if isinstance(current, dict) else {}
                holder = current
                for step in inside:
                    holder = holder.setdefault(step, {})
                if space in holder:
                    raise SpaceMoveRefused("id_collides", 409, "The space's entry is here already.")
                holder[space] = value[space]
                return current

            store.atomic_update(key, _add, category)
        else:
            store.set(key, value, category)
    return len(settings)


def _write_files(bundle: _Bundle, entries: list[tuple[str, PurePosixPath, str]]) -> int:
    folders = _space_folders(bundle.space)
    staged = {part: where.with_name(where.name + f".moving-{_secrets.token_hex(4)}")
              for part, where in folders.items()}
    try:
        for part, inside, name in entries:
            path = staged[part].joinpath(*inside.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.zipped.open(name) as src, open(path, "wb") as out:
                shutil.copyfileobj(src, out)
        for part, where in folders.items():
            if staged[part].exists():
                where.parent.mkdir(parents=True, exist_ok=True)
                staged[part].replace(where)
    finally:
        for path in staged.values():
            shutil.rmtree(path, ignore_errors=True)
    return len(entries)


def _journal(space: str, *, add: bool, by: str = "") -> None:
    from kazma_core.config_store import get_config_store

    def _change(current: Any) -> Any:
        held = dict(current) if isinstance(current, dict) else {}
        if add:
            held[space] = {"by": by, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        else:
            held.pop(space, None)
        return held

    get_config_store().atomic_update(JOURNAL_KEY, _change, "system")


def _take_back_failures() -> tuple[type[BaseException], ...]:
    """What a take-back can fail with: a store's file, its database, the
    vault, the settings store away, a walk that refuses."""
    from kazma_core.config_availability import ConfigStoreUnavailableError

    return (OSError, sqlite3.Error, ValueError, ConfigStoreUnavailableError)


def _take_back(space: str, earlier: space_data._Context | None = None) -> None:
    """Remove everything of *space* this install holds: the space was not
    here before its import began (checked), so all of it is the import's."""
    from kazma_core.security.vault import get_vault
    from kazma_core.spaces import threads

    ctx, found = space_data._walk(space, earlier)
    space_data._erase_rows(found, ctx)
    space_data._erase_settings(space, ctx.ids["threads"])
    for thread in ctx.ids["threads"]:
        threads.forget_thread(thread)  # its record went with the rows; its cached space too
    if space_data._vault_rows(space) or space_data._vault_keys(space):
        vault = get_vault()
        if vault is None:
            raise SpaceMoveRefused("vault_unavailable", 503, "The vault cannot be opened.")
        vault.erase_space(space)
    for where in _space_folders(space).values():
        if where.exists():
            shutil.rmtree(where)


def settle_unfinished_imports() -> list[str]:
    """Take back every import a stop cut off (its space is in the journal and
    not in the identity store). Returns the spaces taken back; one whose take
    back failed stays in the journal for the next call."""
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import identity

    held = get_config_store().get(JOURNAL_KEY)
    if not isinstance(held, dict) or not held:
        return []
    recorded = identity.read_registry()["spaces"]
    settled: list[str] = []
    for space in list(held):
        if not valid_space_id(space):
            continue
        if space not in recorded:
            try:
                _take_back(space)
            except _take_back_failures():
                logger.warning("[spaces] an unfinished import of space %s could not be taken back yet", space,
                               exc_info=True)
                continue
        _journal(space, add=False)
        settled.append(space)
        logger.warning("[spaces] an unfinished import of space %s was taken back", space)
    return settled


# ── import ──────────────────────────────────────────────────────────────


def import_bundle(source: Path, password: str, *, owner: str, by: str) -> dict[str, Any]:
    """Take the space in *source* into this install, owned by *owner*. *by*
    is the admin doing it. Returns what was taken (counts)."""
    from kazma_core.db.backend import is_postgres
    from kazma_core.memory.state_backend import mirrors_to_postgres
    from kazma_core.spaces import identity, people

    if not spaces_enforced():
        raise SpaceMoveRefused("spaces_off", 409, "A space is brought in once Spaces is on for this install.")
    if is_postgres() or mirrors_to_postgres():
        raise SpaceMoveRefused("postgres_not_yet", 409, "Bringing a space in is not done on a Postgres install yet.")
    if not identity.is_admin_account(by):
        raise SpaceMoveRefused("admin_only", 403, "Only the admin brings a space in.")
    if owner not in identity.read_registry()["accounts"]:
        raise SpaceMoveRefused("no_such_account", 400, "There is no such person here.")
    with _IMPORT_LOCK:
        settle_unfinished_imports()
        folder = Path(tempfile.mkdtemp(prefix="kazma-space-import-"))
        bundle: _Bundle | None = None
        try:
            bundle = _open_bundle(source, password, folder)
            return _take_in(bundle, password, owner=owner, by=by, people=people)
        finally:
            if bundle is not None:
                bundle.zipped.close()
            shutil.rmtree(folder, ignore_errors=True)


def _take_in(bundle: _Bundle, password: str, *, owner: str, by: str, people: Any) -> dict[str, Any]:
    from kazma_core.security.vault import SpaceSecretsRefused, get_vault

    space = bundle.space
    exporter = str(bundle.manifest.get("exported_by") or "")
    _nothing_of_the_space_here(space)
    targets = _targets(bundle)
    ctx = _bundle_context(bundle)
    _check_rows(bundle, targets, ctx)
    _check_threads(ctx.ids["threads"])
    settings = bundle.json("settings.json", {})
    if not isinstance(settings, dict):
        raise SpaceMoveRefused("not_a_bundle", 400, "The bundle's settings are not as written.")
    _check_settings(space, settings, set(ctx.ids["threads"]))
    entries = _file_entries(bundle)
    rows = []
    for line in bundle.zipped.read("secrets.jsonl").decode("utf-8").splitlines() if \
            "secrets.jsonl" in bundle.zipped.namelist() else []:
        if line.strip():
            row = json.loads(line)
            rows.append({**row, "encrypted_value": _unb64(row.get("encrypted_value")),
                         "nonce": _unb64(row.get("nonce"))})
    keys = _open_keys(space, bundle.json("keys.json", {}), password) if rows or "keys.json" in \
        bundle.zipped.namelist() else []
    vault = get_vault() if rows or keys else None
    if (rows or keys) and vault is None:
        raise SpaceMoveRefused("vault_unavailable", 503, "The vault cannot be opened; nothing was taken.")
    # Everything checked: write, the journal first, the identity store last.
    _journal(space, add=True, by=by)
    done = False
    try:
        if vault is not None:
            try:
                vault.import_space_sealed(space, keys, rows)
            except SpaceSecretsRefused as exc:
                status = 409 if exc.code in ("space_in_vault", "key_collides", "name_collides") else 400
                code = "id_collides" if exc.code in ("space_in_vault", "key_collides", "name_collides") else (
                    "foreign_secret" if exc.code == "not_the_spaces" else "changed")
                raise SpaceMoveRefused(code, status, "The bundle's secrets were not taken.") from exc
        written = _write_rows(bundle, targets, exporter, owner)
        _write_settings(space, settings, exporter, owner)
        files = _write_files(bundle, entries)
        people.record_moved_space(space, str(bundle.manifest.get("name") or space), owner, by=by)
        done = True
    finally:
        for key in keys:
            key["key"] = b""
        if not done:
            try:
                _take_back(space, ctx)
                _journal(space, add=False)
            except _take_back_failures():  # the journal keeps it for the next import's settle
                logger.warning("[spaces] the import of space %s was not taken back yet; the next import will",
                               space, exc_info=True)
    _journal(space, add=False)
    logger.info("[spaces] space moved in space=%s owner=%s by=%s rows=%d files=%d secrets=%d",
                space, owner, by, written, files, len(rows))
    return {"space": space, "name": str(bundle.manifest.get("name") or ""), "rows": written,
            "settings": len(settings), "secrets": len(rows), "files": files}
