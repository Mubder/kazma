/** Settings mixin: alerts — Settings -> Alerts (the unified alerts plan,
 * Stage 3): where Kazma's alerts go, the server status messages, the
 * guard's pager and its test page, Kazma Watch (pair, unpair, test page,
 * what it is told), notices, and the last week's alerts from every sender.
 * Routes: kazma_ui/settings_alerts.py. */
(function (root) {
    "use strict";
    // Text built here: the catalog's text in the page's language, else the
    // English given; {name} placeholders filled from vars.
    function _k(key, en, vars) {
        var f = root && root.kazmaT;
        if (typeof f === "function") return f(key, en, vars);
        var s = en;
        if (vars) for (var v in vars) s = s.split("{" + v + "}").join(String(vars[v]));
        return s;
    }

    // A guard test page is answered within a second; its own credential
    // lookup can take a minute (guard_pager.ANSWER_WAIT_S).
    var GUARD_POLL_MS = 1500;
    var GUARD_WAIT_MS = 155000;

    function lang() {
        return (root && root.KAZMA_LANG) || (typeof document !== "undefined" ? document.documentElement.lang : "en") || "en";
    }

    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.alerts = function () {
        return {
        alerts: {
            loaded: false, failed: false, saving: false,
            routes: [], routeNames: ['telegram', 'telegram-group', 'discord', 'slack'], ready: {},
            lifecycle: [], lifecycleNames: ['started', 'startup_failed', 'starting', 'shutting_down'],
            noticesChoice: 'chats', groupChat: '', guardChat: '', guard: null, watch: null, watchEmail: '',
            // Which Telegram bot sends them: Kazma's chat bot, or a separate
            // alerts bot (its token is never read back: only whether one is
            // saved; a token typed here is sent once, on Save).
            botChoice: 'chat_bot', botToken: '', botTokenSaved: false, botChat: '',
        },
        alertsSnapshot: '',
        // What Telegram said of the alerts bot at the last Save.
        botCheck: { note: '', tone: '' },
        _watchEmailSaved: '',
        // "Send a test alert": one alert through the saved routes; the
        // result names the routes that took it.
        opsTestAlert: { sending: false, result: null },
        guardTest: { busy: false, note: '', tone: '' },
        watchPair: { token: '', url: '' },
        watchBusy: '',
        watchResult: { note: '', tone: '' },
        recentAlerts: { loaded: false, failed: false, loading: false, items: [], errors: [] },

        async loadAlerts() {
            // A reload after a test or a pairing keeps what the owner has
            // typed and not saved yet (it stays "unsaved").
            const edits = this.alerts.loaded && this.alertsDirty ? this._alertsDiffable() : null;
            const typedEmail = this.alerts.loaded && this.alerts.watchEmail !== this._watchEmailSaved
                ? this.alerts.watchEmail : null;
            const data = await window.kazmaGetJson('/api/settings/alerts');
            if (!data || !data.routes) {
                this.alerts.failed = true;
                return;
            }
            const a = this.alerts;
            a.failed = false;
            a.routeNames = Array.isArray(data.routes.names) ? data.routes.names : a.routeNames;
            a.routes = Array.isArray(data.routes.chosen) ? data.routes.chosen.slice() : [];
            a.ready = data.routes.ready || {};
            a.groupChat = String(data.routes.group_chat || '');
            a.lifecycleNames = (data.lifecycle && Array.isArray(data.lifecycle.names)) ? data.lifecycle.names : a.lifecycleNames;
            a.lifecycle = (data.lifecycle && Array.isArray(data.lifecycle.events)) ? data.lifecycle.events.slice() : [];
            a.noticesChoice = data.notices_to_chats === false ? 'bell' : 'chats';
            a.guard = data.guard || null;
            a.guardChat = String((data.guard && data.guard.chat) || '');
            const bot = data.bot || {};
            a.botChoice = bot.choice === 'alerts_bot' ? 'alerts_bot' : 'chat_bot';
            a.botTokenSaved = !!bot.token_saved;
            a.botToken = '';
            a.botChat = String(bot.chat || '');
            a.watch = data.watch || null;
            a.watchEmail = String((data.watch && data.watch.email_to) || '');
            this._watchEmailSaved = a.watchEmail;
            if (!this.watchPair.url) this.watchPair.url = 'https://status.kazma.ai';
            a.loaded = true;
            this.alertsSnapshot = JSON.stringify(this._alertsDiffable());
            if (edits) {
                a.routes = edits.routes.slice();
                a.lifecycle = edits.lifecycle_events.slice();
                a.noticesChoice = edits.notices_to_chats ? 'chats' : 'bell';
                a.groupChat = edits.group_chat;
                a.guardChat = edits.guard_chat;
                a.botChoice = edits.bot;
                a.botToken = edits.bot_token;
                a.botChat = edits.bot_chat;
            }
            if (typedEmail !== null) a.watchEmail = typedEmail;
            this.loadRecentAlerts();
        },

        _alertsDiffable() {
            const a = this.alerts;
            return {
                routes: a.routeNames.filter(r => a.routes.includes(r)),
                lifecycle_events: a.lifecycleNames.filter(ev => a.lifecycle.includes(ev)),
                notices_to_chats: a.noticesChoice !== 'bell',
                group_chat: String(a.groupChat || '').trim(),
                guard_chat: String(a.guardChat || '').trim(),
                bot: a.botChoice === 'alerts_bot' ? 'alerts_bot' : 'chat_bot',
                bot_chat: String(a.botChat || '').trim(),
                // Empty keeps the saved token; anything typed is a change.
                bot_token: String(a.botToken || '').trim(),
            };
        },

        // The email field is on the page like the others, so the page's Save
        // takes it too (live 2026-11-09: the owner typed an address, Save
        // stayed greyed out, and Kazma Watch never got it).
        get watchEmailDirty() {
            return this.alerts.loaded && !!this.alerts.watch && !!this.alerts.watch.paired
                && String(this.alerts.watchEmail || '').trim() !== this._watchEmailSaved;
        },

        get alertsDirty() {
            return this.alerts.loaded && (JSON.stringify(this._alertsDiffable()) !== this.alertsSnapshot
                || this.watchEmailDirty);
        },

        toggleAlertList(which, item, on) {
            const list = which === 'routes' ? this.alerts.routes : this.alerts.lifecycle;
            const idx = list.indexOf(item);
            if (on && idx === -1) list.push(item);
            if (!on && idx !== -1) list.splice(idx, 1);
        },

        resetAlerts() {
            let snap = {};
            try { snap = JSON.parse(this.alertsSnapshot || '{}'); } catch (e) { snap = {}; }
            const a = this.alerts;
            a.routes = (snap.routes || []).slice();
            a.lifecycle = (snap.lifecycle_events || []).slice();
            a.noticesChoice = snap.notices_to_chats === false ? 'bell' : 'chats';
            a.groupChat = String(snap.group_chat || '');
            a.guardChat = String(snap.guard_chat || '');
            a.botChoice = snap.bot === 'alerts_bot' ? 'alerts_bot' : 'chat_bot';
            a.botChat = String(snap.bot_chat || '');
            a.botToken = '';
            a.watchEmail = this._watchEmailSaved;
        },

        async saveAlerts() {
            // Only the fields that changed (the routes keep their stored shape).
            let prev = {};
            try { prev = JSON.parse(this.alertsSnapshot || '{}'); } catch (e) { prev = {}; }
            const curr = this._alertsDiffable();
            const body = {};
            for (const field of Object.keys(curr)) {
                if (JSON.stringify(curr[field]) !== JSON.stringify(prev[field])) body[field] = curr[field];
            }
            const emailChanged = this.watchEmailDirty;
            if (!Object.keys(body).length && !emailChanged) {
                showToast(_k('settings.hub.nothing_to_save_no_changes', 'Nothing to save — no changes.'), 'info');
                return;
            }
            this.alerts.saving = true;
            let saved = true;
            if (Object.keys(body).length) {
                try {
                    const res = await window.kazmaSave('/api/settings/alerts', { method: 'PUT', body });
                    this.alertsSnapshot = JSON.stringify(curr);
                    this.botCheck = this._botCheckNote(res && res.bot);
                } catch (e) {
                    saved = false;
                    if (e.body && e.body.code === 'bot_refused') {
                        const note = _k('settings.alerts.bot_refused', 'Not saved: Telegram does not know that bot token. Copy it again from BotFather.');
                        this.botCheck = { note, tone: 'tone-danger' };
                        showToast(note, 'error');
                    } else {
                        showToast(_k('settings.alerts.save_failed', 'Not saved: {error}', { error: e.message }), 'error');
                    }
                }
            }
            // The email is kept and told to Kazma Watch by its own route; its
            // answer is shown under the Kazma Watch buttons.
            if (saved && emailChanged) saved = await this.saveWatchSettings();
            if (saved) showToast(_k('settings.hub.saved', 'Saved.'), 'success');
            // The guard reads its chat at its next page; the page shows
            // what is stored now.
            if (saved && !emailChanged) await this.loadAlerts();
            this.alerts.saving = false;
        },

        // What Telegram said of the alerts bot when it was saved (the save's
        // ``bot``: its name, whether it sees the chat); nothing when the save
        // did not touch it.
        _botCheckNote(check) {
            if (!check) return { note: '', tone: '' };
            if (!check.ok) {
                return { note: _k('settings.alerts.bot_saved_unchecked', 'Saved. Telegram could not be asked about the bot just now: send a test alert to check it.'), tone: 'tone-warning' };
            }
            if (check.chat_ok === false) {
                return { note: _k('settings.alerts.bot_saved_chat_unseen', 'Saved, but {bot} cannot see that chat yet: send it /start from your account, or add it to the group, then send a test alert.', { bot: check.bot }), tone: 'tone-warning' };
            }
            return { note: _k('settings.alerts.bot_saved_ok', 'Alerts now come from {bot}.', { bot: check.bot }), tone: 'tone-success' };
        },

        // ── 1. Where Kazma's alerts go ──
        alertRouteName(route) {
            return ({
                telegram: 'Telegram',
                'telegram-group': _k('settings.hub.route_tg_group', 'Telegram (group)'),
                'alerts-bot': _k('settings.alerts.route_name_alerts_bot', 'the alerts bot'),
                discord: 'Discord',
                slack: 'Slack',
            })[route] || route;
        },

        alertRouteLine(route) {
            if (!this.alerts.ready[route]) return _k('settings.alerts.route_not_set_up', 'Not set up yet');
            const own = this.alerts.botChoice === 'alerts_bot';
            return ({
                telegram: own
                    ? _k('settings.alerts.route_alerts_bot', "The alerts bot's chat")
                    : _k('settings.alerts.route_telegram', 'Your chat with Kazma'),
                'telegram-group': this.alerts.groupChat
                    ? _k('settings.alerts.route_alerts_group', 'Your alerts group (below)')
                    : _k('settings.alerts.route_group', 'The group Kazma posts to'),
                discord: _k('settings.alerts.route_discord', 'The Discord channel Kazma posts to'),
                slack: _k('settings.alerts.route_slack', 'The Slack channel Kazma posts to'),
            })[route] || '';
        },

        async sendTestAlert() {
            // One alert along the SAVED routes (the server reads them), sent
            // the way an alert raised by a background job is.
            if (this.opsTestAlert.sending) return;
            this.opsTestAlert = { sending: true, result: null };
            let result;
            try {
                const resp = await fetch('/api/settings/notifications/test-alert', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                result = resp.ok
                    ? await resp.json()
                    : { ok: false, sent: [], failed: [], error: 'HTTP ' + resp.status };
            } catch (e) {
                result = { ok: false, sent: [], failed: [], error: e.message };
            }
            this.opsTestAlert = { sending: false, result };
            this.loadRecentAlerts();
        },

        testAlertSummary() {
            const res = this.opsTestAlert.result;
            if (!res) return '';
            const sent = (res.sent || []).map(r => this.alertRouteName(r)).join(', ');
            const botFailed = (res.failed || []).includes('alerts-bot');
            const failed = (res.failed || []).filter(r => r !== 'alerts-bot').map(r => this.alertRouteName(r)).join(', ');
            const parts = [];
            if (botFailed) parts.push(_k('settings.alerts.test_alert_bot_failed', "The alerts bot did not take it, so Kazma's chat bot sent it. Check the bot's token, and that the bot can see its chat."));
            if (sent) parts.push(_k('settings.hub.test_alert_sent', 'Delivered to {routes}.', { routes: sent }));
            if (failed) parts.push(_k('settings.alerts.test_alert_failed', 'Not delivered to {routes}: its Test button (Providers & Connectors) says why.', { routes: failed }));
            if (res.error) {
                parts.push(_k('settings.hub.test_alert_error', 'The test alert was not sent: {error}', { error: res.error }));
            } else if (!sent && !failed) {
                parts.push(_k('settings.alerts.test_alert_nowhere', 'No alert route sends anywhere. Set up a chat app in Providers & Connectors, then save a route here.'));
            }
            return parts.join(' ');
        },

        // ── 3. The guard's pager ──
        guardBotText() {
            const g = this.alerts.guard || {};
            if (g.via_alerts_bot) return _k('settings.alerts.guard_via_alerts_bot', 'The alerts bot, to the alerts chat above');
            return g.bot
                ? _k('settings.alerts.guard_bot_ready', "Kazma's Telegram bot")
                : _k('settings.alerts.guard_bot_missing', 'No Telegram bot yet (Providers & Connectors -> Platform connectors)');
        },

        guardRunningText() {
            const g = this.alerts.guard || {};
            if (g.guard === 'running') return _k('settings.alerts.guard_is_running', 'Running');
            if (g.guard === 'silent') return _k('settings.alerts.guard_is_silent', 'Not running (no heartbeat for a minute)');
            return _k('settings.alerts.guard_none', 'No record: Kazma was not started by its guard');
        },

        guardLastTone() {
            const p = this.alerts.guard && this.alerts.guard.pager;
            if (!p) return 'tone-neutral';
            return p.ok ? 'tone-success' : 'tone-danger';
        },

        guardLastPageText() {
            const p = this.alerts.guard && this.alerts.guard.pager;
            if (!p || typeof p.at !== 'number') return _k('settings.alerts.guard_no_page', 'No page since the guard started.');
            const when = window.KazmaFormat.time(p.at * 1000) + ', ' + window.KazmaFormat.relative(p.at * 1000);
            if (p.ok) return _k('settings.alerts.guard_last_ok', 'Last page delivered {when}.', { when });
            if (p.held) return _k('settings.alerts.guard_last_held', 'Last page not sent {when}: the guard could not read its bot or chat, so it holds the page until it can.', { when });
            return _k('settings.alerts.guard_last_failed', 'Last page not delivered {when}: {error}', { when, error: p.error || '' });
        },

        async sendGuardTestPage() {
            if (this.guardTest.busy) return;
            this.guardTest = { busy: true, note: _k('settings.alerts.guard_test_waiting', 'Asked the guard; waiting for its answer…'), tone: 'tone-info' };
            let asked;
            try {
                asked = await window.kazmaSave('/api/settings/alerts/guard-test', { method: 'POST' });
            } catch (e) {
                const code = (e.body && e.body.code) || '';
                this.guardTest = { busy: false, note: this._guardRefusal(code, e.message), tone: 'tone-danger' };
                return;
            }
            const deadline = Date.now() + GUARD_WAIT_MS;
            const url = '/api/settings/alerts/guard-test?id=' + encodeURIComponent(asked.id)
                + '&at=' + encodeURIComponent(asked.requested_at);
            while (Date.now() < deadline) {
                await new Promise(r => setTimeout(r, GUARD_POLL_MS));
                const res = await window.kazmaGetJson(url);
                if (!res || res.state === 'waiting' || res.state === 'sending') continue;
                this.guardTest = { busy: false, note: this._guardAnswer(res), tone: res.state === 'sent' ? 'tone-success' : 'tone-danger' };
                await this.loadAlerts();
                return;
            }
            this.guardTest = { busy: false, note: this._guardAnswer({ state: 'unanswered' }), tone: 'tone-danger' };
        },

        _guardRefusal(code, fallback) {
            const words = {
                no_guard: ['settings.alerts.guard_test_no_guard', 'Kazma was not started by its guard, so nothing can send the test page. Start Kazma through its guard.'],
                guard_silent: ['settings.alerts.guard_test_silent', 'The guard is not running (no heartbeat for a minute).'],
                guard_too_old: ['settings.alerts.guard_test_too_old', 'The running guard is older than test pages: restart the guard once (Restart the guard, in the operations guide).'],
                not_written: ['settings.alerts.guard_test_not_written', 'The request could not be written beside the guard\'s files.'],
            };
            const pair = words[code];
            return pair ? _k(pair[0], pair[1]) : _k('settings.alerts.guard_test_failed', 'The test page was not asked for: {error}', { error: fallback || '' });
        },

        _guardAnswer(res) {
            if (res.state === 'sent') {
                return _k('settings.alerts.guard_test_sent', 'The guard sent the test page to chat {chat}. Check Telegram.', { chat: res.chat || '' });
            }
            if (res.state === 'expired') return _k('settings.alerts.guard_test_expired', 'The guard took the request too late and did not send it. Try again.');
            if (res.state === 'unanswered') return _k('settings.alerts.guard_test_unanswered', 'The guard did not answer. Is it running? Check its log (guard.log).');
            return _k('settings.alerts.guard_test_not_sent', 'The guard could not send the test page: {error}', { error: res.error || '' });
        },

        // ── 4. Kazma Watch ──
        watchObserver() {
            return (this.alerts.watch && this.alerts.watch.observer) || {};
        },

        watchZoneText() {
            const told = this.watchObserver().settings || {};
            const ours = (this.alerts.watch && this.alerts.watch.timezone) || '';
            if (!told.timezone) return _k('settings.alerts.watch_zone_untold', 'Not told yet: its cards use UTC. Press "Tell Kazma Watch".');
            if (ours && told.timezone !== ours) {
                return _k('settings.alerts.watch_zone_stale', '{zone} (Kazma\'s is now {ours}: press "Tell Kazma Watch")', { zone: told.timezone, ours });
            }
            return told.timezone;
        },

        _watchError(code) {
            const words = {
                bad_token: ['settings.alerts.watch_err_bad_token', 'Kazma Watch refused the pairing token. Paste its PAIRING_TOKEN again.'],
                bad_url: ['settings.alerts.watch_err_bad_url', 'The observer\'s address must start with https://.'],
                unreachable: ['settings.alerts.watch_err_unreachable', 'Kazma Watch did not answer. Check the address and try again.'],
                refused: ['settings.alerts.watch_err_refused', 'Kazma Watch refused the request.'],
                not_restricted: ['settings.alerts.watch_err_not_restricted', 'The pairing file could not be kept to this account, so the key was not written.'],
                not_paired: ['settings.alerts.watch_err_not_paired', 'This install is not paired.'],
                too_soon: ['settings.alerts.watch_err_too_soon', 'A test page was just sent. Wait 20 seconds.'],
                bad_timezone: ['settings.alerts.watch_err_bad_timezone', 'Kazma Watch does not know Kazma\'s time zone. Check it in Settings -> Memory.'],
                bad_email: ['settings.alerts.watch_err_bad_email', 'Kazma Watch refused the email address.'],
            };
            const pair = words[code];
            return pair ? _k(pair[0], pair[1]) : _k('settings.alerts.watch_err_other', 'Kazma Watch said: {code}', { code: code || '?' });
        },

        async pairWatch() {
            if (this.watchBusy) return;
            this.watchBusy = 'pair';
            this.watchResult = { note: '', tone: '' };
            const token = this.watchPair.token;
            // The token is used once and forgotten here too.
            this.watchPair.token = '';
            try {
                const res = await window.kazmaSave('/api/settings/alerts/watch/pair', {
                    method: 'POST', body: { token, url: this.watchPair.url },
                });
                const told = res.told && res.told.ok;
                this.watchResult = {
                    note: told
                        ? _k('settings.alerts.watch_paired', 'Paired. The guard sends its first heartbeat within a minute.')
                        : _k('settings.alerts.watch_paired_untold', 'Paired, but Kazma Watch was not told the time zone yet: press "Tell Kazma Watch".'),
                    tone: told ? 'tone-success' : 'tone-warning',
                };
                await this.loadAlerts();
            } catch (e) {
                this.watchResult = { note: this._watchError((e.body && e.body.code) || ''), tone: 'tone-danger' };
            }
            this.watchBusy = '';
        },

        async unpairWatch() {
            if (this.watchBusy) return;
            const answer = await window.kazmaConfirm({
                title: _k('settings.alerts.watch_unpair_title', 'Stop Kazma Watch watching this install?'),
                message: _k('settings.alerts.watch_unpair_message', 'Kazma Watch stops checking this machine and paging you about it. You can pair again later.'),
                confirmText: _k('settings.alerts.watch_unpair', 'Unpair'),
                danger: true,
                checkbox: _k('settings.alerts.watch_unpair_forget', 'Also delete what Kazma Watch kept about it (its incidents and uptime)'),
            });
            const ok = answer && typeof answer === 'object' ? answer.ok : answer;
            if (!ok) return;
            const forget = !!(answer && typeof answer === 'object' && answer.checked);
            this.watchBusy = 'unpair';
            try {
                await window.kazmaSave('/api/settings/alerts/watch/unpair', { method: 'POST', body: { forget } });
                this.watchResult = { note: _k('settings.alerts.watch_unpaired', 'Unpaired. Kazma Watch no longer watches this install.'), tone: 'tone-success' };
                await this.loadAlerts();
            } catch (e) {
                this.watchResult = { note: this._watchError((e.body && e.body.code) || ''), tone: 'tone-danger' };
            }
            this.watchBusy = '';
        },

        async testWatch() {
            if (this.watchBusy) return;
            this.watchBusy = 'test';
            this.watchResult = { note: '', tone: '' };
            try {
                const res = await window.kazmaSave('/api/settings/alerts/watch/test', { method: 'POST' });
                this.watchResult = { note: this._watchChannels(res), tone: res.ok ? 'tone-success' : 'tone-danger' };
            } catch (e) {
                const body = e.body || {};
                this.watchResult = body.channels
                    ? { note: this._watchChannels(body), tone: 'tone-danger' }
                    : { note: this._watchError(body.code || ''), tone: 'tone-danger' };
            }
            this.watchBusy = '';
        },

        _watchChannels(res) {
            const ch = res.channels || {};
            const state = (v) => ({
                sent: _k('settings.alerts.channel_sent', 'took it'),
                failed: _k('settings.alerts.channel_failed', 'refused it'),
                off: _k('settings.alerts.channel_off', 'not set up'),
            })[v] || String(v || '');
            const reason = ((res.reasons || {}).email) || '';
            let email = state(ch.email);
            if (ch.email === 'failed' && reason) {
                email = _k('settings.alerts.channel_failed_because', 'refused it: {reason}', { reason: this._emailReason(reason) });
            } else if (ch.email === 'off' && !this._watchEmailSaved) {
                // Off because Kazma Watch was told no address, not because its
                // email is missing on Cloudflare's side.
                email = _k('settings.alerts.channel_off_no_address', 'no address yet: enter one above and press Save');
            }
            return _k('settings.alerts.watch_test_result', 'Kazma Watch sent a test page. Telegram: {telegram}. Email: {email}.', {
                telegram: state(ch.telegram), email,
            });
        },

        // Cloudflare Email Service's refusal codes, worded for the owner.
        _emailReason(code) {
            const key = ({
                E_SENDER_NOT_VERIFIED: 'sender',
                E_SENDER_DOMAIN_NOT_AVAILABLE: 'sender',
                E_RECIPIENT_NOT_ALLOWED: 'recipient',
                E_RECIPIENT_SUPPRESSED: 'suppressed',
                E_RATE_LIMIT_EXCEEDED: 'limit',
                E_DAILY_LIMIT_EXCEEDED: 'limit',
            })[code];
            if (key === 'sender') return _k('settings.alerts.email_reason_sender', 'alerts.kazma.ai is not verified for sending in Cloudflare yet');
            if (key === 'recipient') return _k('settings.alerts.email_reason_recipient', 'Cloudflare does not allow sending to that address');
            if (key === 'suppressed') return _k('settings.alerts.email_reason_suppressed', 'that address bounced before, so Cloudflare blocks it');
            if (key === 'limit') return _k('settings.alerts.email_reason_limit', "Cloudflare's sending limit is reached");
            return _k('settings.alerts.email_reason_other', 'Cloudflare refused it ({code})', { code });
        },

        /** Keep the email and tell Kazma Watch it and the zone. True when it did. */
        async saveWatchSettings() {
            if (this.watchBusy) return false;
            this.watchBusy = 'tell';
            this.watchResult = { note: '', tone: '' };
            let ok = false;
            try {
                const res = await window.kazmaSave('/api/settings/alerts/watch/settings', {
                    method: 'PUT', body: { email_to: String(this.alerts.watchEmail || '').trim() },
                });
                this.watchResult = { note: _k('settings.alerts.watch_told', 'Kazma Watch now writes times in {zone}.', { zone: (res.told && res.told.settings && res.told.settings.timezone) || 'UTC' }), tone: 'tone-success' };
                ok = true;
                this._watchEmailSaved = String(this.alerts.watchEmail || '').trim();
                await this.loadAlerts();
            } catch (e) {
                const body = e.body || {};
                const code = (body.told && body.told.error) || body.code || '';
                this.watchResult = {
                    note: code === 'rejected'
                        ? _k('settings.alerts.watch_err_bad_email', 'Kazma Watch refused the email address.')
                        : this._watchError(code),
                    tone: 'tone-danger',
                };
            }
            this.watchBusy = '';
            return ok;
        },

        _OUTSIDE_CONDITIONS: {
            up: ['settings.outside_watch_cond_up', 'Up'],
            maintenance: ['settings.outside_watch_cond_maintenance', 'Planned maintenance'],
            machine_unreachable: ['settings.outside_watch_cond_machine_unreachable', 'The machine is not reachable (power, network or sleep)'],
            tunnel_down: ['settings.outside_watch_cond_tunnel_down', 'The tunnel is down; Kazma itself is running'],
            kazma_restarting: ['settings.outside_watch_cond_kazma_restarting', 'Kazma is down; the guard is restarting it'],
            kazma_down: ['settings.outside_watch_cond_kazma_down', 'Kazma is down'],
            dependency_down: ['settings.outside_watch_cond_dependency_down', 'Kazma is up; a dependency is away'],
            guard_gone: ['settings.outside_watch_cond_guard_gone', 'The guard stopped; Kazma is unsupervised'],
            disk_critical: ['settings.outside_watch_cond_disk_critical', 'The disk is almost full'],
            observer_refused: ['settings.outside_watch_cond_observer_refused', 'The observer cannot pass Cloudflare Access'],
            unreachable: ['settings.outside_watch_cond_unreachable', 'Kazma is not reachable']
        },

        _OUTSIDE_GUARD: {
            reached: ['settings.outside_watch_guard_reached', 'Delivered'],
            reaching: ['settings.outside_watch_guard_reaching', 'Starting'],
            unreachable: ['settings.outside_watch_guard_unreachable', 'The observer did not answer'],
            rejected: ['settings.outside_watch_guard_rejected', 'The observer refused it: pair again'],
            key_unprotected: ['settings.outside_watch_guard_key_unprotected', 'The key file could not be restricted to this account, so it is not used'],
            key_invalid: ['settings.outside_watch_guard_key_invalid', 'The key file cannot be read: pair again'],
            bad_url: ['settings.outside_watch_guard_bad_url', "The observer's address is not https"],
            off: ['settings.outside_watch_guard_off', 'Off'],
            none: ['settings.outside_watch_guard_none', 'No record: Kazma was not started by the guard']
        },

        outsideCondition(condition) {
            const pair = this._OUTSIDE_CONDITIONS[condition];
            return pair ? _k(pair[0], pair[1]) : String(condition || '');
        },

        outsideGuardState(guard) {
            const pair = this._OUTSIDE_GUARD[(guard && guard.state) || 'none'];
            return pair ? _k(pair[0], pair[1]) : String(guard.state);
        },

        outsideLastAck(guard) {
            if (!guard || typeof guard.last_ack !== 'number') return _k('settings.outside_watch_never', 'None yet');
            return window.KazmaFormat.relative(guard.last_ack * 1000);
        },

        outsideUptime(observer) {
            if (!observer || typeof observer.up_7d_pct !== 'number') {
                return _k('settings.outside_watch_no_readings', 'No readings yet');
            }
            return _k('settings.outside_watch_up_pct', 'Up {pct}% of the time',
                { pct: window.KazmaFormat.number(observer.up_7d_pct) });
        },

        outsideIncidentWhen(incident) {
            const began = _k('settings.outside_watch_started', 'began {when}',
                { when: window.KazmaFormat.relative(incident.started_at * 1000) });
            const end = incident.ended_at
                ? _k('settings.outside_watch_ended', 'ended {when}',
                    { when: window.KazmaFormat.relative(incident.ended_at * 1000) })
                : _k('settings.outside_watch_ongoing', 'still going on');
            return began + ', ' + end;
        },

        // ── 6. Recent alerts ──
        async loadRecentAlerts() {
            this.recentAlerts.loading = true;
            const data = await window.kazmaGetJson('/api/settings/alerts/recent');
            if (!data || !Array.isArray(data.items)) {
                this.recentAlerts = { loaded: true, failed: true, loading: false, items: [], errors: [] };
                return;
            }
            this.recentAlerts = {
                loaded: true, failed: false, loading: false,
                items: data.items, errors: Array.isArray(data.errors) ? data.errors : [],
            };
        },

        recentWhen(item) {
            return window.KazmaFormat.dateTime(item.at * 1000);
        },

        recentSource(item) {
            return ({
                kazma: 'Kazma',
                guard: _k('settings.alerts.source_guard', 'Guard'),
                watch: _k('settings.alerts.source_watch', 'Kazma Watch'),
            })[item.source] || String(item.source || '');
        },

        recentSeverity(item) {
            return ({
                info: _k('settings.alerts.sev_info', 'Info'),
                success: _k('settings.alerts.sev_success', 'Good news'),
                warn: _k('settings.alerts.sev_warn', 'Warning'),
                error: _k('settings.alerts.sev_error', 'Error'),
                critical: _k('settings.alerts.sev_critical', 'Critical'),
            })[item.severity] || String(item.severity || '');
        },

        recentSeverityClass(item) {
            return ({
                info: 'badge-info', success: 'badge-success', warn: 'badge-warning',
                error: 'badge-danger', critical: 'badge-danger',
            })[item.severity] || 'badge-neutral';
        },

        recentTitleIsCatalog(item) {
            return lang() === 'ar' && !!item.title_ar;
        },

        recentTitle(item) {
            return this.recentTitleIsCatalog(item) ? item.title_ar : String(item.title || item.key || '');
        },

        recentHeld(item) {
            return window.kazmaCount('settings.alerts.held', item.held, { n: window.KazmaFormat.number(item.held) });
        },

        recentTaken(item) {
            if (item.delivered === true) return _k('settings.alerts.taken_yes', 'Delivered');
            if (item.delivered === false) return _k('settings.alerts.taken_no', 'Not delivered');
            if (item.delivered === 'kept') return _k('settings.alerts.taken_kept', 'Bell and digest only');
            return _k('settings.alerts.taken_unknown', 'Unknown');
        },

        recentErrorsText() {
            const names = this.recentAlerts.errors.map(src => this.recentSource({ source: src })).join(', ');
            return _k('settings.alerts.recent_partial', 'Could not read the alerts of: {names}.', { names });
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
