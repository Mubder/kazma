"""A list says "none" only after the server answered with none.

Found on the live install 2026-10-15: opening the Documents page showed "No
documents yet." for several seconds while the library was still loading, then
five documents appeared. The empty state was ``x-show="documents.length ===
0"``: an empty array before the first answer reads exactly like an answer
with nothing in it. The chat sidebar had the same shape a month earlier
(``tests/e2e/test_session_list_loading.py``), and this gate found 39 more
empty states of that shape on the code of the day -- Settings' providers,
profiles, tokens, sessions, users, logs and backups, the Workspace's GitHub
tabs and files, the IDE's file tree, the Memory tables, the Knowledge
libraries, the Scheduled list, the Agents history, the alerts dropdown.

The rule (AGENTS.md, UI Conventions): every empty state over data loaded from
the server names the load in its condition --

* a flag set when the load SUCCEEDED (``documentsLoaded``, ``audit.loaded``,
  ``ghTabLoaded.pulls``): before it, and after a failed read, nothing is
  claimed; or
* a loading flag AND an error flag (``!loading && !error && !rows.length``):
  the shape X Studio and the dataset panel use, where the error is shown in
  place of the list.

A loading flag alone is not enough: when the read fails, loading ends, the
list is still empty, and the page says "none" over a failure (Settings' MCP,
skills and tools tabs did, behind ``!loading``; the page's ``loading`` was
not even the tab's load).

Everything else -- a filter over a list already loaded, a terminal's "ready",
a search answer, a list the page fills itself -- is declared in
``NOT_A_SERVER_LOAD`` with its reason. A declaration that no longer matches a
template's condition fails, so the list cannot rot.

Lists a script paints have the same rule on their failure path: no ``catch``
block in a page script paints an empty list (``renderHistoryTable([])``, the
Swarm history's), an empty-state text (a ``no_...``/``...empty...`` key), or
invented rows (``this.files = fallbackFiles()``: the Workspace showed
kazma-core/, README.md... that were in no workspace when its file read
failed). A failed read says it failed.

Each check has a negative control built from the shape that shipped.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "kazma-ui" / "kazma_ui" / "templates"
STATIC_JS = REPO / "kazma-ui" / "kazma_ui" / "static" / "js"

#: An empty-state condition: a collection's length compared with zero, a
#: negated length (``!rows.length``, ``!(c.history || []).length``,
#: ``!arabicVoices().length``), or "none of them" (``!list.some(...)``).
_EMPTY_TEST = re.compile(
    r"\.length\s*(?:===?|<=)\s*0(?![\w.])"
    r"|\.length\s*<\s*1(?![\w.])"
    r"|!\s*\(\s*[^()]*\)\s*\.length\b(?!\s*(?:[<>=!]|\.|\[|\())"
    r"|!\s*[\w$.?\[\]]+(?:\([^()]*\))?\.length\b(?!\s*(?:[<>=!]|\.|\[|\())"
    r"|!\s*[\w$.?\[\]]+\.some\("
)
_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")
_NAME = re.compile(r"[A-Za-z_$][\w$]*")
#: Names a successful load sets.
_LOADED = re.compile(r"loaded", re.I)
#: Names a load in flight sets, and names its failure sets.
_LOADING = re.compile(r"loading|loadstates|busy|searching|fetching", re.I)
_ERROR = re.compile(r"error|failed", re.I)

#: (template, condition) -> why its emptiness needs no load flag.
NOT_A_SERVER_LOAD: dict[tuple[str, str], str] = {
    ("base.html", "!$store.search.loading && $store.search.searched && !$store.search.results.length"): (
        "The search overlay's answer to a query the user typed: shown only after "
        "the search finished (!loading, searched). It searches the page list, "
        "which is in the page, so a failed session read still leaves a real "
        "answer about the pages."
    ),
    ("documents.html", "inLibraries && inLibraries.length === 0"): (
        "inLibraries is null until the server says which libraries hold the "
        "document (documents.js); the null IS the not-loaded state, and the "
        "panel around it is hidden while it is null."
    ),
    ("partials/x_datasets.html", "!filtered.length"): (
        "A search over the cases of a collection already opened: it renders "
        "inside x-if=\"dataset\", which only a successful open sets."
    ),
    ("settings.html", "filteredDiscoveredModels(selectedHubProvider()).length === 0"): (
        "The filter box over a provider's discovered models: it renders inside "
        "the panel shown only when that provider HAS discovered models, so "
        "empty here means nothing matches the filter."
    ),
    ("settings.html", "!xReplyPolicyChanges().length"): (
        "A comparison of the edited reply policy with the saved one, made in "
        "the page; not a list the server returns."
    ),
    ("settings.html", "!Object.keys(s.reply_style || {}).length"): (
        "One subject card's own style, inside the x-for over the subjects the "
        "loaded policy holds: no keys means the subject inherits the global "
        "style, which is what the line says."
    ),
    ("workspace.html", "gitStaged.length === 0"): (
        "Inside the Git panel shown only while gitIsGit, which only a "
        "successful /api/git/status sets (a failed read clears it)."
    ),
    ("workspace.html", "gitModified.length === 0"): (
        "Inside the Git panel shown only while gitIsGit, which only a "
        "successful /api/git/status sets (a failed read clears it)."
    ),
    ("workspace.html", "gitUntracked.length === 0"): (
        "Inside the Git panel shown only while gitIsGit, which only a "
        "successful /api/git/status sets (a failed read clears it)."
    ),
    ("workspace.html", "termOutput.length === 0"): (
        "The terminal's \"ready\" line before the first command: the page fills "
        "termOutput itself, nothing is loaded."
    ),
}


def _normalize(expression: str) -> str:
    return re.sub(r"\s+", " ", expression).strip()


def is_empty_state(expression: str) -> bool:
    """True when ``expression`` tests a collection for being empty."""
    return bool(_EMPTY_TEST.search(_STRING.sub("''", expression)))


def names_its_load(expression: str) -> bool:
    """True when the condition waits for a successful load, or for a load
    that ended without an error."""
    names = _NAME.findall(_STRING.sub("''", expression))
    if any(_LOADED.search(n) for n in names):
        return True
    return any(_LOADING.search(n) for n in names) and any(_ERROR.search(n) for n in names)


def load_flags(expression: str) -> set[str]:
    """The success flags a condition names (``documentsLoaded``, ``loaded``)."""
    return {n for n in _NAME.findall(_STRING.sub("''", expression)) if _LOADED.search(n)}


class _Conditions(HTMLParser):
    """Every ``x-show`` / ``x-if`` value with its line."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in ("x-show", "x-if") and value:
                self.found.append((self.getpos()[0], value))


def _keep_lines(match: re.Match[str], fill: str) -> str:
    return fill + "\n" * match.group(0).count("\n")


def conditions_of(markup: str) -> list[tuple[int, str]]:
    """``(line, condition)`` for every x-show/x-if; Jinja is blanked with its
    newlines kept, so lines are the file's own."""
    markup = re.sub(r"\{%.*?%\}|\{#.*?#\}", lambda m: _keep_lines(m, ""), markup, flags=re.S)
    markup = re.sub(r"\{\{.*?\}\}", lambda m: _keep_lines(m, "__jinja__"), markup, flags=re.S)
    parser = _Conditions()
    parser.feed(markup)
    return parser.found


def unloaded_empty_states(markup: str) -> list[tuple[int, str]]:
    """The empty states in ``markup`` whose condition does not name a load."""
    return [
        (line, _normalize(cond))
        for line, cond in conditions_of(markup)
        if is_empty_state(cond) and not names_its_load(cond)
    ]


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def _rel(path: Path) -> str:
    return path.relative_to(TEMPLATES).as_posix()


def _all_empty_states() -> list[tuple[str, int, str]]:
    sites = []
    for path in _templates():
        for line, cond in conditions_of(path.read_text(encoding="utf-8")):
            if is_empty_state(cond):
                sites.append((_rel(path), line, _normalize(cond)))
    return sites


# ── the gate ────────────────────────────────────────────────────────────


def test_the_scan_reads_the_templates() -> None:
    """The scan finds the empty states it is about, from the real folder."""
    sites = _all_empty_states()
    assert len(sites) >= 40, f"the scan found only {len(sites)} empty states"
    templates = {s[0] for s in sites}
    for expected in ("documents.html", "settings.html", "workspace.html", "memory.html"):
        assert expected in templates, f"no empty state found in {expected}"


def test_every_empty_state_waits_for_its_load() -> None:
    bad = []
    for path in _templates():
        rel = _rel(path)
        for line, cond in unloaded_empty_states(path.read_text(encoding="utf-8")):
            if (rel, cond) not in NOT_A_SERVER_LOAD:
                bad.append(f"{rel}:{line}  {cond}")
    assert not bad, (
        "These empty states show before their list has loaded (or after a "
        "failed read). Name the load in the condition -- a flag the loader "
        "sets on success (xLoaded), or loading AND error flags -- or declare "
        "the site in NOT_A_SERVER_LOAD with why:\n  " + "\n  ".join(bad)
    )


def test_every_declaration_still_matches_an_unflagged_empty_state() -> None:
    """A declaration nothing matches is stale: the condition changed (or now
    names its load and needs no declaration)."""
    live = {(rel, cond) for rel, _line, cond in _all_empty_states()
            if not names_its_load(cond)}
    stale = sorted(f"{rel}: {cond}" for rel, cond in NOT_A_SERVER_LOAD if (rel, cond) not in live)
    assert not stale, "Stale NOT_A_SERVER_LOAD entries:\n  " + "\n  ".join(stale)


def _page_sources() -> str:
    parts = [p.read_text(encoding="utf-8") for p in STATIC_JS.rglob("*.js") if "min.js" not in p.name]
    parts += [p.read_text(encoding="utf-8") for p in _templates()]
    return "\n".join(parts)


def test_every_load_flag_an_empty_state_waits_for_is_set_somewhere() -> None:
    """A flag nothing sets keeps its empty state hidden for good: the gate
    must not be satisfied by a name alone."""
    sources = _page_sources()
    missing = []
    for rel, line, cond in _all_empty_states():
        for flag in load_flags(cond):
            assign = re.compile(r"\b" + re.escape(flag) + r"\b(?:\.[\w$]+|\[[^\]]*\])*\s*=(?!=)")
            if not assign.search(sources):
                missing.append(f"{rel}:{line}  {flag}")
    assert not missing, "Load flags no script ever sets:\n  " + "\n  ".join(missing)


# ── negative controls: the shapes that shipped ──────────────────────────


def test_the_documents_page_that_shipped_is_flagged() -> None:
    original = (
        '<div x-show="documents.length === 0" x-cloak style="color:var(--text-muted);'
        "font-size:.8rem;\">{{ t('documents.no_documents') }}</div>"
    )
    assert unloaded_empty_states(original) == [(1, "documents.length === 0")]
    fixed = original.replace('"documents.length === 0"', '"documentsLoaded && documents.length === 0"')
    assert unloaded_empty_states(fixed) == []


def test_a_loading_flag_alone_is_flagged() -> None:
    """``!loading`` ends with the read whether it succeeded or failed, so a
    failed read still said "none" (Settings' MCP, skills and tools tabs)."""
    for shipped in (
        '<div x-show="mcpServers.length === 0 && !loading" class="empty-state"></div>',
        '<div x-show="!loading && libraries.length === 0"></div>',
        '<div x-show="!audit.loading && !audit.entries.length"></div>',
    ):
        assert unloaded_empty_states(shipped), shipped
    # The loading-and-error shape is honest: the error replaces the list.
    assert unloaded_empty_states('<p x-show="!loading && !error && !rows.length"></p>') == []


def test_every_shape_of_an_empty_test_is_recognised() -> None:
    for cond in (
        "rows.length === 0",
        "rows.length == 0",
        "rows.length < 1",
        "!rows.length",
        "!(c.history || []).length",
        "!arabicVoices().length",
        "$store.notifications.items.length === 0",
        "!hubConnectors.some(x => x.name === 'email')",
        "filteredDiscoveredModels(selectedHubProvider()).length === 0",
    ):
        assert is_empty_state(cond), cond
    for cond in (
        "rows.length > 0",
        "rows.length",
        "!loading && rows.length",
        "tabs.length > 0",
        "validateResult?.errors?.length",
        "rows.length === 10",
    ):
        assert not is_empty_state(cond), cond


def test_a_name_in_a_string_is_not_a_load_flag() -> None:
    assert not names_its_load("rows.length === 0 && mode !== 'loaded'")


def test_a_flag_nothing_sets_is_caught() -> None:
    """The setter check's control: a template naming a flag no script sets."""
    sources = "this.documents = [];\nthis.documentsLoaded = true;\n"
    flag_ok = re.compile(r"\bdocumentsLoaded\b(?:\.[\w$]+|\[[^\]]*\])*\s*=(?!=)")
    flag_never = re.compile(r"\bneverLoaded\b(?:\.[\w$]+|\[[^\]]*\])*\s*=(?!=)")
    assert flag_ok.search(sources)
    assert not flag_never.search(sources)
    # A comparison is not an assignment.
    assert not flag_never.search("if (neverLoaded === true) {}")


# ── the failure path of a list a script paints ──────────────────────────

#: A ``catch``: ``.catch(function (e) {``, ``.catch((e) => {``, ``catch (e) {``.
_CATCH = re.compile(
    r"\.catch\(\s*(?:function\s*\([^)]*\)|\(?[\w$]*\)?\s*=>)\s*\{"
    r"|\bcatch\s*(?:\([^)]*\))?\s*\{"
)
#: What a failure path must not paint: an empty list handed to a renderer,
#: invented rows, or an empty-state catalog text.
_PAINTS_NONE = re.compile(
    r"render\w*\(\s*\[\s*\]"
    r"|=\s*fallback\w*\("
    r"|['\"][a-z_]+\.[a-z_.]*(?:\bno_[a-z_]+|[a-z_]*empty[a-z_]*)['\"]"
)


def _block_at(text: str, brace: int) -> str:
    """The ``{...}`` block opening at ``brace``, skipping string literals and
    comments (an apostrophe in ``// shouldn't`` is not a quote)."""
    depth = 0
    quote = ""
    i = brace
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        elif c in "'\"`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return text[brace:]


def failure_paths_that_paint_none(script: str) -> list[tuple[int, str]]:
    """``(line, what)`` for each catch block in ``script`` that paints an
    empty list, an empty-state text or invented rows."""
    found = []
    for m in _CATCH.finditer(script):
        hit = _PAINTS_NONE.search(_block_at(script, m.end() - 1))
        if hit:
            found.append((script.count("\n", 0, m.start()) + 1, hit.group(0)))
    return found


def _page_scripts() -> list[Path]:
    scripts = [p for p in STATIC_JS.rglob("*.js") if not p.name.endswith(".min.js")]
    return sorted(scripts) + _templates()


def test_no_failed_read_is_painted_as_none_or_invented() -> None:
    bad = []
    for path in _page_scripts():
        for line, what in failure_paths_that_paint_none(path.read_text(encoding="utf-8")):
            bad.append(f"{path.relative_to(REPO).as_posix()}:{line}  {what}")
    assert not bad, (
        "A failed read is painted as an empty list, an empty state or made-up "
        "rows. Say the read failed instead (a 'load failed' text, the page's "
        "error line), and keep what an earlier read gave:\n  " + "\n  ".join(bad)
    )


def test_the_failure_paths_that_shipped_are_flagged() -> None:
    """Negative controls: the Swarm history's and the Workspace's catches as
    they were, and a catch that writes an empty-state text."""
    swarm_history = (
        "    fetch(url)\n      .then(function(r) { return r.json(); })\n"
        "      .then(function(data) { renderHistoryTable(data.tasks || []); })\n"
        "      .catch(function() {\n        renderHistoryTable([]);\n      });\n"
    )
    assert failure_paths_that_paint_none(swarm_history) == [(4, "renderHistoryTable([]")]
    workspace_files = (
        "      try {\n        const resp = await fetch(u);\n      } catch (e) {\n"
        "        this.files = fallbackFiles();\n      }\n"
    )
    assert failure_paths_that_paint_none(workspace_files) == [(3, "= fallbackFiles(")]
    empty_text = "} catch (e) { el.textContent = _mt('memory.console.no_memories', 'None'); }"
    assert failure_paths_that_paint_none(empty_text) == [(1, "'memory.console.no_memories'")]


def test_a_failure_path_that_says_it_failed_passes() -> None:
    for honest in (
        "} catch (e) { el.textContent = _mt('memory.console.memories_load_failed', 'Failed'); }",
        ".catch(function () { warning('research-load-error', true, 'research.ui.results_unavailable'); });",
        "} catch (e) { this.filesFailed = true; }",
        # A brace inside a string does not end the block early.
        "} catch (e) { el.innerHTML = '<b>{</b>' + t('common.request_failed'); }",
        # Nor does an apostrophe in a comment start a string that swallows
        # the code after the block (mcp.js: "// ... shouldn't fail").
        "} catch (e) {\n  // it shouldn't fail\n  this.err = 1;\n}\nvar later = t('mcp.ui.no_detail');",
    ):
        assert failure_paths_that_paint_none(honest) == [], honest


# ── an ``ok: false`` answer (audit 2026-10-26 AUD-058) ───────────────────

#: ``if (j.ok) {`` / ``if (aud?.ok) {``: a load that branches on the body.
_OK_BRANCH = re.compile(r"\bif\s*\(\s*[A-Za-z_$][\w$]*\??\.ok\s*\)\s*\{")
#: The branch sets the load's success flag: the empty state's condition.
_SETS_LOADED = re.compile(r"\b[\w$]*[Ll]oaded\b(?:\.[\w$]+|\[[^\]]*\])*\s*=\s*true\b")


def ok_branches_with_no_failure(script: str) -> list[int]:
    """Lines of each ``if (x.ok) {...}`` that sets a loaded flag and has no
    ``else``: an ``ok: false`` body (the service unavailable, a 500) then
    leaves the list with no loading, empty or error state."""
    found = []
    for m in _OK_BRANCH.finditer(script):
        block = _block_at(script, m.end() - 1)
        after = script[m.end() - 1 + len(block):].lstrip()
        if _SETS_LOADED.search(block) and not after.startswith("else"):
            found.append(script.count("\n", 0, m.start()) + 1)
    return found


def test_an_ok_false_answer_is_said_not_left_blank() -> None:
    bad = []
    for path in _page_scripts():
        for line in ok_branches_with_no_failure(path.read_text(encoding="utf-8")):
            bad.append(f"{path.relative_to(REPO).as_posix()}:{line}")
    assert not bad, (
        "A load sets its loaded flag on `ok` and does nothing on `ok: false`: "
        "the list is left blank with no state. Say the read failed in an "
        "`else`:\n  " + "\n  ".join(bad)
    )


def test_the_documents_load_that_shipped_is_flagged() -> None:
    """Negative control: documents.js's loader as it shipped (AUD-058)."""
    shipped = (
        "        const j = await r.json();\n"
        "        if (j.ok) {\n"
        "          this.documents = j.documents || [];\n"
        "          this.documentsLoaded = true;\n"
        "        }\n"
        "      } catch (e) {\n"
    )
    assert ok_branches_with_no_failure(shipped) == [2]
    fixed = shipped.replace(
        "        }\n      } catch", "        } else {\n          this.documentsError = j.error;\n"
        "        }\n      } catch",
    )
    assert ok_branches_with_no_failure(fixed) == []
