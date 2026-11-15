"""An env file, and every copy of a secret file, is written one way: restricted first.

Measured 2026-11-15: ``kazma migrate import`` rewrote ``.env`` with the vault
key through ``tmp.write_text`` + ``os.replace`` and no restriction -- a
restricted ``.env`` came out readable by ``Users`` -- and so did the import's
rollback; ``kazma setup`` (``port_setup``, ``install``, the Postgres setup)
wrote first and restricted after, never reading whether the restriction
held; the universal backup copied ``.env`` with ``shutil.copy2`` beside the
vault (the copy took what the backups folder grants: readable by Users).

The one writer is ``security.file_access.write_private`` (an empty sibling
restricted before the secret is in it, then filled and renamed over), which
``env_files.set_env_values`` / ``write_env_text`` (every env file) and
``file_access.copy_private`` (every copy of a secret file) use. This gate
finds, in the product source, every function that names an env or secret
file (a literal such as ``".env"``/``"meta.env"``/``"vault.db"``, a variable
named ``env_path``/``pass_file``..., or a ``f"{key}={value}"`` env line) AND
writes a file any other way (``write_text``, ``open(..., "w")``,
``os.replace``, ``shutil.copy2``, ``atomic_write_text``...). Each is a writer
to convert, or is declared below with why it is not one.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: The one raw writer of a secret file.
WRITERS = {
    "kazma-core/kazma_core/security/file_access.py::write_private",
    "kazma-core/kazma_core/security/file_access.py::create_private",
}

#: Functions that name an env/secret file and write something else -- each
#: with why its write is not a secret file's.
DECLARED: dict[str, str] = {
    "kazma-core/kazma_core/migration/importer.py::_import_bundle": (
        "renames staged databases into place; the env file goes through "
        "vault_pairing.sync_vault_key -> env_files.set_env_values, and meta.env "
        "is never extracted (bundle.extract_all(exclude=...))"
    ),
    "kazma-core/kazma_core/migration/exporter.py::export_bundle": (
        "writes config.yaml and the settings rows into the export staging "
        "folder; meta.env goes through env_files.write_env_text and the bundle "
        "is made by file_access.create_private"
    ),
    "scripts/backup_kazma.py::main": (
        "writes the archive into a file _private_zip made empty and restricted "
        "first (owner_only.py, loaded by path: the script is standard library only)"
    ),
    "scripts/restore_kazma.py::main": (
        "renames the old data folder aside; it never extracts the archive's .env"
    ),
    "kazma-core/kazma_core/backup/restore.py::restore_files": (
        "copies kazma.yaml, the manifest and research/ into the restore; .env goes "
        "through file_access.copy_private, and every secret file of the restore is "
        "restricted after (file_access.restrict_secret_files)"
    ),
    "kazma-core/kazma_core/backup/restore_drill.py::_check_vault_opens": (
        "writes the vault's -wal/-shm sidecars into its own temporary folder; the "
        "vault goes through file_access.write_private (the key is only read)"
    ),
    "kazma-core/kazma_core/backup/universal.py::_copy_root_artifacts": (
        "copies the configured root artifacts (research/, kazma.yaml); .env goes "
        "through file_access.copy_private, and every secret file of the backup is "
        "restricted after (file_access.restrict_secret_files)"
    ),
    "kazma-core/kazma_core/migration/importer.py::_VaultUndo.restore": (
        "puts the vault.db it moved aside back with a rename (a rename keeps the "
        "file's own access list); the env file goes through env_files.write_env_text"
    ),
}

_SECRET_IDENT = re.compile(r"(?:^|_)(?:env_(?:path|file)|pass_file|meta_env)$")
_FILE_WRITES = {"write_text", "write_bytes", "rename", "atomic_write_text", "atomic_write_bytes",
                "copy", "copy2", "copyfile", "copyfileobj", "move"}
_MODULE_WRITES = {("os", "replace"), ("os", "rename"), ("os", "open"), ("os", "fdopen")}


def _names_a_secret_file(fn: ast.AST) -> bool:
    from kazma_core.store_registry import holds_a_secret

    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if text and len(text) < 120 and "\n" not in text and holds_a_secret(re.split(r"[/\\]", text)[-1]):
                return True
        elif isinstance(node, ast.Name) and _SECRET_IDENT.search(node.id):
            return True
        elif isinstance(node, ast.Attribute) and _SECRET_IDENT.search(node.attr):
            return True
        elif isinstance(node, ast.JoinedStr):
            parts = node.values
            if (len(parts) == 3 and isinstance(parts[0], ast.FormattedValue)
                    and isinstance(parts[1], ast.Constant) and parts[1].value == "="
                    and isinstance(parts[2], ast.FormattedValue)):
                return True  # an env line: f"{key}={value}"
    return False


def _writing_mode(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else next(
        (k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and bool(set(mode.value) & set("wax+"))


def _writes_a_file(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else ""
            if (owner, func.attr) in _MODULE_WRITES or func.attr in _FILE_WRITES:
                return True
            if func.attr == "open" and _writing_mode(node) and owner not in ("zipfile", "tarfile"):
                # Path.open("w"); a call with no literal mode is a read
                return True
        elif isinstance(func, ast.Name):
            if func.id in ("atomic_write_text", "atomic_write_bytes"):
                return True
            if func.id == "open" and _writing_mode(node):
                return True
    return False


def secret_file_writers(sources: dict[str, str]) -> set[str]:
    found: set[str] = set()

    def visit(node: ast.AST, path: str, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                own = ast.Module(body=[n for n in child.body if not isinstance(
                    n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[])
                if _names_a_secret_file(own) and _writes_a_file(own):
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


def _sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/*.py", "kazma-*/**/*.py", "scripts/*.py", "scripts/**/*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {
        f: (REPO / f).read_text(encoding="utf-8", errors="replace")
        for f in sorted(set(files))
        if "/tests/" not in f and "_tests/" not in f and (REPO / f).is_file()
    }


def test_no_secret_file_is_written_any_other_way():
    found = secret_file_writers(_sources())
    undeclared = sorted(found - WRITERS - DECLARED.keys())
    assert not undeclared, (
        "These name an env or secret file and write a file another way. Write an env "
        "file with kazma_core.env_files.set_env_values / write_env_text, a secret file "
        "with security.file_access.write_private, a copy with copy_private -- or declare "
        "the write with why it is not a secret file's:\n  " + "\n  ".join(undeclared))
    stale = sorted(DECLARED.keys() - found)
    assert not stale, f"declared but no longer found (renamed, or no longer writing?): {stale}"
    sources = _sources()
    for writer in WRITERS:
        path, name = writer.split("::")
        assert f"def {name}(" in sources[path], f"the one writer moved: {writer}"


def test_control_the_gate_finds_the_old_writers():
    """Negative control: the writers this replaced, and a planted one, are found."""
    planted = {
        "kazma-core/kazma_core/migration/vault_pairing.py": (
            "def _set_env_key(env_path, key, value):\n"
            "    lines = []\n"
            "    tmp = env_path.with_suffix(env_path.suffix + '.tmp')\n"
            "    tmp.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')\n"
            "    os.replace(tmp, env_path)\n"
        ),
        "kazma-cli/kazma_cli/setup.py": (
            "def _set_env_value(path, key, value):\n"
            "    out = [f'{key}={value}']\n"
            "    atomic_write_text(path, '\\n'.join(out) + '\\n')\n"
            "    _restrict_to_owner(path)\n"
        ),
        "kazma-core/kazma_core/backup/universal.py": (
            "def _copy_root_artifacts(dest):\n"
            "    env_path = root / '.env'\n"
            "    shutil.copy2(env_path, dest / '.env')\n"
        ),
        "kazma-x/kazma_x/planted.py": (
            "def keep_it(value):\n"
            "    with open('.env', 'a') as fh:\n"
            "        fh.write(value)\n"
            "def read_it():\n"
            "    return open('.env').read()\n"
        ),
    }
    assert secret_file_writers(planted) == {
        "kazma-core/kazma_core/migration/vault_pairing.py::_set_env_key",
        "kazma-cli/kazma_cli/setup.py::_set_env_value",
        "kazma-core/kazma_core/backup/universal.py::_copy_root_artifacts",
        "kazma-x/kazma_x/planted.py::keep_it",
    }
