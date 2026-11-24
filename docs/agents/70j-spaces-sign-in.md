<!-- AGENTS.md, piece 70j-spaces-sign-in. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Once a second account exists, no switch opens the install (S4-03,
  2026-11-23).** The boot refuses sign-in off (`KAZMA_AUTH_DISABLED`,
  `KAZMA_DEV_WS_BYPASS`, `KAZMA_LOOPBACK_AUTOLOGIN`, `KAZMA_TRUST_LAN`:
  the last two hand a peer the owner's auto-login, refused though S4-02's
  rule already ends it, so a lab is told at start), demo mode
  (`KAZMA_DEMO_MODE`) and the chat apps' open switch
  (`KAZMA_GATEWAY_OPEN_TO_EVERYONE`, unless
  `KAZMA_GATEWAY_STRICT_ALLOWLIST` overrides it) and the open season
  take-over (`KAZMA_SESSION_OPEN_TAKEOVER`). One list,
  `boot_guard.OPEN_SWITCHES`, read through `boot_guard.open_switches_on`
  (the strict override is there) by this refusal and by the cloud profile
  (S4-08) alike: a new switch that lets anyone in goes there. `boot_guard.open_switches_refusal` is asked by
  `KazmaAppBuilder._bootstrap_services` once the settings store answers:
  exit 1 before anything serves, the refusal on stderr and in the log,
  naming the accounts it counted. It counts accounts, not Spaces: an install
  still on the older multi-user mode refuses too. With no switch on it reads
  nothing; an identity store that cannot answer refuses. Gate:
  `tests/test_open_switches_refused_with_members.py` (each switch through
  the real `create_app`; the boot guard without the rule as the control).
- **Stronger sign-in (S4-02, 2026-11-23).**
  - **A second factor** (`kazma_core/security/totp.py`, RFC 6238 on the
    standard library; `kazma_core/security/second_factor.py`): turned on in
    My account (`kazma_ui/sign_in_api.py`, `static/js/sign_in_settings.js`),
    in every mode. The secret and the recovery codes' digests are kept in
    the vault, install-scoped (`account.2fa.<account id>`: sign-in reads it
    before any space is bound), never in settings. A right password -- or,
    for the owner, the install's secret -- answers 401
    `second_factor_required` for an account with one
    (`routes_direct.auth._second_factor_verdict`), and the sign-in page
    asks for the code; a code is taken once (the last step kept per
    account, `second_factor.STEPS_KEY`), a recovery code used once. No API
    token, whatever its role, turns it on or off. Whether an account has
    one is `second_factor.factor_on`: a plain marker in settings
    (`second_factor.ON_KEY`, no secret), or the vault's copy when the
    marker is off (a settings restore from before the factor took the
    marker away; the marker is put back). With the marker on, a vault that
    cannot be read refuses (503); with it off, an unreadable vault is no
    factor, so a vault that failed to open stops no account without one. An
    install with no vault at all has none (`vault.vault_disabled`). Turning
    it on ends the account's other sessions (this one stays). **Wrong codes
    pause** an account's code checks in every mode, from every address, a
    known one too: `second_factor.WRONG_CODES` in a row, then `PAUSE_S`,
    doubling to `PAUSE_MAX_S` (`PAUSES_KEY`), counted under
    `second_factor._lock` so wrong codes at once make one pause; sign-in answers 429
    `second_factor_paused`, My account shows it, and the person is told
    where Spaces is enforced. The admin resets a person's from People
    (told), only from a signed-in admin's session (never the install's
    secret or a token: `session_only`) and never their own
    (`own_in_my_account`); deleting a person forgets it. **The way back
    with no code** is on the machine only: `kazma account second-factor off
    --account <id>` (`kazma_cli/account.py`, `second_factor.break_glass`)
    holds the runtime lock (refused while a server holds it), reads the
    vault itself, takes the factor off even when the vault lost it, and is
    an event of the People record.
  - **The install's secret is a key for programs once the owner has a
    second factor** (`auth._owner_second_factor_on`): it never mints a
    browser session (`auth._auto_cookie_via`), and a browser-style request
    that sends it (`Accept: text/html` or `Sec-Fetch-*`, or the legacy
    secret cookie) is refused 401 `secret_in_browser`
    (`auth._secret_refused_in_a_browser`), and so is the legacy
    `kazma-secret` cookie on a chat socket (`auth.get_websocket_principal`).
    The CLI (`local_api`), the guard and `scripts/deploy_live.py` keep it.
    The local network's auto-login (`KAZMA_TRUST_LAN`) ends then too
    (`auth._lan_auto_login`); this computer keeps its own. My account says
    the second factor does not cover the secret, and that the auto-login on
    this computer asks no code (by design, before Spaces or with one
    account).
  - **Sign-in limits per account** (only where Spaces is enforced): wrong
    passwords for one name from every address together, past
    `routes_direct.auth.ACCOUNT_MAX_FAILS` in `ACCOUNT_WINDOW_S`, pause
    sign-ins to it, but from an address it signed in from in the last 30
    days (`KNOWN_ADDRESSES_KEY`, hashed); the person is told
    (`sign_in_paused`).
  - **Invitation links that expire** (`kazma_core/spaces/invitations.py`):
    the admin makes one on People (one use, at most `invitations.HOURS`
    hours, only its digest kept). The link is `/invite#<token>`: the token
    after the '#' never reaches a server or its log (the page reads it,
    takes it out of the address bar and asks `POST /api/invitations/lookup`);
    the access log cuts any `/invite/<x>` path
    (`observability.log_noise.NoTokensInPaths`). `/invite`, the lookup and
    `POST /api/invitations/accept` are open before sign-in and make the
    account through `people.create_person` (S1-33's preconditions too),
    claiming the link first so it makes one account. Revoked at will.
  - **Each person's sessions**: People lists a person's and ends one
    (`web_sessions.end_one_session`, told); My account ends one of one's
    own. The owner's are every kind it has (`web_sessions.sessions_of_account`:
    a sign-in with the secret, the auto-login, a browser that sent the
    secret's header), each named by how it came (the session's `via`).
  - **No auto-login once a second person has an account**
    (`auth.everyone_signs_in`, asked by `_peer_trust_allowed`): with Spaces
    enforced and an account besides the owner's, the peer address is no
    credential, so this machine gets no session for nothing and its sockets
    are refused; the owner signs in like anyone and the session lasts as any
    does. The same rule ends `KAZMA_TRUST_LAN`'s auto-login for a private
    network peer (a Docker bridge included): it is peer trust too, on HTTP
    and on sockets. The sign-in page says why (`#everyone-signs-in`). When
    the rule first holds -- `people.create_person` while enforced, and
    `preconditions.switch_on` with an account besides the owner's -- the
    auto-login sessions already made end
    (`web_sessions.end_peer_trust_sessions`: those of `via`
    `this_computer` or `local_network`; one signed in with the secret
    stays). Before Spaces, or with one account, nothing changes. The root conftest keeps today's
    auto-login for other tests; a test of sign-in is marked `real_sign_in`.
  - Gates: `tests/test_sign_in_hardening.py` (a password alone signing in,
    a link spent never, and today's auto-login as controls),
    `tests/test_sign_in_review_fixes.py` (the review's eight items, each with
    today's behaviour as its control), `tests/test_sign_in_recheck.py` (the
    re-check's seven, each with today's behaviour as its control),
    `tests/e2e/test_sign_in_hardening_browser.py` (by the sidebar, EN/AR).

