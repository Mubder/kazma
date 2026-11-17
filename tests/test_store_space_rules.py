"""Every store says how it keeps a space apart (Spaces plan, item S1-03).

Kazma is moving from one operator to several people on one install, each in
a space of their own. Each store answered "whose row is this" in its own
way, or not at all, and nothing listed them. Now every database, store
family and data-folder entry of ``store_registry`` has a ``SPACE_RULES``
entry: a space id on every row, files under the space's folder, the
install's own, rebuilt from space data, or tied to the host's files. A
``row`` store also names the columns that hold a person's words.

The gate:

- every key of ``STORES``, ``STORE_FAMILIES`` and ``DATA_DIR_ENTRIES`` has a
  rule, and no rule names something else;
- a rule's kind and today's state are known words; ``install`` and ``host``
  say why; a ``row`` rule names content columns or says why it has none; a
  retired store is the install's;
- the tables and columns a ``row`` rule names exist: they are read from the
  ``CREATE TABLE`` statements of the store's own module (``DDL_SOURCES``),
  with the columns the module adds later. A store whose tables cannot be
  read that way is listed in ``NOT_CHECKED`` with the reason, and an entry
  there that is no longer needed fails.

Each check is a function that returns the problems it found, so the
negative controls run the same checks on planted data.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Mapping, Set
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PRODUCT_PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui")

#: Where each ``row`` store's tables are made: files of this repository, or
#: ``module:<name>`` for a table an installed package makes.
DDL_SOURCES: dict[str, tuple[str, ...]] = {
    "agent_artifacts.db": ("kazma-core/kazma_core/agent/artifacts.py",),
    "task_ledgers.db": ("kazma-core/kazma_core/agent/task_ledger.py",),
    "tool_effects.db": ("kazma-core/kazma_core/agent/effect_journal.py",),
    "memory_state.db": ("kazma-core/kazma_core/memory/schema_v2.py",),
    "memory_ops.db": (
        "kazma-core/kazma_core/memory/schema_v2.py",
        "kazma-core/kazma_core/safety/commitment/store.py",
    ),
    # LangGraph's saver makes the checkpoint tables, not Kazma's code.
    "checkpoints.db": ("module:langgraph.checkpoint.sqlite",),
    "checkpoints_*.db": ("module:langgraph.checkpoint.sqlite",),
    "snapshots.db": ("kazma-core/kazma_core/time_travel.py",),
    "chat_sessions.db": ("kazma-ui/kazma_ui/session_manager.py",),
    "chat_sessions_test.db": ("kazma-ui/kazma_ui/session_manager.py",),
    "chat_sessions_test_*.db": ("kazma-ui/kazma_ui/session_manager.py",),
    "chat_sessions_spool.db": ("kazma-ui/kazma_ui/session_spool.py",),
    "sessions.db": ("kazma-gateway/kazma_gateway/stores/sqlite.py",),
    "cron.db": ("kazma-core/kazma_core/cron/store.py",),
    "swarm_tasks.db": ("kazma-core/kazma_core/swarm/task_store.py",),
    "pipeline_logs.db": ("kazma-core/kazma_core/swarm/memory/pipeline_logger.py",),
    "research_sessions.db": ("kazma-core/kazma_core/tools/research_session.py",),
    "x_posts.db": ("kazma-core/kazma_core/x_api/ledger.py",),
    "x_scheduled.db": ("kazma-core/kazma_core/x_api/schedule.py",),
    "x_replies.db": (
        "kazma-core/kazma_core/x_api/reply_store.py",
        "kazma-core/kazma_core/x_api/notifications.py",
    ),
    "x_datasets.db": ("kazma-core/kazma_core/x_api/datasets.py",),
    "x_audit.db": ("kazma-core/kazma_core/x_api/audit.py",),
    "x_publications.db": (
        "kazma-core/kazma_core/x_api/publication_store.py",
        "kazma-core/kazma_core/x_api/composer.py",
        "kazma-core/kazma_core/x_api/thread_store.py",
        "kazma-core/kazma_core/x_api/notifications.py",
    ),
    "hitl_gates.db": ("kazma-core/kazma_core/safety/hitl_gates.py",),
    "run_incidents.db": ("kazma-core/kazma_core/runs/incidents.py",),
    "vault.db": ("kazma-core/kazma_core/security/vault.py",),
    "settings.db": (
        "kazma-core/kazma_core/config_store.py",
        "kazma-core/kazma_core/stores/bookmarks.py",
    ),
    "knowledge.db": ("kazma-core/kazma_core/stores/knowledge.py",),
    "documents.db": (
        "kazma-core/kazma_core/documents/repository.py",
        "kazma-core/kazma_core/documents/audit.py",
        "kazma-core/kazma_core/documents/jobs.py",
    ),
    "documents-jobs-fallback.db": ("kazma-core/kazma_core/documents/jobs.py",),
    "sandbox_emails.db": ("kazma-skills/kazma_skills/native/email_manager/backends/sandbox.py",),
    "chat_inbox.db": ("kazma-ui/kazma_ui/read_only_mode.py",),
    "chat_outbox.db": ("kazma-gateway/kazma_gateway/chat_outbox.py",),
    # A repair's copy of memory_state.db has its tables.
    "*.pre_entity_key": ("kazma-core/kazma_core/memory/schema_v2.py",),
    "*.pre_relabel": ("kazma-core/kazma_core/memory/schema_v2.py",),
    "*.pre_reopen": ("kazma-core/kazma_core/memory/schema_v2.py",),
    "*.pre_reembed": ("kazma-core/kazma_core/memory/schema_v2.py",),
}

#: ``row`` stores whose tables cannot be read from a module's text, and why.
#: None today. An entry must name a ``row`` store with content columns that
#: has no ``DDL_SOURCES`` entry and whose tables Kazma's code does not make.
NOT_CHECKED: dict[str, str] = {}


# ── reading a module's tables ────────────────────────────────────────────

_SQL_COMMENT = re.compile(r"--[^\n]*")
_CREATE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_]\w*)\s*(?:USING\s+\w+\s*)?\(",
    re.IGNORECASE,
)
_ALTER_ADD = re.compile(
    r"ALTER\s+TABLE\s+([A-Za-z_]\w*)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_]\w*)",
    re.IGNORECASE,
)
#: A column a module adds after its table shipped: ``("name", "TEXT ...")``,
#: the pairs ``add_missing_columns`` takes.
_ADDED_PAIR = re.compile(
    r"""\(\s*["']([A-Za-z_]\w*)["']\s*,\s*["'](?:TEXT|INTEGER|REAL|BLOB|NUMERIC|BOOLEAN|TIMESTAMP|JSONB?|DOUBLE)\b"""
)
_NOT_A_COLUMN = {"primary", "unique", "foreign", "check", "constraint"}
_QUOTES_AND_SPACE = re.compile(r"[\"'`\s]+")
_FIRST_WORD = re.compile(r"[A-Za-z_]\w*")
_OPTION = re.compile(r"[A-Za-z_]\w*\s*=")


def _body(text: str, start: int) -> str:
    """The text between the bracket at *start* and its closing bracket."""
    depth, i = 1, start
    while i < len(text) and depth:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        i += 1
    return text[start : i - 1]


def _columns(body: str) -> set[str]:
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    found: set[str] = set()
    for part in parts:
        part = _QUOTES_AND_SPACE.sub(" ", part).strip()
        if _OPTION.match(part):  # an FTS5 option: tokenize = ..., content = ...
            continue
        word = _FIRST_WORD.match(part)
        if word and word.group(0).lower() not in _NOT_A_COLUMN:
            found.add(word.group(0))
    return found


def schema_in(text: str) -> tuple[dict[str, set[str]], set[str]]:
    """The tables a module's text makes, and the columns it adds later.

    Returns ``(table -> columns, added)``. ``added`` holds the columns of the
    module's ``("name", "TYPE ...")`` pairs: they name no table in the text,
    so they count for every table of the module.
    """
    text = _SQL_COMMENT.sub("", text)
    tables: dict[str, set[str]] = {}
    for match in _CREATE.finditer(text):
        tables.setdefault(match.group(1), set()).update(_columns(_body(text, match.end())))
    for match in _ALTER_ADD.finditer(text):
        tables.setdefault(match.group(1), set()).add(match.group(2))
    return tables, set(_ADDED_PAIR.findall(text))


def read_source(source: str) -> str | None:
    """The text of one ``DDL_SOURCES`` entry, or None when it cannot be read."""
    if source.startswith("module:"):
        try:
            spec = importlib.util.find_spec(source.removeprefix("module:"))
        except (ImportError, ValueError):
            return None
        if spec is None or not spec.origin:
            return None
        path = Path(spec.origin)
    else:
        path = REPO / source
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _product_tables() -> set[str]:
    """Every table a ``CREATE TABLE`` in Kazma's own packages names."""
    names: set[str] = set()
    for package in PRODUCT_PACKAGES:
        for path in (REPO / package).rglob("*.py"):
            if "tests" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "CREATE" in text:
                names.update(schema_in(text)[0])
    return names


# ── the checks ───────────────────────────────────────────────────────────


def coverage_problems(declared: set[str], rules: Mapping[str, object]) -> list[str]:
    """Entries with no rule, and rules that name no entry."""
    problems = [f"{name}: no space rule" for name in sorted(declared - set(rules))]
    problems += [f"{name}: a space rule for something the registry does not declare"
                 for name in sorted(set(rules) - declared)]
    return problems


def shape_problems(rules: Mapping[str, object], kinds: tuple[str, ...], today: tuple[str, ...],
                   retired: Set[str] = frozenset()) -> list[str]:
    """Rules whose words are wrong or missing."""
    problems = []
    for name, rule in sorted(rules.items()):
        if rule.kind not in kinds:
            problems.append(f"{name}: unknown kind {rule.kind!r}")
            continue
        if rule.today not in today:
            problems.append(f"{name}: today is {rule.today!r}, not one of {today}")
        if rule.kind in ("install", "host") and not rule.why.strip():
            problems.append(f"{name}: a rule of kind {rule.kind} must say why")
        if rule.kind != "row" and rule.content:
            problems.append(f"{name}: only a row rule names content columns")
        if rule.kind == "row":
            if not rule.content and not rule.why.strip():
                problems.append(f"{name}: a row rule names its content columns, or says why it has none")
            for table, columns in rule.content.items():
                if not columns:
                    problems.append(f"{name}: table {table} names no column")
        if name in retired and rule.kind != "install":
            problems.append(f"{name}: a retired store is the install's, not {rule.kind}")
    return problems


def _checked(rules: Mapping[str, object]) -> set[str]:
    return {name for name, rule in rules.items() if rule.kind == "row" and rule.content}


def column_problems(rules: Mapping[str, object], sources: Mapping[str, tuple[str, ...]],
                    not_checked: Mapping[str, str]) -> list[str]:
    """Tables and columns a row rule names that its store does not have."""
    problems = []
    for name in sorted(_checked(rules)):
        if name in not_checked:
            continue
        if name not in sources:
            problems.append(f"{name}: no DDL_SOURCES entry (or a NOT_CHECKED reason)")
            continue
        tables: dict[str, set[str]] = {}
        added: set[str] = set()
        for source in sources[name]:
            text = read_source(source)
            if text is None:
                problems.append(f"{name}: cannot read {source}")
                continue
            found, extra = schema_in(text)
            for table, columns in found.items():
                tables.setdefault(table, set()).update(columns)
            added |= extra
        for table, columns in rules[name].content.items():
            if table not in tables:
                problems.append(f"{name}: no table {table} in {', '.join(sources[name])}")
                continue
            for column in columns:
                if column not in tables[table] and column not in added:
                    problems.append(f"{name}: table {table} has no column {column}")
    for name in sorted(set(sources) - _checked(rules)):
        problems.append(f"{name}: a DDL_SOURCES entry for something with no content columns")
    return problems


def not_checked_problems(rules: Mapping[str, object], sources: Mapping[str, tuple[str, ...]],
                         not_checked: Mapping[str, str]) -> list[str]:
    """``NOT_CHECKED`` entries that are no longer needed."""
    problems = []
    product = _product_tables() if not_checked else set()
    for name, reason in sorted(not_checked.items()):
        if not reason.strip():
            problems.append(f"{name}: NOT_CHECKED needs a reason")
        if name not in _checked(rules):
            problems.append(f"{name}: NOT_CHECKED names no row store with content columns")
        elif name in sources:
            problems.append(f"{name}: has a DDL_SOURCES entry, so it is checked: remove it from NOT_CHECKED")
        elif set(rules[name].content) <= product:
            problems.append(f"{name}: Kazma's code makes its tables, so it can be checked: "
                            "give it a DDL_SOURCES entry")
    return problems


def _declared(reg) -> set[str]:
    return set(reg.STORES) | set(reg.STORE_FAMILIES) | set(reg.DATA_DIR_ENTRIES)


def _retired(reg) -> set[str]:
    entries = {**reg.STORES, **reg.STORE_FAMILIES, **reg.DATA_DIR_ENTRIES}
    return {name for name, entry in entries.items() if entry.migration == "legacy"}


# ── the registry as it is ────────────────────────────────────────────────


def test_every_entry_has_a_space_rule_and_no_rule_is_stray():
    from kazma_core import store_registry as reg

    assert coverage_problems(_declared(reg), reg.SPACE_RULES) == []


def test_every_rule_is_well_formed():
    from kazma_core import store_registry as reg

    assert shape_problems(
        reg.SPACE_RULES, reg.SPACE_RULE_KINDS, reg.SPACE_TODAY_STATES, _retired(reg)) == []


def test_the_tables_and_columns_a_row_rule_names_exist():
    from kazma_core import store_registry as reg

    assert column_problems(reg.SPACE_RULES, DDL_SOURCES, NOT_CHECKED) == []


def test_nothing_is_left_unchecked_without_need():
    from kazma_core import store_registry as reg

    assert not_checked_problems(reg.SPACE_RULES, DDL_SOURCES, NOT_CHECKED) == []


def test_the_helpers_answer_by_name():
    from kazma_core import store_registry as reg

    assert reg.space_rule("chat_sessions.db").kind == "row"
    assert reg.content_columns("chat_sessions.db") == {"sessions": ("messages", "title")}
    assert reg.content_columns("backups") == {}
    # A copy: changing it changes no rule.
    reg.content_columns("chat_sessions.db")["sessions"] = ()
    assert reg.content_columns("chat_sessions.db") == {"sessions": ("messages", "title")}
    with pytest.raises(KeyError, match="planted.db"):
        reg.space_rule("planted.db")


def test_the_reader_finds_what_a_module_makes():
    tables, added = schema_in('''
        _CREATE = """
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,   -- the key (a slug, made once)
            body TEXT NOT NULL DEFAULT '',
            content TEXT,
            PRIMARY KEY (id),
            FOREIGN KEY (id) REFERENCES other(id)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            body, content='notes', tokenize = "unicode61"
        );
        """
        conn.execute("CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY, "
                     "text TEXT, state TEXT DEFAULT 'kept')")
        conn.execute("ALTER TABLE notes ADD COLUMN title TEXT")
        add_missing_columns(conn, "notes", (("pinned", "INTEGER DEFAULT 0"),))
    ''')
    assert tables == {
        "notes": {"id", "body", "content", "title"},
        "notes_fts": {"body"},
        "inbox": {"id", "text", "state"},
    }
    assert added == {"pinned"}


# ── negative controls: each check on planted data ────────────────────────


def test_negative_control_an_undeclared_store_is_found():
    from kazma_core import store_registry as reg

    stores = {**reg.STORES, "planted.db": reg.Store("a planted store", "bundle")}
    declared = set(stores) | set(reg.STORE_FAMILIES) | set(reg.DATA_DIR_ENTRIES)
    assert coverage_problems(declared, reg.SPACE_RULES) == ["planted.db: no space rule"]


def test_negative_control_an_undeclared_family_and_folder_are_found():
    from kazma_core import store_registry as reg

    declared = _declared(reg) | {"planted_*.db", "planted-folder"}
    assert coverage_problems(declared, reg.SPACE_RULES) == [
        "planted-folder: no space rule", "planted_*.db: no space rule"]


def test_negative_control_a_rule_for_nothing_is_found():
    from kazma_core import store_registry as reg

    rules = {**reg.SPACE_RULES, "gone.db": reg.SpaceRule("install", why="left behind", today="whole")}
    assert coverage_problems(_declared(reg), rules) == [
        "gone.db: a space rule for something the registry does not declare"]


def _shape(reg, rule, name="planted.db", retired=frozenset()):
    return shape_problems({name: rule}, reg.SPACE_RULE_KINDS, reg.SPACE_TODAY_STATES, retired)


def test_negative_control_a_bad_kind_is_found():
    from kazma_core import store_registry as reg

    assert _shape(reg, reg.SpaceRule("tenant", today="whole")) == ["planted.db: unknown kind 'tenant'"]


def test_negative_control_an_install_or_host_rule_with_no_why_is_found():
    from kazma_core import store_registry as reg

    assert _shape(reg, reg.SpaceRule("install", today="whole")) == [
        "planted.db: a rule of kind install must say why"]
    assert _shape(reg, reg.SpaceRule("host", why="  ", today="whole")) == [
        "planted.db: a rule of kind host must say why"]
    assert _shape(reg, reg.SpaceRule("install", why="the guard's state", today="whole")) == []


def test_negative_control_a_row_rule_with_neither_columns_nor_a_reason_is_found():
    from kazma_core import store_registry as reg

    assert _shape(reg, reg.SpaceRule("row", today="whole")) == [
        "planted.db: a row rule names its content columns, or says why it has none"]
    assert _shape(reg, reg.SpaceRule("row", content={"notes": ()}, today="whole")) == [
        "planted.db: table notes names no column"]
    assert _shape(reg, reg.SpaceRule("row", why="ids only", today="whole")) == []


def test_negative_control_other_malformed_rules_are_found():
    from kazma_core import store_registry as reg

    assert _shape(reg, reg.SpaceRule("folder", today="shared")) == [
        f"planted.db: today is 'shared', not one of {reg.SPACE_TODAY_STATES}"]
    assert _shape(reg, reg.SpaceRule("folder", content={"notes": ("body",)}, today="whole")) == [
        "planted.db: only a row rule names content columns"]
    assert _shape(reg, reg.SpaceRule("folder", today="whole"), retired={"planted.db"}) == [
        "planted.db: a retired store is the install's, not folder"]


def test_negative_control_a_column_that_does_not_exist_is_found():
    from kazma_core import store_registry as reg

    sources = {"planted.db": DDL_SOURCES["agent_artifacts.db"]}
    good = {"planted.db": reg.SpaceRule("row", content={"agent_artifacts": ("value",)}, today="apart")}
    assert column_problems(good, sources, {}) == []
    bad_column = {"planted.db": reg.SpaceRule(
        "row", content={"agent_artifacts": ("value", "body_text")}, today="apart")}
    assert column_problems(bad_column, sources, {}) == [
        "planted.db: table agent_artifacts has no column body_text"]
    bad_table = {"planted.db": reg.SpaceRule("row", content={"drafts": ("value",)}, today="apart")}
    assert column_problems(bad_table, sources, {}) == [
        f"planted.db: no table drafts in {sources['planted.db'][0]}"]


def test_negative_control_a_row_store_with_no_schema_source_is_found():
    from kazma_core import store_registry as reg

    rules = {"planted.db": reg.SpaceRule("row", content={"notes": ("body",)}, today="apart")}
    assert column_problems(rules, {}, {}) == [
        "planted.db: no DDL_SOURCES entry (or a NOT_CHECKED reason)"]
    assert column_problems(rules, {}, {"planted.db": "an outside program makes its tables"}) == []
    assert column_problems(rules, {"planted.db": ("kazma-core/kazma_core/no_such_module.py",)}, {}) == [
        "planted.db: cannot read kazma-core/kazma_core/no_such_module.py",
        "planted.db: no table notes in kazma-core/kazma_core/no_such_module.py",
    ]
    assert column_problems({}, {"planted.db": ("x.py",)}, {}) == [
        "planted.db: a DDL_SOURCES entry for something with no content columns"]


def test_negative_control_an_unneeded_not_checked_entry_is_found():
    from kazma_core import store_registry as reg

    rules = reg.SPACE_RULES
    # It names nothing with content columns.
    assert not_checked_problems(rules, DDL_SOURCES, {"backups": "no tables"}) == [
        "backups: NOT_CHECKED names no row store with content columns"]
    # It is checked already.
    assert not_checked_problems(rules, DDL_SOURCES, {"cron.db": "hard to read"}) == [
        "cron.db: has a DDL_SOURCES entry, so it is checked: remove it from NOT_CHECKED"]
    # Kazma's own code makes its tables, so it can be checked.
    without = {name: paths for name, paths in DDL_SOURCES.items() if name != "cron.db"}
    assert not_checked_problems(rules, without, {"cron.db": "hard to read"}) == [
        "cron.db: Kazma's code makes its tables, so it can be checked: give it a DDL_SOURCES entry"]
    # A store whose tables an outside program makes stays unchecked, with its reason.
    outside = {"checkpoints.db": rules["checkpoints.db"]}
    assert not_checked_problems(outside, {}, {"checkpoints.db": "LangGraph makes the tables"}) == []
    assert not_checked_problems(outside, {}, {"checkpoints.db": " "}) == [
        "checkpoints.db: NOT_CHECKED needs a reason"]
