<!-- AGENTS.md, piece 32-ssrf. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 32. SSRF pin-IP (Wave 8 H-7)

`validate_url` returns the public IPs it resolved. Direct scraping (no
`proxy=`) uses `PinHostAsyncTransport` (`kazma_core/security/ssrf_pin.py`)
to connect to the pin while keeping Host + SNI. After every `read_url`
hop, `assert_peer_public` / `peer_ip_from_response` abort if the peer is
private. The bounded reader checks BEFORE reading, preserves httpcore
`server_addr` evidence after closing, and requires either peer metadata or
a successful direct pin attestation. A missing/invalid pin never falls back
to unvalidated DNS. Targets with embedded credentials are refused.
`tests/test_web_acquisition_safety.py` covers these runtime boundaries.

**Pin sockets, never pool origins** (2026-10-10): rewriting the request URL
to an IP makes httpcore reuse one TLS connection for different hostnames
sharing that IP. `PinHostAsyncTransport` instead wraps the network backend's
`connect_tcp`; URL, connection-pool origin, Host and SNI retain the hostname.
The stream carries its connected pin through TLS/reuse, so a changed map
cannot attest an old socket as a new address. The single HTTPX pool/backend
compatibility seam is covered by `tests/test_ssrf_pin_authority.py` using the
real HTTPX/httpcore pool, including same-origin keep-alive and shared-IP hosts.

**Do not pin through `proxy=`.** `get_scraping_client` skips the pin
transport when a proxy is configured — CONNECT would break scraping.
Peer-private abort still runs. Tests: `tests/test_audit_wave8.py`.
httpx `>=0.27`.

**Browser guards are not all equivalent (2026-10-10 redirect witness).**
Scraper rendering uses the CDP/HTTPX broker and native deny proxy described in
§24B. Native automation now shares that broker and contained owner, with bounded
UTF-8 form requests; crawler rendering remains read-only. Playwright routes alone
do NOT validate subsequent redirect hops. The unused legacy browser_egress module
was retired on 2026-10-12; all production callers use the broker. Strict production
launch additionally requires Linux network namespace isolation; Windows refuses
strict launch. Explicit compatibility mode never attests OS isolation. Each fresh
context installs its own broker; no global object-id deduplication is allowed.
Gate: `tests/test_browser_egress_every_context.py`; the real pre-fix redirect
witness is archived in `docs/audits/evidence/browser_redirect_before_broker_2026_10_10.log`.

**An address someone else chose is fetched on the pinned path** (2026-11-15,
the defensive check before the independent review). `web_acquire.http.fetch_public`
fetches a page or file; `security.pinned_request.pinned_request` sends one
request: https only by default, no redirect followed, no proxy. Chat
attachments (`agent_handler.attachments._fetch_attachment_url`: a Discord
attachment or embed image re-resolved its name after the check), voice
attachments (`voice_helpers.download_audio_bytes` ->
`downloads.public_attachment_download`, a Slack token only for
`downloads.slack_owned_url` hosts), generated images
(`image_backends.base.fetch_generated_image`: no provider key leaves its API
host) and MCP OAuth (`mcp.oauth._oauth_request`) use it. For MCP OAuth the
issuer is one the resource's metadata lists (`authorization_servers`, and its
`resource` must be this server), its metadata is read from that issuer's own
addresses (RFC 8414, then OpenID Connect's; `_metadata_addresses`), never the
MCP server's host, and must name that same issuer (2026-11-16; gate
`tests/test_mcp_oauth_pinned.py`, the old discovery as the control); the
token and registration endpoints must be on the issuer's origin; the owner's
own local MCP server is asked directly, its origin only; an answer's text
never reaches an error. `ssrf._is_blocked_ip` refuses fec0::/10. Gate:
`tests/test_chosen_urls_fetched_pinned.py`: every unpinned request whose URL is
not fixed in code, and every redirect-following client, is declared with why
in `UNFIXED_URLS` or `REDIRECTS` (the four old fetches as negative controls).



