/**
 * My account → Chat apps (Spaces S3-01): the signed-in person's linked
 * chat-app accounts, and a one-use code to link another.
 *
 * Only on an install that enforces Spaces (the template draws the panel
 * then). "Link Telegram" asks the server for a code
 * (POST /api/account/chat-links/code); the page shows it once, with what to
 * send the bot and how long it works, and reads the links again every few
 * seconds while it does, so the new link appears the moment the bot takes
 * the code. The code is never kept by the page after it runs out.
 */
(function () {
    'use strict';

    function say(key, english, vars) {
        return typeof window.kazmaT === 'function' ? window.kazmaT(key, english, vars) : english;
    }

    const POLL_MS = 3000;

    function chatLinksPanel() {
        return {
            links: [],
            platforms: [],
            linksLoaded: false,
            failed: false,
            busy: false,
            code: '',
            codePlatform: '',
            codeLabel: '',
            sendText: '',
            botUrl: '',
            expiresAt: 0,
            now: Date.now(),
            codeProblem: '',
            justLinked: '',
            _tick: null,
            _poll: null,

            async init() {
                const self = this;
                if (window.kazmaAddLeaveHook) {
                    window.kazmaAddLeaveHook(function () { self.stop(); });
                }
                await this.load();
            },

            async load() {
                const before = this.links.length;
                try {
                    const resp = await fetch('/api/account/chat-links', { credentials: 'same-origin' });
                    const body = await resp.json().catch(function () { return null; });
                    if (!resp.ok || !body || !body.ok) throw new Error('not ok');
                    this.links = Array.isArray(body.links) ? body.links : [];
                    this.platforms = Array.isArray(body.platforms) ? body.platforms : [];
                    this.failed = false;
                    this.linksLoaded = true;
                } catch (e) {
                    this.failed = true;
                    return;
                }
                if (this.code && this.links.length > before) {
                    const linked = this.links.find(
                        (l) => l.platform === this.codePlatform) || this.links[this.links.length - 1];
                    this.justLinked = say('chat_links.done', 'Linked: your {platform} account {id}.',
                        { platform: linked.label || this.codeLabel, id: linked.user_id });
                    this.clearCode();
                }
            },

            linkLabel(p) {
                return say('chat_links.link', 'Link {platform}', { platform: p.label });
            },

            async getCode(platform) {
                if (this.busy) return;
                this.busy = true;
                this.codeProblem = '';
                this.justLinked = '';
                try {
                    const resp = await fetch('/api/account/chat-links/code', {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ platform: platform }),
                    });
                    const body = await resp.json().catch(function () { return null; });
                    if (!resp.ok || !body || !body.ok) {
                        this.codeProblem = this.refusal(body && body.error);
                        return;
                    }
                    const found = this.platforms.find((p) => p.id === platform) || {};
                    this.code = body.code;
                    this.codePlatform = platform;
                    this.codeLabel = found.label || platform;
                    this.botUrl = found.bot || '';
                    this.sendText = body.send || body.code;
                    this.expiresAt = Number(body.expires_at || 0) * 1000;
                    this.now = Date.now();
                    this.startClocks();
                } catch (e) {
                    this.codeProblem = this.refusal('');
                } finally {
                    this.busy = false;
                }
            },

            refusal(code) {
                if (code === 'spaces_off') {
                    return say('chat_links.err_spaces_off', 'Chat apps are linked once Spaces is on for this install.');
                }
                if (code === 'identity_unavailable') {
                    return say('chat_links.err_unavailable', 'Accounts cannot be read right now. Try again.');
                }
                return say('chat_links.err_code', 'Could not get a code. Try again.');
            },

            startClocks() {
                this.stop();
                const self = this;
                this._tick = setInterval(function () {
                    self.now = Date.now();
                    if (self.code && self.now >= self.expiresAt) {
                        self.clearCode();
                        self.codeProblem = say('chat_links.expired', 'That code has run out. Get a new one.');
                    }
                }, 1000);
                this._poll = setInterval(function () { self.load(); }, POLL_MS);
            },

            stop() {
                if (this._tick) clearInterval(this._tick);
                if (this._poll) clearInterval(this._poll);
                this._tick = null;
                this._poll = null;
            },

            clearCode() {
                this.stop();
                this.code = '';
                this.sendText = '';
                this.botUrl = '';
                this.expiresAt = 0;
            },

            remaining() {
                const left = Math.max(0, Math.round((this.expiresAt - this.now) / 1000));
                const mm = String(Math.floor(left / 60));
                const ss = String(left % 60).padStart(2, '0');
                return say('chat_links.expires_in', 'Works for {time} more', { time: mm + ':' + ss });
            },

            sendHint() {
                return say('chat_links.send_hint',
                    'Send this to the {platform} bot in a direct message:', { platform: this.codeLabel });
            },

            async copy() {
                try {
                    await navigator.clipboard.writeText(this.sendText);
                    if (window.showToast) window.showToast(say('chat_links.copied', 'Copied'), 'success', 2000);
                } catch (e) {
                    if (window.showToast) window.showToast(say('chat_links.copy_failed', 'Could not copy. Select the text instead.'), 'warning', 4000);
                }
            },

            linkedAt(link) {
                return window.KazmaFormat ? window.KazmaFormat.dateTime(link.linked_at) : (link.linked_at || '');
            },
        };
    }

    window.chatLinksPanel = chatLinksPanel;
})();
