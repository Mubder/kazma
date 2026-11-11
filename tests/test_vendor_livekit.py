"""The LiveKit browser client ships with Kazma, pinned; no page loads a script from another host.

Live duplex voice (``static/js/voice.js``) loaded ``livekit-client@2`` from
cdn.jsdelivr.net when it was used: whatever version the CDN served that day,
run with the page's full rights (security audit 2026-11-10), and the one
script that kept the pages from sending ``script-src``. It is vendored now
(``scripts/vendor_livekit.py``, like ``scripts/vendor_codemirror.py``):
version, licence, npm's integrity for the tarball and the SHA-256 of each
file sit in ``static/vendor/livekit/vendored.json``, and this test holds the
files to that record. The page policy (``kazma_ui.security_headers``) then
names no other host for scripts.

A product script or template that loads a script, stylesheet or module from
another host fails here; the planted CDN line, the old ``voice.js`` line
among them, is the negative control.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "kazma-ui" / "kazma_ui" / "static" / "vendor" / "livekit"
VOICE = REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "voice.js"


def _script():
    spec = importlib.util.spec_from_file_location("_vendor_livekit", REPO / "scripts" / "vendor_livekit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record() -> dict:
    return json.loads((VENDOR / "vendored.json").read_text(encoding="utf-8"))


def test_the_vendored_files_are_the_ones_recorded() -> None:
    record = _record()
    script = _script()
    assert record["package"] == "livekit-client"
    assert record["version"] == script.LK_VERSION
    assert record["integrity"] == script.LK_INTEGRITY
    assert record["license"] == "Apache-2.0"
    assert set(record["sha256"]) == set(script.FILES.values())
    for name, digest in record["sha256"].items():
        data = (VENDOR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, f"{name} is not the file recorded"
    assert "Apache License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert b"LivekitClient" in (VENDOR / "livekit-client.umd.js").read_bytes()


def test_the_files_stay_byte_for_byte_on_every_checkout() -> None:
    """``text=auto`` turns LF into CRLF on a Windows checkout, which would
    change the recorded SHA-256: the vendored files are ``-text``."""
    for name in _record()["sha256"]:
        rel = f"kazma-ui/kazma_ui/static/vendor/livekit/{name}"
        out = subprocess.run(["git", "-C", str(REPO), "check-attr", "text", "--", rel],
                             capture_output=True, text=True, check=True).stdout
        assert out.strip().endswith(": text: unset"), out


def test_voice_loads_the_vendored_client_at_its_version() -> None:
    voice = VOICE.read_text(encoding="utf-8")
    version = _record()["version"]
    assert f"'/static/vendor/livekit/livekit-client.umd.js?v={version}'" in voice
    assert "cdn.jsdelivr" not in voice and "unpkg.com" not in voice


def test_the_app_serves_it_as_a_script() -> None:
    from fastapi.testclient import TestClient

    from kazma_ui.app import create_app

    response = TestClient(create_app()).get("/static/vendor/livekit/livekit-client.umd.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert hashlib.sha256(response.content).hexdigest() == _record()["sha256"]["livekit-client.umd.js"]


# ── no script from another host ─────────────────────────────────────────

#: A script, stylesheet or module whose address is another host's.
_OUTSIDE_LOAD = re.compile(
    r"""(?:<script\b[^>]*\bsrc\s*=\s*["']\s*(?:https?:)?//"""
    r"""|<link\b[^>]*\bhref\s*=\s*["']\s*(?:https?:)?//"""
    r"""|\.src\s*=\s*["'`]\s*(?:https?:)?//"""
    r"""|\bimport\s*\(\s*["'`]\s*(?:https?:)?//"""
    r"""|\bimportScripts\s*\(\s*["'`]\s*(?:https?:)?//)""",
    re.IGNORECASE,
)

#: Files that are not pages Kazma serves to its users, with why.
NOT_A_PAGE = {
    "kazma-ui/kazma_ui/static/font-preview.html": (
        "a static font specimen for the design work, linked from no page; its "
        "Google Fonts link is refused by style-src like any other"
    ),
}


def outside_loads(sources: dict[str, str]) -> list[str]:
    found = []
    for rel, text in sources.items():
        if rel in NOT_A_PAGE:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if _OUTSIDE_LOAD.search(line):
                found.append(f"{rel}:{number}: {line.strip()[:120]}")
    return found


def _product_pages() -> dict[str, str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-ui/kazma_ui/static", "kazma-ui/kazma_ui/templates"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    keep = {}
    for rel in out:
        if not rel.endswith((".js", ".html")) or "/vendor/" in rel or rel.endswith(".min.js"):
            continue
        keep[rel] = (REPO / rel).read_text(encoding="utf-8", errors="replace")
    return keep


def test_no_page_loads_a_script_from_another_host() -> None:
    pages = _product_pages()
    assert len(pages) > 50
    assert outside_loads(pages) == []


def test_every_exemption_is_still_a_file() -> None:
    for rel, why in NOT_A_PAGE.items():
        assert why and (REPO / rel).is_file(), rel


def test_negative_control_each_outside_load_is_found() -> None:
    planted = {
        "kazma-ui/kazma_ui/static/js/voice.js":
            "      s.src = 'https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.umd.js';",
        "kazma-ui/kazma_ui/templates/a.html": '<script src="https://cdn.example.com/x.js"></script>',
        "kazma-ui/kazma_ui/templates/b.html": '<link rel="stylesheet" href="//fonts.example.com/f.css">',
        "kazma-ui/kazma_ui/static/js/c.js": "await import('https://esm.example.com/m.js');",
    }
    assert len(outside_loads(planted)) == 4
    same_site = {"kazma-ui/kazma_ui/static/js/d.js": "s.src = '/static/vendor/livekit/livekit-client.umd.js';"}
    assert outside_loads(same_site) == []
