"""The screenshots on kazma.ai, captured from a real Kazma (not a test).

Run by `scripts/site_screenshots.py`, which turns them into the website's
WebP files; skipped unless ``KAZMA_SITE_SHOTS_DIR`` names where to write.

Boots the real app on the unified-turn harness (isolated temp data dir, a
scripted model at the provider boundary) with a small demo repository
(tasks-api) as the active workspace, demo memories written through the
product's own writers and test connector values (never sent anywhere: no
adapter starts under pytest), then drives real turns through the browser and
saves 1440x900 captures at 2x: chat-answer, chat-approval, ide, connectors
and memory. Never from a live install.

One language and one theme per process (``SHOT_LANG`` en|ar, ``SHOT_THEME``
dark|light): a harness per process, so no singleton carries one run's data
directory into the next. Every step must land -- a capture that missed its
click would put the wrong page on the website.
"""

from __future__ import annotations

import json
import os
import sqlite3
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

if not os.environ.get("KAZMA_SITE_SHOTS_DIR"):
    pytest.skip(
        "website screenshots: run scripts/site_screenshots.py", allow_module_level=True
    )

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

import tests.e2e._unified_turn_harness as harness  # noqa: E402
from tests.e2e._unified_turn_harness import Step, unified_turn_server  # noqa: E402

SHOT_DIR = Path(os.environ["KAZMA_SITE_SHOTS_DIR"])
LANG = os.environ.get("SHOT_LANG", "en")
THEME = os.environ.get("SHOT_THEME", "dark")
SUFFIX = "" if THEME == "dark" else "-" + THEME
AR = LANG == "ar"
MODEL = "deepseek-chat"
DAY = 86400.0
NOW = time.time()

README = textwrap.dedent(
    """\
    # tasks-api

    A small task tracker with a JSON API, due-date reminders and search.

    ## Usage

    `GET /tasks` lists tasks, `POST /tasks` adds one, `GET /tasks?q=` searches.
    """
)

CHANGELOG = textwrap.dedent(
    """\
    # Changelog

    ## v0.4.0 (unreleased)
    - Due-date reminders: a reminder an hour before a task is due.
    - Search index: listing 10,000 tasks went from 1.8 s to 0.2 s.
    - Arabic task titles sort and search correctly.

    ## Open
    - Rate limiting on /tasks (in review).
    - CSV export (not started; asked for by the support team).
    """
)

API_PY = textwrap.dedent(
    '''\
    """The tasks API: list, add and search tasks."""

    from __future__ import annotations

    from datetime import datetime, timedelta

    from fastapi import FastAPI, HTTPException, Query
    from pydantic import BaseModel

    from .reminders import schedule_reminder
    from .store import TaskStore

    app = FastAPI(title="tasks-api")
    store = TaskStore()


    class TaskIn(BaseModel):
        title: str
        due: datetime | None = None


    @app.get("/tasks")
    def list_tasks(q: str | None = Query(default=None)) -> list[dict]:
        """Every task, or the ones whose title matches ``q``."""
        return store.search(q) if q else store.all()


    @app.post("/tasks", status_code=201)
    def add_task(task: TaskIn) -> dict:
        """Add a task; a due date books a reminder an hour before it."""
        created = store.add(task.title, task.due)
        if task.due is not None:
            schedule_reminder(created["id"], task.due - timedelta(hours=1))
        return created


    @app.delete("/tasks/{task_id}")
    def delete_task(task_id: int) -> dict:
        if not store.remove(task_id):
            raise HTTPException(status_code=404, detail="No such task")
        return {"deleted": task_id}
    '''
)

# ── What each language says ──────────────────────────────────────────────

if not AR:
    Q_ANSWER = "What's still open for the tasks-api release, and when does it go out?"
    Q_APPROVAL = "Email the team the release notes for v0.4."
    Q_IDE = "What does add_task do?"
    QUICK = [
        ("Draft a short thank-you to Sara for the search review.",
         "Here's a draft:\n\n> Thanks, Sara. Your review caught the missing owner column in the new index. Merging it today.\n\nWant me to send it?"),
        ("Make a release checklist for v0.4.",
         "**v0.4 release checklist**\n\n- [ ] Merge rate limiting once review passes\n- [ ] Run the full test suite\n- [ ] Tag `v0.4.0` and push\n- [ ] Post the release notes\n\nCSV export can move to v0.5 if it isn't started by Thursday."),
    ]
    ANSWER = textwrap.dedent(
        """\
        **v0.4 goes out on Friday**: you asked me to keep releases on Fridays.

        **Shipped this week**
        - **Due-date reminders**: a reminder an hour before a task is due.
        - **Faster search**: listing 10,000 tasks went from 1.8 s to 0.2 s.
        - **Arabic titles**: they sort and search correctly.

        **Still open**

        | Item | Status |
        |---|---|
        | Rate limiting on `/tasks` | In review |
        | CSV export | Not started; the support team asked for it |

        Want me to draft the release notes in short bullets, the way you like them?"""
    )
    NARR_READ = "Checking the changelog for what shipped and what's left."
    NARR_NOTES = "I'll take the notes from the changelog."
    NARR_SEND = "Here's the email. Sending it needs your approval."
    EMAIL = {
        "to": "team@example.com",
        "subject": "tasks-api v0.4: release notes",
        "body": "Hi all, v0.4 goes out on Friday. New: due-date reminders, task lists 9x faster, Arabic titles that sort and search correctly. Still open: rate limiting (in review) and CSV export.",
    }
    SENT = "Sent to team@example.com."
    IDE_ANSWER = (
        "`add_task` saves the task and returns it with status **201**. When the task has a due date, "
        "it also books a reminder one hour before it through `schedule_reminder`.\n\n"
        "One thing to watch: a due date less than an hour away books a reminder in the past. "
        "Want me to skip the reminder in that case?"
    )
else:
    Q_ANSWER = "ما الذي ما زال مفتوحاً في إصدار tasks-api، ومتى يصدر؟"
    Q_APPROVAL = "أرسل ملاحظات إصدار v0.4 إلى الفريق بالبريد."
    Q_IDE = "ماذا تفعل الدالة add_task؟"
    QUICK = [
        ("اكتب رسالة شكر قصيرة لسارة على مراجعة البحث.",
         "هذه مسودة:\n\n> شكراً يا سارة، مراجعتك كشفت العمود الناقص في الفهرس الجديد. سأدمجه اليوم.\n\nهل أرسلها؟"),
        ("جهّز قائمة مراجعة لإصدار v0.4.",
         "**قائمة مراجعة إصدار v0.4**\n\n- [ ] دمج تحديد المعدّل بعد اجتياز المراجعة\n- [ ] تشغيل الاختبارات كاملة\n- [ ] وسم `v0.4.0` ورفعه\n- [ ] نشر ملاحظات الإصدار\n\nيمكن نقل التصدير إلى CSV إلى v0.5 إن لم يبدأ العمل عليه قبل الخميس."),
    ]
    ANSWER = textwrap.dedent(
        """\
        **يصدر v0.4 يوم الجمعة**، فقد طلبت مني أن تكون الإصدارات أيام الجمعة.

        **ما أُنجز هذا الأسبوع**
        - **تذكيرات المواعيد**: تذكير قبل موعد المهمة بساعة.
        - **بحث أسرع**: عرض 10,000 مهمة انخفض من 1.8 ثانية إلى 0.2 ثانية.
        - **العناوين العربية**: تُرتَّب ويُبحث فيها بشكل صحيح.

        **ما زال مفتوحاً**

        | البند | الحالة |
        |---|---|
        | تحديد المعدّل على `/tasks` | قيد المراجعة |
        | التصدير إلى CSV | لم يبدأ، وقد طلبه فريق الدعم |

        هل أصوغ ملاحظات الإصدار في نقاط قصيرة كما تفضّل؟"""
    )
    NARR_READ = "أراجع سجل التغييرات لأعرف ما أُنجز وما بقي."
    NARR_NOTES = "سآخذ الملاحظات من سجل التغييرات."
    NARR_SEND = "هذه الرسالة. إرسالها يحتاج إلى موافقتك."
    EMAIL = {
        "to": "team@example.com",
        "subject": "ملاحظات إصدار tasks-api v0.4",
        "body": "مرحباً بالجميع، يصدر v0.4 يوم الجمعة. الجديد: تذكيرات المواعيد، وعرض المهام أسرع بتسع مرات، والعناوين العربية تُرتَّب ويُبحث فيها بشكل صحيح. ما زال مفتوحاً: تحديد المعدّل (قيد المراجعة) والتصدير إلى CSV.",
    }
    SENT = "أُرسلت إلى team@example.com."
    IDE_ANSWER = (
        "`add_task` تحفظ المهمة وتعيدها بالحالة **201**. وإن كان للمهمة موعد، "
        "تحجز أيضاً تذكيراً قبله بساعة عبر `schedule_reminder`.\n\n"
        "انتبه لأمر واحد: الموعد الذي يبعد أقل من ساعة يحجز تذكيراً في الماضي. "
        "هل أتخطّى التذكير في هذه الحالة؟"
    )


def _text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


def _role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or message.get("type") or "").lower()
    return str(getattr(message, "role", "") or getattr(message, "type", "")).lower()


class SiteScript:
    """Answers from the latest user message; steps advance with the tool
    results that follow it. Anything that is not one of the demo questions
    (memory extraction, summaries) gets an empty JSON list."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.scenarios: dict[str, tuple[list[Step], str]] = {
            Q_ANSWER: ([Step("file_read", {"path": "CHANGELOG.md"}, NARR_READ)], ANSWER),
            Q_APPROVAL: (
                [
                    Step("file_read", {"path": "CHANGELOG.md"}, NARR_NOTES),
                    Step("email_send", dict(EMAIL), NARR_SEND),
                ],
                SENT,
            ),
        }
        for q, a in QUICK:
            self.scenarios[q] = ([], a)

    def respond(self, messages: list[Any]) -> Any:
        from kazma_core.llm_provider import LLMResponse, ToolCall

        msgs = list(messages or [])
        last_user = max((i for i, m in enumerate(msgs) if _role(m) in ("user", "human")), default=-1)
        text = _text(msgs[last_user]) if last_user >= 0 else ""
        prompt_tokens = 1800 + sum(len(_text(m)) for m in msgs) // 4

        def usage(completion: int) -> dict[str, Any]:
            return {
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion,
                          "total_tokens": prompt_tokens + completion},
                "cost_usd": round(prompt_tokens * 0.27e-6 + completion * 1.10e-6, 6),
            }

        if "add_task" in text:
            self.calls.append("ide:final")
            return LLMResponse(content=IDE_ANSWER, finish_reason="stop", model=MODEL, **usage(120))
        key = next((q for q in self.scenarios if q in text), None)
        if key is None:
            self.calls.append("other:" + text[:40].replace("\n", " "))
            return LLMResponse(content="[]", finish_reason="stop", model=MODEL, **usage(2))
        steps, final = self.scenarios[key]
        done = sum(1 for m in msgs[last_user + 1:] if _role(m) == "tool")
        if done >= len(steps):
            self.calls.append(f"{key[:20]}:final")
            return LLMResponse(content=final, finish_reason="stop", model=MODEL, **usage(len(final) // 3))
        step = steps[done]
        self.calls.append(f"{key[:20]}:{step.tool}")
        return LLMResponse(
            content=step.narration,
            tool_calls=[ToolCall(id=f"call_{abs(hash(key)) % 10**8}_{done + 1}", name=step.tool, arguments=dict(step.args))],
            finish_reason="tool_calls",
            model=MODEL,
            **usage(60),
        )


# ── Seeding ──────────────────────────────────────────────────────────────


def _make_repo(root: Path) -> Path:
    repo = root / "tasks-api"
    (repo / "src" / "tasks_api").mkdir(parents=True, exist_ok=True)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "README.md").write_text(README, encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "tasks-api"\nversion = "0.4.0"\nrequires-python = ">=3.11"\n', encoding="utf-8")
    (repo / "src" / "tasks_api" / "__init__.py").write_text('"""tasks-api."""\n', encoding="utf-8")
    (repo / "src" / "tasks_api" / "api.py").write_text(API_PY, encoding="utf-8")
    (repo / "src" / "tasks_api" / "store.py").write_text('"""Task storage."""\n', encoding="utf-8")
    (repo / "src" / "tasks_api" / "reminders.py").write_text('"""Due-date reminders."""\n', encoding="utf-8")
    (repo / "tests" / "test_api.py").write_text("def test_list_is_empty():\n    assert True\n", encoding="utf-8")
    return repo


def _seed_connectors() -> None:
    # Placeholders, never token-shaped: the page shows a saved token as dots,
    # and realistic fakes trip secret scanning.
    from kazma_core.config_store import get_config_store

    get_config_store().batch_set([
        ("connectors.telegram.token", "demo-telegram-bot-token", "connectors"),
        ("connectors.telegram.enabled", True, "connectors"),
        ("connectors.telegram.allowed_users", "412093856", "connectors"),
        ("connectors.telegram.swarm_chat_id", "412093856", "connectors"),
        ("connectors.discord.token", "demo-discord-bot-token", "connectors"),
        ("connectors.discord.enabled", True, "connectors"),
        ("connectors.discord.guild_id", "1187654321098765432", "connectors"),
        ("connectors.discord.allowed_users", "923456789012345678", "connectors"),
        ("connectors.discord.swarm_channel_id", "1187654321098769001", "connectors"),
        ("connectors.slack.token", "demo-slack-bot-token", "connectors"),
        ("connectors.slack.enabled", True, "connectors"),
        ("connectors.slack.app_token", "demo-slack-app-token", "connectors"),
        ("connectors.slack.workspace", "tasks-team", "connectors"),
        ("connectors.slack.allowed_users", "U07ABCD1234", "connectors"),
        ("connectors.slack.swarm_channel_id", "C07TASKS001", "connectors"),
    ])


if not AR:
    EPISODES = [
        ("3f2a9c1e-7b4d-4e21", 14, 1, "Remember that tasks-api releases go out on Fridays, never on a Monday.",
         "Noted: tasks-api releases go out on Fridays. I'll plan release work around that."),
        ("3f2a9c1e-7b4d-4e21", 14, 2, "Who usually reviews the search changes?",
         "Sara reviewed the last three search pull requests, so she's the natural reviewer for the new index."),
        ("8c41d0b2-19ae-4f07", 11, 1, "Why did listing tasks get slow?",
         "The /tasks list scanned every row on each request. An index on (owner, created_at) brings 10,000 tasks from 1.8 s to about 0.2 s."),
        ("b7e3a5f9-2c68-4d93", 7, 1, "Set a reminder an hour before each task is due.",
         "Done: a task with a due date now books a reminder one hour before it."),
        ("b7e3a5f9-2c68-4d93", 7, 2, "Can Arabic task titles sort properly?",
         "Yes. Sorting now uses a locale-aware collation, so Arabic titles sort alphabetically and search ignores diacritics."),
        ("e05f6d2a-8b17-4c5e", 4, 1, "What's left before v0.4?",
         "Two items: rate limiting on /tasks (in review) and CSV export (not started). The support team asked for the export."),
        ("51c8b9e7-0d34-4a6f", 2, 1, "I prefer short bullet-point summaries, not long paragraphs.",
         "Got it. I'll keep summaries short and in bullets."),
    ]
    FACTS = [
        ("tasks-api", "release_day", "Friday", "functional", "user_explicit", 5, 0),
        ("tasks-api", "next_release", "v0.4.0", "functional", "llm_inferred", 4, 5),
        ("user", "prefers_summary_style", "short bullet points", "functional", "user_explicit", 4, 6),
        ("user", "works_on", "tasks-api", "set", "llm_inferred", 4, 0),
        ("sara", "reviews", "search pull requests", "set", "llm_inferred", 3, 1),
        ("tasks-api", "search_index", "owner, created_at", "functional", "llm_inferred", 3, 2),
        ("tasks-api", "open_item", "rate limiting on /tasks", "set", "llm_inferred", 3, 5),
        ("tasks-api", "open_item", "CSV export", "set", "llm_inferred", 3, 5),
        ("csv_export", "requested_by", "support team", "functional", "llm_inferred", 2, 5),
        ("user", "timezone", "Asia/Kuwait", "functional", "user_explicit", 3, 6),
    ]
    SUMMARIES = [
        (14, "tasks-api: release rhythm and search speed",
         "Releases go out on Fridays. Listing tasks had become slow (1.8 s for 10,000 tasks); an index on owner and created_at brought it to 0.2 s. Sara reviews the search changes.",
         [0, 1, 2]),
        (7, "tasks-api v0.4: reminders, Arabic titles, what's left",
         "Due-date reminders now fire an hour before a task is due, and Arabic titles sort and search correctly. Left for v0.4: rate limiting on /tasks (in review) and CSV export, which the support team asked for.",
         [3, 4, 5]),
    ]
else:
    EPISODES = [
        ("3f2a9c1e-7b4d-4e21", 14, 1, "تذكّر أن إصدارات tasks-api تكون يوم الجمعة، وليس يوم الاثنين أبداً.",
         "تم: إصدارات tasks-api تكون يوم الجمعة، وسأخطط لأعمال الإصدار على هذا الأساس."),
        ("3f2a9c1e-7b4d-4e21", 14, 2, "من يراجع عادةً تغييرات البحث؟",
         "راجعت سارة آخر ثلاثة طلبات دمج للبحث، فهي المراجِعة الأنسب للفهرس الجديد."),
        ("8c41d0b2-19ae-4f07", 11, 1, "لماذا أصبح عرض المهام بطيئاً؟",
         "كانت قائمة /tasks تمر على كل الصفوف في كل طلب. فهرس على (owner, created_at) يخفض عرض 10,000 مهمة من 1.8 ثانية إلى نحو 0.2 ثانية."),
        ("b7e3a5f9-2c68-4d93", 7, 1, "اضبط تذكيراً قبل موعد كل مهمة بساعة.",
         "تم: المهمة التي لها موعد تحجز الآن تذكيراً قبله بساعة."),
        ("b7e3a5f9-2c68-4d93", 7, 2, "هل يمكن ترتيب عناوين المهام العربية بشكل صحيح؟",
         "نعم. الترتيب يستخدم الآن مقارنة تراعي اللغة، فتُرتَّب العناوين العربية أبجدياً ويتجاهل البحث التشكيل."),
        ("e05f6d2a-8b17-4c5e", 4, 1, "ما الذي بقي قبل إصدار v0.4؟",
         "بندان: تحديد المعدّل على /tasks (قيد المراجعة) والتصدير إلى CSV (لم يبدأ). فريق الدعم طلب التصدير."),
        ("51c8b9e7-0d34-4a6f", 2, 1, "أفضّل الملخصات القصيرة في نقاط، لا الفقرات الطويلة.",
         "فهمت. سأجعل الملخصات قصيرة وفي نقاط."),
    ]
    FACTS = [
        ("tasks-api", "release_day", "الجمعة", "functional", "user_explicit", 5, 0),
        ("tasks-api", "next_release", "v0.4.0", "functional", "llm_inferred", 4, 5),
        ("user", "prefers_summary_style", "نقاط قصيرة", "functional", "user_explicit", 4, 6),
        ("user", "works_on", "tasks-api", "set", "llm_inferred", 4, 0),
        ("سارة", "reviews", "طلبات دمج البحث", "set", "llm_inferred", 3, 1),
        ("tasks-api", "search_index", "owner, created_at", "functional", "llm_inferred", 3, 2),
        ("tasks-api", "open_item", "تحديد المعدّل على /tasks", "set", "llm_inferred", 3, 5),
        ("tasks-api", "open_item", "التصدير إلى CSV", "set", "llm_inferred", 3, 5),
        ("التصدير إلى CSV", "requested_by", "فريق الدعم", "functional", "llm_inferred", 2, 5),
        ("user", "timezone", "Asia/Kuwait", "functional", "user_explicit", 3, 6),
    ]
    SUMMARIES = [
        (14, "tasks-api: إيقاع الإصدارات وسرعة البحث",
         "الإصدارات تكون يوم الجمعة. أصبح عرض المهام بطيئاً (1.8 ثانية لعشرة آلاف مهمة)، وفهرس على owner وcreated_at خفّضه إلى 0.2 ثانية. سارة تراجع تغييرات البحث.",
         [0, 1, 2]),
        (7, "tasks-api v0.4: التذكيرات، والعناوين العربية، وما بقي",
         "التذكيرات تعمل قبل موعد المهمة بساعة، والعناوين العربية تُرتَّب ويُبحث فيها بشكل صحيح. بقي لإصدار v0.4: تحديد المعدّل على /tasks (قيد المراجعة) والتصدير إلى CSV الذي طلبه فريق الدعم.",
         [3, 4, 5]),
    ]


def _seed_memory() -> None:
    """Turns, facts and weekly summaries, written the way the product writes
    them (the retrieval benchmark's path), into the harness's own database."""
    from kazma_core import paths
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob, get_embedding_model_name
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.memory.topic_summaries import _week_of

    conn = sqlite3.connect(paths.primary_memory_db())
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        ids: list[str] = []
        for session, days_ago, turn, user, assistant in EPISODES:
            at = NOW - days_ago * DAY + turn * 90
            row = episode_row(
                session_id=session, turn_number=turn, user_text=user, assistant_text=assistant,
                tenant_id="default", source="chat", created_at=at,
            )
            blob = encode_text_to_blob(row["embed_text"]) if row["embed_text"] else None
            conn.execute(
                "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, "
                "user_text, assistant_text, summary_text, tier, structural_importance, "
                "created_at, metadata_json, embedding_model_version, embedding) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["tenant_id"], row["session_id"], row["turn_number"],
                 row["user_text"], row["assistant_text"], row["summary_text"],
                 "episodic", row["importance"], row["created_at"],
                 json.dumps(row["meta"], ensure_ascii=False), row["embedding_model_version"], blob),
            )
            ids.append(row["id"])
        conn.commit()
        for subject, predicate, obj, ptype, method, importance, ep in FACTS:
            session, days_ago, turn = EPISODES[ep][0], EPISODES[ep][1], EPISODES[ep][2]
            result = mutate_belief(
                conn, subject, predicate, obj, predicate_type=ptype, confidence=0.9,
                importance=importance, extraction_method=method, tenant_id="default",
                source_session=session, source_turn=turn, now=NOW - days_ago * DAY + turn * 90 + 30,
            )
            assert result.get("belief_id"), result
        model = get_embedding_model_name()
        for n, (days_ago, title, text, sources) in enumerate(SUMMARIES):
            key, start, end = _week_of(NOW - days_ago * DAY)
            blob = encode_text_to_blob(f"{title}\n{text}")
            row_id = f"sum-{key}-{n}"
            conn.execute(
                "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, "
                "title, summary_text, status, turn_count, chat_count, created_at, updated_at, "
                "embedding, embedding_model_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                (row_id, "default", key, start, end, title, text, len(sources),
                 len({EPISODES[i][0] for i in sources}), end + DAY, end + DAY, blob, model if blob else None),
            )
            conn.executemany(
                "INSERT INTO memory_summary_sources (summary_id, episode_id) VALUES (?, ?)",
                [(row_id, ids[i]) for i in sources],
            )
        conn.commit()
    finally:
        conn.close()


# ── Driving the page ─────────────────────────────────────────────────────


def _shot(pg: Any, name: str) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    pg.wait_for_timeout(800)
    pg.screenshot(path=str(SHOT_DIR / f"{name}{SUFFIX}.png"))
    print("SHOT", name, flush=True)


def _new_chat(pg: Any, base: str) -> None:
    pg.goto(f"{base}/chat?new=1", wait_until="domcontentloaded", timeout=30000)
    pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
    pg.wait_for_timeout(900)


def _send(pg: Any, text: str) -> None:
    pg.fill("#chat-input", text)
    pg.evaluate("() => window.KazmaChat.sendMessage()")


def _wait_completed(pg: Any, n: int) -> None:
    pg.wait_for_function(
        "(n) => document.querySelectorAll('.turn-header.is-completed').length >= n",
        arg=n, timeout=120000,
    )
    pg.wait_for_timeout(1200)


def _panel_to_top(pg: Any, element_id: str) -> None:
    pg.evaluate(
        """(id) => {
            const e = document.getElementById(id);
            if (!e) return;
            const box = e.closest('div[style*="border-radius"]') || e;
            box.scrollIntoView({ block: 'start' });
        }""",
        element_id,
    )


def test_site_shots(monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.sync_api import sync_playwright

    # The memory seed embeds with the local model: two threads, so a live
    # install on the same machine keeps its CPU.
    try:
        import torch
    except ImportError:
        torch = None
    if torch is not None:
        torch.set_num_threads(2)
    monkeypatch.setattr(harness, "HARNESS_MODEL", MODEL)
    work = SHOT_DIR / f"demo-{LANG}-{THEME}"
    repo = _make_repo(work)
    script = SiteScript()

    with unified_turn_server(script) as h:  # type: ignore[arg-type]
        from kazma_core.config_store import get_config_store
        from kazma_core.stores import get_workspace_store

        harness.seed_provider_config()
        cs = get_config_store()
        cs.set("appearance.theme", THEME, category="appearance")
        ws = get_workspace_store()
        created = ws.create_workspace("tasks-api", str(repo))
        ws.set_active_workspace(created["id"])
        _seed_connectors()
        _seed_memory()
        print("SEEDED", flush=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme=THEME)
            ctx.add_init_script(f"try {{ localStorage.setItem('kazma-lang', '{LANG}'); }} catch (e) {{}}")
            ctx.add_cookies([{"name": "kazma-lang", "value": LANG, "url": h.base}])
            pg = ctx.new_page()
            try:
                # Two earlier chats, so the list looks like a used install.
                for q, _a in QUICK:
                    _new_chat(pg, h.base)
                    _send(pg, q)
                    _wait_completed(pg, 1)

                # 1. An answer that used memory and a tool, steps open.
                _new_chat(pg, h.base)
                _send(pg, Q_ANSWER)
                _wait_completed(pg, 1)
                pg.locator(".agent-progress-header").last.click(timeout=4000)
                pg.wait_for_timeout(900)
                _shot(pg, f"chat-answer-{LANG}")

                # 2. The hero: a turn waiting for approval to send an email.
                _new_chat(pg, h.base)
                _send(pg, Q_APPROVAL)
                pg.wait_for_function(
                    "() => !!document.querySelector('.turn-approvals-rows .hitl-approval-card button:not([disabled])')",
                    timeout=120000,
                )
                pg.wait_for_timeout(1500)
                _shot(pg, f"chat-approval-{LANG}")

                # 3. The IDE: a file open and a question about it.
                pg.goto(f"{h.base}/ide", wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(2500)
                for name in ("src", "tasks_api", "api.py"):
                    pg.get_by_text(name, exact=True).first.click(timeout=4000)
                    pg.wait_for_timeout(700)
                pg.fill("#ide-chat-input", Q_IDE)
                pg.keyboard.press("Enter")
                pg.wait_for_function(
                    "() => { const t = document.body.innerText; return t.indexOf('schedule_reminder') >= 0 && t.indexOf('201') >= 0; }",
                    timeout=60000,
                )
                pg.wait_for_timeout(1500)
                _shot(pg, f"ide-{LANG}")

                # 4. Chat apps: the Platform Connectors sub-tab.
                pg.goto(f"{h.base}/settings", wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(2000)
                pg.get_by_role("button", name="موصلات المنصات" if AR else "Platform Connectors", exact=True).first.click(timeout=4000)
                pg.wait_for_timeout(1800)
                _shot(pg, f"connectors-{LANG}")

                # 5. Memory: a question to the memory probe, with the
                # conversations list (and its Forget buttons) below it.
                pg.goto(f"{h.base}/memory", wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(4500)
                pg.fill("#v2-probe-input", "When do releases go out?" if not AR else "متى تكون الإصدارات؟")
                pg.click("#v2-probe-btn")
                pg.wait_for_function(
                    "() => document.querySelectorAll('#v2-probe-results > *').length > 0", timeout=60000
                )
                pg.wait_for_timeout(1000)
                _panel_to_top(pg, "v2-probe-input")
                pg.wait_for_timeout(800)
                _shot(pg, f"memory-{LANG}")
            finally:
                ctx.close()
                browser.close()
    print("CALLS", script.calls, flush=True)
