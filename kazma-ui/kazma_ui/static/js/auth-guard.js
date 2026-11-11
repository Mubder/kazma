/* Kazma auth guard — global fetch interceptor for session-expired handling.
 *
 * Problem: when a session cookie goes stale (server restart, secret change,
 * expiry), the API returns 401 and most pages silently swallowed it in empty
 * `.catch()` blocks, leaving the user staring at a dead "Disconnected" UI with
 * no explanation. Only settings.js redirected to /login.
 *
 * This classic (non-module) script wraps window.fetch ONCE, before any page
 * script runs, so every fetch-based call across every page handles 401/403 the
 * same way: show a brief "session expired" message and route to the login page
 * with ?reason=session_expired so login.html can explain what happened.
 *
 * Installed as the first <script> in base.html (right after window.t injection).
 * Login.html intentionally does NOT load it (it must not intercept /api/auth/*).
 */
(function () {
    "use strict";
    if (window.__kazmaFetchGuarded) return; // idempotent — only patch once
    window.__kazmaFetchGuarded = true;

    var _fetch = window.fetch;
    if (typeof _fetch !== "function") return;

    // One-shot guard: once a redirect to /login is in flight, don't trigger
    // another (concurrent 401s from many API calls all fire at once).
    // Mirrors the pre-existing window.__kazmaAuthRedirecting convention.
    function _redirectToLogin() {
        if (window.__kazmaAuthRedirecting) return;
        window.__kazmaAuthRedirecting = true;
        try {
            if (typeof window.showToast === "function") {
                window.showToast(
                    (window.t && window.t("auth.session_expired")) || "Your session has expired. Redirecting to login…",
                    "warning",
                    3500
                );
            }
        } catch (e) { /* best-effort; page is about to unload */ }
        var next = encodeURIComponent(location.pathname + location.search);
        // Keep next-path same-origin (open-redirect guard, matching login.html).
        location.href = "/login?next=" + next + "&reason=session_expired";
    }

    window.fetch = function (input, init) {
        return _fetch.call(this, input, init).then(function (res) {
            // Only intercept the unauthorized responses. Never touch the body
            // or status so callers that await res.status see the real value.
            //
            // 401 ONLY. 403 used to redirect here too, and it meant that any
            // forbidden response logged the operator out of Kazma entirely.
            // Nothing in this app returns 403 for an expired session: it is
            // the CSRF guard (csrf.py), the RBAC check ("Forbidden for your
            // role"), and the email endpoints' same-origin guard. Every one
            // of those means "you are signed in, but this request is not
            // allowed" — the opposite of a session expiry.
            //
            // Live, 2026-08-29: Disconnect on the Gmail settings card sent no
            // X-Requested-With header, the same-origin guard returned 403, and
            // this line threw the operator back to the login page. Every
            // attempt. They logged back in and it happened again, which made a
            // one-line missing header look like a broken session.
            if (res && res.status === 401) {
                // Don't intercept the auth endpoints themselves (login, status,
                // logout) — their 401 is meaningful to the login form, not a
                // session-expiry signal.
                var url = typeof input === "string" ? input : (input && input.url) || "";
                if (url.indexOf("/api/auth/") === -1) {
                    _redirectToLogin();
                }
            }
            return res;
        });
    };
})();

/* kazmaSave — the one way the UI writes and then says "saved".
 *
 * 24 save/delete handlers did `await fetch(...)`, threw the Response away,
 * and showed a success toast (audit 2026-09-22): a 403, 422 or 500 still said
 * "saved" — Settings → Safety, the HITL policy, among them — and seven more
 * had no error path at all. This resolves with the parsed body only when the
 * server accepted the write, and rejects with the server's own reason
 * otherwise, so the caller's catch shows what actually happened.
 *
 * Goes through window.fetch at call time, so the 401 guard above still runs.
 * tests/test_static_gates.py forbids discarding an awaited fetch() result;
 * tests/js/test_kazma_save.js holds the behaviour.
 */
(function () {
    "use strict";
    if (typeof window.kazmaSave === "function") return;

    function _reason(body, res) {
        if (body && typeof body === "object") {
            var r = body.detail || body.error || body.message || body.reason;
            if (Array.isArray(r) && r.length) {
                // FastAPI validation errors: [{loc, msg, type}, ...]
                return r.map(function (x) { return (x && x.msg) || String(x); }).join("; ");
            }
            if (typeof r === "string" && r) return r;
        }
        return "HTTP " + res.status;
    }

    window.kazmaSave = async function (url, init) {
        var opts = Object.assign({ credentials: "same-origin" }, init || {});
        var headers = Object.assign({ "X-Requested-With": "XMLHttpRequest" }, opts.headers || {});
        var isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
        if (opts.body !== undefined && opts.body !== null && typeof opts.body !== "string" && !isForm) {
            opts.body = JSON.stringify(opts.body);
            if (!headers["Content-Type"]) headers["Content-Type"] = "application/json";
        }
        opts.headers = headers;
        var res = await window.fetch(url, opts);
        var body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        // A 200 that says {"ok": false} is a refusal too (Knowledge audit
        // KB-S10, program W1.5): the Settings Memory/Knowledge toggles
        // reported "saved" over a refused write.
        if (!res.ok || (body && (body.status === "error" || body.ok === false))) {
            var err = new Error(_reason(body, res));
            err.status = res.status;
            err.body = body;
            throw err;
        }
        return body;
    };
})();

/**
 * window.kazmaGetJson(url, init) -> Promise<object|null> — the read side, for
 * pollers.
 *
 * The parsed body of a 2xx JSON response; otherwise null: an error status, a
 * proxy's HTML page (Cloudflare answers 502 HTML while the server restarts),
 * or no network. It never throws and never logs: a poller treats null as
 * "nothing new; the next tick tries again". Pollers that parsed the error
 * page as JSON wrote "Unexpected token '<'" into the console on every restart
 * (the Memory and Swarm pages, 2026-09-27). Gates:
 * tests/js/test_kazma_get_json.js and, in a browser, every page under a 502,
 * tests/e2e/test_pages_load_clean.py.
 */
(function () {
    "use strict";
    if (typeof window.kazmaGetJson === "function") return;

    window.kazmaGetJson = async function (url, init) {
        try {
            var res = await window.fetch(url, Object.assign({ credentials: "same-origin" }, init || {}));
            if (!res || !res.ok) return null;
            var type = (res.headers && typeof res.headers.get === "function"
                ? res.headers.get("content-type") : "") || "";
            if (type && type.toLowerCase().indexOf("json") < 0) return null;
            return await res.json();
        } catch (e) {
            // No network, or a body that is not JSON after all: nothing to show.
            return null;
        }
    };
})();

/**
 * window.kazmaStartSignIn(url, body) -> Promise<object> — start an OAuth
 * sign-in (GitHub, Google, Microsoft) in THIS tab.
 *
 * The start routes are POSTs from Kazma's own page (security audit
 * 2026-11-10: they were GETs that wrote state). Each answers
 * {ok, authorize_url} and sets the cookie that binds the sign-in to this
 * browser (kazma_ui.oauth_sign_in); the provider then sends the browser back
 * through the callback to the page it started from. No popup and no
 * window.opener: the pages send Cross-Origin-Opener-Policy: same-origin.
 *
 * Posts *body* as JSON with X-Requested-With (the mail and calendar routes
 * require it) and, on success, goes to the provider's page -- only an
 * http(s) address is followed. Otherwise resolves with the answer
 * ({ok: false, code, error, origin, status}) for kazmaSignInProblem to word.
 * A network failure rejects. Gate: tests/js/test_kazma_start_sign_in.js.
 */
(function () {
    "use strict";
    if (typeof window.kazmaStartSignIn === "function") return;

    function _words(key, english, vars) {
        return typeof window.kazmaT === "function" ? window.kazmaT(key, english, vars) : english;
    }

    window.kazmaStartSignIn = async function (url, body) {
        var res = await window.fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
            body: JSON.stringify(body || {}),
        });
        var data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        data = data && typeof data === "object" ? data : {};
        if (res.ok && data.ok && typeof data.authorize_url === "string") {
            var target = null;
            try { target = new URL(data.authorize_url, window.location.href); } catch (e) { target = null; }
            if (target && (target.protocol === "https:" || target.protocol === "http:")) {
                window.location.assign(target.href);
                return { ok: true, navigating: true };
            }
            return { ok: false, code: "bad_authorize_url", status: res.status };
        }
        data.ok = false;
        data.status = res.status;
        return data;
    };

    /* The words for a start that did not go: the page is on another origin
       than the callback (the binding cookie would never come back), else the
       server's own reason, else *fallback*. */
    window.kazmaSignInProblem = function (data, fallback) {
        if (data && data.code === "sign_in_from_callback_origin" && data.origin) {
            return _words("oauth.sign_in_from",
                "Open Kazma at {origin} to sign in: the provider sends you back there.",
                { origin: String(data.origin) });
        }
        return (data && (data.error || data.detail)) || fallback || "";
    };

    /* The words for a sign-in the callback refused or that failed, by its
       code (oauth_attempts.REFUSAL_REASONS, and GitHub's denied / no_code /
       exchange_failed). An unknown code: *fallback*. */
    var _REASONS = {
        other_browser: "The sign-in was started in another browser. Start it again here.",
        unknown: "This sign-in was already used or never started. Start it again.",
        expired: "The sign-in took longer than ten minutes. Start it again.",
        other_provider: "The sign-in came back to the wrong place. Start it again.",
        denied: "The sign-in was cancelled at the provider.",
        no_code: "The provider sent no sign-in code back. Start it again.",
        exchange_failed: "The provider refused to finish the sign-in. Start it again.",
    };
    window.kazmaSignInFailed = function (reason, fallback) {
        var code = String(reason || "");
        if (Object.prototype.hasOwnProperty.call(_REASONS, code)) {
            return _words("oauth.failed." + code, _REASONS[code]);
        }
        return fallback || "";
    };
})();
