// Small HTTP helpers for the routes Settings -> Alerts calls (watch_settings.js).

export const MAX_BODY = 4096;

export function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/** The request's body as bytes, or null past ``MAX_BODY``. */
export async function readBody(request) {
  const declared = Number(request.headers.get("Content-Length") || "0");
  if (declared > MAX_BODY) return null;
  const raw = await request.arrayBuffer();
  return raw.byteLength > MAX_BODY ? null : raw;
}
