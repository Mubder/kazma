"""The docs site's build packages stay above the advisories fixed in them
(2026-11, supply chain).

``docs/package-lock.json`` (the Docusaurus site) held 50 vulnerable packages
by ``npm audit`` (91 Dependabot alerts). ``npm audit fix`` -- never
``--force`` -- moved 23 of them past their advisories within the ranges their
dependents allow. It also took Mermaid 12, a major version (and eight days
old) that Docusaurus 3's ``theme-mermaid`` allows with ``>=11.6.0``;
``docs/package.json`` held Mermaid on 11.x (``overrides``), whose 11.16.1
already fixed Mermaid's own advisories.

2026-11-13: Mermaid 12 (12.3.0, two months after 12.0) is taken through the
same override. It brings KaTeX 0.18, which fixes the KaTeX advisory Mermaid
11's 0.16 carried; every diagram in the docs parses with it
(``tests/test_docs_mermaid_diagrams_parse.py``) and the site builds. ``uuid``
11 is taken through ``overrides`` too: the dev server's ``sockjs`` asked for
8.x (``require('uuid').v4``, which 11 still exports), and its advisory was
every moderate one ``npm audit`` reported (each Docusaurus package depends on
the dev server). What is left is low and has no fixed release (``deepmerge``).

Each floor below is the first release that fixes the advisories npm audit
reported for that package on 2026-11-11; the lock must not fall back below
one. What is left needs a major version Docusaurus 3.10.2 holds back
(``docs/docs/contributing/development-setup.md``).

Two of those were taken through ``overrides`` (2026-11-11, Dependabot's
critical and high alerts): ``tinypool`` 2 (prototype pollution leading to code
execution; Docusaurus loads it with ``import()`` only for its SSG worker pool,
off in this site's config, and 2.2.0 still hands a worker ``workerData[1]`` and
its ``__tinypool_state__.workerId``) and ``serialize-javascript`` 7 (code
execution, cross-site scripting, a denial of service; webpack's copy and CSS
plugins). The site builds with both. Their floors are below.
"""

from __future__ import annotations

import json
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "docs" / "package-lock.json"

#: package -> the first fixed release, by major line where the lock has two.
FLOORS: dict[str, str | dict[str, str]] = {
    "baseline-browser-mapping": "2.11.0",
    "body-parser": "1.20.9",
    "brace-expansion": {"1": "1.1.21"},
    "browserslist": "4.28.7",
    "colord": "2.9.4",
    "compression": "1.8.2",
    "dompurify": "3.4.17",
    "fast-uri": "3.1.9",
    "follow-redirects": "1.16.2",
    "image-size": "2.0.3",
    "joi": "17.13.9",
    "js-yaml": {"4": "4.3.2"},
    "launch-editor": "2.14.2",
    "mermaid": "11.16.1",
    "katex": "0.18.2",
    "nanoid": {"3": "3.3.18"},
    "postcss": "8.5.23",
    "postcss-selector-parser": {"6": "6.1.5", "7": "7.1.6"},
    "proxy-addr": "2.0.8",
    "qs": "6.16.1",
    "shell-quote": "1.11.0",
    "source-map-js": "1.2.2",
    "svgo": {"3": "3.3.5"},
    "undici": "7.30.1",
    # Through overrides (2026-11-11): Docusaurus 3.10.2 holds both a major back.
    "tinypool": "2.1.2",
    "serialize-javascript": "7.1.3",
    # 2026-11-13: the dev server's sockjs asks for uuid 8.
    "uuid": "11.1.1",
}


def _versions(lock: dict, name: str) -> list[str]:
    return [entry["version"] for key, entry in lock.get("packages", {}).items()
            if key.rsplit("node_modules/", 1)[-1] == name and "version" in entry]


def below_floor(lock: dict) -> list[str]:
    found = []
    for name, floor in FLOORS.items():
        for version in _versions(lock, name):
            line_floor = floor.get(version.split(".")[0]) if isinstance(floor, dict) else floor
            if line_floor and Version(version) < Version(line_floor):
                found.append(f"{name}@{version} < {line_floor}")
    return found


def test_no_package_falls_below_its_fixed_release():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert all(_versions(lock, name) for name in FLOORS), "a floor names a package the lock no longer has"
    assert not below_floor(lock), below_floor(lock)


def test_mermaid_is_12_and_one_copy():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    package = json.loads((ROOT / "docs" / "package.json").read_text(encoding="utf-8"))
    assert package["overrides"]["mermaid"].startswith("^12.")
    assert {v.split(".")[0] for v in _versions(lock, "mermaid")} == {"12"}


def test_the_overrides_hold_the_majors_docusaurus_holds_back():
    package = json.loads((ROOT / "docs" / "package.json").read_text(encoding="utf-8"))
    assert package["overrides"]["tinypool"].startswith("^2.")
    assert package["overrides"]["serialize-javascript"].startswith("^7.")
    assert package["overrides"]["uuid"].startswith("^11.")
    # Both need Node 20, as Docusaurus 3.10.2 does.
    assert package["engines"]["node"] == ">=20.0"


def test_negative_control_the_old_lock_is_refused():
    """Versions the lock held before the fix (2026-11-11)."""
    old = {"packages": {
        "node_modules/dompurify": {"version": "3.4.12"},
        "node_modules/undici": {"version": "7.28.0"},
        "node_modules/postcss-selector-parser": {"version": "7.1.4"},
        "node_modules/foo/node_modules/postcss-selector-parser": {"version": "6.1.6"},
        "node_modules/tinypool": {"version": "1.1.1"},
        "node_modules/serialize-javascript": {"version": "6.0.2"},
    }}
    assert below_floor(old) == ["dompurify@3.4.12 < 3.4.17", "postcss-selector-parser@7.1.4 < 7.1.6",
                                "undici@7.28.0 < 7.30.1", "tinypool@1.1.1 < 2.1.2",
                                "serialize-javascript@6.0.2 < 7.1.3"]
