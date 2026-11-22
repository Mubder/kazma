"""Memory in real use, in the browser: the suite every memory change passes
(``docs/plans/MEMORY_TOP_SCORES_PLAN.md``, section 2.1, rule 3).

A private Kazma (``kazma serve`` from this checkout, a temporary data folder
and home, SQLite, this machine's environment without any Kazma or credential
variable) runs its shipped path: the model the guide sets up (DeepSeek,
``deepseek-flash`` unless ``--model``; another provider with ``--provider``,
at its own address with ``--base-url``, saved as Settings saves it), the
reasoning level the owner runs
(``agent.chat_reasoning``, ``low`` unless ``--reasoning``), the embedding model from this machine's cache, the deep pass on the memory
queue. A browser, in English and then in Arabic (each its own private
install), reached by the sidebar:

1. tells two facts in a chat, each in its own message;
2. asks for one in a new chat;
3. asks for it in other words, in a new chat;
4. changes it in a new chat, then asks again in another;
5. forgets the other on the Memory page (Forget), then asks for it;
6. asks about something never said.

Each answer is judged by the same model (``--judge`` another) with LongMemEval's judge prompts
(``scripts/longmemeval_bench.py``: an answer that should say "I don't know"
is judged as an abstention). The result (``--out``) holds each step's kind,
pass or fail, and the tokens spent; never a reply's text.

Run (the provider's key from the variable ``--key-env`` names, never
written)::

    python scripts/memory_real_use.py --out docs/audits/evidence/memory_real_use/<date>.json

``--checkout DIR`` runs the private Kazma from another checkout of this
repository (the code before a change, driven by today's suite): a change is
judged with and without itself on one model.

It needs Playwright's Chromium and the embedding model in this machine's
cache (``HF_HUB_OFFLINE=1``). Nothing of an install on this machine is read:
the server starts in an empty folder with a scrubbed environment.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import longmemeval_bench as lme  # noqa: E402
from upgrade_drill import Server, scrubbed_env  # noqa: E402

PACKAGES = ("kazma-core", "kazma-gateway", "kazma-ui", "kazma-cli", "kazma-skills", "kazma-tui")
TURN_TIMEOUT_S = 240
MAX_APPROVALS = 4
MAX_FORGETS = 10
MEMORY_TIMEOUT_S = 300
#: The kinds of step rule 3 names; every language holds each.
KINDS = ("told", "asked", "other_words", "changed", "forgotten", "never_said")


@dataclass
class Step:
    kind: str
    #: "tell" (a message that gives a fact), "ask" (a question judged against
    #: *gold*), "forget" (forget the memory holding *forget*).
    action: str
    text: str = ""
    gold: str = ""
    #: The words of the memory a "forget" step forgets, and that a "tell"
    #: step waits for in memory.
    words: str = ""
    #: LongMemEval's question type the judge prompt is chosen by; an
    #: abstention step's answer should say it does not know.
    judged_as: str = "single-session-user"
    abstention: bool = False
    new_chat: bool = True


@dataclass
class Language:
    lang: str
    steps: list[Step] = field(default_factory=list)


SUITE = (
    Language("en", [
        Step("told", "tell", "I live in Lisbon these days.", words="Lisbon"),
        Step("told", "tell", "My sister Noura works as a dentist.", words="dentist", new_chat=False),
        Step("asked", "ask", "Where do I live?", gold="Lisbon"),
        Step("other_words", "ask", "Which city is home for me at the moment?", gold="Lisbon"),
        Step("changed", "tell", "I moved to Porto last month.", words="Porto"),
        Step("changed", "ask", "Where do I live now?", gold="Porto", judged_as="knowledge-update"),
        Step("forgotten", "forget", words="dentist"),
        Step("forgotten", "ask", "What does my sister do for work?", gold="The user never said",
             abstention=True),
        Step("never_said", "ask", "What is my dog's name?", gold="The user never said", abstention=True),
    ]),
    Language("ar", [
        Step("told", "tell", "أسكن في لشبونة هالأيام.", words="لشبونة"),
        Step("told", "tell", "أختي نورة تشتغل طبيبة أسنان.", words="أسنان", new_chat=False),
        Step("asked", "ask", "وين أسكن؟", gold="لشبونة (Lisbon)"),
        Step("other_words", "ask", "أي مدينة صارت بيتي هالفترة؟", gold="لشبونة (Lisbon)"),
        Step("changed", "tell", "انتقلت إلى بورتو الشهر الماضي.", words="بورتو"),
        Step("changed", "ask", "وين أسكن الحين؟", gold="بورتو (Porto)", judged_as="knowledge-update"),
        Step("forgotten", "forget", words="أسنان"),
        Step("forgotten", "ask", "شنو تشتغل أختي؟", gold="The user never said", abstention=True),
        Step("never_said", "ask", "شنو اسم كلبي؟", gold="The user never said", abstention=True),
    ]),
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_env(home: Path, data: Path, port: int, checkout: Path = REPO) -> dict[str, str]:
    """The private install's environment: this machine's minus every Kazma and
    credential variable (``upgrade_drill.scrubbed_env``), *checkout*'s
    packages first on the path (this one unless named), the embedding model
    from the cache."""
    env = scrubbed_env(home, data, port)
    env["PYTHONPATH"] = os.pathsep.join(str(checkout / p) for p in PACKAGES)
    env["KAZMA_WORKSPACE"] = str(home / "workspace")
    return env


#: What a kept store holds: the memory databases only. The data folder's
#: vault holds the provider's key, saved as Settings saves it; it is never
#: kept.
MEMORY_DATABASES = ("memory_state.db", "memory_ops.db")


def keep_memory_databases(data: Path, into: Path) -> list[str]:
    """Copy the private install's memory databases (a consistent copy of
    each, the write-ahead log folded in) to *into*, to read why a step
    failed with no model call; never the vault or the settings."""
    # Kazma's one copier of a live SQLite database (AGENTS §21: a batched
    # copy restarts under a writer; tests/test_sqlite_snapshot.py).
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    into.mkdir(parents=True, exist_ok=True)
    kept = []
    for name in MEMORY_DATABASES:
        source = data / name
        if not source.is_file():
            continue
        snapshot_copy(source, into / name, read_only=True)
        kept.append(name)
    return kept


def turn_failed(answer: str) -> bool:
    """Whether *answer* is Kazma's own error message (``friendly_llm_error``
    starts every one with the warning sign), not the model's reply."""
    return answer.lstrip().startswith("⚠")


def judge_item(step: Step, n: int) -> dict[str, Any]:
    """The item LongMemEval's judge prompt reads for *step*."""
    qid = f"real-use-{n}" + ("_abs" if step.abstention else "")
    return {"question_id": qid, "question_type": step.judged_as, "question": step.text, "answer": step.gold}


def spent_tokens(data: Path) -> dict[str, int]:
    """What the private install's model calls cost, from its own ledger: the
    calls, their prompt and completion tokens, and the prompt tokens the
    provider served from its cache (the ledger's ``cached_tokens``, finding
    9; a ledger from before it counts none)."""
    path = data / "llm_calls.db"
    if not path.is_file():
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_calls)")}
        cached = "COALESCE(SUM(cached_tokens), 0)" if "cached_tokens" in columns else "0"
        row = conn.execute("SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
                           f"COALESCE(SUM(completion_tokens), 0), {cached} FROM llm_calls").fetchone()
    finally:
        conn.close()
    return {"calls": int(row[0]), "prompt_tokens": int(row[1]), "completion_tokens": int(row[2]),
            "cached_tokens": int(row[3])}


class Browser:
    """The private install's pages, driven as a person does: by the sidebar."""

    def __init__(self, base: str, lang: str, playwright: Any) -> None:
        self.base = base
        self.browser = playwright.chromium.launch(headless=True)
        host = base.split("//", 1)[1].split(":", 1)[0]
        self.context = self.browser.new_context(viewport={"width": 1400, "height": 1000})
        self.context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        # The first page this browser opens is bounced to the sign-in page,
        # which signs a loopback browser in (the cookie a person's first
        # visit gets); the Dashboard opens after it.
        self.page.goto(f"{base}/login", wait_until="domcontentloaded", timeout=60000)
        self.page.goto(f"{base}/dashboard", wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_function("() => !!window.Alpine && location.pathname === '/dashboard'",
                                    timeout=30000)

    def close(self) -> None:
        self.context.close()
        self.browser.close()

    def api(self, method: str, path: str, data: Any = None) -> Any:
        response = self.context.request.fetch(f"{self.base}{path}", method=method, data=data, timeout=120000)
        return response.json()

    def by_sidebar(self, path: str) -> None:
        self.page.click(f'nav a[href="{path}"], aside a[href="{path}"]', timeout=20000)
        self.page.wait_for_function("(p) => location.pathname === p", arg=path, timeout=30000)

    def new_chat(self) -> None:
        if not self.page.url.split("?", 1)[0].endswith("/chat"):
            self.by_sidebar("/chat")
        self.page.locator("#chat-input").wait_for(state="visible", timeout=30000)
        self.page.wait_for_function("() => !!window.KazmaChat", timeout=30000)
        self.page.click("#new-session-btn")
        self.page.wait_for_function("() => document.querySelectorAll('.message-assistant').length === 0",
                                    timeout=20000)

    def _phase(self, before: int) -> str:
        return str(self.page.wait_for_function(
            "(n) => { const b = document.querySelectorAll('.message-assistant');"
            " if (b.length <= n) return false;"
            " const h = b[b.length - 1].querySelector('.turn-header');"
            " const m = h && /is-(completed|no_answer|failed|cancelled|interrupted|approval)\\b/.exec(h.className);"
            " return m ? m[1] : false; }",
            arg=before, timeout=TURN_TIMEOUT_S * 1000).json_value())

    def send(self, text: str) -> tuple[str, str, list[str]]:
        """Send *text*; the answer's text, the turn's last phase, and the
        tools it asked to approve. Each is approved once, as the person
        telling Kazma something about themselves would."""
        before = self.page.evaluate("() => document.querySelectorAll('.message-assistant').length")
        self.page.fill("#chat-input", text)
        self.page.click("#send-btn")
        phase = self._phase(before)
        asked: list[str] = []
        while phase == "approval" and len(asked) < MAX_APPROVALS:
            tools = self.page.evaluate(
                "() => { const b = document.querySelectorAll('.message-assistant'); const last = b[b.length - 1];"
                " return [...last.querySelectorAll('.hitl-approval-card')]"
                ".filter(c => c.querySelector('.hitl-approve:not([disabled])'))"
                ".map(c => c.getAttribute('data-tool') || '?'); }")
            if not tools:
                break
            asked.append(str(tools[0]))
            self.page.locator(".message-assistant .hitl-approval-card .hitl-approve:not([disabled])").last.click()
            self.page.wait_for_timeout(1500)
            phase = self._phase(before)
        answer = self.page.evaluate(
            "() => { const b = document.querySelectorAll('.message-assistant .message-text');"
            " return b.length ? b[b.length - 1].innerText : ''; }")
        return str(answer or ""), phase, asked

    def wait_for_memory(self, words: str) -> bool:
        """The memory holding *words* is stored, and the memory queue (the
        deep pass) has nothing left to do."""
        from urllib.parse import quote

        deadline = time.monotonic() + MEMORY_TIMEOUT_S
        while time.monotonic() < deadline:
            found = self.api("GET", f"/api/memory/v2/episodes?q={quote(words)}")
            waiting = sum(int(self.api("GET", f"/api/memory/v2/queue?status={s}").get("total") or 0)
                          for s in ("pending", "processing"))
            if int(found.get("total") or 0) > 0 and waiting == 0:
                return True
            time.sleep(3)
        return False

    def forget(self, words: str) -> int:
        """Forget every memory the Memory page finds for *words*, one by
        one, as a person would; how many there were."""
        self.by_sidebar("/memory")
        search = self.page.locator("#v2-memories-search")
        search.wait_for(state="visible", timeout=30000)
        search.fill(words)
        forgotten = 0
        while forgotten < MAX_FORGETS:
            button = self.page.locator("#v2-memories-list .v2-memory-forget").first
            try:
                button.wait_for(state="visible", timeout=15000 if forgotten else 30000)
            except Exception:  # noqa: BLE001 - none (left) to forget
                break
            memory_id = button.get_attribute("data-id")
            button.click()
            confirm = self.page.locator(".modal-footer button.btn-danger")
            confirm.wait_for(state="visible", timeout=15000)
            confirm.click()
            self.page.wait_for_function(
                "(id) => !document.querySelector('#v2-memories-list .v2-memory-forget[data-id=\"' + id + '\"]')",
                arg=memory_id, timeout=30000)
            forgotten += 1
            search.fill("")
            search.fill(words)
            self.page.wait_for_timeout(1500)
        return forgotten


def set_up_model(browser: Any, *, provider: str, model: str, key: str, base_url: str | None) -> None:
    """The model as a person sets it up: at the preset's address through the
    guide; at an address of the provider's own (``base_url``) saved in
    Settings first, then the guide, which keeps a saved address and key."""
    if base_url:
        saved = browser.api("POST", "/api/providers",
                            {"name": provider, "preset": provider, "base_url": base_url, "api_key": key})
        if saved.get("detail") or saved.get("error"):
            raise SystemExit(f"the provider could not be saved: {saved.get('detail') or saved.get('error')}")
        key = ""
    set_up = browser.api("POST", "/api/setup/bootstrap", {"provider": provider, "api_key": key, "model": model})
    if not set_up.get("ready", True) and set_up.get("error"):
        raise SystemExit(f"the model could not be set up: {set_up.get('error')}")


def run_language(language: Language, *, model: str, key: str, judge: lme.Chat, playwright: Any,
                 workdir: Path, reasoning: str = "low", transcript: list[dict[str, Any]] | None = None,
                 provider: str = "deepseek", base_url: str | None = None, checkout: Path = REPO,
                 keep_memory: Path | None = None) -> dict[str, Any]:
    home, data = workdir / language.lang / "home", workdir / language.lang / "data"
    for folder in (home, data, home / "workspace"):
        folder.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = Server(Path(sys.executable), server_env(home, data, port, checkout), home,
                    workdir / f"{language.lang}.log", port)
    steps: list[dict[str, Any]] = []
    from kazma_core.token_usage import token_counts

    # The judge's own calls (the install's are in its ledger): calls, tokens,
    # the cache's share, read as the ledger reads it (token_counts).
    judged = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    browser = None
    try:
        server.wait_live()
        browser = Browser(server.base, language.lang, playwright)
        set_up_model(browser, provider=provider, model=model, key=key, base_url=base_url)
        saved = browser.api("PUT", "/api/settings/single", {"key": "agent.chat_reasoning", "value": reasoning})
        if saved.get("status") != "ok":
            raise SystemExit(f"the reasoning level could not be set: {saved}")
        for n, step in enumerate(language.steps, 1):
            row: dict[str, Any] = {"n": n, "kind": step.kind, "action": step.action}
            answer = ""
            if step.action == "forget":
                row["forgotten"] = browser.forget(step.words)
                row["passed"] = row["forgotten"] > 0
            else:
                if step.new_chat:
                    browser.new_chat()
                answer, phase, asked = browser.send(step.text)
                row["phase"] = phase
                row["answered"] = bool(answer.strip())
                # A turn that ended in Kazma's own error (friendly_llm_error's
                # sign: a model call the provider refused, such as Qwen's
                # content inspection) is no memory miss: said apart.
                if turn_failed(answer):
                    row["turn_failed"] = True
                if asked:
                    row["approval_tools"] = asked
                if step.action == "tell":
                    row["passed"] = phase == "completed" and browser.wait_for_memory(step.words)
                else:
                    verdict, usage = judge.ask(lme.judge_prompt(judge_item(step, n), answer), max_tokens=10)
                    judged["calls"] += 1
                    counts = token_counts(usage)
                    judged["prompt_tokens"] += counts.prompt
                    judged["completion_tokens"] += counts.completion
                    judged["cached_tokens"] += counts.cached
                    row["passed"] = phase == "completed" and verdict.strip().lower().startswith("yes")
            steps.append(row)
            if transcript is not None:
                transcript.append({"lang": language.lang, **row, "text": step.text, "reply": answer})
            print(f"  {language.lang} {n}. {step.kind:<11} {step.action:<6} "
                  f"{'pass' if row['passed'] else 'FAIL'}", flush=True)
        page_errors = list(browser.errors)
    finally:
        if browser is not None:
            browser.close()
        server.stop()
        if keep_memory is not None:
            keep_memory_databases(data, keep_memory / language.lang)
    return {"lang": language.lang, "steps": steps, "passed": all(s["passed"] for s in steps),
            "page_errors": len(page_errors), "spent": spent_tokens(data), "judge_spent": judged}


def suite_covers_every_kind() -> list[str]:
    """Kinds rule 3 names that a language's steps leave out."""
    return [f"{language.lang}: {kind}" for language in SUITE for kind in KINDS
            if kind not in {s.kind for s in language.steps}]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", default="deepseek-flash")
    p.add_argument("--provider", default="deepseek", help="the provider preset the model is set up with")
    p.add_argument("--base-url", default=None, help="the provider's own address (else the preset's)")
    p.add_argument("--key-env", default="DEEPSEEK_API_KEY", help="the variable holding the key (never written)")
    p.add_argument("--judge", default=None, help="the judge model (the model unless named)")
    p.add_argument("--checkout", type=Path, default=REPO, help="the checkout the private Kazma runs from")
    p.add_argument("--languages", default="en,ar")
    p.add_argument("--reasoning", default="low", help="agent.chat_reasoning in the private install")
    p.add_argument("--keep-memory", type=Path, default=None,
                   help="a folder outside the repository to keep each language's memory databases in "
                        "(never the vault or the settings), to read a failure with no model call")
    p.add_argument("--transcript", type=Path, default=None,
                   help="a folder outside the repository for the replies and the servers' logs (to read why)")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = os.environ.get(args.key_env, "")
    if not key:
        print(f"set {args.key_env}", file=sys.stderr)
        return 2
    from kazma_core.providers import PROVIDER_PRESETS
    from playwright.sync_api import sync_playwright

    address = args.base_url or str((PROVIDER_PRESETS.get(args.provider) or {}).get("base_url") or "")
    if not address:
        print(f"no address for provider {args.provider!r}: give --base-url", file=sys.stderr)
        return 2
    checkout = args.checkout.resolve()
    judge_model = args.judge or args.model
    judge = lme.Chat(model=judge_model, base_url=address, key=key)
    wanted = [x.strip() for x in args.languages.split(",") if x.strip()]
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=checkout, capture_output=True,
                            text=True, check=False).stdout.strip()
    for folder, what in ((args.transcript, "the transcript"), (args.keep_memory, "the kept memory")):
        if folder is not None and REPO in folder.resolve().parents:
            print(f"{what} holds the replies' text: keep it outside the repository", file=sys.stderr)
            return 2
    transcript: list[dict[str, Any]] | None = [] if args.transcript is not None else None
    results = []
    with tempfile.TemporaryDirectory(prefix="kazma-real-use-", ignore_cleanup_errors=True) as tmp, \
            sync_playwright() as playwright:
        for language in SUITE:
            if language.lang in wanted:
                results.append(run_language(language, model=args.model, key=key, judge=judge,
                                            playwright=playwright, workdir=Path(tmp), reasoning=args.reasoning,
                                            transcript=transcript, provider=args.provider,
                                            base_url=args.base_url, checkout=checkout,
                                            keep_memory=args.keep_memory))
        if args.transcript is not None:
            args.transcript.mkdir(parents=True, exist_ok=True)
            (args.transcript / "transcript.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False),
                                                             encoding="utf-8")
            for log in Path(tmp).glob("*.log"):
                (args.transcript / log.name).write_bytes(log.read_bytes())
    report = {
        "made_on": datetime.now(UTC).date().isoformat(), "commit": commit, "model": args.model,
        "provider": args.provider, "judge": judge_model, "reasoning": args.reasoning,
        "what": "Kazma's shipped path in a private install, driven in the browser by the sidebar; each "
                "answer judged with LongMemEval's prompts. Pass or fail per step, and tokens; no reply text.",
        "passed": all(r["passed"] for r in results), "languages": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}; {'every step passed' if report['passed'] else 'a step failed'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
