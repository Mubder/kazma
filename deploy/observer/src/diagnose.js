// The diagnosis: the pull probe (the site from outside) and the guard's
// heartbeat (the machine from inside) together say what broke.
//
//   pull probe            heartbeat                    condition
//   200                   fresh                        up
//   530 / 1033 / error    missing                      machine_unreachable
//   530 / 1033 / error    fresh                        tunnel_down
//   502 / 504             fresh, Kazma not running     kazma_restarting
//   200, ready 503        fresh                        dependency_down
//   200                   missing                      guard_gone
//   anything              fresh, maintenance           maintenance (no page)
//
// With no install paired the probe alone decides, and cannot tell the
// machine from the tunnel (``unreachable``).

// A heartbeat older than this is missing. The guard sends one a minute; 90 s
// allows one late beat. Any longer and a machine that just went off still
// reads as alive when the incident opens, and the page blames the tunnel.
export const FRESH_S = 90;
// A /health/ready answer older than this says nothing about now.
export const READY_FRESH_S = 6 * 60;
// A heartbeat saying "ready" received this long after a /health/ready 503
// carries a newer answer: the guard asks the same route every 30 s (10 s at
// most each). Live 2026-11-06 the 5-minute probe met Kazma booting after a
// restart, and its 503 kept the install "dependency_down" for 6 minutes --
// an incident and a page -- while every heartbeat said ready.
export const READY_SUPERSEDED_S = 45;

export const GOOD = new Set(["up", "maintenance"]);

const NAME_RE = /^[a-z_]{1,40}$/;
const WORD_RE = /^[a-z_]{1,24}$/;
const BUILD_RE = /^[0-9a-f]{12}$/;

function word(value) {
  return typeof value === "string" && WORD_RE.test(value) ? value : null;
}

function number(value) {
  return typeof value === "number" && Number.isFinite(value) ? Math.round(value * 10) / 10 : null;
}

/**
 * The heartbeat cut to the allowlist the guard sends (heartbeat_body in the
 * guard): whatever else a body holds is not stored.
 */
export function sanitize(body) {
  const b = body && typeof body === "object" ? body : {};
  const guard = b.guard && typeof b.guard === "object" ? b.guard : {};
  const kazma = b.kazma && typeof b.kazma === "object" ? b.kazma : {};
  const db = b.database && typeof b.database === "object" ? b.database : {};
  const disk = b.disk && typeof b.disk === "object" ? b.disk : {};
  const power = b.power && typeof b.power === "object" ? b.power : null;
  const why = Array.isArray(kazma.why)
    ? [...new Set(kazma.why.filter((n) => typeof n === "string" && NAME_RE.test(n)))].sort().slice(0, 8)
    : [];
  return {
    v: 1,
    seq: Number.isInteger(b.seq) ? b.seq : 0,
    guard: {
      state: word(guard.state),
      uptime_s: number(guard.uptime_s),
      code: typeof guard.code === "string" && /^[0-9a-f]{1,12}$/.test(guard.code) ? guard.code : null,
    },
    kazma: {
      state: word(kazma.state),
      ready: kazma.ready === true,
      why,
      uptime_s: number(kazma.uptime_s),
      build: typeof kazma.build === "string" && BUILD_RE.test(kazma.build) ? kazma.build : null,
    },
    maintenance: b.maintenance === true,
    database: { state: word(db.state), confirmed: db.confirmed === true },
    docker: word(b.docker),
    cloudflared: word(b.cloudflared),
    disk: { free_pct: number(disk.free_pct), level: word(disk.level) },
    power: power ? { on_battery: power.on_battery === true, percent: number(power.percent) } : null,
  };
}

/**
 * What the site's answer says: ``ok``, ``tunnel`` (Cloudflare reaches no
 * tunnel: 530 / error 1033), ``origin`` (the tunnel answers, Kazma does not:
 * 502 / 504 and the like), ``refused`` (Cloudflare Access turned the
 * observer away: the service token), or ``error`` (no answer at all).
 */
export function classifyLive(live) {
  if (!live || live.error || !live.status) return "error";
  const s = live.status;
  if (s === 200) return "ok";
  if (s === 530 || /\b1033\b/.test(live.hint || "")) return "tunnel";
  if (s === 401 || s === 403 || (s >= 300 && s < 400 && /cloudflareaccess/.test(live.hint || ""))) {
    return "refused";
  }
  return "origin";
}

const DOWN_STATES = new Set(["down", "starting", "unreachable"]);

/**
 * The condition of one install now.
 *
 * ``live``: the pull probe ({status, ms, hint, error}); ``ready``: the last
 * /health/ready answer ({status, at}) or null; ``heartbeat``: the latest
 * ({received_at, body}) or null; ``expectHeartbeat``: false for the site
 * alone (no install paired).
 */
export function diagnose({ live, ready, heartbeat, now, expectHeartbeat = true }) {
  const site = classifyLive(live);
  const hb = heartbeat && now - heartbeat.received_at <= FRESH_S ? heartbeat.body : null;
  const newerReady = !!(hb && hb.kazma && hb.kazma.state === "ready" && ready
    && heartbeat.received_at - ready.at >= READY_SUPERSEDED_S);
  const readyDown = !!(ready && ready.status >= 500 && now - ready.at <= READY_FRESH_S && !newerReady);

  if (hb && hb.maintenance) return { condition: "maintenance", detail: "planned work" };
  if (site === "refused") {
    return { condition: "observer_refused", detail: `HTTP ${live.status} from Cloudflare Access` };
  }
  if (!expectHeartbeat) {
    if (site === "ok") return readyDown ? { condition: "dependency_down", detail: "ready answers 503" } : { condition: "up" };
    if (site === "origin") return { condition: "kazma_down", detail: `HTTP ${live.status}` };
    return { condition: "unreachable", detail: live && live.status ? `HTTP ${live.status}` : "no answer" };
  }
  if (site === "ok") {
    if (!hb) return { condition: "guard_gone", detail: "the site answers, the guard is silent" };
    if (hb.kazma.state === "not_ready" || readyDown) {
      const why = [...hb.kazma.why];
      if (hb.database.state === "away" && !why.includes("database")) why.push("database");
      if (hb.docker === "down" && !why.includes("docker")) why.push("docker");
      return { condition: "dependency_down", detail: why.length ? why.join(", ") : "not ready" };
    }
    if (hb.disk && hb.disk.level === "critical") {
      return { condition: "disk_critical", detail: `${hb.disk.free_pct}% free` };
    }
    return { condition: "up" };
  }
  if (site === "tunnel" || site === "error") {
    if (!hb) {
      return { condition: "machine_unreachable", detail: live && live.status ? `HTTP ${live.status}` : "no answer" };
    }
    return { condition: "tunnel_down", detail: `cloudflared ${hb.cloudflared || "unknown"}` };
  }
  // origin: the tunnel answers for the machine, Kazma behind it does not.
  if (!hb) return { condition: "guard_gone", detail: `HTTP ${live.status}, the guard is silent` };
  if (DOWN_STATES.has(hb.kazma.state)) {
    return { condition: "kazma_restarting", detail: `Kazma ${hb.kazma.state}` };
  }
  if (hb.kazma.state === "not_ready") {
    return { condition: "dependency_down", detail: hb.kazma.why.join(", ") || "not ready" };
  }
  return { condition: "tunnel_down", detail: `the tunnel cannot reach Kazma (HTTP ${live.status})` };
}

/** What a page says for each condition. */
export const TITLES = {
  machine_unreachable: "Kazma's machine is not reachable (power, network or sleep)",
  tunnel_down: "The tunnel is down; Kazma itself is running",
  kazma_restarting: "Kazma is down; the guard is restarting it",
  kazma_down: "Kazma is down",
  dependency_down: "Kazma is up; a dependency is away",
  guard_gone: "The guard stopped; Kazma is unsupervised",
  disk_critical: "Kazma's disk is almost full",
  observer_refused: "The observer cannot pass Cloudflare Access",
  unreachable: "Kazma is not reachable",
};

export function titleOf(condition) {
  return TITLES[condition] || `Kazma: ${condition}`;
}
