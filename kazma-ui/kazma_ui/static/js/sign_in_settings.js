/**
 * Stronger sign-in (Spaces S4-02), on Settings.
 *
 * My account → Second factor (secondFactorPanel): turn on a code from an
 * authenticator app -- the secret is shown once, as text and as a link the
 * app reads; a first code turns it on, and the ten recovery codes are shown
 * once -- or turn it off with a code.
 *
 * People → Invitations (invitationsPanel): a link that works once, for a few
 * hours or days; shown once, then only its state is listed, with Revoke.
 *
 * People → each person (personSignIn): their open sessions, each with End,
 * and Reset second factor when they lost the phone and the codes.
 */
(function () {
    'use strict';

    function say(key, english, vars) {
        return typeof window.kazmaT === 'function' ? window.kazmaT(key, english, vars) : english;
    }

    function iso(text) {
        return '\u2068' + String(text == null ? '' : text) + '\u2069';
    }

    async function call(url, init) {
        const resp = await fetch(url, Object.assign({ credentials: 'same-origin' }, init || {}));
        let body = null;
        try { body = await resp.json(); } catch (e) { body = null; }
        return { ok: resp.ok && !!(body && body.ok), status: resp.status, body: body || {} };
    }

    function post(url, data) {
        return call(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data || {}),
        });
    }

    // A refusal's code, in the page's language.
    function refusal(body) {
        const code = body && body.error;
        const words = {
            wrong_code: say('sign_in.err_wrong_code', 'That code is not right.'),
            already_on: say('sign_in.err_already_on', 'A second factor is on already.'),
            not_on: say('sign_in.err_not_on', 'No second factor is on.'),
            nothing_begun: say('sign_in.err_nothing_begun', 'Start again: no second factor is waiting.'),
            begun_too_long_ago: say('sign_in.err_too_long_ago', 'That was too long ago. Start again.'),
            no_vault: say('sign_in.err_no_vault', 'A second factor is kept in the vault, and this install has none.'),
            own_sign_in_only: say('sign_in.err_own_sign_in', 'Set this from your own sign-in.'),
            unavailable: say('sign_in.err_unavailable', 'This cannot be read right now. Try again.'),
            no_such_session: say('sign_in.err_no_session', 'That session has ended already.'),
            second_factor_paused: say('sign_in.err_paused', 'Too many wrong codes: codes are not checked for a while. Try again later.'),
            session_only: say('sign_in.err_session_only', "This is done from a signed-in admin's session, never with the install's secret or a token."),
            own_in_my_account: say('sign_in.err_own_in_my_account', 'Your own second factor is turned off in My account, with a code.'),
            bad_hours: say('sign_in.err_bad_hours', 'An invitation lasts 1 to 72 hours.'),
            spaces_off: say('sign_in.err_spaces_off', 'Invitations work once Spaces is on.'),
            preconditions_missing: say('sign_in.err_not_ready', 'This install is not ready for a second person yet.'),
        };
        return words[code] || say('sign_in.err_failed', 'That did not work. Try again.');
    }

    function toast(message, tone) {
        if (typeof window.showToast === 'function') window.showToast(message, tone);
    }

    function secondFactorPanel() {
        return {
            loaded: false,
            failed: false,
            // A sign-in with no account of its own (an OIDC session, a token):
            // the panel is not drawn.
            hidden: false,
            on: false,
            since: '',
            recoveryLeft: 0,
            // On this computer the auto-login signs the owner in with no
            // code; the install's secret is a key the second factor does not
            // cover; a pause wrong codes made (the review of S4-02).
            autoLoginHere: false,
            installSecret: false,
            lastPause: 0,
            busy: false,
            secret: '',
            uri: '',
            code: '',
            offCode: '',
            recoveryCodes: [],
            problem: '',

            async init() { await this.load(); },

            async load() {
                const got = await call('/api/account/second-factor?_=' + Date.now());
                if (got.status === 403) { this.hidden = true; return; }
                if (!got.ok) { this.failed = true; return; }
                this.failed = false;
                this.on = !!got.body.on;
                this.since = got.body.since || '';
                this.recoveryLeft = Number(got.body.recovery_left || 0);
                this.autoLoginHere = !!got.body.auto_login_here;
                this.installSecret = !!got.body.install_secret;
                this.lastPause = Number(got.body.last_pause || 0);
                this.loaded = true;
            },

            sinceText() {
                if (!this.since) return '';
                const when = window.KazmaFormat ? window.KazmaFormat.date(this.since) : this.since;
                return say('sign_in.on_since', 'On since {when}.', { when: when });
            },

            pausedText() {
                if (!this.lastPause) return '';
                const when = window.KazmaFormat ? window.KazmaFormat.dateTime(this.lastPause) : '';
                return say('sign_in.paused_note',
                    'Wrong codes paused the checks of your codes at {when}. If that was not you, change your password.',
                    { when: when });
            },

            leftText() {
                const n = Number(this.recoveryLeft || 0);
                return typeof window.kazmaCount === 'function'
                    ? window.kazmaCount('sign_in.recovery_left', n,
                        { n: window.KazmaFormat ? window.KazmaFormat.number(n) : String(n) })
                    : String(n);
            },

            async begin() {
                this.busy = true;
                this.problem = '';
                const got = await post('/api/account/second-factor/begin');
                this.busy = false;
                if (!got.ok) { this.problem = refusal(got.body); return; }
                this.secret = got.body.secret || '';
                this.uri = got.body.uri || '';
                this.code = '';
            },

            async confirm() {
                this.busy = true;
                this.problem = '';
                const got = await post('/api/account/second-factor/confirm', { code: this.code });
                this.busy = false;
                if (!got.ok) { this.problem = refusal(got.body); return; }
                this.recoveryCodes = Array.isArray(got.body.recovery_codes) ? got.body.recovery_codes : [];
                this.secret = '';
                this.uri = '';
                this.code = '';
                toast(say('sign_in.turned_on', 'Your second factor is on.'), 'success');
                await this.load();
            },

            cancel() {
                this.secret = '';
                this.uri = '';
                this.code = '';
                this.problem = '';
            },

            async turnOff() {
                this.busy = true;
                this.problem = '';
                const got = await post('/api/account/second-factor/off', { code: this.offCode });
                this.busy = false;
                if (!got.ok) { this.problem = refusal(got.body); return; }
                this.offCode = '';
                toast(say('sign_in.turned_off', 'Your second factor is off.'), 'success');
                await this.load();
            },

            async copyCodes() {
                try {
                    await navigator.clipboard.writeText(this.recoveryCodes.join('\n'));
                    toast(say('sign_in.copied', 'Copied'), 'success');
                } catch (e) { /* the codes are on the page to write down */ }
            },
        };
    }

    function invitationsPanel() {
        return {
            loaded: false,
            failed: false,
            list: [],
            hours: 72,
            name: '',
            busy: false,
            link: '',
            problem: '',

            async init() { await this.load(); },

            async load() {
                const got = await call('/api/people/invitations?_=' + Date.now());
                if (!got.ok) { this.failed = true; return; }
                this.failed = false;
                this.list = Array.isArray(got.body.invitations) ? got.body.invitations.slice() : [];
                this.loaded = true;
            },

            async make() {
                this.busy = true;
                this.problem = '';
                const got = await post('/api/people/invitations', { name: this.name, hours: Number(this.hours) });
                this.busy = false;
                if (!got.ok) { this.problem = refusal(got.body); return; }
                this.link = window.location.origin + got.body.path;
                this.name = '';
                await this.load();
            },

            async copy() {
                try {
                    await navigator.clipboard.writeText(this.link);
                    toast(say('sign_in.copied', 'Copied'), 'success');
                } catch (e) { /* the link is on the page to copy by hand */ }
            },

            async revoke(invitation) {
                const ok = await window.kazmaConfirm({
                    title: say('sign_in.revoke_title', 'Revoke this invitation?'),
                    message: say('sign_in.revoke_confirm', 'The link stops working now.'),
                    confirmText: say('sign_in.revoke', 'Revoke'),
                    danger: true,
                });
                if (!ok) return;
                const got = await post('/api/people/invitations/' + encodeURIComponent(invitation.id) + '/revoke');
                if (!got.ok) { toast(refusal(got.body), 'error'); return; }
                await this.load();
            },

            statusText(invitation) {
                const words = {
                    open: say('sign_in.invite_open', 'Open'),
                    claimed: say('sign_in.invite_open', 'Open'),
                    used: say('sign_in.invite_used', 'Used'),
                    revoked: say('sign_in.invite_revoked', 'Revoked'),
                    expired: say('sign_in.invite_expired', 'Ran out'),
                };
                return words[invitation.status] || invitation.status;
            },

            untilText(invitation) {
                const when = window.KazmaFormat
                    ? window.KazmaFormat.dateTime(invitation.expires_at) : '';
                return say('sign_in.invite_until', 'Until {when}', { when: when });
            },
        };
    }

    function personSignIn(person) {
        return {
            person: person,
            open: false,
            sessions: [],
            sessionsLoaded: false,
            sessionsFailed: false,
            busy: false,

            async toggle() {
                this.open = !this.open;
                if (this.open) await this.loadSessions();
            },

            async loadSessions() {
                const got = await call('/api/people/' + encodeURIComponent(this.person.id) + '/sessions?_=' + Date.now());
                this.sessionsFailed = !got.ok;
                this.sessions = got.ok && Array.isArray(got.body.sessions) ? got.body.sessions.slice() : [];
                this.sessionsLoaded = got.ok;
            },

            startedText(session) {
                return window.KazmaFormat ? window.KazmaFormat.dateTime(session.started_at) : '';
            },

            // How the session came: signed in, or one of the owner's
            // auto-login kinds (Spaces S4-02).
            originText(session) {
                const words = {
                    this_computer: say('sign_in.via_this_computer', 'This computer (auto-login)'),
                    local_network: say('sign_in.via_local_network', 'The local network (auto-login)'),
                    secret_header: say('sign_in.via_secret_header', "A browser with the install's secret"),
                };
                if (words[session.via]) return words[session.via];
                return session.platform === 'auto-cookie'
                    ? say('sign_in.via_auto', 'Auto-login') : say('sign_in.via_signed_in', 'Signed in');
            },

            async endOne(session) {
                const ok = await window.kazmaConfirm({
                    title: say('sign_in.end_one_title', 'End this session of {name}?', { name: iso(this.person.name) }),
                    message: say('sign_in.end_one_confirm', 'They sign in again on that device. They are told the next time they sign in.'),
                    confirmText: say('sign_in.end', 'End'),
                });
                if (!ok) return;
                const got = await post('/api/people/' + encodeURIComponent(this.person.id)
                    + '/sessions/' + encodeURIComponent(session.id) + '/end');
                if (!got.ok) { toast(refusal(got.body), 'error'); return; }
                await this.loadSessions();
            },

            async resetSecondFactor() {
                const ok = await window.kazmaConfirm({
                    title: say('sign_in.reset_title', "Reset {name}'s second factor?", { name: iso(this.person.name) }),
                    message: say('sign_in.reset_confirm', 'They sign in with their password alone until they turn a new one on. They are told the next time they sign in.'),
                    confirmText: say('sign_in.reset', 'Reset second factor'),
                    danger: true,
                });
                if (!ok) return;
                const got = await post('/api/people/' + encodeURIComponent(this.person.id) + '/second-factor/reset');
                if (!got.ok) { toast(refusal(got.body), 'error'); return; }
                this.person.second_factor = false;
                toast(say('sign_in.reset_done', 'Their second factor is off.'), 'success');
            },
        };
    }

    window.secondFactorPanel = secondFactorPanel;
    window.invitationsPanel = invitationsPanel;
    window.personSignIn = personSignIn;
})();
