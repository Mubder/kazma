// Pages: Telegram through the observer's own bot ("Kazma Watch", so a fault
// in Kazma's bot cannot silence it), and email through Cloudflare Email
// Service (Email Sending) when a send_email binding, a sender (EMAIL_FROM, an
// address on a domain onboarded for sending: alerts.kazma.ai) and a recipient
// are set. A page counts as sent when at least one channel took it; otherwise
// the run tries it again.

const TELEGRAM_TIMEOUT_MS = 8000;
// Email Service's error codes (E_SENDER_NOT_VERIFIED, ...): what a refused
// email says. Anything else is reported as E_SEND_FAILED.
const EMAIL_CODE_RE = /^E_[A-Z_]{1,40}$/;
const SUBJECT_CHARS = 120;

async function telegram(env, text, fetcher) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  try {
    const res = await fetcher(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, disable_web_page_preview: true }),
      signal: AbortSignal.timeout(TELEGRAM_TIMEOUT_MS),
    });
    if (!res.ok) return false;
    const data = await res.json().catch(() => null);
    return !!(data && data.ok);
  } catch {
    return false;
  }
}

/** The card's first line, as a subject: one line, cut at SUBJECT_CHARS. */
export function emailSubject(text) {
  const first = String(text || "").split("\n")[0].replace(/[\r\n\t]+/g, " ").trim();
  return first.length > SUBJECT_CHARS ? `${first.slice(0, SUBJECT_CHARS - 1).trimEnd()}…` : first;
}

/**
 * One email through Email Service's structured send: ``{ok: true}`` or
 * ``{ok: false, code}`` with Email Service's error code.
 */
async function email(env, text, to) {
  try {
    await env.EMAIL.send({
      to,
      from: { email: env.EMAIL_FROM, name: "Kazma Watch" },
      subject: emailSubject(text),
      text,
    });
    return { ok: true, code: null };
  } catch (err) {
    const code = err && typeof err.code === "string" && EMAIL_CODE_RE.test(err.code)
      ? err.code : "E_SEND_FAILED";
    return { ok: false, code };
  }
}

/**
 * Send ``text`` on every configured channel: ``{telegram, email}``, each
 * true (taken), false (refused) or null (that channel is not set up), and
 * ``emailCode``: why an email was refused (Email Service's code), else null.
 * The recipient is the one Kazma's Settings -> Alerts set (watch_settings.js),
 * else EMAIL_TO; with neither, email is not set up.
 */
export async function pageChannels(env, text, now, { fetcher = fetch, emailTo = null } = {}) {
  const hasTelegram = !!(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID);
  const to = String(emailTo || env.EMAIL_TO || "").trim();
  const hasEmail = !!(env.EMAIL && env.EMAIL_FROM && to);
  const [tg, mail] = await Promise.all([
    hasTelegram ? telegram(env, text, fetcher || fetch) : null,
    hasEmail ? email(env, text, to) : null,
  ]);
  return { telegram: tg, email: mail === null ? null : mail.ok, emailCode: mail && !mail.ok ? mail.code : null };
}

/** Send ``text`` on every configured channel. True when one took it. */
export async function page(env, text, now, opts = {}) {
  const sent = await pageChannels(env, text, now, opts);
  return sent.telegram === true || sent.email === true;
}
