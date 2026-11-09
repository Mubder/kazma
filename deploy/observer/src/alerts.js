// Pages: Telegram through the observer's own bot ("Kazma Watch", so a fault
// in Kazma's bot cannot silence it), and email through Cloudflare Email
// Routing when a send_email binding is configured. A page counts as sent when
// at least one channel took it; otherwise the run tries it again.

const TELEGRAM_TIMEOUT_MS = 8000;

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

function mime(from, to, subject, text, now) {
  const id = `${now}.${Math.random().toString(16).slice(2)}@kazma.ai`;
  return [
    `From: Kazma Watch <${from}>`,
    `To: <${to}>`,
    `Subject: ${subject.replace(/[\r\n]+/g, " ")}`,
    `Message-ID: <${id}>`,
    `Date: ${new Date(now * 1000).toUTCString()}`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=utf-8",
    "Content-Transfer-Encoding: 8bit",
    "",
    text,
    "",
  ].join("\r\n");
}

async function email(env, text, now, makeMessage, emailTo = null) {
  if (!env.EMAIL || !env.EMAIL_FROM) return false;
  try {
    const subject = text.split("\n")[0].slice(0, 120);
    // The recipient Kazma's Settings -> Alerts set (watch_settings.js), else
    // the one in wrangler.jsonc.
    const to = emailTo || env.EMAIL_TO || "owner";
    const make = makeMessage || (async (from, rcpt, raw) => {
      const { EmailMessage } = await import("cloudflare:email");
      return new EmailMessage(from, rcpt, raw);
    });
    const message = await make(env.EMAIL_FROM, to, mime(env.EMAIL_FROM, to, subject, text, now));
    await env.EMAIL.send(message);
    return true;
  } catch {
    return false;
  }
}

/**
 * Send ``text`` on every configured channel: ``{telegram, email}``, each
 * true (taken), false (refused) or null (that channel is not set up).
 */
export async function pageChannels(env, text, now,
  { fetcher = fetch, makeMessage = null, emailTo = null } = {}) {
  const hasTelegram = !!(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID);
  const hasEmail = !!(env.EMAIL && env.EMAIL_FROM);
  const [tg, mail] = await Promise.all([
    hasTelegram ? telegram(env, text, fetcher || fetch) : null,
    hasEmail ? email(env, text, now, makeMessage, emailTo) : null,
  ]);
  return { telegram: tg, email: mail };
}

/** Send ``text`` on every configured channel. True when one took it. */
export async function page(env, text, now, opts = {}) {
  const sent = await pageChannels(env, text, now, opts);
  return sent.telegram === true || sent.email === true;
}
