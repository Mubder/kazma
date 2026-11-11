// Signatures: the guard signs each heartbeat (scripts/service/kazma_guard.py,
// sign_request) and Kazma each Settings -> Alerts request
// (kazma_core/observability/outside_watch.py, _sign) with the install's
// pairing key: "v2=<hex>", HMAC-SHA256 over
//
//     "v2\n<timestamp>\n<METHOD>\n<path>\n" + body
//
// (v2Message). The route is in what is signed, so a signature made for one
// route is refused on another. The older "v1=<hex>", over "<timestamp>." +
// body, did not bind the route. v1 is accepted while ACCEPT_V1 is true -- the
// transition, until every guard and Kazma sends v2 -- and each one accepted
// is logged. The shared test vector is
// tests/fixtures/observer/heartbeat_vector.json.

const enc = new TextEncoder();

// How far a signed request's timestamp may be from the observer's clock.
export const CLOCK_WINDOW_S = 300;

// The one switch that drops v1: set false (and deploy) once no install sends
// it -- `wrangler tail` shows "accepted a v1 signature" while one still does.
export const ACCEPT_V1 = true;

export const INSTALL_RE = /^[0-9a-f]{16}$/;
const SIGNATURE_RE = /^v[12]=[0-9a-f]{64}$/;
const TIMESTAMP_RE = /^[0-9]{9,11}$/;

function hex(buffer) {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function bytes(body) {
  if (body == null) return new Uint8Array(0);
  return typeof body === "string" ? enc.encode(body) : new Uint8Array(body);
}

function concat(a, b) {
  const out = new Uint8Array(a.byteLength + b.byteLength);
  out.set(a, 0);
  out.set(b, a.byteLength);
  return out;
}

async function hmac(key, message) {
  const k = await crypto.subtle.importKey(
    "raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", k, message));
}

/** What a v2 signature covers: ``"v2\n<ts>\n<METHOD>\n<path>\n" + body``. */
export function v2Message(ts, method, path, body) {
  return concat(enc.encode(`v2\n${ts}\n${String(method).toUpperCase()}\n${path}\n`), bytes(body));
}

/** "v2=<hex>": HMAC-SHA256 under ``key`` of ``v2Message``. */
export async function signV2(key, ts, method, path, body) {
  return `v2=${await hmac(key, v2Message(ts, method, path, body))}`;
}

/** "v1=<hex>": HMAC-SHA256 under ``key`` of ``"<ts>." + body`` (the old form). */
export async function sign(key, ts, body) {
  return `v1=${await hmac(key, concat(enc.encode(`${ts}.`), bytes(body)))}`;
}

/** Compare two strings in time that does not depend on where they differ. */
export function sameText(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  let diff = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

/**
 * Read the signing headers of ``request``. Returns ``{install, ts, signature}``
 * or ``{error}`` naming what is wrong (never the values). A v1 signature is
 * ``v1_retired`` once v1 is dropped (``acceptV1`` false).
 */
export function signingHeaders(request, now, { acceptV1 = ACCEPT_V1 } = {}) {
  const install = request.headers.get("X-Kazma-Install") || "";
  const tsText = request.headers.get("X-Kazma-Timestamp") || "";
  const signature = request.headers.get("X-Kazma-Signature") || "";
  if (!INSTALL_RE.test(install)) return { error: "bad_install" };
  if (!TIMESTAMP_RE.test(tsText)) return { error: "bad_timestamp" };
  if (!SIGNATURE_RE.test(signature)) return { error: "bad_signature" };
  if (signature.startsWith("v1=") && !acceptV1) return { error: "v1_retired" };
  const ts = Number(tsText);
  if (Math.abs(now - ts) > CLOCK_WINDOW_S) return { error: "stale" };
  return { install, ts, signature };
}

/**
 * True when ``h.signature`` (from ``signingHeaders``) is ``key``'s for this
 * request: a v2 one over its method, its path and ``body``; a v1 one (only
 * while it is accepted) over ``v1Body`` -- the body, or what the route's v1
 * form signed instead (the summary's "GET /v1/summary").
 */
export async function verifyRequest(key, h, request, body, { v1Body = body, acceptV1 = ACCEPT_V1 } = {}) {
  const path = new URL(request.url).pathname;
  if (h.signature.startsWith("v2=")) {
    return sameText(await signV2(key, h.ts, request.method, path, body), h.signature);
  }
  if (!acceptV1) return false;
  const ok = sameText(await sign(key, h.ts, v1Body), h.signature);
  if (ok) {
    // Logged so the owner can see which installs still send v1 before
    // ACCEPT_V1 goes false (`wrangler tail`). The install id is a hash.
    console.warn(`observer: accepted a v1 signature on ${request.method} ${path} from install ${h.install}`);
  }
  return ok;
}
