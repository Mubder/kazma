"""Features per person (Spaces S2-11, plan 4.8, decision N18).

The owner asked for "give User Y the X Studio, but not User G". A feature is
more than its tools, so this is one table (:data:`FEATURES`): each feature
names its pages, its routes, its tools, its chat commands, its background
work and its connected accounts, and one switch per person covers them all.
What every person has whatever the switches say is declared
(:data:`ALWAYS_ON`, with why: chat, memory, My account), and so is what is
the install's own and never a person's feature (:data:`INSTALL`: the admin's
tools and commands, Kazma's own loops). ``tests/test_feature_grants.py``
reads the real app's routes, the tool registry, the chat commands and the
background loops: one with neither a feature nor a declaration fails.

Who has what
------------
* The admin has every feature. Development is the one the admin may switch
  off for themself (N28: on at first; ``identity.holds_development``).
* Any other person starts with chat (and its memory), documents, knowledge,
  web, research, and images and voice (N18, :attr:`Feature.new_member`);
  every other feature is off until the admin turns it on (People). Grants
  are per person, in a shared space too. Development's switch is the one
  ``identity.holds_development`` reads.
* A grant never passes the group rule (S1-20): Development's tools exist
  only in a host space (``spaces.tools.host_allowance``), whatever a grant
  says.
* **A disabled person's work waits** (S2-07): while the admin has a person
  disabled (``accounts[id]["enabled"]`` false), :meth:`Grants.has` holds
  nothing for them, so every door that asks for a feature (:func:`work_refusal`,
  the admissions) and every tool (:func:`tool_refusal`) refuses their work
  with :func:`_disabled_refusal` -- their background work waits, as work of a
  feature that is off does, and goes on once they are enabled again. Their
  switches are kept as they are (People still shows them).
* **Inside a feature the admin can still switch single tools off for a
  person** (S2-01, plan 4.8: "X Studio on, but posting off"): the person's
  ``policies[account]["tools_off"]`` (:data:`TOOLS_OFF_POLICY`). A tool
  switched off is neither offered nor callable for them, at the same places
  a feature's tools are refused (:func:`tool_refusal`): chat, a scheduled
  job's executor, a swarm worker, the MCP gate. Only tools a person can
  have are switched (:func:`checked_tools`); the admin's own set is not.

A feature that is off is gone for the person: no menu entry, its pages and
routes refuse (the tenant middleware, its live sockets at the handshake),
its tools are neither offered nor callable (``spaces.tools.offered_here``
and ``tool_refusal``), its chat command refuses (:func:`command_refusal`,
and each chat app's command handler), the intent engine does not start its
work (:data:`INTENT_HANDLERS`), and its work is checked where it is admitted
(:data:`ADMISSIONS`: every door to the work passes one of them, and each
asks :func:`work_refusal` for the person the work is for). Work that cannot
run waits (:class:`FeatureOff`): nothing is failed or changed, and it goes on
once the feature is on again.

Nothing here acts unless Spaces is enforced: an install with one account
behaves as before.

Every function that reads the identity store is blocking: async code calls
it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any

from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, current_scope, spaces_enforced

__all__ = [
    "ADMISSIONS",
    "ALSO_NEEDS",
    "ALWAYS_ON",
    "FEATURES",
    "INSTALL",
    "INTENT_HANDLERS",
    "NAMES",
    "NO_ADMISSION",
    "NOT_A_TOOL_DOOR",
    "TOOLS_OFF_POLICY",
    "TOOL_ROUTE_DOORS",
    "TOOL_WORK_DOORS",
    "AlwaysOn",
    "Feature",
    "FeatureOff",
    "FeatureRefused",
    "Grants",
    "ToolOff",
    "command_refusal",
    "feature_here",
    "feature_refusal",
    "forget_page_grants",
    "grants_here",
    "grants_of",
    "intent_refusal",
    "precondition",
    "remember_page_grants",
    "tool_door_refusal",
    "tool_off_text",
    "tool_refusal",
    "tool_work_refusal",
    "work_refusal",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Feature:
    """One feature: everything that belongs to it (plan 4.8)."""

    id: str
    group: str  # "space", "outside" or "host" (plan 4.8's groups)
    new_member: bool  # N18: on for a new member
    pages: tuple[str, ...] = ()  # menu entries (page paths)
    routes: tuple[str, ...] = ()  # route patterns (``kazma_ui.route_scopes``'s form)
    tools: frozenset[str] = field(default_factory=frozenset)
    commands: frozenset[str] = field(default_factory=frozenset)
    background: tuple[str, ...] = ()  # ``path::qualified.name`` of its loops
    accounts: tuple[str, ...] = ()  # the connected accounts it uses


@dataclass(frozen=True)
class AlwaysOn:
    """What every person has whatever the switches say, and why."""

    why: str
    pages: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    tools: frozenset[str] = field(default_factory=frozenset)
    commands: frozenset[str] = field(default_factory=frozenset)
    background: tuple[str, ...] = ()


def _loops(*names: str) -> tuple[str, ...]:
    return tuple(names)


FEATURES: dict[str, Feature] = {f.id: f for f in (
    Feature(
        "x_studio", "space", False,
        pages=("/x",),
        # Booking an X post from the Scheduled page is X Studio's (the S2-11
        # review: a person with Scheduled and without X booked posts there).
        routes=("/x$", "/api/x", "/api/x/oauth2/callback$", "/api/scheduled/x"),
        tools=frozenset({
            "x_account", "x_api_act", "x_api_operations", "x_api_read", "x_cancel_scheduled_post",
            "x_delete_post", "x_download_media", "x_draft_posts", "x_list_scheduled", "x_post",
            "x_schedule_post", "x_status", "x_upload_media",
            # Drafts to publish (save, list, put aside): X posts.
            "save_proposal", "list_proposals", "discard_proposal",
        }),
        commands=frozenset({"x"}),
        background=_loops(
            "kazma-core/kazma_core/x_api/mentions_fire.py::_loop",
            "kazma-core/kazma_core/x_api/scheduled_fire.py::_loop",
            "kazma-core/kazma_core/x_api/client.py::XClient.upload_media",
        ),
        accounts=("x",),
    ),
    Feature(
        "email_calendar", "space", False,
        routes=("/api/email", "/api/calendar"),
        tools=frozenset({
            "email_accounts", "email_analyze", "email_categorize", "email_delete", "email_get",
            "email_list", "email_send", "create_event", "delete_event", "update_event", "list_events",
            "find_free_slots",
        }),
        accounts=("gmail", "microsoft", "imap", "google_calendar", "outlook_calendar"),
    ),
    Feature(
        "documents", "space", True,
        pages=("/documents",),
        # The Files panel of the Documents page (Spaces S2-12): a space's own
        # files, and moving one's own between spaces.
        routes=("/documents$", "/api/documents", "/api/space-files"),
        tools=frozenset({
            "convert_document", "document_cancel", "document_convert", "document_import",
            "document_index", "document_read", "document_redact", "document_search", "document_status",
            "generate_docx", "generate_markdown_doc", "generate_pdf", "generate_pptx", "generate_xlsx",
            "ocr_document", "parse_document", "pdf_fill_form", "pdf_info", "pdf_merge", "pdf_redact",
            "pdf_split", "read_document",
        }),
        commands=frozenset({"documents", "docs"}),
        background=_loops(
            "kazma-core/kazma_core/documents/worker.py::DocumentWorker._heartbeat",
            "kazma-skills/kazma_skills/native/document_platform/tools.py::_wait_terminal",
        ),
    ),
    Feature(
        "knowledge", "space", True,
        pages=("/knowledge",),
        routes=("/knowledge$", "/api/kb"),
        tools=frozenset({
            "knowledge_create_library", "knowledge_ingest_site", "knowledge_ingest_url",
            "knowledge_list_libraries", "knowledge_search",
        }),
        commands=frozenset({"kb"}),
        background=_loops(
            "kazma-core/kazma_core/stores/knowledge_ingest.py::_fetch_with_retries",
            "kazma-core/kazma_core/stores/knowledge_ingest.py::_ingest_site_impl",
        ),
    ),
    Feature(
        "research", "space", True,
        pages=("/research",),
        routes=("/research$", "/api/research"),
        tools=frozenset({
            "cancel_research_session", "critique_synthesis_gaps", "digest_research_file",
            "list_research_chunks", "list_research_papers", "plan_research_queries",
            "read_research_chunk", "research_readiness", "research_session_status",
            "run_research_pipeline", "start_deep_research", "summarize_research_file",
            "synthesize_from_digests",
        }),
        commands=frozenset({"research"}),
        background=_loops("kazma-gateway/kazma_gateway/agent_handler/commands.py::_try_research_command"),
    ),
    Feature(
        "swarm", "space", False,
        pages=("/swarm",),
        routes=("/swarm$", "/api/swarm/tasks", "/api/swarm/dispatch$", "/api/swarm/approvals",
                "/api/swarm/status$", "/api/swarm/workers/{name}/logs$"),
        tools=frozenset({"dispatch_swarm", "check_swarm_task", "handoff_to_worker", "spawn_agent",
                         "spawn_agents"}),
        commands=frozenset({"swarm"}),
        background=_loops(
            "kazma-core/kazma_core/swarm/durable.py::_heartbeat",
            "kazma-core/kazma_core/swarm/single_operator.py::wait_for_owner_answer",
            "kazma-core/kazma_core/agent/tool_builtins/system.py::register_system_tools.check_swarm_task",
        ),
    ),
    Feature(
        "scheduled", "space", False,
        pages=("/scheduled",),
        routes=("/scheduled$", "/api/scheduled"),
        tools=frozenset({"schedule_task", "list_scheduled", "edit_scheduled", "cancel_scheduled"}),
    ),
    Feature(
        "code", "space", False,
        tools=frozenset({"python_exec"}),
    ),
    Feature(
        "web", "outside", True,
        tools=frozenset({"web_search", "web_search_duckduckgo", "read_url", "read_url_to_file",
                         "crawl_page", "crawl_site"}),
        background=_loops(
            "kazma-core/kazma_core/tools/web_research.py::crawl_site",
            "kazma-core/kazma_core/web_acquire/origins.py::_OriginScheduler.request",
            "kazma-core/kazma_core/web_acquire/process_worker.py::ProcessOwner.finish",
        ),
    ),
    Feature(
        "browser", "outside", False,
        tools=frozenset({"browser_navigate", "browser_click", "browser_fill_form", "browser_extract_text",
                         "browser_eval_js", "browser_screenshot"}),
    ),
    Feature(
        "images_voice", "outside", True,
        routes=("/api/voice/stt$", "/api/voice/tts$", "/api/voice/livekit/token$", "/ws/voice$"),
        tools=frozenset({"generate_image", "generate_ui_mockup", "download_image", "analyze_image",
                         "analyze_local_image"}),
    ),
    # Linking a chat app to a person's account is stage 3: nothing of it
    # exists yet, so the switch has nothing to act on until then.
    Feature("chat_apps", "space", False),
    Feature(
        "development", "host", False,
        pages=("/workspace", "/ide"),
        routes=("/workspace$", "/api/workspace", "/api/workspaces", "/api/git", "/api/bookmarks",
                "/ide$", "/api/ide", "/api/github"),
        # Its tools are spaces.tools.DEVELOPMENT (filled below), and every
        # tool in no group (an MCP server's).
        commands=frozenset({"ide", "skill"}),
        accounts=("github",),
    ),
)}

#: What each feature is called, (English, Arabic): the People page's words
#: (the catalog's ``settings.feature.<id>``, held equal by a test), which a
#: refusal names.
NAMES: dict[str, tuple[str, str]] = {
    "x_studio": ("X Studio", "استوديو X"),
    "email_calendar": ("Email and calendar", "البريد والتقويم"),
    "documents": ("Documents", "المستندات"),
    "knowledge": ("Knowledge", "المعرفة"),
    "research": ("Research", "البحث"),
    "swarm": ("Swarm", "السرب"),
    "scheduled": ("Scheduled", "المجدولة"),
    "code": ("Code", "الكود"),
    "web": ("Web", "الويب"),
    "browser": ("Browser", "المتصفح"),
    "images_voice": ("Images and voice", "الصور والصوت"),
    "chat_apps": ("Chat apps", "تطبيقات المحادثة"),
    "development": ("Development", "التطوير"),
}

#: A tool or route that needs a second feature as well (decided in the S2-11
#: review): putting a document into a library, or searching libraries
#: through Documents, is Knowledge's too. Tool name, or route pattern in
#: ``kazma_ui.route_scopes``'s form, -> the features it needs besides its own.
ALSO_NEEDS: dict[str, tuple[str, ...]] = {
    "document_index": ("knowledge",),
    "document_search": ("knowledge",),
    "/api/documents/index$": ("knowledge",),
    "/api/documents/search$": ("knowledge",),
}

#: The intent engine's handlers (``agent.intent.handlers``) -> the features
#: the work each starts needs. A person without them gets no such decision:
#: the turn goes on as an ordinary one, where the feature's tools are not
#: offered (the supervisor, :func:`intent_refusal`).
INTENT_HANDLERS: dict[str, tuple[str, ...]] = {
    "research_deep": ("research",),
    "document_generate": ("documents",),
    "research_then_document": ("research", "documents"),
}

#: Where each feature's work is admitted (``path::qualified.name``): every
#: door to the work -- a tool, a route, a chat command, the intent engine,
#: a resume, a booked job when it runs -- passes one of these, and each asks
#: :func:`work_refusal` for the person the work is for (a gate reads them).
#: A new door that does not pass one of them is a new admission here.
ADMISSIONS: dict[str, tuple[str, ...]] = {
    "x_studio": (
        "kazma-core/kazma_core/x_api/booking.py::book_x_post",
        "kazma-core/kazma_core/x_api/ownership.py::x_work_scope",
    ),
    "documents": ("kazma-core/kazma_core/documents/worker.py::DocumentWorker._process_claimed",),
    "knowledge": (
        "kazma-core/kazma_core/stores/knowledge_ingest.py::_knowledge_off_now",  # before every page
        "kazma-ui/kazma_ui/kb_api.py::_in_the_crawls_space",
    ),
    "research": ("kazma-core/kazma_core/web_acquire/research_runtime.py::_frozen",),
    "swarm": ("kazma-core/kazma_core/swarm/worker_dispatch.py::_worker_scope",),
    "scheduled": ("kazma-core/kazma_core/cron/scheduler.py::_fire_binding",),
    "images_voice": ("kazma-ui/kazma_ui/space_binding.py::_socket_feature_refusal",),
}

#: The features with no work admitted outside a tool call or a request,
#: and why: refused at the offer, the call and the route, nothing of theirs
#: runs later.
NO_ADMISSION: dict[str, str] = {
    "email_calendar": "tools and routes only: a mailbox or calendar call is made in the tool or the request",
    "code": "one tool (python_exec), run in its call",
    "web": "tools only: a fetch, a search or a crawl runs in its tool call",
    "browser": "tools only: a page is driven in its tool call",
    "chat_apps": "nothing of it exists until stage 3 links chat apps to accounts",
    "development": "host tools and routes (S1-20's group rule, checked at the offer, the call and the route)",
}


#: The doors to a tool's effect that are not a call of the tool (Spaces
#: S2-01, the second review). A tool the admin switched off for a person
#: (``tools_off``) -- or whose feature they lack, or whose account is
#: disabled -- is refused at each of them as at its call: switched off, the
#: effect is off for them, never only the agent's way to it.
#:
#: A route door: ``(METHOD, pattern)`` (``kazma_ui.route_scopes``' grammar)
#: and the tools whose work it does; the tenant middleware refuses it (403
#: ``tool_off``) when any of them is off for the person.
TOOL_ROUTE_DOORS: dict[tuple[str, str], tuple[str, ...]] = {
    ("POST", "/api/documents$"): ("document_import",),
    ("POST", "/api/documents/import$"): ("document_import",),
    ("POST", "/api/documents/jobs/{job_id}/retry$"): ("document_import",),
    ("POST", "/api/documents/jobs/{job_id}/cancel$"): ("document_cancel",),
    ("POST", "/api/documents/merge$"): ("pdf_merge",),
    ("POST", "/api/documents/search$"): ("document_search",),
    ("POST", "/api/documents/{document_id}/convert$"): ("document_convert",),
    ("POST", "/api/documents/{document_id}/fill-form$"): ("pdf_fill_form",),
    ("POST", "/api/documents/{document_id}/index$"): ("document_index",),
    ("POST", "/api/documents/{document_id}/redact$"): ("document_redact",),
    ("POST", "/api/documents/{document_id}/split$"): ("pdf_split",),
    ("POST", "/api/kb/libraries$"): ("knowledge_create_library",),
    ("POST", "/api/kb/libraries/{library_id}/refresh$"): ("knowledge_ingest_site",),
    ("POST", "/api/kb/jobs/{job_id}/resume$"): ("knowledge_ingest_site",),
    ("POST", "/api/kb/preview$"): ("knowledge_ingest_site",),
    ("POST", "/api/kb/search$"): ("knowledge_search",),
    ("POST", "/api/research/sessions$"): ("start_deep_research",),
    ("POST", "/api/research/sessions/{session_id}/resume$"): ("start_deep_research",),
    ("POST", "/api/research/sessions/{session_id}/cancel$"): ("cancel_research_session",),
    ("POST", "/api/scheduled/cron$"): ("schedule_task",),
    ("PUT", "/api/scheduled/cron/{job_id}$"): ("edit_scheduled",),
    ("DELETE", "/api/scheduled/cron/{job_id}$"): ("cancel_scheduled",),
    ("POST", "/api/scheduled/cron/{job_id}/restore$"): ("schedule_task",),
    ("POST", "/api/scheduled/recipes/{recipe_id}$"): ("schedule_task",),
    ("POST", "/api/swarm/dispatch$"): ("dispatch_swarm",),
    ("POST", "/api/swarm/tasks/{task_id}/retry$"): ("dispatch_swarm",),
    ("POST", "/api/x/generate$"): ("x_draft_posts",),
    ("POST", "/api/x/drafts/discard$"): ("discard_proposal",),
    ("POST", "/api/ide/write$"): ("file_write",),
    ("POST", "/api/ide/apply_patch$"): ("file_apply_patch",),
    ("POST", "/api/ide/apply_patch_set$"): ("file_apply_patch_set",),
    ("POST", "/api/ide/delete$"): ("file_delete",),
    ("POST", "/api/ide/swarm$"): ("dispatch_swarm",),
}

#: A work door (``path::qualified.name``): a function every way to a tool's
#: effect passes that is not a call of the tool -- a choke point a page, the
#: chat apps and the tool share (asked again on the tool's path: harmless),
#: a page's action whose tool depends on what it is asked (a document's
#: format, a crawl's kind), work that runs later for the person it is for
#: (a booked post when it fires), the intent engine. Each asks
#: :func:`tool_work_refusal` or :func:`tool_door_refusal`, or names the tool
#: to ``x_api.ownership.x_work_scope``.
TOOL_WORK_DOORS: dict[str, tuple[str, ...]] = {
    "x_post": (
        "kazma-core/kazma_core/x_api/booking.py::publish_x_post",
        "kazma-core/kazma_core/x_api/threads.py::publish_thread",
    ),
    "x_delete_post": ("kazma-core/kazma_core/x_api/booking.py::delete_x_post",),
    "x_schedule_post": (
        "kazma-core/kazma_core/x_api/booking.py::book_x_post",
        "kazma-core/kazma_core/x_api/publication_service.py::fire_due_operations",
        "kazma-core/kazma_core/x_api/scheduled_fire.py::_fire_post",
        "kazma-ui/kazma_ui/x_api.py::x_queue_action",
    ),
    "x_cancel_scheduled_post": ("kazma-ui/kazma_ui/x_api.py::x_queue_action",),
    "start_deep_research": ("kazma-core/kazma_core/agent/intent/handlers/research.py::run_research_deep",),
    "knowledge_ingest_url": ("kazma-ui/kazma_ui/kb_api.py::create_kb_router.ingest",),
    "knowledge_ingest_site": ("kazma-ui/kazma_ui/kb_api.py::create_kb_router.ingest",),
    "generate_pdf": ("kazma-ui/kazma_ui/documents_api.py::create_documents_router.generate",),
    "generate_docx": ("kazma-ui/kazma_ui/documents_api.py::create_documents_router.generate",),
    "generate_pptx": ("kazma-ui/kazma_ui/documents_api.py::create_documents_router.generate",),
    "generate_xlsx": ("kazma-ui/kazma_ui/documents_api.py::create_documents_router.generate",),
    "generate_markdown_doc": ("kazma-ui/kazma_ui/documents_api.py::create_documents_router.generate",),
}

#: The routes that change something in a space and are no tool's door, and
#: why. Every such route is a door above or here (a gate reads the app).
NOT_A_TOOL_DOOR: dict[tuple[str | None, str], str] = {
    (None, "/api/chat"): "the chat itself: tools are asked at their calls; the rest is a chat's own housekeeping",
    (None, "/api/memory"): ("the person's own memory on the Memory page: a switch is what Kazma's agent may do "
                            "for them; Forget and the page's edits stay theirs (the user decides what is kept)"),
    (None, "/api/replay"): "Time Travel over the person's own chats",
    (None, "/api/sessions"): "the person's own chats",
    (None, "/api/approve"): "answering an approval card",
    (None, "/api/pending-approvals"): "clearing one's own waiting cards",
    (None, "/api/commitment"): "confirming or rejecting a held lesson",
    (None, "/api/runs"): "deciding on one's own run: each kind's own control",
    (None, "/api/a2a$"): "another agent's read-only turn: its tools are asked at their calls",
    (None, "/api/import/history"): "bringing one's own chat history in; no tool does it",
    (None, "/api/bookmarks"): "the Workspace's bookmarks; no tool keeps them",
    (None, "/api/github"): "GitHub's sign-in, issues and pull requests: Development's, no switchable tool",
    (None, "/api/workspace"): "the Workspace's folder choice: Development's, no switchable tool",
    (None, "/api/workspaces"): "the Workspace's folder choice: Development's, no switchable tool",
    ("POST", "/api/ide/checkpoints"): "putting files back as a checkpoint had them; no tool does it",
    ("POST", "/api/ide/diff$"): "a read",
    ("POST", "/api/ide/lsp$"): "a language server's answer: a read",
    ("POST", "/api/ide/git$"): "Development's host git (S1-20's group rule), no switchable tool",
    ("POST", "/api/ide/run$"): "Development's host run (S1-20's group rule), no switchable tool",
    ("POST", "/api/ide/runfile$"): "Development's host run (S1-20's group rule), no switchable tool",
    ("POST", "/api/ide/skill$"): "Development's skill run (S1-20's group rule), no switchable tool",
    ("DELETE", "/api/documents/{document_id}$"): "deleting one's document; no tool deletes one",
    ("POST", "/api/documents/{document_id}/delete$"): "deleting one's document; no tool deletes one",
    ("POST", "/api/documents/{document_id}/move$"): "the person's own move between their spaces (S2-12)",
    ("POST", "/api/space-files/move$"): "the person's own move between their spaces (S2-12); no tool moves a file",
    ("POST", "/api/documents/{document_id}/unindex$"): "taking a document out of a library; no tool does it",
    ("POST", "/api/documents/generate$"): "a work door: its handler asks the format's tool",
    (None, "/api/documents/ops"): "the admin's document maintenance",
    (None, "/api/email"): "connecting a mailbox: the tools send and read",
    (None, "/api/calendar"): "connecting a calendar: the tools change events",
    (None, "/api/voice"): "speech in and out; no switchable tool",
    ("DELETE", "/api/kb/libraries/{library_id}$"): "deleting one's library; no tool does it",
    ("PATCH", "/api/kb/libraries/{library_id}$"): "renaming one's library; no tool does it",
    ("POST", "/api/kb/libraries/{library_id}/archive$"): "archiving one's library; no tool does it",
    ("POST", "/api/kb/libraries/{library_id}/unarchive$"): "restoring one's library; no tool does it",
    ("POST", "/api/kb/ingest$"): "a work door: its handler asks the crawl's or the page's tool",
    ("POST", "/api/kb/services"): "a service's credit balance, read on request",
    ("DELETE", "/api/research/sessions/{session_id}$"): "deleting one's research; no tool does it",
    ("DELETE", "/api/research/tasks/{task_id}$"): "deleting one's research; no tool does it",
    ("POST", "/api/research/sessions/{session_id}/archive$"): "archiving one's research",
    ("POST", "/api/research/sessions/{session_id}/unarchive$"): "restoring one's research",
    ("POST", "/api/research/tasks/{task_id}/archive$"): "archiving one's research",
    ("POST", "/api/research/tasks/{task_id}/unarchive$"): "restoring one's research",
    ("POST", "/api/research/sessions/{session_id}/export$"): "exporting one's research: a read",
    ("POST", "/api/research/{task_id}/export$"): "exporting one's research: a read",
    ("POST", "/api/research/papers/export$"): "exporting one's papers: a read",
    ("POST", "/api/research/compare$"): "comparing two reports: a read",
    ("POST", "/api/research/acquisition/credits$"): "a service's credit balance, read on request",
    ("POST", "/api/swarm/approvals/{approval_id}/decide$"): "answering a worker's approval",
    ("POST", "/api/swarm/tasks/{task_id}/approve$"): "answering one's task's checkpoint",
    ("POST", "/api/swarm/tasks/{task_id}/reject$"): "answering one's task's checkpoint",
    ("POST", "/api/swarm/tasks/{task_id}/cancel$"): "cancelling one's own task",
    ("POST", "/api/scheduled/x$"): "a work door: x_api.booking.book_x_post asks x_schedule_post",
    ("POST", "/api/x/post$"): "a work door: x_api.booking.publish_x_post asks x_post",
    ("POST", "/api/x/delete$"): "a work door: x_api.booking.delete_x_post asks x_delete_post",
    ("POST", "/api/x/reply/approve$"): "a work door: the reply goes out through x_api.booking.publish_x_post",
    ("POST", "/api/x/reply/retry$"): "a work door: a reply goes out through x_api.booking.publish_x_post",
    ("POST", "/api/x/reply/delete$"): "a work door: x_api.booking.delete_x_post asks x_delete_post",
    ("POST", "/api/x/threads/{ident}/{action}$"): "a work door: x_api.threads.publish_thread asks x_post",
    ("POST", "/api/x/queue/{operation_id}/{action}$"): "a work door: its handler asks the action's tool",
    ("POST", "/api/x/threads$"): "saving a thread's draft for review",
    ("POST", "/api/x/preview$"): "a preview: nothing is sent",
    ("POST", "/api/x/reply/preview$"): "a preview: nothing is sent",
    ("POST", "/api/x/reply/deny$"): "declining a reply",
    ("POST", "/api/x/reply/poll$"): "reading new mentions now",
    ("POST", "/api/x/reply/model-test$"): "testing the reply model",
    ("POST", "/api/x/reply/policy/validate$"): "checking a reply policy",
    ("PUT", "/api/x/reply$"): "the reply bot's settings",
    ("PUT", "/api/x/reply/qualification$"): "the reply bot's settings",
    ("PUT", "/api/x/composer$"): "the composer's settings",
    ("POST", "/api/x/operations/{operation_id}/check$"): "checking a post's receipt: a read",
    ("POST", "/api/x/datasets$"): "drafting datasets; no tool keeps them",
    ("POST", "/api/x/datasets/{ident}/collect$"): "collecting a drafting dataset; no tool does it",
    ("PUT", "/api/x/datasets/{ident}/case$"): "drafting datasets; no tool keeps them",
    ("POST", "/api/x/credentials$"): "connecting the X account",
    ("POST", "/api/x/api-access$"): "connecting the X account",
    ("POST", "/api/x/disconnect$"): "disconnecting the X account",
    ("POST", "/api/x/oauth2/start$"): "connecting the X account",
    ("POST", "/api/x/oauth2/sign-out$"): "disconnecting the X account",
    ("POST", "/api/x/resume$"): "the admin's resume of X after a stop",
    ("POST", "/api/x/test$"): "testing the X connection",
}


class ToolOff(PermissionError):
    """Work of a tool switched off for the person it is for, reached by a
    door that is not a call of the tool (Spaces S2-01). Like
    :class:`FeatureOff` it waits: nothing failed or changed, and it goes on
    once the admin turns the tool on again. ``str()`` is the refusal."""

    code = "tool_off"

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(message)
        self.tool = tool


class FeatureOff(PermissionError):
    """Work of a feature that is off for the person it is for (S2-11). It
    waits: nothing is failed, held for review or changed, and it goes on
    once the admin turns the feature on again. ``str()`` is the refusal."""

    code = "feature_off"

    def __init__(self, feature: str, message: str) -> None:
        super().__init__(message)
        self.feature = feature


ALWAYS_ON: dict[str, AlwaysOn] = {
    "chat": AlwaysOn(
        "Chat is what Kazma is: a person's own conversations, the turn's approvals, its steps "
        "(Time Travel), what is running, and the files of their own space that chat reads and writes.",
        pages=("/chat", "/replay", "/dashboard", "/agents"),
        routes=("/chat$", "/api/chat", "/ws/chat", "/api/approve", "/api/pending-approvals", "/replay$",
                "/api/replay", "/api/sessions", "/api/commitment", "/api/runs", "/api/a2a$",
                "/dashboard$", "/ws/dashboard$", "/agents$", "/api/agents"),
        tools=frozenset({
            "current_datetime", "context_info", "export_session", "hijri_convert", "insert_diacritics",
            "arabic_translate", "task_ledger_update", "update_scratchpad", "list_runs", "run_status",
            "send_file",
            "file_append", "file_apply_patch", "file_apply_patch_set", "file_delete", "file_list",
            "file_read", "file_search", "file_write", "execute_db_query", "inspect_db_schema",
            "sqlite_query",
        }),
        commands=frozenset({
            "help", "sessions", "seasons", "session", "season", "switch", "space", "new", "reset",
            "compact",
            "long", "mission", "yolo", "unrestricted", "plan", "steer", "abort", "stop", "replay",
            "report", "runs", "fork", "undo", "edit", "model", "models", "context", "status", "cost",
            "hitl",
        }),
        background=_loops(
            "kazma-core/kazma_core/llm_provider.py::LLMProvider._chat_stream_once",
            "kazma-core/kazma_core/safety/bus_bridge.py::request_approval_via_registry",
            "kazma-gateway/kazma_gateway/agent_handler/graph.py::create_graph_handler._handler_body",
            "kazma-ui/kazma_ui/routes_direct/misc.py::_await_segment_tail",
            "kazma-ui/kazma_ui/sse_chat/__init__.py::create_sse_chat_router.steer_chat_turn",
            "kazma-ui/kazma_ui/sse_chat/_streaming.py::_stream_langgraph_events",
            "kazma-ui/kazma_ui/sse_chat/_streaming.py::_stream_langgraph_events._pump_watchdog",
        ),
    ),
    "memory": AlwaysOn(
        "Memory is part of chat (N18: chat, with its memory): what a person told Kazma, kept in "
        "their space.",
        pages=("/memory",),
        routes=("/memory$", "/memory/you$", "/api/memory", "/api/import/history"),
        tools=frozenset({
            "memory_admin", "memory_delete_entity", "memory_forget_mentions", "memory_invalidate",
            "memory_link_entities", "memory_list_beliefs", "memory_list_entities",
            "memory_merge_entities", "memory_purge_empty_entities", "memory_read", "memory_list",
            "memory_search", "memory_store",
        }),
        commands=frozenset({"memory"}),
        background=_loops(
            "kazma-core/kazma_core/memory/task_queue.py::_MemoryWorker._run_lease_renewal",
            "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_backup_export_scheduler._loop",
            "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_commitment_gc_scheduler._loop",
            "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_macro_sleep_scheduler._loop",
            "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_reconsolidation_scheduler._loop",
            "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_session_purge_scheduler._loop",
        ),
    ),
    "activity": AlwaysOn(
        "Activity: what was done in a person's space, under each member's name (S2-09); its "
        "members' own record, which nothing could take away from them.",
        pages=("/activity",),
        routes=("/activity$", "/api/activity$"),
    ),
    "account": AlwaysOn(
        "My account: a person's own sign-in, sessions, appearance, notifications and alerts.",
        routes=("/api/auth/me$", "/api/account", "/api/settings/account", "/api/settings/appearance$",
                "/api/settings/shortcuts", "/api/push", "/api/notifications/turn-complete$",
                "/api/alerts", "/api/problems"),
    ),
}

#: The install's own: the admin's tools and commands (never a person's
#: feature, S1-20 and S1-25) and Kazma's own loops. id -> why.
INSTALL: dict[str, str] = {
    # The admin's commands: they change Kazma for everyone
    # (slash_commands.changes_global_config).
    "command:config": "the admin's: Kazma's settings for everyone",
    "command:personality": "the admin's: the agent's personality for everyone",
    # Kazma's own loops: maintenance, its connections and its watchers.
    **{f"loop:{name}": "Kazma's own work, for the install" for name in (
        "kazma-core/kazma_core/backup/restore_drill.py::drill_scheduler",
        "kazma-core/kazma_core/db/availability.py::wait_while_away",
        "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._sweep_stranded_intakes",
        "kazma-core/kazma_core/documents/retention.py::start_document_maintenance_loop._loop",
        "kazma-core/kazma_core/lifecycle_notifier.py::_settled",
        "kazma-core/kazma_core/mcp/reconnect.py::MCPReconnector.run",
        "kazma-core/kazma_core/observability/alerts.py::_health_check_loop",
        "kazma-core/kazma_core/observability/daily_digest.py::digest_scheduler",
        "kazma-core/kazma_core/observability/firing_ledger.py::ledger_scheduler",
        "kazma-core/kazma_core/observability/loop_stall.py::start_stall_watchdog._heartbeat",
        "kazma-core/kazma_core/runs/supervisor.py::_loop",
        "kazma-core/kazma_core/swarm/engine.py::SwarmEngine.start_maintenance_loop._loop",
        "kazma-core/kazma_core/telemetry.py::HardwareMonitor.stream",
        "kazma-core/kazma_core/time_travel.py::start_snapshot_maintenance_loop._loop",
        "kazma-core/kazma_core/web_acquire/canary.py::_loop",
        "kazma-core/kazma_core/web_acquire/monitor.py::_loop",
        "kazma-gateway/kazma_gateway/adapters/discord.py::DiscordAdapter._heartbeat",
        "kazma-gateway/kazma_gateway/adapters/slack.py::SlackAdapter._listen_polling",
        "kazma-gateway/kazma_gateway/adapters/slack.py::SlackAdapter._listen_socket_mode",
        "kazma-gateway/kazma_gateway/adapters/telegram_send.py::send_chunks_with_retry",
        "kazma-gateway/kazma_gateway/chat_adapters.py::ChatAdapters.first_outcome",
        "kazma-gateway/kazma_gateway/gateway.py::GatewayManager._outbox_loop",
        "kazma-gateway/kazma_gateway/gateway.py::RateLimiter.acquire",
        "kazma-ui/kazma_ui/app.py::KazmaAppBuilder._start_background_loops._heartbeat_loop",
        "kazma-ui/kazma_ui/hitl_timeout.py::_watchdog_loop",
        "kazma-ui/kazma_ui/read_only_mode.py::create_read_only_app._watch",
        "kazma-ui/kazma_ui/turn_liveness.py::_loop",
    )},
}


def _development_tools() -> frozenset[str]:
    from kazma_core.spaces.tools import DEVELOPMENT

    return DEVELOPMENT


def _feature_of_tool(name: str) -> str | None:
    """The feature the tool *name* belongs to; None for an always-on tool and
    the admin's own (whose rule is S1-20's)."""
    for feature in FEATURES.values():
        if name in feature.tools:
            return feature.id
    for always in ALWAYS_ON.values():
        if name in always.tools:
            return None
    from kazma_core.spaces.tools import ADMIN

    if name in ADMIN:
        return None
    # Development's own, and every tool in no group (an MCP server's): host,
    # never guessed safe (S1-20).
    return "development"


def _features_of_tool(name: str) -> tuple[str, ...]:
    """Every feature the tool *name* needs: its own and :data:`ALSO_NEEDS`."""
    own = _feature_of_tool(name)
    return ((own,) if own else ()) + ALSO_NEEDS.get(name, ())


def _command_head(text: str) -> str:
    """The command a message starts with, as the chat apps' handlers read it:
    the first word without its "/" and a bot's name ("/kb@kazma_bot"),
    lower case; "" for a message that is no command."""
    words = str(text or "").strip().split(maxsplit=1)
    if not words or not words[0].startswith("/"):
        return ""
    return words[0][1:].split("@", 1)[0].lower()


def _feature_of_command(name: str) -> str | None:
    """The feature of the chat command *name* (without its "/"); None for an
    always-on or an admin's command. A name that only starts with a
    feature's command ("kbx", "researchfoo") is that feature's, as the chat
    apps' handlers read it by prefix; an always-on command's own name is
    never a feature's."""
    name = str(name or "").lower().lstrip("/").split("@", 1)[0]
    if not name:
        return None
    for feature in FEATURES.values():
        if name in feature.commands:
            return feature.id
    if any(name in always.commands for always in ALWAYS_ON.values()) or f"command:{name}" in INSTALL:
        return None
    best: tuple[int, str] | None = None
    for feature in FEATURES.values():
        for command in feature.commands:
            if name.startswith(command) and (best is None or len(command) > best[0]):
                best = (len(command), feature.id)
    return best[1] if best else None


# ── Who has what ────────────────────────────────────────────────────────


class FeatureRefused(ValueError):
    """A grant that is refused; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: The policy key of the tools the admin switched off for a person (S2-01).
TOOLS_OFF_POLICY = "tools_off"


@dataclass(frozen=True)
class Grants:
    """The features one person has (``on``), the single tools switched off
    for them inside those features (``tools_off``, S2-01), and whose they are."""

    account: str
    on: frozenset[str]
    admin: bool = False
    tools_off: frozenset[str] = frozenset()
    #: The admin disabled the person (S2-07): nothing is theirs to use, and
    #: their work waits; ``on`` keeps their switches as stored.
    disabled: bool = False
    #: The identity store could not be read: nothing is known about them, so
    #: nothing is theirs to use now (fail closed, the S2-01 second review).
    unreadable: bool = False

    def has(self, feature: str) -> bool:
        return not self.disabled and not self.unreadable and feature in self.on


def grants_of(registry: dict[str, Any], account_id: str) -> Grants:
    """What *account_id* has, read from *registry* (no I/O)."""
    account_id = str(account_id or "")
    accounts = registry.get("accounts") or {}
    policy = (registry.get("policies") or {}).get(account_id) or {}
    policy = policy if isinstance(policy, dict) else {}
    held_development = policy.get("development")
    if account_id not in accounts:
        return Grants(account_id, frozenset())
    admin = account_id == OWNER_ACCOUNT_ID or str((accounts.get(account_id) or {}).get("role")) == "admin"
    if admin:
        on = set(FEATURES)
        if held_development is False:
            on.discard("development")
        return Grants(account_id, frozenset(on), admin=True)
    stored = policy.get("features") if isinstance(policy.get("features"), dict) else {}
    on = set()
    for fid, feature in FEATURES.items():
        if fid == "development":
            if held_development is True:
                on.add(fid)
            continue
        value = stored.get(fid)
        if (value if isinstance(value, bool) else feature.new_member):
            on.add(fid)
    off = policy.get(TOOLS_OFF_POLICY)
    tools_off = frozenset(str(t) for t in off if t) if isinstance(off, (list, tuple)) else frozenset()
    disabled = (accounts.get(account_id) or {}).get("enabled") is False
    return Grants(account_id, frozenset(on), tools_off=tools_off, disabled=disabled)


def grants_here() -> Grants | None:
    """What the person of the work bound now has, or None when no rule
    applies (Spaces not enforced, or no person bound). Blocking."""
    if not spaces_enforced():
        return None
    bound = current_scope()
    if bound is None or not bound.asker:
        return None
    from kazma_core.spaces import identity

    return grants_of(identity.read_registry(), bound.asker)


def _english(feature: str) -> str:
    return NAMES.get(feature, (feature, feature))[0]


def _refusal(feature: str, lang: str = "en") -> str:
    """The refusal of *feature* in *lang* ("en", "ar", or "both": Arabic
    first, for work no message of the person's says the language of)."""
    english, arabic = NAMES.get(feature, (feature, feature))
    ar = f"غير متاح لك: ميزة «{arabic}» غير مفعّلة. يفعّلها المسؤول من الإعدادات ← الأشخاص."
    en = f"Not available to you: {english} is off. The admin turns it on in Settings → People."
    return ar if lang == "ar" else en if lang == "en" else f"{ar}\n{en}"


def _lang_of(user_text: str | None) -> str:
    from kazma_core.language_lock import detect_user_language

    found = detect_user_language(user_text or "") if user_text else ""
    return found if found in ("en", "ar") else "both"


def _disabled_refusal(lang: str = "both") -> str:
    """Why a disabled person's work does not run now (S2-07), in *lang*
    ("en", "ar", or "both": Arabic first)."""
    ar = "غير متاح الآن: حساب هذا الشخص معطّل، فأعماله تنتظر حتى يفعّله المسؤول من الإعدادات ← الأشخاص."
    en = ("Not available now: this person's account is disabled, so their work waits until the admin "
          "enables them in Settings → People.")
    return ar if lang == "ar" else en if lang == "en" else f"{ar}\n{en}"


def tool_refusal(name: str, grants: Grants | None = None) -> str | None:
    """Why the tool *name* is not for the person of the work bound now, or
    None. With *grants* none (no rule), never refused here. Said to the
    model, which tells the person in their language."""
    if grants is None:
        return None
    if grants.unreadable:
        # Who may use what cannot be read (the accounts store is away): no
        # tool runs on a guess, an always-on one included (the S2-01 second
        # review: a switched-off chat or memory tool ran while it was away).
        return (f"Tool '{name}' is not run now: Kazma cannot read who may use it (the accounts store is "
                "away). Tell the user to try again in a moment.")
    if grants.disabled:
        return f"Tool '{name}' is not run. " + _disabled_refusal("en")
    for feature in _features_of_tool(name):
        if not grants.has(feature):
            return (f"Tool '{name}' is not available to this person: {_english(feature)} is off for "
                    "them. Tell the user the admin turns it on in Settings → People.")
    if name in grants.tools_off:
        # A single tool the admin switched off for them (S2-01).
        return (f"Tool '{name}' is switched off for this person. Tell the user the admin turns it "
                "on in Settings → People.")
    return None


def command_refusal(text: str, lang: str | None = None) -> str | None:
    """Why the chat command that *text* starts with refuses for the person
    of the work bound now, or None: in *lang* ("en" or "ar"), else in the
    message's own language. Blocking (one identity read, only for a command
    of a feature)."""
    feature = _feature_of_command(_command_head(text))
    if feature is None:
        return None
    grants = grants_here()
    if grants is None or grants.has(feature):
        return None
    return _refusal(feature, lang or _lang_of(text))


def feature_refusal(feature: str, user_text: str | None = None) -> str | None:
    """Why *feature* is not for the person of the work bound now, in the
    language of *user_text* (a chat app's command handler, once it knows the
    message is its own), or None. Blocking (one identity read)."""
    grants = grants_here()
    if grants is None or grants.has(feature):
        return None
    return _refusal(feature, _lang_of(user_text))


def intent_refusal(handler: str) -> str | None:
    """Whether the intent engine may start *handler*'s work for the person
    of the turn: None, or the feature it needs that they lack. Blocking."""
    needed = INTENT_HANDLERS.get(str(handler or ""), ())
    if not needed:
        return None
    grants = grants_here()
    if grants is None:
        return None
    return next((feature for feature in needed if not grants.has(feature)), None)


def work_refusal(feature: str, account: str | None) -> str | None:
    """Whether background work of *feature* may run for *account* now (the
    check made again when the work runs). None: it may. Blocking. Raises
    ``identity.IdentityUnavailable`` when the store cannot be read: the
    caller holds the work."""
    if not spaces_enforced() or not account:
        return None
    from kazma_core.spaces import identity

    registry = identity.read_registry()
    if str(account) not in (registry.get("accounts") or {}):
        # Not a person: Kazma's own work, or a record made before Spaces
        # (a document's "operator"); the space's own rules decide it.
        return None
    grants = grants_of(registry, account)
    if grants.has(feature):
        return None
    return _disabled_refusal() if grants.disabled else _refusal(feature, "both")


def tool_off_text(tool: str, lang: str = "both") -> str:
    """The refusal of a tool switched off for the person (S2-01), in *lang*
    ("en", "ar", or "both": Arabic first), said to the person."""
    ar = f"غير متاح لك: أداة «{tool}» مطفأة لك. يشغّلها المسؤول من الإعدادات ← الأشخاص."
    en = f"Not available to you: the tool {tool} is switched off for you. The admin turns it on in Settings → People."
    return ar if lang == "ar" else en if lang == "en" else f"{ar}\n{en}"


def tool_work_refusal(tool: str, account: str | None, lang: str = "both") -> str | None:
    """Whether the work of the tool *tool* may run for *account* now, at a
    door that is not a call of the tool (:data:`TOOL_ROUTE_DOORS`,
    :data:`TOOL_WORK_DOORS`): None when it may, else the refusal -- the
    account disabled, the tool's feature off, or the tool switched off for
    them (S2-01). Asked when the work runs: a booked post asks again when it
    fires. Raises ``identity.IdentityUnavailable`` when the store cannot be
    read: the caller holds the work. None always while Spaces is not
    enforced, and for an account that is no person (Kazma's own work).
    Blocking."""
    if not spaces_enforced() or not account:
        return None
    from kazma_core.spaces import identity

    registry = identity.read_registry()
    if str(account) not in (registry.get("accounts") or {}):
        return None
    grants = grants_of(registry, account)
    if grants.disabled:
        return _disabled_refusal(lang)
    for feature in _features_of_tool(tool):
        if not grants.has(feature):
            return _refusal(feature, lang)
    return tool_off_text(tool, lang) if tool in grants.tools_off else None


def tool_door_refusal(tool: str, user_text: str | None = None) -> str | None:
    """:func:`tool_work_refusal` for the person bound now (a request, a
    turn), in the language of *user_text* (both when none). Blocking."""
    from kazma_core.spaces.scope import bound_asker

    return tool_work_refusal(tool, bound_asker() or "", _lang_of(user_text))


# ── The page being drawn ────────────────────────────────────────────────

_page_grants: contextvars.ContextVar[Grants | None] = contextvars.ContextVar(
    "kazma_page_grants", default=None
)


def remember_page_grants(grants: Grants | None) -> contextvars.Token:
    """Keep what the person of the page being drawn has (the tenant
    middleware), so the sidebar leaves out what is off with no read."""
    return _page_grants.set(grants)


def forget_page_grants(token: contextvars.Token) -> None:
    _page_grants.reset(token)


def feature_here(feature: str) -> bool:
    """Whether the page being drawn shows *feature*'s menu entry. No I/O: as
    before Spaces while it is not enforced; once enforced, what the tenant
    middleware worked out."""
    if not spaces_enforced():
        return True
    grants = _page_grants.get()
    return grants is not None and grants.has(feature)


# ── Changing a person's features ────────────────────────────────────────


def checked_change(account_id: str, changes: dict[str, Any], registry: dict[str, Any]) -> dict[str, bool]:
    """*changes* (feature -> on) as a person's switches, refused
    (:class:`FeatureRefused`): an unknown feature, a value that is not a
    switch, or a change of the admin's other than Development."""
    out: dict[str, bool] = {}
    admin = grants_of(registry, account_id).admin
    for fid, on in changes.items():
        if fid not in FEATURES:
            raise FeatureRefused("unknown_feature", f"There is no feature {fid!r}.")
        if not isinstance(on, bool):
            raise FeatureRefused("bad_switch", "A feature is switched on or off.")
        if admin and fid != "development":
            raise FeatureRefused("admin_has_every_feature",
                                 "The admin has every feature; only Development can be switched off.")
        out[fid] = on
    return out


def apply_change(registry: dict[str, Any], account_id: str, changes: dict[str, bool]) -> None:
    """Write checked *changes* into *registry*'s policies (in place)."""
    policy = dict(registry["policies"].get(account_id) or {})
    stored = dict(policy.get("features") or {}) if isinstance(policy.get("features"), dict) else {}
    for fid, on in changes.items():
        if fid == "development":
            policy["development"] = on  # the switch identity.holds_development reads
        else:
            stored[fid] = on
    if stored:
        policy["features"] = stored
    registry["policies"][account_id] = policy


def _switchable_tools() -> dict[str, frozenset[str]]:
    """The tools the admin may switch off for a person, by where they come
    from: each feature's tools, and the always-on ones (chat, memory), a
    person may have (S2-01). The admin's own tools and Kazma's own are not
    a person's to have, so not theirs to switch. No I/O."""
    out: dict[str, frozenset[str]] = {fid: f.tools for fid, f in FEATURES.items() if f.tools}
    for key, always in ALWAYS_ON.items():
        if always.tools:
            out[key] = always.tools
    return out


def checked_tools(account_id: str, off: Any, registry: dict[str, Any]) -> list[str]:
    """*off* (a list of tool names) as the tools switched off for a person,
    each once, refused (:class:`FeatureRefused`): not a list of names, a
    tool no person can have, or the admin (who has every tool)."""
    if not isinstance(off, (list, tuple)) or not all(isinstance(t, str) for t in off):
        raise FeatureRefused("bad_tools", "Name the tools to switch off, as a list.")
    if grants_of(registry, account_id).admin:
        raise FeatureRefused("admin_has_every_tool", "The admin has every tool.")
    known = frozenset().union(*_switchable_tools().values())
    unknown = sorted({t for t in off if t not in known})
    if unknown:
        raise FeatureRefused("unknown_tool", f"There is no tool a person can have named {unknown[0]!r}.")
    return sorted(set(off))


def apply_tools(registry: dict[str, Any], account_id: str, off: list[str]) -> None:
    """Write checked tools *off* into *registry*'s policies (in place)."""
    policy = dict(registry["policies"].get(account_id) or {})
    if off:
        policy[TOOLS_OFF_POLICY] = list(off)
    else:
        policy.pop(TOOLS_OFF_POLICY, None)
    registry["policies"][account_id] = policy


def listed_tools(grants: Grants) -> list[dict[str, Any]]:
    """The switchable tools of what *grants* holds (a feature that is off
    has none to switch), each with whether it is on for them: ``[{"from":
    feature or always-on key, "tools": [{"name", "on"}]}]``."""
    out: list[dict[str, Any]] = []
    for key, tools in _switchable_tools().items():
        if key in FEATURES and key not in grants.on:
            continue
        out.append({"from": key, "tools": [{"name": name, "on": name not in grants.tools_off}
                                           for name in sorted(tools)]})
    return out


def listed(grants: Grants) -> list[dict[str, Any]]:
    """Every feature with whether *grants* holds it, in the table's order."""
    return [{"id": fid, "group": f.group, "on": fid in grants.on,
             "fixed": grants.admin and fid != "development"} for fid, f in FEATURES.items()]


# ── The precondition of a second account (S1-33) ────────────────────────


def precondition() -> tuple[bool, str]:
    """Whether the rule applies to every person: every person but the admin
    has the features the admin gave them (N18's for the rest), and every
    stored switch names a feature of the table. Raises when the identity
    store cannot be read (unknown is never passed). Blocking."""
    from kazma_core.spaces import identity

    registry = identity.read_registry()
    unknown: list[str] = []
    for account_id, policy in (registry.get("policies") or {}).items():
        stored = (policy or {}).get("features") if isinstance(policy, dict) else None
        if isinstance(stored, dict):
            unknown += [f"{account_id}: {fid}" for fid in stored if fid not in FEATURES]
        off = (policy or {}).get(TOOLS_OFF_POLICY) if isinstance(policy, dict) else None
        if off is not None and not isinstance(off, (list, tuple)):
            unknown.append(f"{account_id}: {TOOLS_OFF_POLICY} is not a list")
    if unknown:
        return False, "switches name features that do not exist, or cannot be read: " + ", ".join(sorted(unknown))
    defaults = ", ".join(fid for fid, f in FEATURES.items() if f.new_member)
    return True, (f"every person has the features the admin gave them; a new person starts with "
                  f"chat and its memory, and {defaults}")
