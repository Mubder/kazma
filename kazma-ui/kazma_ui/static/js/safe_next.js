/**
 * Where to go after sign-in: a path on THIS server, or "/" (audit 2026-09-30,
 * AUD-019).
 *
 * The login page accepted `?next=` when it started with "/" and not "//".
 * Browsers read "\" as "/" and drop tabs and newlines inside an http(s) URL,
 * so "/\evil.example", "/\/evil.example" and "/<TAB>/evil.example" passed and
 * sent a freshly signed-in user to another site. The URL is now parsed the
 * way the browser will navigate it, and kept only when its origin is ours.
 */
(function (root) {
  "use strict";

  function kazmaSafeNext(raw, origin) {
    var base = origin || (root.location && root.location.origin) || "http://localhost";
    if (!raw) return "/";
    var target;
    try {
      target = new URL(String(raw), base);
    } catch (e) {
      return "/";
    }
    if (target.origin !== new URL(base).origin) return "/";
    return target.pathname + target.search + target.hash;
  }

  root.kazmaSafeNext = kazmaSafeNext;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = kazmaSafeNext;
  }
})(typeof window !== "undefined" ? window : globalThis);
