"""The settings kazma.yaml ships, against their committed snapshot.

A stored setting wins over kazma.yaml and the first boot stores every value,
so a changed default reaches an install that booted before it only when it is
declared in ``kazma_core/config_defaults.py`` (``RETIRED_DEFAULTS``: installs
follow; ``NEW_INSTALLS_ONLY``: they keep the old value). This compares
kazma.yaml with ``tests/fixtures/shipped_config_defaults.json``:

    python scripts/shipped_defaults.py           # report; exit 1 when stale
    python scripts/shipped_defaults.py --write   # refresh the snapshot --
                                                 # refused while a changed
                                                 # value is undeclared
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "kazma-core"))

from kazma_core.config_defaults import (  # noqa: E402
    NEW_INSTALLS_ONLY,
    RETIRED_DEFAULTS,
    same_value,
)
from kazma_core.config_store import shipped_settings  # noqa: E402

YAML_PATH = REPO_ROOT / "kazma.yaml"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "shipped_config_defaults.json"


def current_defaults(yaml_path: Path = YAML_PATH) -> dict[str, Any]:
    """kazma.yaml's settings, key -> value, the way the database is seeded."""
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    return {key: value for key, value, _cat in shipped_settings(data)}


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def diff(current: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """What changed since the snapshot: added and removed keys, and changed
    values (key -> (snapshot value, current value))."""
    return {
        "added": sorted(set(current) - set(snapshot)),
        "removed": sorted(set(snapshot) - set(current)),
        "changed": {
            key: (snapshot[key], current[key])
            for key in sorted(set(current) & set(snapshot))
            if not same_value(snapshot[key], current[key])
        },
    }


def undeclared(changed: dict[str, tuple[Any, Any]]) -> list[str]:
    """Changed values neither list declares: a RETIRED_DEFAULTS entry for the
    key whose ``old`` holds the snapshot value, or a NEW_INSTALLS_ONLY key."""
    out = []
    for key, (was, _now) in changed.items():
        if key in NEW_INSTALLS_ONLY:
            continue
        if any(e.key == key and any(same_value(was, old) for old in e.old) for e in RETIRED_DEFAULTS):
            continue
        out.append(key)
    return out


def report(delta: dict[str, Any]) -> str:
    lines = []
    for key in delta["added"]:
        lines.append(f"  added    {key}")
    for key in delta["removed"]:
        lines.append(f"  removed  {key}")
    for key, (was, now) in delta["changed"].items():
        lines.append(f"  changed  {key}: {json.dumps(was, ensure_ascii=False)} -> {json.dumps(now, ensure_ascii=False)}")
    return "\n".join(lines)


def write_snapshot(current: dict[str, Any], path: Path = SNAPSHOT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(current, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="refresh the snapshot")
    args = parser.parse_args(argv)

    current = current_defaults()
    delta = diff(current, load_snapshot())
    if not any(delta.values()):
        print("kazma.yaml matches its snapshot.")
        return 0
    print("kazma.yaml differs from tests/fixtures/shipped_config_defaults.json:")
    print(report(delta))
    missing = undeclared(delta["changed"])
    if missing:
        print(
            "\nA changed default reaches installs that booted before it only when "
            "declared in kazma_core/config_defaults.py: add a RetiredDefault (installs "
            "follow; `old` holds the value above) or a NEW_INSTALLS_ONLY entry (they "
            "keep it; say why) for:\n  " + "\n  ".join(missing)
        )
        return 1
    if args.write:
        write_snapshot(current)
        print("\nSnapshot refreshed.")
        return 0
    print("\nEvery changed value is declared. Refresh the snapshot: python scripts/shipped_defaults.py --write")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
