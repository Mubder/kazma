// Signatures: the guard signs each heartbeat (scripts/service/kazma_guard.py,
// sign_heartbeat) with the install's pairing key, HMAC-SHA256 over
// "<timestamp>.<body>", sent as "v1=<hex>". The shared test vector is
// tests/fixtures/observer/heartbeat_vector.json.

const enc = new TextEncoder();

// How far a signed request's timestamp may be from the observer's clock.
export const CLOCK_WINDOW_S = 300;

export const INSTALL_RE = /^[0-9a-f]{16}$/;
const SIGNATURE_RE = /^v1=[0-9a-f]{64}$/;
const TIMESTAMP_RE = /^[0-9]{9,11}$/;

function hex(buffer) {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function concat(a, b) {
  const out = new Uint8Array(a.byteLength + b.byteLength);
  out.set(new Uint8Array(a), 0);
  out.set(new Uint8Array(b), a.byteLength);
  return out;
}

/** "v1=<hex>": HMAC-SHA256 under ``key`` of ``"<ts>." + body`` (bytes). */
export async function sign(key, ts, body) {
  const raw = typeof body === "string" ? enc.encode(body) : new Uint8Array(body);
  const k = await crypto.subtle.importKey(
    "raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", k, concat(enc.encode(`${ts}.`), raw));
  return `v1=${hex(mac)}`;
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
 * or ``{error}`` naming what is wrong (never the values).
 */
export function signingHeaders(request, now) {
  const install = request.headers.get("X-Kazma-Install") || "";
  const tsText = request.headers.get("X-Kazma-Timestamp") || "";
  const signature = request.headers.get("X-Kazma-Signature") || "";
  if (!INSTALL_RE.test(install)) return { error: "bad_install" };
  if (!TIMESTAMP_RE.test(tsText)) return { error: "bad_timestamp" };
  if (!SIGNATURE_RE.test(signature)) return { error: "bad_signature" };
  const ts = Number(tsText);
  if (Math.abs(now - ts) > CLOCK_WINDOW_S) return { error: "stale" };
  return { install, ts, signature };
}

/** True when ``signature`` is ``key``'s over ``ts`` and ``body``. */
export async function verify(key, ts, body, signature) {
  return sameText(await sign(key, ts, body), signature);
}
