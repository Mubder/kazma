// The status page is the owner's only: Cloudflare Access sits in front of
// it, and the Worker checks Access's signed token itself as well, so a
// misconfigured Access application leaves the page closed, never open.
// Unset ACCESS_TEAM_DOMAIN or ACCESS_AUD refuses every request.

const CERTS_TTL_MS = 60 * 60 * 1000;
let certsCache = { team: "", at: 0, keys: [] };

function b64urlToBytes(text) {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
}

function decodePart(part) {
  return JSON.parse(new TextDecoder().decode(b64urlToBytes(part)));
}

async function teamKeys(team, fetcher, nowMs) {
  if (certsCache.team === team && nowMs - certsCache.at < CERTS_TTL_MS && certsCache.keys.length) {
    return certsCache.keys;
  }
  const res = await fetcher(`https://${team}/cdn-cgi/access/certs`, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`certs HTTP ${res.status}`);
  const data = await res.json();
  const keys = Array.isArray(data.keys) ? data.keys : [];
  certsCache = { team, at: nowMs, keys };
  return keys;
}

export function forgetCerts() {
  certsCache = { team: "", at: 0, keys: [] };
}

/**
 * ``{ok: true, email}`` when the request carries a valid Access token for
 * this application, else ``{ok: false, status, reason}``.
 */
export async function checkAccess(request, env, { fetcher = fetch, now = Date.now() } = {}) {
  const team = (env.ACCESS_TEAM_DOMAIN || "").trim();
  const aud = (env.ACCESS_AUD || "").trim();
  if (!team || !aud) return { ok: false, status: 503, reason: "access_not_configured" };
  const token = request.headers.get("Cf-Access-Jwt-Assertion") || "";
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, status: 403, reason: "no_token" };
  let header;
  let payload;
  try {
    header = decodePart(parts[0]);
    payload = decodePart(parts[1]);
  } catch {
    return { ok: false, status: 403, reason: "bad_token" };
  }
  if (header.alg !== "RS256" || typeof header.kid !== "string") {
    return { ok: false, status: 403, reason: "bad_token" };
  }
  const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!audiences.includes(aud)) return { ok: false, status: 403, reason: "wrong_audience" };
  if (payload.iss !== `https://${team}`) return { ok: false, status: 403, reason: "wrong_issuer" };
  const nowS = Math.floor(now / 1000);
  if (typeof payload.exp !== "number" || payload.exp < nowS) return { ok: false, status: 403, reason: "expired" };
  if (typeof payload.nbf === "number" && payload.nbf > nowS + 60) {
    return { ok: false, status: 403, reason: "not_yet_valid" };
  }
  let keys;
  try {
    keys = await teamKeys(team, fetcher, now);
  } catch {
    return { ok: false, status: 503, reason: "certs_unavailable" };
  }
  const jwk = keys.find((k) => k.kid === header.kid);
  if (!jwk) return { ok: false, status: 403, reason: "unknown_key" };
  const key = await crypto.subtle.importKey(
    "jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  const valid = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlToBytes(parts[2]), signed);
  if (!valid) return { ok: false, status: 403, reason: "bad_signature" };
  return { ok: true, email: typeof payload.email === "string" ? payload.email : null };
}
