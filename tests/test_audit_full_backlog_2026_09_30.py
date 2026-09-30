"""Gates for the AUD-0xx backlog fixed after AUDIT_FULL_2026-09-30.

Each test pins one finding's fix with a negative control where the class
admits one (AGENTS §45 / memory: fixes land with a class gate). The
delivery-path / async-loop findings are covered by their own suites
(voice route, static gates); this file holds the pure-function gates.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from starlette.requests import Request

import kazma_ui.auth as auth_mod

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATES = _REPO / "kazma-ui" / "kazma_ui" / "templates"


def _req(headers: dict[str, str]) -> Request:
    """A real Starlette Request from an ASGI scope (test_csrf philosophy)."""
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw,
        "query_string": b"",
        "client": ("127.0.0.1", 5000),
        "server": ("127.0.0.1", 9090),
        "scheme": "http",
    }
    return Request(scope)


# ── AUD-021: 0.0.0.0 is not a loopback host name ─────────────────────────

def test_host_local_name_accepts_loopback() -> None:
    assert auth_mod._host_is_local_name(_req({"host": "127.0.0.1:9090"})) is True
    assert auth_mod._host_is_local_name(_req({"host": "localhost:9090"})) is True


def test_host_local_name_rejects_0_0_0_0() -> None:
    """AUD-021: browsers route 0.0.0.0 to loopback, so it must NOT count as a
    local host name (it would inherit the peer-trust auto-login)."""
    assert auth_mod._host_is_local_name(_req({"host": "0.0.0.0:9090"})) is False


def test_0_0_0_0_removed_from_the_local_name_set() -> None:
    """Negative control: 0.0.0.0 is gone from the executable code (a comment
    may still name it as the reason it was dropped)."""
    import inspect

    code = [
        line
        for line in inspect.getsource(auth_mod._host_is_local_name).splitlines()
        if not line.strip().startswith("#")
    ]
    assert not any("0.0.0.0" in line for line in code)


# ── AUD-023: sessions are minted only for browser clients ────────────────

def test_looks_like_browser_true_for_sec_fetch() -> None:
    assert auth_mod._looks_like_browser(_req({"sec-fetch-mode": "navigate"})) is True


def test_looks_like_browser_true_for_html_accept() -> None:
    assert auth_mod._looks_like_browser(
        _req({"accept": "text/html,application/xhtml+xml"})
    ) is True


def test_looks_like_browser_true_when_cookie_already_present() -> None:
    assert auth_mod._looks_like_browser(
        _req({"cookie": f"{auth_mod.SESSION_COOKIE}=abc"})
    ) is True


def test_looks_like_browser_false_for_bare_header_client() -> None:
    """A curl/CLI/webhook client (X-Kazma-Secret only, JSON accept, no
    Sec-Fetch, no cookie) is not a browser — no session row is minted."""
    assert auth_mod._looks_like_browser(
        _req({"x-kazma-secret": "s", "accept": "application/json"})
    ) is False


# ── AUD-014: the dead per-session WS token machinery stays removed ───────

def test_ws_session_token_machinery_removed() -> None:
    assert not hasattr(auth_mod, "generate_ws_session_token")
    assert not hasattr(auth_mod, "verify_ws_session_token")
    assert not hasattr(auth_mod, "_ws_session_tokens")


# ── AUD-025: JSON embedded in a <script> block is context-escaped ────────

def test_json_for_script_neutralizes_script_break() -> None:
    from kazma_ui.app import json_for_script

    payload = {"msg": "</script><!--    a>b"}
    out = json_for_script(payload)
    # No raw sequence that could end the element or break a JS string literal.
    for bad in ("<", ">", " ", " "):
        assert bad not in out, (bad, out)
    # Still valid JSON that decodes back to the original value.
    import json

    assert json.loads(out) == payload


def test_json_for_script_is_noop_for_plain_text() -> None:
    """Negative control: text with none of the dangerous chars is unchanged
    except normal JSON quoting."""
    from kazma_ui.app import json_for_script

    import json

    assert json_for_script({"k": "hello world"}) == json.dumps(
        {"k": "hello world"}, ensure_ascii=False
    )


# ── AUD-025 class: template JSON is never decoded twice ─────────────────
# `JSON.parse('{{ x | tojson }}')` runs tojson's output through a JS string
# literal first, which decodes \" \n \\ before JSON.parse sees them — so any
# value holding a quote, newline or backslash throws. The Swarm page did this
# with each worker's task and logs (2026-09-30).

_JSON_FROM_JS_STRING = re.compile(r"""JSON\.parse\(\s*['"`]\s*\{\{""")
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)


def _template_code(path: Path) -> str:
    return _JINJA_COMMENT.sub("", path.read_text(encoding="utf-8"))


def test_no_template_parses_jinja_json_out_of_a_js_string() -> None:
    offenders = [
        f"{p.relative_to(_TEMPLATES).as_posix()}:{n}"
        for p in sorted(_TEMPLATES.rglob("*.html"))
        for n, line in enumerate(_template_code(p).splitlines(), 1)
        if _JSON_FROM_JS_STRING.search(line)
    ]
    assert not offenders, (
        "Embed template JSON as `{{ value | tojson }}` directly — never inside "
        f"JSON.parse('...'), which decodes it twice: {offenders}"
    )


def test_the_detector_catches_the_old_swarm_line() -> None:
    """Negative control: the line the Swarm page shipped with is flagged."""
    old = (
        "window.KAZMA_WORKERS = JSON.parse('{{ workers | tojson | "
        'replace("<", "\\\\u003c") | safe if workers else "[]" }}\');'
    )
    assert _JSON_FROM_JS_STRING.search(old)


def test_swarm_workers_line_survives_ordinary_worker_text() -> None:
    """Render the REAL swarm.html line with a worker whose task and logs hold a
    quote, a newline, a backslash and a script-closing sequence."""
    import jinja2

    line = next(
        ln.strip()
        for ln in _template_code(_TEMPLATES / "swarm.html").splitlines()
        if "window.KAZMA_WORKERS =" in ln
    )
    env = jinja2.Environment(autoescape=True)
    workers = [{
        "name": "coder",
        "last_task": 'fix the "quoted" bug\nthen C:\\path\\x',
        "logs": ['said "hi"', "</script><!-- x"],
    }]
    for data, expected in ((workers, workers), (None, []), ([], [])):
        rendered = env.from_string(line).render(workers=data)
        assert "</script" not in rendered.lower()
        rhs = rendered.split("=", 1)[1].strip().rstrip(";")
        assert json.loads(rhs) == expected


# ── AUD-008 / AUD-009: voice upload is bounded and the ext is allow-listed ─

def test_voice_audio_ext_allowlists_known_formats() -> None:
    from kazma_ui.routes_voice import _audio_ext

    assert _audio_ext("clip.mp3", None) == "mp3"
    assert _audio_ext("clip.WEBM", None) == "webm"  # normalized
    assert _audio_ext(None, "audio/ogg") == "ogg"


def test_voice_audio_ext_rejects_unknown_and_traversal() -> None:
    """A non-audio / traversal-shaped suffix falls back to the safe default,
    so it never reaches NamedTemporaryFile(suffix=...) as-is."""
    from kazma_ui.routes_voice import _audio_ext

    assert _audio_ext("evil.sh", None) == "ogg"
    assert _audio_ext("a.tar.gz", None) == "ogg"
    assert _audio_ext("../../etc/passwd", None) == "ogg"
    assert _audio_ext("noext", None) == "ogg"


def test_voice_audio_ext_reads_alias_mime_spellings() -> None:
    """A filename-less upload typed with a non-canonical spelling is labelled
    by what it is, not the default."""
    from kazma_ui.routes_voice import _audio_ext

    assert _audio_ext(None, "audio/x-wav") == "wav"
    assert _audio_ext(None, "audio/wave") == "wav"
    assert _audio_ext(None, "audio/x-m4a") == "m4a"
    assert _audio_ext(None, "audio/mp4;codecs=mp4a.40.2") == "mp4"


def test_voice_stt_route_bounds_the_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real route: one byte over the bound is a 413 that never
    reaches the provider; exactly at the bound is transcribed (AUD-008).

    The bound is lowered to 16 bytes so the test needs no 25 MB body; the
    route must read at most bound+1 bytes, or the 17-byte case could not be
    told apart from a legitimate upload.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import routes_voice

    monkeypatch.setenv("KAZMA_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setattr(routes_voice, "_MAX_STT_BYTES", 16)
    monkeypatch.setattr(
        routes_voice, "_read_settings", lambda *keys: {k: None for k in keys}
    )
    seen: dict[str, object] = {}

    async def _fake_transcribe(audio: bytes, **kwargs: object) -> str:
        seen["bytes"] = len(audio)
        seen["format"] = kwargs.get("audio_format")
        return "hello"

    monkeypatch.setattr("kazma_core.voice.stt.transcribe_preferring", _fake_transcribe)

    app = FastAPI()
    app.include_router(routes_voice.router)
    client = TestClient(app)

    over = client.post(
        "/api/voice/stt", files={"file": ("voice.webm", b"x" * 17, "audio/webm")}
    )
    assert over.status_code == 413
    assert "bytes" not in seen  # rejected before any provider call

    at_bound = client.post(
        "/api/voice/stt", files={"file": ("voice.mp4", b"x" * 16, "audio/mp4")}
    )
    assert at_bound.status_code == 200, at_bound.text
    assert at_bound.json()["text"] == "hello"
    assert seen == {"bytes": 16, "format": "mp4"}


# ── AUD-004: the system-log tool tails from the end, off the loop ────────

def test_system_log_tail_returns_the_last_lines(tmp_path: Path) -> None:
    from kazma_skills.native.system_health_monitor.tools import _tail_lines

    big = tmp_path / "kazma.log"
    big.write_text("".join(f"line {i}\n" for i in range(5000)), encoding="utf-8")
    assert _tail_lines(big, 3) == ["line 4997", "line 4998", "line 4999"]
    assert _tail_lines(big, 1) == ["line 4999"]

    small = tmp_path / "small.log"  # shorter than one block, CRLF, no final EOL
    small.write_bytes(b"a\r\nb\r\nc")
    assert _tail_lines(small, 2) == ["b", "c"]
    assert _tail_lines(small, 10) == ["a", "b", "c"]

    empty = tmp_path / "empty.log"
    empty.write_bytes(b"")
    assert _tail_lines(empty, 5) == []


def test_system_log_tail_reads_only_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 2 MB log is tailed by reading a few blocks, not the whole file (the
    old code ran readlines() over the whole day's log on the event loop)."""
    from kazma_skills.native.system_health_monitor import tools

    log = tmp_path / "kazma.log"
    log.write_bytes(b"".join(b"x" * 100 + b"\n" for _ in range(20_000)))
    read = {"bytes": 0}
    real_open = open

    class _Counting:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

        def seek(self, *a):
            return self._f.seek(*a)

        def tell(self):
            return self._f.tell()

        def read(self, n=-1):
            data = self._f.read(n)
            read["bytes"] += len(data)
            return data

    monkeypatch.setattr(
        tools, "open", lambda *a, **kw: _Counting(real_open(*a, **kw)), raising=False
    )
    assert len(tools._tail_lines(log, 50)) == 50
    assert read["bytes"] <= 4 * 8192, read

    # Detector self-check: a whole-file read through the same counter is seen.
    read["bytes"] = 0
    with tools.open(log, "rb") as f:
        f.read()
    assert read["bytes"] == log.stat().st_size


async def test_read_system_logs_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from kazma_skills.native.system_health_monitor import tools

    seen: list[bool] = []

    def _body(lines: int) -> str:
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return "ok"

    monkeypatch.setattr(tools, "_read_system_logs_sync", _body)
    assert await tools.read_system_logs(10) == "ok"
    assert seen == [False]


# ── AUD-005: a skill install validates and extracts off the loop ─────────

async def test_skill_zip_extracts_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import io
    import shutil
    import zipfile

    import httpx

    from kazma_core.agent_skills import installer

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "owner-repo-abc123/skills/demo/SKILL.md",
            "---\nname: demo\ndescription: a demo skill\n---\nbody\n",
        )
    payload = buf.getvalue()

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda req: httpx.Response(200, content=payload)
        )
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)

    on_loop: list[bool] = []
    real_extract = zipfile.ZipFile.extractall

    def _spy(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real_extract(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "extractall", _spy)

    root, cleanup = await installer._download_github_zip("owner", "repo")
    try:
        assert on_loop == [False]  # the 500 MB-capable extract ran in a thread
        assert (root / "skills" / "demo" / "SKILL.md").is_file()
    finally:
        shutil.rmtree(cleanup, ignore_errors=True)

    # Detector self-check: an extract made on the loop IS recorded as such.
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        zf.extractall(tmp_path)
    assert on_loop == [False, True]


# ── AUD-028: migration PK identifiers are quote-doubled ──────────────────

def test_quote_ident_doubles_embedded_quote() -> None:
    from kazma_core.migration.path_rewrite import _quote_ident

    assert _quote_ident("plain") == '"plain"'
    assert _quote_ident('we"ird') == '"we""ird"'


def test_rewrite_survives_a_quoted_pk_column_name() -> None:
    """A bundle whose PK column name contains a double-quote must not produce
    a malformed SELECT on the staging copy (AUD-028)."""
    from kazma_core.migration.path_rewrite import (
        PathMap,
        _rewrite_one_column,
        _quote_ident,
    )

    conn = sqlite3.connect(":memory:")
    conn.execute('CREATE TABLE t ("we""ird" TEXT PRIMARY KEY, val TEXT)')
    conn.execute(
        'INSERT INTO t VALUES (?, ?)', ("k1", "/home/user/kazma/workspace/a.txt")
    )
    conn.commit()

    pm = PathMap()
    pm.add("/home/user/kazma", "/data/kazma")

    changed = _rewrite_one_column(conn, "t", "val", pm, None, 1000)
    assert changed == 1
    got = conn.execute('SELECT val FROM t').fetchone()[0]
    assert got == "/data/kazma/workspace/a.txt"

    # Negative control: the pre-fix quoting (single "{c}") builds broken SQL
    # for this exact column name, proving the double-quote is what saves it.
    bad_pk = '"' + 'we"ird' + '"'  # what f'"{c}"' produced
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(f'SELECT {bad_pk} FROM t')
    # ...while the doubled form is valid.
    conn.execute(f'SELECT {_quote_ident("we" + chr(34) + "ird")} FROM t')
    conn.close()
