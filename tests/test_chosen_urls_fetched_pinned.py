"""An address someone else chose is fetched on the pinned path, or declared.

Security audit 2026-11-15. The rule (AGENTS.md §24B, §32): every fetch of an
address a model, a page, a message or a remote server chose goes only to
public addresses, checked at every redirect hop, the socket pinned to the
checked address. The pinned path is ``web_acquire.http.fetch_public`` (a page
or file) and ``security.pinned_request.pinned_request`` (one request, no
redirect: an OAuth endpoint), and any client built on
``security.ssrf_pin.PinHostAsyncTransport``. The review found four fetches
off that path, each a sibling of one already on it: chat attachments (a
Discord attachment or embed image), MCP OAuth discovery and token requests,
and the generated-image downloads of three image backends. This is the
class gate.

**What is a fetch.** Every request a product module makes with an HTTP
client: a call of ``get``/``post``/``put``/``patch``/``delete``/``head``/
``options`` (URL first), ``request``/``stream``/``build_request`` (URL
second) on a client -- a name or ``self`` attribute bound to ``httpx.Client``
/ ``httpx.AsyncClient`` / ``requests.Session`` / ``aiohttp.ClientSession``,
Kazma's client factories (``get_scraping_client[_sync]``,
``get_http_client``) or a local function returning one, or a parameter
annotated as a client -- and ``httpx.get(...)``, ``requests.get(...)``,
``urllib.request.urlopen(...)`` / ``Request(...)``. Names are resolved
through the module's imports, never matched by spelling alone.

**What is fixed in code.** A URL whose scheme and host are written in the
code: a string literal; an f-string, ``+`` or ``.format`` whose literal start
closes the host (``https://api.example.com/...``) or is a path on the
client's base URL (``/v1/...``); a name every assignment of which is fixed
(module constants, class attributes and constants imported from another
Kazma module included). Anything else -- a parameter, a value read from an
answer, a setting -- is chosen elsewhere.

**Rule.** A fetch on an unpinned client whose URL is not fixed in code is a
finding, and so is a client that follows redirects (``follow_redirects=True``,
or ``get_scraping_client`` without ``follow_redirects=False``: its default
follows). A finding is fixed (moved to the pinned path) or declared with the
reason the address is not someone else's choice -- the owner's own setting,
Kazma's own server, a provider's own host checked in code -- in
``UNFIXED_URLS`` or ``REDIRECTS``. A declaration that matches nothing fails,
so the lists only shrink. The negative controls run the same scanner on the
four fetches as they were.
"""

from __future__ import annotations

import ast
import functools
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Qualified client constructors and factories. The value: the keyword that
#: pins it (``None``: a ``transport=`` built on PinHostAsyncTransport).
CLIENT_FACTORIES: dict[str, str | None] = {
    "httpx.Client": None,
    "httpx.AsyncClient": None,
    "requests.Session": None,
    "aiohttp.ClientSession": None,
    "kazma_core.proxy.client.get_scraping_client": "pin_hosts",
    "kazma_core.proxy.client.get_scraping_client_sync": "pin_hosts",
    "kazma_core.proxy.get_scraping_client": "pin_hosts",
    "kazma_core.proxy.get_scraping_client_sync": "pin_hosts",
    "kazma_core.http_pool.get_http_client": None,
}
#: Factories whose default follows redirects.
FOLLOWS_BY_DEFAULT = {
    "kazma_core.proxy.client.get_scraping_client",
    "kazma_core.proxy.client.get_scraping_client_sync",
    "kazma_core.proxy.get_scraping_client",
    "kazma_core.proxy.get_scraping_client_sync",
}
URL_FIRST = {"get", "post", "put", "patch", "delete", "head", "options"}
URL_SECOND = {"request", "stream", "build_request"}
MODULE_FETCHES = {f"httpx.{m}" for m in URL_FIRST | URL_SECOND} | {
    f"requests.{m}" for m in URL_FIRST | {"request"}
} | {"urllib.request.urlopen", "urllib.request.Request"}
CLIENT_ANNOTATIONS = {"httpx.AsyncClient", "httpx.Client", "aiohttp.ClientSession", "requests.Session"}
_HOST_CLOSED = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]+[/?#]")


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

_OWN_SERVER = "Kazma's own server, at the address and port of this install"
_OWNER_SETTING = "an address the owner set"

#: ``"<file>::<function>"`` -> why its fetch's address is nobody else's choice.
UNFIXED_URLS: dict[str, str] = {
    # Kazma's own server (the CLI, the terminal app, the turn client).
    "kazma-cli/kazma_cli/gateway.py::_request": _OWN_SERVER + " (`kazma gateway`)",
    "kazma-cli/kazma_cli/install.py::_answers": _OWN_SERVER + " (setup waits for it to answer)",
    "kazma-cli/kazma_cli/maintenance.py::_deep_ok": _OWN_SERVER + " (`/health/deep` after an update window)",
    "kazma-core/kazma_core/agent/turn_client.py::stream_chat_turn": _OWN_SERVER + " (a client of its chat stream)",
    "kazma-core/kazma_core/runtime/local_api.py::request_json": _OWN_SERVER + " (runtime.local_api.candidate_api_bases)",
    "kazma-core/kazma_core/runtime/local_api.py::request_json_async": _OWN_SERVER + " (runtime.local_api.candidate_api_bases)",
    "kazma-core/kazma_core/support/problem_report.py::_readiness": _OWN_SERVER + " (http://127.0.0.1:<port>/health/ready)",
    "kazma-tui/kazma_tui/app.py::KazmaTUI._check_pending_approvals": _OWN_SERVER + " (the terminal app's API base)",
    "kazma-tui/kazma_tui/app.py::KazmaTUI._submit_hitl_decision": _OWN_SERVER + " (the terminal app's API base)",
    "kazma-tui/kazma_tui/season_load.py::fetch_season_messages_http": _OWN_SERVER + " (the terminal app's API base)",
    "kazma-tui/kazma_tui/season_load.py::fetch_season_messages_http_async": _OWN_SERVER + " (the terminal app's API base)",
    # Kazma's own release and skill downloads (fixed hosts).
    "kazma-cli/kazma_cli/update.py::_read_release": "api.github.com's release API for Kazma's own repository (_GITHUB_REPO), built in the module",
    "kazma-cli/kazma_cli/update.py::do_pip_update": "the release's wheel and bundle URLs, refused unless they start with _RELEASE_DOWNLOAD_PREFIX (Kazma's own GitHub release downloads); the file is then checked against SHA256SUMS and its Sigstore signature",
    "kazma-cli/kazma_cli/update.py::_download_source": "a release install's source archive and its bundle, refused by _source_archive unless they start with _RELEASE_DOWNLOAD_PREFIX (Kazma's own GitHub release downloads); the archive is then checked against SHA256SUMS, GitHub's digest and its Sigstore signature",
    "kazma-core/kazma_core/agent_skills/installer.py::_download_github_zip": "codeload.github.com / api.github.com zipball URLs the function builds (fixed hosts, the owner's approved owner/repo in the path)",
    "kazma-core/kazma_core/http_tls.py::open_download": "setup's pinned downloads (system_deps, container_engine_setup, embedder tables): every URL is written in those tables and the file is kept only when its size and SHA-256 match",
    # The owner's own settings: backups, memory stores, model servers, voice, tracing.
    "kazma-core/kazma_core/backup/cloud_sync.py::OneDriveSync.list_backups": "Microsoft Graph's drive API, then the @odata.nextLink pages Graph's own answer names, with the owner's own token",
    "kazma-core/kazma_core/backup/cloud_sync.py::S3Sync._multipart_upload": _OWNER_SETTING + ": the S3 endpoint and bucket of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::S3Sync.delete_backup": _OWNER_SETTING + ": the S3 endpoint and bucket of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::S3Sync.list_backups": _OWNER_SETTING + ": the S3 endpoint and bucket of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::S3Sync.stat_file": _OWNER_SETTING + ": the S3 endpoint and bucket of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::S3Sync.test_connection": _OWNER_SETTING + ": the S3 endpoint and bucket of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::S3Sync.upload_file": _OWNER_SETTING + ": the S3 endpoint and bucket of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::WebDAVSync.delete_backup": _OWNER_SETTING + ": the WebDAV server of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::WebDAVSync.list_backups": _OWNER_SETTING + ": the WebDAV server of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::WebDAVSync.test_connection": _OWNER_SETTING + ": the WebDAV server of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::WebDAVSync.upload_file": _OWNER_SETTING + ": the WebDAV server of the owner's offsite backup",
    "kazma-core/kazma_core/backup/cloud_sync.py::_gdrive_upload": "the resumable-upload session Google Drive's own answer opens (its Location), for the owner's own Drive",
    "kazma-core/kazma_core/backup/cloud_sync.py::_onedrive_session_upload": "the upload session Microsoft Graph's own answer opens (uploadUrl), for the owner's own OneDrive",
    "kazma-core/kazma_core/backup/restic_repo.py::_sigv4_request": _OWNER_SETTING + ": the S3 endpoint of the owner's restic repository",
    "kazma-core/kazma_core/hub/cli.py::_http_request": _OWNER_SETTING + ": the skills hub address (`kazma hub`)",
    "kazma-core/kazma_core/memory/backends.py::QdrantVectorBackend.delete": _OWNER_SETTING + ": the Qdrant server of the memory vector backend",
    "kazma-core/kazma_core/memory/backends.py::QdrantVectorBackend.probe": _OWNER_SETTING + ": the Qdrant server of the memory vector backend",
    "kazma-core/kazma_core/memory/backends.py::QdrantVectorBackend.search": _OWNER_SETTING + ": the Qdrant server of the memory vector backend",
    "kazma-core/kazma_core/memory/backends.py::QdrantVectorBackend.similarities": _OWNER_SETTING + ": the Qdrant server of the memory vector backend",
    "kazma-core/kazma_core/memory/backends.py::QdrantVectorBackend.upsert": _OWNER_SETTING + ": the Qdrant server of the memory vector backend",
    "kazma-core/kazma_core/model_registry.py::ModelRegistry.discover": _OWNER_SETTING + ": a provider row's base URL (the model list); a host off the owner's network is checked public with validate_url first",
    "kazma-core/kazma_core/models/discovery.py::_discover_openai_compatible": _OWNER_SETTING + ": a configured provider's base URL",
    "kazma-core/kazma_core/models/discovery.py::check_ollama_health": _OWNER_SETTING + ": the Ollama server address",
    "kazma-core/kazma_core/models/discovery.py::discover_custom_models": _OWNER_SETTING + ": a custom provider's base URL",
    "kazma-core/kazma_core/models/discovery.py::discover_lm_studio_models": _OWNER_SETTING + ": the LM Studio server address",
    "kazma-core/kazma_core/models/discovery.py::discover_ollama_models": _OWNER_SETTING + ": the Ollama server address",
    "kazma-core/kazma_core/settings_manager.py::SettingsManager.compare_models": _OWNER_SETTING + ": configured providers' base URLs",
    "kazma-core/kazma_core/stores/knowledge_ingest.py::_firecrawl_map_site": _OWNER_SETTING + ": the Firecrawl API base, used only when the acquisition policy names firecrawl",
    "kazma-core/kazma_core/tools/read_url.py::_try_firecrawl": _OWNER_SETTING + ": the Firecrawl API base, used only when the acquisition policy names firecrawl",
    "kazma-core/kazma_core/web_acquire/credits.py::refresh_firecrawl_balance": _OWNER_SETTING + ": the Firecrawl API base",
    "kazma-core/kazma_core/web_acquire/search_http.py::_request": "the search backends the acquisition policy names: SearXNG at the owner's address, and Tavily, Bing and Wikipedia at fixed hosts, in the bounded search worker",
    "kazma-core/kazma_core/swarm/tracing.py::OtlpHttpExporter._flush": _OWNER_SETTING + ": the OTLP trace endpoint",
    "kazma-core/kazma_core/voice/stt.py::_nvidia_stt._transcribe": _OWNER_SETTING + ": the NVIDIA speech-to-text endpoint",
    "kazma-core/kazma_core/voice/tts.py::_nvidia_tts_provider._synthesize": _OWNER_SETTING + ": the NVIDIA text-to-speech endpoint",
    "kazma-core/kazma_core/support/problem_report.py::send": _OWNER_SETTING + ": support.report_url, https only, no redirect followed",
    "kazma-core/kazma_core/observability/outside_watch.py::_observer_summary": "the Kazma Watch observer the owner paired (its address is in the pairing file)",
    "kazma-core/kazma_core/observability/outside_watch.py::_post_pairing": "the Kazma Watch observer address the owner gives `kazma observer pair`",
    "kazma-core/kazma_core/observability/outside_watch.py::_signed_post": "the Kazma Watch observer the owner paired (its address is in the pairing file)",
    "kazma-core/kazma_core/observability/watch_setup.py::WatchSetup._probe": "the address the owner types in `kazma observer setup`, no redirect followed",
    "kazma-core/kazma_core/security/oidc.py::fetch_discovery": _OWNER_SETTING + ": the identity provider's issuer (KAZMA_OIDC_ISSUER)",
    "kazma-core/kazma_core/security/oidc.py::exchange_code": "the token endpoint of the identity provider the owner configured, from that provider's own discovery document",
    "kazma-core/kazma_core/security/oidc.py::_userinfo_claims": "the userinfo endpoint of the identity provider the owner configured, from that provider's own discovery document",
    "kazma-ui/kazma_ui/settings.py::SettingsRouterBuilder._build_models_routes.api_test_model": "the base URL an administrator types in Settings to test a model (an admin route)",
    "kazma-ui/kazma_ui/setup_api.py::_find_local_servers.fetch": "loopback model servers on their usual ports (the setup guide's search)",
    "kazma-ui/kazma_ui/setup_api.py::_network_server.fetch": "a server the owner names in the setup guide, asked only after resolves_to_own_network says it is on their own network",
    # A provider's own API, at its fixed base.
    "kazma-core/kazma_core/x_api/client.py::XClient._request": "api.x.com (API_HOST) and a path Kazma's own methods build, each starting with '/'",
    "kazma-skills/kazma_skills/native/calendar/backends/google_calendar.py::GoogleCalendarBackend._request": "callers pass Google Calendar API URLs built on the module's _API base",
    "kazma-skills/kazma_skills/native/calendar/backends/outlook_calendar.py::OutlookCalendarBackend._request": "callers pass Microsoft Graph URLs built on the module's _GRAPH base",
    "kazma-skills/kazma_skills/native/email_manager/backends/microsoft_graph.py::MicrosoftGraphBackend._request": "callers pass Microsoft Graph paths and the @odata.nextLink pages Graph's own answer names",
    "kazma-gateway/kazma_gateway/adapters/discord_diagnose.py::_call": "Discord API paths on the Test's client, whose base URL is discord.com's API",
    "kazma-gateway/kazma_gateway/adapters/downloads.py::bounded_download": "Telegram's own file host: the adapter builds https://api.telegram.org/file/bot<token>/<path> from getFile; no redirect followed",
    # Slack's own hosts, checked in code (downloads.slack_owned_url).
    "kazma-gateway/kazma_gateway/adapters/slack.py::SlackAdapter._listen_socket_mode": "the interaction's response_url, only when slack_commands.safe_response_url says it is Slack's own https interaction reply address (hooks.slack.com/actions/...)",
    "kazma-gateway/kazma_gateway/adapters/slack.py::SlackAdapter._prefetch_private_files": "a Slack file address with the bot token, only when slack_owned_url says it is an https address on Slack's own hosts (any other address is left to the pinned public download, without the token)",
    "kazma-gateway/kazma_gateway/adapters/slack.py::SlackAdapter._send_attachment": "the upload URL files.getUploadURLExternal answers, only when slack_owned_url says it is an https address on Slack's own hosts",
    "kazma-gateway/kazma_gateway/adapters/slack_stt.py::send_voice_reply": "the upload URL files.getUploadURLExternal answers, only when slack_owned_url says it is an https address on Slack's own hosts",
    # The pinned path's own exception.
    "kazma-core/kazma_core/mcp/oauth.py::_oauth_request": "only the MCP server's own origin, when the owner configured that server on this machine or their network (_check_address refuses any other address first, and the rest go through pinned_request); no redirect followed",
}

#: ``"<file>::<function>"`` -> why following redirects there is safe.
REDIRECTS: dict[str, str] = {
    "kazma-cli/kazma_cli/update.py::_read_release": "api.github.com, Kazma's own repository; httpx drops the Authorization header on a redirect to another origin",
    "kazma-cli/kazma_cli/update.py::do_pip_update": "GitHub's release download redirects to its own object store; the file is checked against SHA256SUMS and its Sigstore signature before it is installed",
    "kazma-cli/kazma_cli/update.py::_download_source": "GitHub's release download redirects to its own object store; the source archive is checked against SHA256SUMS, GitHub's digest and its Sigstore signature before it is laid down",
    "kazma-core/kazma_core/agent_skills/installer.py::_download_github_zip": "api.github.com's zipball redirects to codeload.github.com; no credential is sent",
    "kazma-core/kazma_core/settings_manager.py::SettingsManager.check_updates": "api.github.com's release API for Kazma's own repository; no credential is sent",
}


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    key: str
    kind: str  # "url" or "redirects"
    line: int
    detail: str


def _product_files() -> list[str]:
    # --others: a new module counts before it is committed (as the debt ratchet).
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "kazma-*/kazma_*"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(
        rel for rel in listed
        if rel.endswith(".py") and "/tests/" not in rel and (ROOT / rel).exists()
    )


def _module_path(module: str) -> Path | None:
    head, _, rest = module.partition(".")
    if not head.startswith("kazma_"):
        return None
    base = ROOT / head.replace("_", "-") / head
    sub = rest.replace(".", "/") if rest else ""
    for candidate in (base / f"{sub}.py", base / sub / "__init__.py") if sub else (base / "__init__.py",):
        if candidate.exists():
            return candidate
    return None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _unwrap(node: ast.AST) -> ast.AST:
    while isinstance(node, ast.Await):
        node = node.value
    return node


@functools.lru_cache(maxsize=None)
def _module_constants(module: str) -> dict[str, ast.AST]:
    path = _module_path(module)
    if path is None:
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}
    return _top_level_assignments(tree)


def _top_level_assignments(tree: ast.Module) -> dict[str, ast.AST]:
    found: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            found.setdefault(node.target.id, []).append(node.value)
    return {name: values[0] for name, values in found.items() if len(values) == 1}


class _Module:
    """One module: its imports, constants and local client factories."""

    def __init__(self, source: str, rel: str) -> None:
        self.rel = rel
        self.tree = ast.parse(source)
        self.imports: dict[str, str] = {}
        package = ".".join(Path(rel).with_suffix("").parts[1:])
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.imports[alias.asname] = alias.name
                    else:
                        head = alias.name.split(".")[0]
                        self.imports.setdefault(head, head)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    parent = parts[: len(parts) - node.level] if not rel.endswith("__init__.py") else parts[: len(parts) - node.level + 1]
                    base = ".".join([*parent, base] if base else parent)
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{base}.{alias.name}"
        self.constants = _top_level_assignments(self.tree)
        self.class_constants: dict[str, ast.AST] = {}
        self.class_clients: dict[str, dict[str, bool]] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                        for prefix in ("self", "cls", node.name):
                            self.class_constants[f"{prefix}.{sub.targets[0].id}"] = sub.value
        self.local_factories = self._local_factories()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                attrs: dict[str, bool] = {}
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                        value = sub.value
                        targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                        pinned = self.client_call(value) if value is not None else None
                        if pinned is None:
                            continue
                        for target in targets:
                            if isinstance(target, ast.Attribute) and _dotted(target.value) == "self":
                                attrs[f"self.{target.attr}"] = pinned
                self.class_clients[node.name] = attrs

    def qualify(self, dotted: str | None) -> str | None:
        if not dotted:
            return None
        head, _, rest = dotted.partition(".")
        base = self.imports.get(head)
        if base is None:
            return dotted
        return f"{base}.{rest}" if rest else base

    def _local_factories(self) -> dict[str, bool]:
        factories: dict[str, bool] = {}
        for _ in range(3):
            for node in ast.walk(self.tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Return) and sub.value is not None:
                            pinned = self._client_call(sub.value, factories)
                            if pinned is not None:
                                factories.setdefault(node.name, pinned)
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
                    pinned = self._client_call(node.value.body, factories)
                    if pinned is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                factories.setdefault(target.id, pinned)
        return factories

    def client_call(self, node: ast.AST) -> bool | None:
        """``None``: not a client; else whether the client is pinned."""
        return self._client_call(node, self.local_factories)

    def _client_call(self, node: ast.AST, factories: dict[str, bool]) -> bool | None:
        node = _unwrap(node)
        if isinstance(node, ast.BoolOp):
            answers = [self._client_call(value, factories) for value in node.values]
            answers = [a for a in answers if a is not None]
            return all(answers) if answers else None
        if not isinstance(node, ast.Call):
            return None
        name = _dotted(node.func)
        if name in factories:
            return factories[name]
        qualified = self.qualify(name)
        if qualified not in CLIENT_FACTORIES:
            return None
        keyword = CLIENT_FACTORIES[qualified]
        for kw in node.keywords:
            if keyword and kw.arg == keyword and not (isinstance(kw.value, ast.Constant) and kw.value.value is None):
                return True
            if kw.arg == "transport" and self._is_pin_transport(kw.value):
                return True
        return False

    def _is_pin_transport(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            return (self.qualify(_dotted(node.func)) or "").endswith("PinHostAsyncTransport")
        if isinstance(node, ast.Name):
            for sub in ast.walk(self.tree):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == node.id for t in sub.targets
                ) and isinstance(sub.value, ast.Call):
                    if (self.qualify(_dotted(sub.value.func)) or "").endswith("PinHostAsyncTransport"):
                        return True
        return False

    def follows_redirects(self, call: ast.Call) -> bool:
        for kw in call.keywords:
            if kw.arg == "follow_redirects":
                return not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
        name = _dotted(call.func)
        return self.qualify(name) in FOLLOWS_BY_DEFAULT


class _Scope:
    """What one function knows: its constants, clients and assignments."""

    def __init__(self, module: _Module, parent: "_Scope | None") -> None:
        self.module = module
        self.parent = parent
        self.assigned: dict[str, list[ast.AST]] = {}
        self.params: set[str] = set()
        self.clients: dict[str, bool] = {}

    def lookup_values(self, name: str) -> list[ast.AST] | None:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.params:
                return None
            if name in scope.assigned:
                return scope.assigned[name]
            scope = scope.parent
        if name in self.module.constants:
            return [self.module.constants[name]]
        qualified = self.module.imports.get(name)
        if qualified and "." in qualified:
            module, _, attr = qualified.rpartition(".")
            value = _module_constants(module).get(attr)
            if value is not None:
                return [value]
        return None

    def client(self, name: str) -> bool | None:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.clients:
                return scope.clients[name]
            scope = scope.parent
        return None


def _prefix(node: ast.AST, scope: _Scope, depth: int = 0) -> tuple[str, bool]:
    """The literal start of a URL expression, and whether it is all of it."""
    if depth > 8:
        return "", False
    node = _unwrap(node)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.JoinedStr):
        text = ""
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                text += value.value
                continue
            inner, complete = _prefix(value.value, scope, depth + 1) if isinstance(value, ast.FormattedValue) else ("", False)
            text += inner
            if not complete:
                return text, False
        return text, True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, complete = _prefix(node.left, scope, depth + 1)
        if not complete:
            return left, False
        right, complete = _prefix(node.right, scope, depth + 1)
        return left + right, complete
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        template, _complete = _prefix(node.func.value, scope, depth + 1)
        return template.split("{", 1)[0], False
    if isinstance(node, ast.Name):
        values = scope.lookup_values(node.id)
        if not values:
            return "", False
        answers = [_prefix(value, scope, depth + 1) for value in values]
        if all(_closed(text, complete) for text, complete in answers):
            return answers[0][0], all(complete for _t, complete in answers)
        return "", False
    if isinstance(node, ast.Attribute):
        value = scope.module.class_constants.get(_dotted(node) or "")
        if value is None:
            dotted = scope.module.qualify(_dotted(node))
            if dotted and "." in dotted:
                module, _, attr = dotted.rpartition(".")
                value = _module_constants(module).get(attr)
        if value is not None:
            return _prefix(value, scope, depth + 1)
    return "", False


def _closed(text: str, complete: bool) -> bool:
    if complete:
        return True
    if text.startswith("/") and not text.startswith("//"):
        return True
    return bool(_HOST_CLOSED.match(text))


def _url_argument(call: ast.Call, method: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == "url":
            return kw.value
    index = 1 if method in URL_SECOND else 0
    return call.args[index] if len(call.args) > index else None


def _body_nodes(fn: ast.AST):
    """The nodes of *fn*'s own body, nested functions left out."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def scan_source(source: str, rel: str) -> list[Finding]:
    module = _Module(source, rel)
    findings: list[Finding] = []

    def visit(fn: ast.AST, qual: str, parent: _Scope | None, class_name: str | None) -> None:
        scope = _Scope(module, parent)
        args = fn.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is None:
                continue
            scope.params.add(arg.arg)
            annotation = module.qualify(_dotted(arg.annotation)) if arg.annotation is not None else None
            if annotation in CLIENT_ANNOTATIONS:
                scope.clients[arg.arg] = False
        if class_name:
            for attr, pinned in module.class_clients.get(class_name, {}).items():
                scope.clients[attr] = pinned
        nodes = list(_body_nodes(fn))
        for node in nodes:
            if isinstance(node, ast.Assign):
                pinned = module.client_call(node.value)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        scope.assigned.setdefault(target.id, []).append(node.value)
                        if pinned is not None:
                            scope.clients[target.id] = pinned
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                scope.assigned.setdefault(node.target.id, []).append(node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                for name in ast.walk(node.target):
                    if isinstance(name, ast.Name):
                        scope.params.add(name.id)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    pinned = module.client_call(item.context_expr)
                    if isinstance(item.optional_vars, ast.Name):
                        if pinned is not None:
                            scope.clients[item.optional_vars.id] = pinned
                        else:
                            scope.params.add(item.optional_vars.id)
        key = f"{rel}::{qual}"
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            if module.client_call(node) is not None and module.follows_redirects(node):
                findings.append(Finding(key, "redirects", node.lineno, ast.unparse(node.func)))
            func = node.func
            qualified = module.qualify(_dotted(func))
            url: ast.AST | None = None
            pinned: bool | None = None
            if qualified in MODULE_FETCHES:
                method = qualified.rsplit(".", 1)[1]
                if method in ("urlopen", "Request"):
                    url = node.args[0] if node.args else None
                    if isinstance(url, ast.Call) and module.qualify(_dotted(url.func)) == "urllib.request.Request":
                        url = None  # judged at the Request
                    elif isinstance(url, ast.Name) and any(
                        isinstance(v, ast.Call) and module.qualify(_dotted(v.func)) == "urllib.request.Request"
                        for v in scope.lookup_values(url.id) or []
                    ):
                        url = None
                else:
                    url = _url_argument(node, method)
                pinned = False
                if any(kw.arg == "follow_redirects" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                ) for kw in node.keywords) or qualified.startswith("requests."):
                    findings.append(Finding(key, "redirects", node.lineno, qualified))
            elif isinstance(func, ast.Attribute) and func.attr in URL_FIRST | URL_SECOND:
                receiver = _unwrap(func.value)
                name = _dotted(receiver)
                pinned = scope.client(name) if name else None
                if pinned is None:
                    pinned = module.client_call(receiver)
                if pinned is None:
                    continue
                url = _url_argument(node, func.attr)
                if any(kw.arg == "follow_redirects" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                ) for kw in node.keywords):
                    findings.append(Finding(key, "redirects", node.lineno, ast.unparse(func)))
            else:
                continue
            if url is None or pinned:
                continue
            text, complete = _prefix(url, scope)
            if not _closed(text, complete):
                findings.append(Finding(key, "url", node.lineno, ast.unparse(url)[:80]))

        for child in ast.iter_child_nodes(fn):
            walk(child, qual + ".", scope, class_name)

    def walk(node: ast.AST, prefix: str, parent: _Scope | None, class_name: str | None) -> None:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                walk(child, f"{prefix}{node.name}.", parent, node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit(node, f"{prefix}{node.name}", parent, class_name)
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(child, prefix, parent, class_name)

    for node in module.tree.body:
        walk(node, "", None, None)
    return findings


@functools.lru_cache(maxsize=1)
def _product_findings() -> tuple[Finding, ...]:
    out: list[Finding] = []
    for rel in _product_files():
        source = (ROOT / rel).read_text(encoding="utf-8")
        try:
            out.extend(scan_source(source, rel))
        except SyntaxError:
            continue
    return tuple(out)


def _report(findings, declared: dict[str, str]) -> list[str]:
    return sorted({
        f"{f.key} (line {f.line}): {f.detail}" for f in findings if f.key not in declared
    })


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_every_chosen_url_is_fetched_pinned_or_declared() -> None:
    missing = _report([f for f in _product_findings() if f.kind == "url"], UNFIXED_URLS)
    assert not missing, (
        "A fetch whose address is not written in the code runs on an unpinned "
        "client. Move it to web_acquire.http.fetch_public or "
        "security.pinned_request.pinned_request, or declare in UNFIXED_URLS "
        "why the address is nobody else's choice:\n  " + "\n  ".join(missing)
    )


def test_every_redirect_following_client_is_declared() -> None:
    missing = _report([f for f in _product_findings() if f.kind == "redirects"], REDIRECTS)
    assert not missing, (
        "A client follows redirects unchecked. Pass follow_redirects=False (and "
        "check each hop, as fetch_public does), or declare in REDIRECTS why the "
        "redirects of that host are safe:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("declared,kind", [(UNFIXED_URLS, "url"), (REDIRECTS, "redirects")])
def test_no_declaration_is_stale(declared: dict[str, str], kind: str) -> None:
    found = {f.key for f in _product_findings() if f.kind == kind}
    stale = sorted(set(declared) - found)
    assert not stale, f"Declarations that match no {kind} finding (remove them): {stale}"
    assert all(reason.strip() for reason in declared.values())


# ---------------------------------------------------------------------------
# Negative controls: the four fetches as they were, and the pinned shapes
# ---------------------------------------------------------------------------

#: The chat-attachment fetch before 2026-11-15 (a sync client resolved the
#: name again after ``validate_url``).
_OLD_ATTACHMENT_FETCH = '''
from urllib.parse import urljoin


def _fetch_attachment_url(url: str) -> bytes:
    from kazma_core.security.ssrf import validate_url
    from kazma_core.proxy.client import get_scraping_client_sync

    def _client_factory():
        return get_scraping_client_sync(follow_redirects=False, timeout=30.0, rotate_ua=True)

    current_url = url
    with _client_factory() as client:
        for _ in range(4):
            validate_url(current_url, block_unresolved=True)
            with client.stream("GET", current_url) as response:
                if response.status_code in (301, 302):
                    current_url = urljoin(current_url, response.headers["location"])
                    continue
                return response.read()
'''

#: MCP OAuth discovery and refresh before 2026-11-15.
_OLD_OAUTH = '''
import httpx
from kazma_core.http_tls import shared_ssl_context


async def discover_auth_requirements(resource_url, *, www_authenticate="", client=None):
    http = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=shared_ssl_context())
    resource_meta_url = www_authenticate
    r = await http.get(resource_meta_url)
    return r.json()


async def _refresh(record):
    endpoints = await discover_auth_requirements(record["resource_url"])
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
        r = await http.post(endpoints["token_endpoint"], data={"refresh_token": record["refresh_token"]})
    return r.json()
'''

#: The DALL-E URL fallback and Pollinations before 2026-11-15.
_OLD_IMAGE_BACKENDS = '''
import httpx

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"


class DallEBackend:
    async def generate(self, prompt):
        async with httpx.AsyncClient(base_url="https://api.openai.com/v1",
                                     headers={"Authorization": "Bearer k"}) as client:
            resp = await client.post("/images/generations", json={"prompt": prompt})
            url = resp.json()["data"][0].get("url")
            img = await client.get(url)
            return img.content


class PollinationsBackend:
    async def generate(self, prompt):
        url = f"{POLLINATIONS_URL}{prompt}"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url)
            return response.content
'''

#: The same fetches on the pinned path, and a fixed host.
_PINNED_SHAPES = '''
import httpx
from kazma_core.security.pinned_request import pinned_request
from kazma_core.security.ssrf_pin import PinHostAsyncTransport
from kazma_core.web_acquire.http import fetch_public


async def attachment(url):
    return (await fetch_public(url, max_bytes=10)).response.content


async def token(url):
    return await pinned_request("POST", url, data={"a": "b"})


async def pinned_client(url, ip):
    transport = PinHostAsyncTransport({"h": ip})
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        return await client.get(url)


async def fixed_host(path):
    async with httpx.AsyncClient() as client:
        return await client.get(f"https://api.example.com/v1/{path}")
'''


def _kinds(source: str) -> set[tuple[str, str]]:
    return {(f.key.split("::", 1)[1], f.kind) for f in scan_source(source, "kazma-core/kazma_core/planted.py")}


def test_control_the_old_attachment_fetch_is_a_finding() -> None:
    assert ("_fetch_attachment_url", "url") in _kinds(_OLD_ATTACHMENT_FETCH)


def test_control_the_old_oauth_discovery_is_a_finding() -> None:
    kinds = _kinds(_OLD_OAUTH)
    assert ("discover_auth_requirements", "url") in kinds
    assert ("discover_auth_requirements", "redirects") in kinds
    assert ("_refresh", "url") in kinds
    assert ("_refresh", "redirects") in kinds


def test_control_the_old_image_downloads_are_findings() -> None:
    kinds = _kinds(_OLD_IMAGE_BACKENDS)
    assert ("DallEBackend.generate", "url") in kinds
    assert ("PollinationsBackend.generate", "redirects") in kinds
    # Pollinations' own address is written in the code: only its redirects are.
    assert ("PollinationsBackend.generate", "url") not in kinds


def test_the_pinned_shapes_are_not_findings() -> None:
    assert _kinds(_PINNED_SHAPES) == set()


@pytest.mark.parametrize("source,name", [
    ("import urllib.request\ndef f(url):\n    return urllib.request.urlopen(url).read()\n", "f"),
    ("from urllib.request import Request, urlopen\ndef f(url):\n    return urlopen(Request(url)).read()\n", "f"),
    ("import httpx\ndef f(url):\n    return httpx.get(url).text\n", "f"),
    ("import httpx\nclass A:\n    def __init__(self):\n        self._http = httpx.AsyncClient()\n"
     "    async def f(self, url):\n        return await self._http.post(url)\n", "A.f"),
    ("import httpx\nasync def f(http: httpx.AsyncClient, url):\n    return await http.get(url)\n", "f"),
    ("import httpx\nasync def f(url):\n    return await httpx.AsyncClient(base_url='https://x.example').get(url)\n", "f"),
])
def test_control_a_planted_unpinned_fetch_is_a_finding(source: str, name: str) -> None:
    assert (name, "url") in _kinds(source)


def test_control_the_four_fixed_fetches_left_the_findings() -> None:
    """The product code no longer has the four findings the review named."""
    keys = {f.key for f in _product_findings()}
    for gone in (
        "kazma-gateway/kazma_gateway/agent_handler/attachments.py::_fetch_attachment_url",
        "kazma-core/kazma_core/mcp/oauth.py::discover_auth_requirements",
        "kazma-core/kazma_core/mcp/oauth.py::_refresh",
        "kazma-core/kazma_core/mcp/oauth.py::_exchange_code",
        "kazma-core/kazma_core/mcp/oauth.py::_register_client",
        "kazma-core/kazma_core/tools/image_backends/dall_e.py::DallEBackend.generate",
        "kazma-core/kazma_core/tools/image_backends/flux.py::FluxBackend.generate",
        "kazma-core/kazma_core/tools/image_backends/pollinations.py::PollinationsBackend.generate",
        "kazma-gateway/kazma_gateway/adapters/voice_helpers.py::download_audio_bytes",
    ):
        assert gone not in keys, gone
