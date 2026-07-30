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
            if (res && (res.status === 401 || res.status === 403)) {
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
