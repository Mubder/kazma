/** Settings mixin: ops — appearance, shortcuts, account, tools, system, packages, import, backup */
(function (root) {
    "use strict";
    function _t(key, fallback, vars) {
        if (typeof window.t === "function") {
            var v = vars ? window.t(key, vars) : window.t(key);
            if (v && v !== key) return v;
        }
        var text = fallback || key;
        if (vars && typeof vars === "object") {
            for (var k in vars) {
                if (Object.prototype.hasOwnProperty.call(vars, k)) {
                    text = String(text).split("{" + k + "}").join(vars[k]);
                }
            }
        }
        return text;
    }
    // A value with its own direction (a file name, a version, setting names)
    // inside a translated sentence of a plain-text dialog: Unicode's isolate
    // marks are the plain-text <bdi>. Without them an Arabic preview showed
    // "(Kazma 0.11.0+gae2008f)" as "f0.11.0+gae2008)".
    function _iso(text) {
        return String.fromCharCode(0x2068) + String(text) + String.fromCharCode(0x2069);
    }
    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.ops = function () {
        return {
        acquisitionMonitor: null,
        acquisitionMonitorBusy: false,
        acquisitionMonitorMessage: '',
        async loadAcquisitionMonitor() {
            if (this.acquisitionMonitorBusy) return;
            this.acquisitionMonitorBusy = true;
            try {
                const response = await fetch('/api/settings/acquisition/monitor', { credentials: 'same-origin' });
                if (!response.ok) throw new Error();
                const data = await response.json();
                this.acquisitionMonitor = data.settings;
                this.acquisitionMonitorMessage = data.last_checked_at
                    ? _t('research.monitor.last_checked') + ': ' + window.KazmaFormat.dateTime(data.last_checked_at * 1000)
                    : _t('research.monitor.not_observed');
            } catch (_) { this.acquisitionMonitorMessage = _t('research.ui.status_unavailable'); }
            finally { this.acquisitionMonitorBusy = false; }
        },
        async saveAcquisitionMonitor() {
            if (this.acquisitionMonitorBusy || !this.acquisitionMonitor) return;
            this.acquisitionMonitorBusy = true;
            let saved;
            try {
                saved = await window.kazmaSave('/api/settings/acquisition/monitor', {
                    method: 'PUT', body: this.acquisitionMonitor,
                });
            } catch (_) {
                this.acquisitionMonitorMessage = _t('research.monitor.save_failed');
                this.acquisitionMonitorBusy = false;
                return;
            }
            this.acquisitionMonitorBusy = false;
            this.acquisitionMonitor = saved;
            this.acquisitionMonitorMessage = _t('research.monitor.saved');
        },
        // The daily crawl check (web_acquire/canary.py).
        canary: null,
        canaryBusy: false,
        canaryLine: '',
        canaryReason: '',
        _showCanary(state) {
            if (!state || !state.ran_at) {
                this.canaryLine = _t('research.canary.never');
                this.canaryReason = '';
                return;
            }
            const when = window.KazmaFormat.dateTime(state.ran_at * 1000);
            this.canaryLine = _t(state.ok ? 'research.canary.passed' : 'research.canary.failed',
                state.ok ? 'Last check passed: {when}' : 'Last check failed: {when}', { when: when });
            this.canaryReason = state.ok ? '' : (state.reason || '');
        },
        async loadCanary() {
            if (this.canaryBusy) return;
            this.canaryBusy = true;
            try {
                const response = await fetch('/api/settings/acquisition/canary', { credentials: 'same-origin' });
                if (!response.ok) throw new Error();
                const data = await response.json();
                this.canary = data.settings;
                this._showCanary(data.state);
            } catch (_) { this.canaryLine = _t('research.ui.status_unavailable'); }
            finally { this.canaryBusy = false; }
        },
        async saveCanary() {
            if (this.canaryBusy || !this.canary) return;
            this.canaryBusy = true;
            try {
                this.canary = await window.kazmaSave('/api/settings/acquisition/canary', {
                    method: 'PUT', body: this.canary,
                });
            } catch (_) {
                this.canaryLine = _t('research.canary.save_failed');
            } finally { this.canaryBusy = false; }
        },
        async runCanary() {
            if (this.canaryBusy) return;
            this.canaryBusy = true;
            try {
                const response = await fetch('/api/settings/acquisition/canary/run', {
                    method: 'POST', credentials: 'same-origin',
                });
                if (!response.ok) throw new Error();
                this._showCanary(await response.json());
            } catch (_) { this.canaryLine = _t('research.canary.run_failed'); }
            finally { this.canaryBusy = false; }
        },
        acquisitionBusy: false,
        acquisitionText: '',
        async inspectAcquisition(refreshCredits) {
            if (this.acquisitionBusy) return;
            this.acquisitionBusy = true;
            try {
                const language = window.KAZMA_LANG === 'ar' ? 'ar' : 'en';
                let response;
                if (refreshCredits) {
                    response = await fetch('/api/research/acquisition/credits?language=' + language, {
                        method: 'POST', credentials: 'same-origin',
                    });
                } else {
                    response = await fetch('/api/research/acquisition?language=' + language, {
                        method: 'GET', credentials: 'same-origin',
                    });
                }
                const data = await response.json();
                this.acquisitionText = data.error || data.text || _t('research.ui.status_unavailable');
            } catch (error) {
                this.acquisitionText = _t('research.ui.status_unavailable');
            } finally {
                this.acquisitionBusy = false;
            }
        },
        async saveAppearance() {
            this.saving = true;
            try {
                await window.kazmaSave('/api/settings/appearance', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this._appearanceToSave()),
                });
            } catch (e) {
                showToast(_t('settings.save_failed', 'Save failed'), 'error');
                this.saving = false;
                return;
            }
            this.saving = false;
            const accentChanged = (this.appearance.accent_color || '') !== (this._accentLoaded || '');
            this._accentLoaded = this.appearance.accent_color || '';
            // The save succeeded. Applying it to this page is a separate
            // step, and a failure there must not report the save as failed.
            try {
                // Apply theme immediately. 'auto' resolves to the OS preference
                // (same logic as previewTheme) so the live preview matches the
                // saved state.
                const root = Alpine.$data(document.querySelector('[x-data]'));
                if (root) root.fontSize = this.appearance.font_size;
                const resolved = this.appearance.theme === 'auto'
                    ? this._resolveAutoTheme()
                    : this.appearance.theme;
                if (resolved === 'light' || resolved === 'dark') {
                    if (window.syncDocumentColorScheme) {
                        window.syncDocumentColorScheme(resolved);
                    } else {
                        document.documentElement.setAttribute('data-theme', resolved);
                    }
                }
                if (accentChanged) this._applyAccentColor(this.appearance.accent_color || '');
            } catch (e) {
                console.warn('[settings] appearance saved; applying it to this page failed:', e);
            }
            showToast(_t('settings.appearance_saved', 'Appearance saved'), 'success');
        },

        /* The appearance fields to save: the accent only when the owner
         * changed it ('' = back to the theme's accent). Saving the theme
         * used to store whatever default the page held as the "chosen"
         * accent (the retired indigo #5e6ad2, on every page after that). */
        _appearanceToSave() {
            const body = { theme: this.appearance.theme, font_size: this.appearance.font_size };
            const accent = this.appearance.accent_color || '';
            if (accent !== (this._accentLoaded || '')) body.accent_color = accent;
            return body;
        },

        previewTheme(theme) {
            // Resolve 'auto' to a concrete light/dark via the OS preference
            // BEFORE applying — syncDocumentColorScheme only accepts light/dark
            // (a single explicit color-scheme value is the Phase-0 iOS canvas
            // fix; passing 'auto' would coerce to 'light' and ignore the OS).
            const resolved = theme === 'auto' ? this._resolveAutoTheme() : theme;
            if (resolved === 'light' || resolved === 'dark') {
                if (window.syncDocumentColorScheme) {
                    window.syncDocumentColorScheme(resolved);
                } else {
                    document.documentElement.setAttribute('data-theme', resolved);
                }
            }
        },

        _resolveAutoTheme() {
            try {
                if (window.matchMedia &&
                    window.matchMedia('(prefers-color-scheme: dark)').matches) {
                    return 'dark';
                }
            } catch (_) { /* matchMedia unavailable */ }
            return 'light';
        },

        applyFontSize(size) {
            this.appearance.font_size = size;
            const root = Alpine.$data(document.querySelector('[x-data]'));
            if (root) root.fontSize = size;
        },

        _applyAccentColor(hex) {
            if (typeof window.kazmaApplyAccentColor === 'function') window.kazmaApplyAccentColor(hex);
        },

        captureShortcut(action, event) {
            this.capturingAction = action;

            // Ignore lone modifier presses — wait for the actual key
            const modifierKeys = ['Control', 'Alt', 'Shift', 'Meta'];
            if (modifierKeys.includes(event.key)) {
                return;
            }

            const parts = [];
            if (event.ctrlKey) parts.push('Ctrl');
            if (event.altKey) parts.push('Alt');
            if (event.shiftKey) parts.push('Shift');
            if (event.metaKey) parts.push((typeof navigator !== 'undefined' && navigator.platform.includes('Mac')) ? 'Cmd' : 'Meta');

            // Normalize the key name
            let keyName = event.key;
            // Capitalize single letters
            if (keyName.length === 1) {
                keyName = keyName.toUpperCase();
            }
            // Handle special keys
            const specialMap = {
                ' ': 'Space',
                'ArrowUp': 'Up',
                'ArrowDown': 'Down',
                'ArrowLeft': 'Left',
                'ArrowRight': 'Right',
                'Escape': 'Esc',
                'Delete': 'Del',
                'Backspace': 'Backspace',
                'Enter': 'Enter',
                'Tab': 'Tab',
            };
            keyName = specialMap[keyName] || keyName;

            parts.push(keyName);
            const combo = parts.join('+');

            this.saveShortcut(action, combo);
            this.capturingAction = null;
        },

        clearCaptureHint(action) {
            if (this.capturingAction === action) {
                this.capturingAction = null;
            }
        },

        async saveShortcut(action, keys) {
            const previous = this.shortcuts[action];
            this.shortcuts[action] = keys;
            try {
                await window.kazmaSave('/api/settings/shortcuts', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action, keys }),
                });
                showToast(_t('settings.shortcut_updated', 'Shortcut for "{action}" updated', {action: action}), 'success');
            } catch (e) {
                this.shortcuts[action] = previous; // the server kept the old one
                showToast(_t('settings.ops.shortcut_not_saved', 'Shortcut not saved: ') + e.message, 'error');
            }
            this.shortcutConflicts = this.detectConflicts();
        },

        async resetShortcuts() {
            if (!(await window.kazmaConfirm({
                title: _t('settings.ops.reset_shortcuts', 'Reset shortcuts'),
                message: _t('settings.ops.reset_all_shortcuts_to_defaults', 'Reset all shortcuts to defaults?'),
                confirmText: _t('settings.ops.reset', 'Reset'),
                danger: false,
            }))) return;
            try {
                await window.kazmaSave('/api/settings/shortcuts/reset', { method: 'POST' });
                showToast(_t('settings.shortcuts_reset', 'Shortcuts reset'), 'success');
            } catch (e) {
                showToast(_t('settings.ops.reset_failed', 'Reset failed: ') + e.message, 'error');
            }
            this.shortcuts = await this._fetch('/api/settings/shortcuts') || {};
            this.shortcutConflicts = this.detectConflicts();
        },

        detectConflicts() {
            const conflicts = [];
            const values = Object.entries(this.shortcuts);
            for (let i = 0; i < values.length; i++) {
                for (let j = i + 1; j < values.length; j++) {
                    if (values[i][1] && values[j][1] && values[i][1] === values[j][1]) {
                        conflicts.push({ action1: values[i][0], action2: values[j][0], keys: values[i][1] });
                    }
                }
            }
            return conflicts;
        },

        // One conflict as a sentence. The template built it inline with
        // double quotes inside a double-quoted x-text, which the browser cut
        // at the first quote: the warning threw and never rendered
        // (2026-09-26, tests/test_alpine_templates.py).
        conflictLine(c) {
            return '"' + c.action1 + '" ' + _t('settings.kb_and', 'and') + ' "' + c.action2 + '" '
                + _t('settings.kb_both_use', 'both use') + ' ' + c.keys;
        },

        /* How a session began, in the page's language (the route answers
         * the session's recorded origin: login, oidc, auto-cookie). */
        sessionOrigin(origin) {
            const key = {
                login: 'settings.session_login',
                oidc: 'settings.session_oidc',
                'auto-cookie': 'settings.session_local',
            }[origin];
            return key ? _t(key, origin) : _t('settings.web', 'Web');
        },

        async loadAccount() {
            // The API tokens are the owner's account's and the platform users
            // the admin's (Install): a member's page reads their own sessions
            // alone (Spaces S1-23).
            const installShown = !this.settingsAreas || this.settingsAreas.indexOf('install') !== -1;
            const [tokens, sessions] = await Promise.all([
                installShown ? this._fetch('/api/settings/account/tokens?_=' + Date.now()) : Promise.resolve([]),
                this._fetch('/api/settings/account/sessions?_=' + Date.now()),
            ]);
            // Always replace arrays so Alpine x-for sees a new reference.
            this.apiTokens = Array.isArray(tokens) ? tokens.slice() : [];
            this.apiTokensLoaded = Array.isArray(tokens);
            this.sessions = Array.isArray(sessions) ? sessions.slice() : [];
            this.sessionsLoaded = Array.isArray(sessions);
            if (installShown) await this.loadSaasAdmin();
            else await this.loadOwnKeys();
        },

        /* A member's own model keys (Spaces S2-04): the providers they may
         * bring a key of their own for, and whether one is kept. The route
         * never answers a key. */
        async loadOwnKeys() {
            const data = await this._fetch('/api/account/model-keys?_=' + Date.now());
            if (data && data.ok && Array.isArray(data.providers)) {
                this.ownKeys = data.providers.slice();
                this.ownKeysFailed = false;
            } else {
                this.ownKeys = [];
                this.ownKeysFailed = true;
            }
            this.ownKeysLoaded = true;
        },

        ownKeyState(p) {
            return p && p.own
                ? _t('settings.own_keys_using_own', 'Your own key')
                : _t('settings.own_keys_using_install', "The install's key");
        },

        /* What a refused key says, by the route's code (the provider check's
         * codes and the own-key rules'), in the page's language. */
        _ownKeyWords(code) {
            const words = {
                key_refused: _t('settings.own_keys_err_key_refused', 'The provider refused this key. Nothing was kept.'),
                unreachable: _t('settings.own_keys_err_unreachable', 'The provider could not be reached. Nothing was kept.'),
                rate_limited: _t('settings.own_keys_err_rate_limited', 'The provider is limiting this key right now. Nothing was kept.'),
                timeout: _t('settings.own_keys_err_timeout', 'The provider did not answer in time. Nothing was kept.'),
                bad_key: _t('settings.own_keys_type_key', 'Type the key to keep.'),
                unknown_provider: _t('settings.own_keys_err_unknown_provider', 'You cannot bring a key for this provider.'),
                identity_unavailable: _t('settings.own_keys_unreadable', 'Your keys cannot be read right now. Try again.'),
                vault_unavailable: _t('settings.own_keys_err_vault', "Kazma's vault is not set up, so no key can be kept."),
            };
            return words[code] || _t('settings.own_keys_err_failed', 'The key did not work. Nothing was kept.');
        },

        async saveOwnKey(p) {
            const key = p && p.provider ? String(this.ownKeyDraft[p.provider] || '').trim() : '';
            if (!key) { showToast(_t('settings.own_keys_type_key', 'Type the key to keep.'), 'error'); return; }
            this.ownKeyBusy = Object.assign({}, this.ownKeyBusy, { [p.provider]: true });
            try {
                const resp = await fetch('/api/account/model-keys/' + encodeURIComponent(p.provider), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ key: key }),
                });
                let data = {};
                try { data = await resp.json(); } catch (e) { data = {}; }
                if (resp.ok && data.ok) {
                    this.ownKeyDraft = Object.assign({}, this.ownKeyDraft, { [p.provider]: '' });
                    showToast(_t('settings.own_keys_saved', 'Your own key is kept. Your calls to {provider} use it now.', { provider: p.label }), 'success');
                    await this.loadOwnKeys();
                } else {
                    showToast(this._ownKeyWords(data && data.code), 'error');
                }
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', { error: e.message }), 'error');
            } finally {
                this.ownKeyBusy = Object.assign({}, this.ownKeyBusy, { [p.provider]: false });
            }
        },

        async removeOwnKey(p) {
            const ok = await window.kazmaConfirm({
                title: _t('settings.own_keys_remove', "Use the install's key"),
                message: _t('settings.own_keys_remove_confirm',
                    "Forget your own key for {provider}? Your calls to it will use the install's key and count against your daily limit.",
                    { provider: p.label }),
                confirmText: _t('settings.own_keys_remove', "Use the install's key"),
            });
            if (!ok) return;
            try {
                const resp = await fetch('/api/account/model-keys/' + encodeURIComponent(p.provider), {
                    method: 'DELETE',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                let data = {};
                try { data = await resp.json(); } catch (e) { data = {}; }
                if (resp.ok && data.ok) {
                    showToast(_t('settings.own_keys_removed', "Your calls to {provider} use the install's key again.", { provider: p.label }), 'success');
                    await this.loadOwnKeys();
                } else {
                    showToast(this._ownKeyWords(data && data.code), 'error');
                }
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', { error: e.message }), 'error');
            }
        },

        async loadSaasAdmin() {
            try {
                const st = await this._fetch('/api/saas/status');
                this.saasStatus = st || null;
                if (!st) return;
                const isAdmin = st.principal && (st.principal.role === 'admin' || st.principal.source === 'secret');
                if (!isAdmin) return;
                const [usersResp, tenantsResp] = await Promise.all([
                    this._fetch('/api/saas/users'),
                    this._fetch('/api/saas/tenants'),
                ]);
                this.platformUsers = (usersResp && usersResp.users) ? usersResp.users.slice() : [];
                this.platformUsersLoaded = !!(usersResp && Array.isArray(usersResp.users));
                this.tenants = (tenantsResp && tenantsResp.tenants) ? tenantsResp.tenants.slice() : [];
            } catch (e) {
                console.debug('[settings] saas admin load skipped', e);
            }
        },

        async createPlatformUser() {
            if (!this.newUser.username || !this.newUser.password) {
                showToast(_t('settings.username_password_required', 'Username and password required'), 'error');
                return;
            }
            try {
                const resp = await fetch('/api/saas/users', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify(this.newUser),
                });
                const data = await resp.json();
                if (!resp.ok) {
                    showToast(data.error || _t('settings.user_create_failed', 'Failed to create user'), 'error');
                    return;
                }
                showToast(_t('settings.user_created', 'User created'), 'success');
                this.newUser = { username: '', password: '', role: 'operator' };
                await this.loadSaasAdmin();
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        async patchPlatformUser(username, patch) {
            try {
                const resp = await fetch('/api/saas/users/' + encodeURIComponent(username), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify(patch),
                });
                const data = await resp.json();
                if (!resp.ok) {
                    showToast(data.error || _t('settings.update_failed', 'Update failed'), 'error');
                    return;
                }
                showToast(_t('settings.user_updated', 'User updated'), 'success');
                await this.loadSaasAdmin();
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        async deletePlatformUser(username) {
            if (!await confirm(_t('settings.ops.delete_user_confirm', 'Delete user {name}?', { name: _iso(username) }))) return;
            try {
                const resp = await fetch('/api/saas/users/' + encodeURIComponent(username), {
                    method: 'DELETE',
                    credentials: 'same-origin',
                });
                const data = await resp.json();
                if (!resp.ok) {
                    showToast(data.error || _t('settings.delete_failed', 'Delete failed'), 'error');
                    return;
                }
                showToast(_t('settings.user_deleted', 'User deleted'), 'success');
                await this.loadSaasAdmin();
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        // ── People (Spaces S1-25): create, reset, delete ──
        people: [],
        peopleLoaded: false,
        peopleFailed: false,
        peopleRecordWhole: true,
        closedSpaces: [],
        newPerson: { name: '', password: '' },
        // Shared spaces (Spaces S2-12): a plain one by default.
        sharedSpaces: [],
        newSpace: { name: '', kind: 'sandboxed', folder: '' },
        // Daily limits (Spaces S2-02).
        limitForPeople: { limit: null, set: false, shipped: null },
        limitDraftForPeople: { tokens: '', usd: '' },
        limitEditing: '',
        limitDraft: { tokens: '', usd: '' },
        // Models per person (Spaces S2-03): every model the install offers.
        peopleModelOptions: [],
        // Limits per space (Spaces S2-08): bytes are shown and typed in MiB.
        spaceUsage: [],
        spaceUsageLoaded: false,
        spaceLimitNames: ['disk_bytes', 'documents_bytes', 'knowledge_bytes', 'crawls', 'research', 'swarm'],
        limitsForSpaces: { limits: null, set: false, shipped: null },
        spaceLimitsDraft: {},
        spaceLimitEditing: '',
        spaceLimitDraftOne: {},

        _peopleError(data) {
            const code = (data && data.error) || 'failed';
            return _t('settings.people_err.' + code, _t('settings.people_err.failed', 'That did not work. Try again.'));
        },

        async loadPeople() {
            this.peopleFailed = false;
            const data = typeof window.kazmaGetJson === 'function'
                ? await window.kazmaGetJson('/api/people') : null;
            if (!data || !Array.isArray(data.people)) {
                this.peopleFailed = true;
                return;
            }
            this.people = data.people;
            this.closedSpaces = Array.isArray(data.closed_spaces) ? data.closed_spaces : [];
            this.sharedSpaces = Array.isArray(data.shared_spaces) ? data.shared_spaces : [];
            this.peopleRecordWhole = data.record_whole !== false;
            if (data.limit_for_people && data.limit_for_people.limit) {
                this.limitForPeople = data.limit_for_people;
                this.limitDraftForPeople = {
                    tokens: String(data.limit_for_people.limit.tokens || ''),
                    usd: String(data.limit_for_people.limit.usd || 0),
                };
            }
            const models = typeof window.kazmaGetJson === 'function'
                ? await window.kazmaGetJson('/api/models/options') : null;
            this.peopleModelOptions = models && Array.isArray(models.options)
                ? models.options.map((o) => o.ref) : [];
            this.peopleLoaded = true;
            await this.loadSpaceUsage();
        },

        // ── Limits per space (Spaces S2-08) ──
        async loadSpaceUsage() {
            const data = typeof window.kazmaGetJson === 'function'
                ? await window.kazmaGetJson('/api/people/spaces/usage') : null;
            if (!data || !Array.isArray(data.spaces)) {
                this.spaceUsageLoaded = false;
                return;
            }
            this.spaceUsage = data.spaces;
            this.limitsForSpaces = { limits: data.limits_for_spaces, set: !!data.set, shipped: data.shipped };
            this.spaceLimitsDraft = this._spaceDraft(data.limits_for_spaces || {});
            this.spaceUsageLoaded = true;
        },

        _isBytes(name) {
            return /_bytes$/.test(name);
        },

        // A limit as typed: MiB for bytes, the number itself for work at once.
        _spaceDraft(limits) {
            const draft = {};
            for (const name of this.spaceLimitNames) {
                const value = limits[name];
                draft[name] = value == null ? '' : String(this._isBytes(name)
                    ? Math.round(Number(value) / 1048576) : Number(value));
            }
            return draft;
        },

        _spaceLimitsBody(draft) {
            const body = {};
            for (const name of this.spaceLimitNames) {
                const raw = String(draft[name] == null ? '' : draft[name]).trim();
                if (raw === '') continue;
                const number = Number(raw);
                body[name] = this._isBytes(name) ? Math.round(number * 1048576) : Math.round(number);
            }
            return body;
        },

        spaceLimitLabel(name) {
            const words = {
                disk_bytes: 'Files (MiB)', documents_bytes: 'Documents (MiB)', knowledge_bytes: 'Knowledge (MiB)',
                crawls: 'Crawls at once', research: 'Research at once', swarm: 'Swarm tasks at once',
            };
            return _t('settings.space_limit.' + name, words[name] || name);
        },

        _mib(bytes) {
            const n = Number(bytes) / 1048576;
            const shown = window.KazmaFormat
                ? window.KazmaFormat.number(n, { maximumFractionDigits: n < 10 ? 1 : 0 }) : n.toFixed(n < 10 ? 1 : 0);
            return _t('settings.people_space_mib', '{size} MiB', { size: shown });
        },

        spaceUsedText(space, name) {
            const used = space[name];
            const limit = (space.limits || {})[name];
            if (name === 'disk_bytes' && space.has_folder === false) {
                return _t('settings.people_space_usage_install_files', "The install's own folders");
            }
            if (used == null || limit == null) return _t('settings.people_space_usage_unknown', 'Not counted');
            if (this._isBytes(name)) {
                return _t('settings.people_space_usage_of', '{used} of {limit}',
                    { used: this._mib(used), limit: this._mib(limit) });
            }
            return _t('settings.people_space_usage_of', '{used} of {limit}',
                { used: String(used), limit: String(limit) });
        },

        async _putSpaceLimits(url, limits) {
            try {
                await window.kazmaSave(url, { method: 'PUT', body: { limits: limits } });
            } catch (e) {
                showToast(this._peopleError(e && e.body), 'error');
                return false;
            }
            showToast(_t('settings.people_space_limits_saved', 'Limits saved.'), 'success');
            await this.loadSpaceUsage();
            return true;
        },

        async saveLimitsForSpaces() {
            await this._putSpaceLimits('/api/people/space-limits', this._spaceLimitsBody(this.spaceLimitsDraft));
        },

        async useShippedSpaceLimits() {
            await this._putSpaceLimits('/api/people/space-limits', null);
        },

        // The space's own limits only: a field left empty takes the limits
        // for spaces (its placeholder shows the one in force).
        editSpaceLimits(space) {
            this.spaceLimitDraftOne = this._spaceDraft(space.own_limits || {});
            this.spaceLimitEditing = space.id;
        },

        async saveSpaceLimits(space) {
            if (!space) return;
            const done = await this._putSpaceLimits('/api/people/spaces/' + encodeURIComponent(space.id) + '/limits',
                this._spaceLimitsBody(this.spaceLimitDraftOne));
            if (done) this.spaceLimitEditing = '';
        },

        async clearSpaceLimits(space) {
            if (!space) return;
            const done = await this._putSpaceLimits('/api/people/spaces/' + encodeURIComponent(space.id) + '/limits', null);
            if (done) this.spaceLimitEditing = '';
        },

        _tokensText(n) {
            return window.kazmaCount('settings.people_tokens', Number(n) || 0,
                { n: window.KazmaFormat ? window.KazmaFormat.number(Number(n) || 0) : String(n) });
        },

        _dollars(usd) {
            return window.KazmaFormat
                ? window.KazmaFormat.number(Number(usd) || 0, { style: 'currency', currency: 'USD' })
                : '$' + (Number(usd) || 0).toFixed(2);
        },

        limitText(limit) {
            if (!limit) return '';
            const tokens = this._tokensText(limit.tokens);
            if (Number(limit.usd) > 0) {
                return _t('settings.people_limit_text', '{tokens} and {usd} a day',
                    { tokens: tokens, usd: this._dollars(limit.usd) });
            }
            return _t('settings.people_limit_text_tokens', '{tokens} a day', { tokens: tokens });
        },

        personLimitText(person) {
            if (!person.limit) return _t('settings.people_limit_none', "None: the install's own limit");
            const whose = person.limit_source === 'own'
                ? _t('settings.people_limit_own', 'Their own')
                : _t('settings.people_limit_from_default', 'The limit for people');
            return this.limitText(person.limit) + ' (' + whose + ')';
        },

        spentText(person) {
            const spent = person.spent_today;
            if (!spent) return _t('settings.people_spent_unknown', 'Not counted');
            return _t('settings.people_spent_text', '{tokens}, {usd}',
                { tokens: this._tokensText(spent.tokens), usd: this._dollars(spent.cost) });
        },

        async _putLimit(url, limit) {
            // kazmaSave: a 200 that says {"ok": false} is a refusal too.
            try {
                await window.kazmaSave(url, { method: 'PUT', body: { limit: limit } });
            } catch (e) {
                showToast(this._peopleError(e && e.body), 'error');
                return false;
            }
            showToast(_t('settings.people_limit_saved', 'Limit saved.'), 'success');
            await this.loadPeople();
            return true;
        },

        async saveLimitForPeople() {
            await this._putLimit('/api/people/default-limit',
                { tokens: this.limitDraftForPeople.tokens, usd: this.limitDraftForPeople.usd });
        },

        async useShippedLimit() {
            await this._putLimit('/api/people/default-limit', null);
        },

        editLimit(person) {
            const own = person.daily_limit || person.limit || {};
            this.limitDraft = { tokens: String(own.tokens || ''), usd: String(own.usd || 0) };
            this.limitEditing = person.id;
        },

        async savePersonLimit(person) {
            if (!person) return;
            const done = await this._putLimit('/api/people/' + encodeURIComponent(person.id) + '/daily-limit',
                { tokens: this.limitDraft.tokens, usd: this.limitDraft.usd });
            if (done) this.limitEditing = '';
        },

        async toggleFeature(person, feature, on) {
            if (feature.id === 'development' && on && !person.owner) {
                // What turning it on means, said before it is on (S2-12, N21).
                const answer = await window.kazmaConfirm({
                    title: _t('settings.people_development_title', 'Turn on Development for {name}?',
                        { name: _iso(person.name) }),
                    message: _t('settings.people_development_means',
                        'Development, for someone other than you: in a shared space with a project folder that they are in, they get git, GitHub and that project\'s files. They approve their own file and git steps. Their always-ask actions, such as posting on X, and their folder grants come to you to approve: only you can answer those cards.'),
                    confirmText: _t('settings.people_development_turn_on', 'Turn on'),
                });
                if (!answer) {
                    await this.loadPeople();
                    return;
                }
            }
            try {
                await window.kazmaSave('/api/people/' + encodeURIComponent(person.id) + '/features',
                    { method: 'PUT', body: { features: { [feature.id]: !!on } } });
            } catch (e) {
                showToast(this._peopleError(e && e.body), 'error');
                await this.loadPeople();
                return;
            }
            showToast(_t('settings.people_features_saved', 'Features saved.'), 'success');
            await this.loadPeople();
        },

        // ── Tools (Spaces S2-01): single tools per person ──
        personToolGroupLabel(key) {
            if (key === 'chat' || key === 'memory') {
                return _t('settings.people_tools_from_' + key, key === 'chat' ? 'Chat' : 'Memory');
            }
            return _t('settings.feature.' + key, key);
        },

        // A change to one person's list (tools, models) sends the whole list,
        // computed from what People shows: one change at a time, each after
        // the last one's save and reload, so two quick clicks never build the
        // second list from the state before the first (Spaces S2-03).
        _personChange(person, work) {
            const run = async () => {
                const now = (this.people || []).find((q) => q.id === person.id) || person;
                await work(now);
            };
            this._personChanges = (this._personChanges || Promise.resolve()).then(run, run);
            return this._personChanges;
        },

        togglePersonTool(person, tool, on) {
            return this._personChange(person, async (now) => {
                const off = [];
                (now.tools || []).forEach((g) => (g.tools || []).forEach((t) => {
                    const isOn = t.name === tool.name ? !!on : !!t.on;
                    if (!isOn) off.push(t.name);
                }));
                try {
                    await window.kazmaSave('/api/people/' + encodeURIComponent(now.id) + '/tools',
                        { method: 'PUT', body: { off: off } });
                } catch (e) {
                    showToast(this._peopleError(e && e.body), 'error');
                    await this.loadPeople();
                    return;
                }
                showToast(_t('settings.people_tools_saved', 'Tools saved.'), 'success');
                await this.loadPeople();
            });
        },

        // ── Models (Spaces S2-03): which a person may use, and their default ──
        personModels(person) {
            return (person && person.models) || { allowed: null, default: '' };
        },

        personModelOn(person, ref) {
            const m = this.personModels(person);
            return m.allowed === null || m.allowed === undefined || m.allowed.indexOf(ref) >= 0;
        },

        personModelChoices(person) {
            const m = this.personModels(person);
            return m.allowed === null || m.allowed === undefined ? this.peopleModelOptions : m.allowed;
        },

        async _putModels(person, allowed, chosen) {
            try {
                await window.kazmaSave('/api/people/' + encodeURIComponent(person.id) + '/models',
                    { method: 'PUT', body: { allowed: allowed, default: chosen || '' } });
            } catch (e) {
                showToast(this._peopleError(e && e.body), 'error');
                await this.loadPeople();
                return;
            }
            showToast(_t('settings.people_models_saved', 'Models saved.'), 'success');
            await this.loadPeople();
        },

        setPersonModelsEvery(person, every) {
            return this._personChange(person, async (now) => {
                const m = this.personModels(now);
                await this._putModels(now, every ? null : this.peopleModelOptions.slice(),
                    every ? m.default : (this.peopleModelOptions.indexOf(m.default) >= 0 ? m.default : ''));
            });
        },

        togglePersonModel(person, ref, on) {
            return this._personChange(person, async (now) => {
                const m = this.personModels(now);
                const held = (m.allowed === null || m.allowed === undefined)
                    ? this.peopleModelOptions.slice() : m.allowed.slice();
                const allowed = on ? Array.from(new Set(held.concat([ref])))
                    : held.filter((r) => r !== ref);
                await this._putModels(now, allowed, allowed.indexOf(m.default) >= 0 ? m.default : '');
            });
        },

        setPersonModelDefault(person, ref) {
            return this._personChange(person, async (now) => {
                const m = this.personModels(now);
                await this._putModels(now, m.allowed === undefined ? null : m.allowed, ref);
            });
        },

        async clearPersonLimit(person) {
            const done = await this._putLimit('/api/people/' + encodeURIComponent(person.id) + '/daily-limit', null);
            if (done) this.limitEditing = '';
        },

        // ── A space's data (Spaces S1-27): count, export, erase ──
        spaceData: {},
        spaceDataLoaded: false,
        spaceDataBusy: false,
        spaceDataError: '',

        _spaceDataError(data) {
            const code = (data && data.error) || 'failed';
            return _t('settings.space_data_err.' + code, _t('settings.space_data_err.failed', 'That did not work. Try again.'));
        },

        _thisSpace() {
            return (window.kazmaSpace && window.kazmaSpace.id) || '';
        },

        async loadSpaceData() {
            this.spaceDataError = '';
            const space = this._thisSpace();
            if (!space) {
                this.spaceDataError = this._spaceDataError({ error: 'no_such_space' });
                return;
            }
            try {
                const resp = await fetch('/api/spaces/' + encodeURIComponent(space) + '/counts', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    this.spaceDataLoaded = false;
                    this.spaceDataError = this._spaceDataError(data);
                    return;
                }
                this.spaceData = data;
                this.spaceDataLoaded = true;
            } catch (e) {
                this.spaceDataError = this._spaceDataError(null);
            }
            if (this.spaceDataLoaded && !this.spaceData.personal) await this.loadSpaceMembers();
        },

        // ── Who is in a shared space (Spaces S2-12) ──
        spaceMembers: [],
        spaceMembersLoaded: false,
        spaceMembersFailed: false,
        spaceAddChoice: {},

        async loadSpaceMembers() {
            const space = this._thisSpace();
            this.spaceMembersFailed = false;
            const data = typeof window.kazmaGetJson === 'function'
                ? await window.kazmaGetJson('/api/spaces/' + encodeURIComponent(space) + '/members') : null;
            if (!data || !Array.isArray(data.members)) {
                this.spaceMembersFailed = true;
                return;
            }
            this.spaceMembers = data.members;
            this.spaceMembersLoaded = true;
        },

        async _memberRequest(url, method, body) {
            const resp = await fetch(url, {
                method: method, credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: body === undefined ? undefined : JSON.stringify(body),
            });
            const data = await resp.json().catch(() => ({}));
            return { ok: resp.ok, data: data };
        },

        async leaveThisSpace() {
            const space = this._thisSpace();
            const name = this.spaceData.name || space;
            // What goes and what stays is said before they leave (N36).
            const answer = await window.kazmaConfirm({
                title: _t('settings.space_leave_title', 'Leave {name}?', { name: _iso(name) }),
                message: _t('settings.space_leave_confirm',
                    'When you leave, Kazma forgets every fact it filed under you in this space: what you said about yourself and what others said about you. Your messages stay in its chats, as the conversation\'s history. To come back, the admin adds you again.'),
                danger: true,
                confirmText: _t('settings.space_leave', 'Leave this space'),
            });
            if (!answer) return;
            this.spaceDataBusy = true;
            try {
                const done = await this._memberRequest('/api/spaces/' + encodeURIComponent(space) + '/leave', 'POST');
                if (!done.ok) {
                    showToast(this._peopleError(done.data), 'error');
                    return;
                }
                showToast(_t('settings.space_left', 'You left {name}.', { name: _iso(name) }), 'success');
                const mine = await window.kazmaGetJson('/api/account/spaces');
                const next = mine && Array.isArray(mine.spaces) ? mine.spaces.find((s) => s.id !== space) : null;
                if (next && window.kazmaSpace) window.kazmaSpace.switchTo(next.id);
                else window.location.assign('/');
            } catch (e) {
                showToast(this._peopleError(null), 'error');
            } finally {
                this.spaceDataBusy = false;
            }
        },

        async removeFromSpace(spaceId, spaceName, member) {
            const answer = await window.kazmaConfirm({
                title: _t('settings.space_remove_title', 'Take {name} out of {space}?',
                    { name: _iso(member.name), space: _iso(spaceName) }),
                message: _t('settings.space_remove_confirm',
                    'Kazma forgets every fact it filed under them in this space: what they said about themself and what others said about them. Their messages stay in its chats. They are told at their next page load.'),
                danger: true,
                confirmText: _t('settings.space_remove', 'Take out'),
            });
            if (!answer) return;
            try {
                const done = await this._memberRequest('/api/spaces/' + encodeURIComponent(spaceId)
                    + '/members/' + encodeURIComponent(member.id), 'DELETE');
                if (!done.ok) {
                    showToast(this._peopleError(done.data), 'error');
                    return;
                }
                showToast(_t('settings.space_removed', '{name} was taken out.', { name: _iso(member.name) }), 'success');
            } catch (e) {
                showToast(this._peopleError(null), 'error');
                return;
            }
            if (this.tab === 'people') await this.loadPeople();
            else await this.loadSpaceMembers();
        },

        peopleOutside(space) {
            const inside = new Set((space.members || []).map((m) => m.id));
            return (this.people || []).filter((p) => !inside.has(p.id));
        },

        async addToSpace(space) {
            const chosen = this.spaceAddChoice[space.id] || '';
            if (!chosen) return;
            try {
                const done = await this._memberRequest('/api/spaces/' + encodeURIComponent(space.id) + '/members',
                    'POST', { account_id: chosen });
                if (!done.ok) {
                    showToast(this._peopleError(done.data), 'error');
                    return;
                }
                const person = (this.people || []).find((p) => p.id === chosen);
                showToast(_t('settings.space_added', '{name} was added. They are told at their next page load.',
                    { name: _iso(person ? person.name : chosen) }), 'success');
                this.spaceAddChoice[space.id] = '';
            } catch (e) {
                showToast(this._peopleError(null), 'error');
                return;
            }
            await this.loadPeople();
        },

        async exportThisSpace() {
            const space = this._thisSpace();
            // Sealed with a password the person chooses: asked twice, since
            // a mistyped one opens nothing.
            const password = await window.kazmaPrompt({
                title: _t('settings.space_data_export', 'Export this space'),
                message: _t('settings.space_data_export_password',
                    'Choose a password for the export (at least 12 characters). It opens with 7-Zip, WinZip or Keka, not with the zip tools built into Windows or macOS. Kazma does not keep it.'),
                inputType: 'password',
            });
            if (password === null || password === undefined) return;
            if (password.length < 12) {
                showToast(this._spaceDataError({ error: 'password_too_short' }), 'error');
                return;
            }
            const again = await window.kazmaPrompt({
                title: _t('settings.space_data_export', 'Export this space'),
                message: _t('settings.space_data_export_again', 'Type the password again.'),
                inputType: 'password',
            });
            if (again === null || again === undefined) return;
            if (again !== password) {
                showToast(_t('settings.space_data_err.passwords_differ', 'The two passwords differ. Nothing was exported.'), 'error');
                return;
            }
            this.spaceDataBusy = true;
            try {
                const resp = await fetch('/api/spaces/' + encodeURIComponent(space) + '/export', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: password }),
                });
                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    showToast(this._spaceDataError(data), 'error');
                    return;
                }
                const blob = await resp.blob();
                const link = document.createElement('a');
                link.href = URL.createObjectURL(blob);
                link.download = 'kazma-space-' + space + '.zip';
                document.body.appendChild(link);
                link.click();
                link.remove();
                setTimeout(() => URL.revokeObjectURL(link.href), 60000);
                showToast(_t('settings.space_data_exported', 'The space was exported.'), 'success');
            } catch (e) {
                showToast(this._spaceDataError(null), 'error');
            } finally {
                this.spaceDataBusy = false;
            }
        },

        async _eraseSpace(space) {
            const resp = await fetch('/api/spaces/' + encodeURIComponent(space), {
                method: 'DELETE', credentials: 'same-origin',
            });
            const data = await resp.json().catch(() => ({}));
            return { ok: resp.ok, data: data };
        },

        async deleteThisSpace() {
            const space = this._thisSpace();
            const name = this.spaceData.name || space;
            const answer = await window.kazmaConfirm({
                title: _t('settings.space_data_delete_title', 'Delete {name}?', { name: _iso(name) }),
                message: _t('settings.space_data_delete_confirm',
                    'Everything in it is erased for good: its chats, memory, files, documents, libraries, schedules and secrets. Its members leave it. Export it first if you want a copy.'),
                danger: true,
                confirmText: _t('settings.space_data_delete', 'Delete this space'),
            });
            if (!answer) return;
            this.spaceDataBusy = true;
            try {
                const done = await this._eraseSpace(space);
                if (!done.ok) {
                    showToast(this._spaceDataError(done.data), 'error');
                    return;
                }
                showToast(_t('settings.space_data_deleted', '{name} was deleted.', { name: _iso(name) }), 'success');
                // This tab's space is gone: go to another of the person's.
                const mine = await window.kazmaGetJson('/api/account/spaces');
                const next = mine && Array.isArray(mine.spaces) ? mine.spaces.find((s) => s.id !== space) : null;
                if (next && window.kazmaSpace) window.kazmaSpace.switchTo(next.id);
                else window.location.assign('/');
            } catch (e) {
                showToast(this._spaceDataError(null), 'error');
            } finally {
                this.spaceDataBusy = false;
            }
        },

        async eraseClosedSpace(closed) {
            const answer = await window.kazmaConfirm({
                title: _t('settings.people_erase_title', 'Erase the space of {name}?', { name: _iso(closed.name) }),
                message: _t('settings.people_erase_confirm',
                    'Everything their space holds is erased for good. You never see what it holds.'),
                danger: true,
                confirmText: _t('settings.people_erase', 'Erase'),
            });
            if (!answer) return;
            try {
                const done = await this._eraseSpace(closed.id);
                if (!done.ok) {
                    showToast(this._spaceDataError(done.data), 'error');
                    return;
                }
                showToast(_t('settings.people_erased', 'The space of {name} was erased.', { name: _iso(closed.name) }), 'success');
            } catch (e) {
                showToast(this._spaceDataError(null), 'error');
                return;
            }
            await this.loadPeople();
        },

        async createSharedSpace() {
            const draft = this.newSpace;
            try {
                const resp = await fetch('/api/people/spaces', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: draft.name, kind: draft.kind,
                        project_folder: draft.kind === 'host' ? draft.folder : '',
                    }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    showToast(this._peopleError(data), 'error');
                    return;
                }
                showToast(_t('settings.people_space_created', '{name} is made.', { name: _iso(draft.name) }), 'success');
                this.newSpace = { name: '', kind: 'sandboxed', folder: '' };
            } catch (e) {
                showToast(this._peopleError(null), 'error');
                return;
            }
            await this.loadPeople();
        },

        async createPerson() {
            try {
                const resp = await fetch('/api/people', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: this.newPerson.name, password: this.newPerson.password }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    showToast(this._peopleError(data), 'error');
                    return;
                }
                showToast(_t('settings.people_created', '{name} can sign in now.', { name: _iso(this.newPerson.name) }), 'success');
                this.newPerson = { name: '', password: '' };
            } catch (e) {
                showToast(this._peopleError(null), 'error');
                return;
            }
            await this.loadPeople();
        },

        async resetPerson(person) {
            const password = await window.kazmaPrompt({
                title: _t('settings.people_reset', 'Reset password'),
                message: _t('settings.people_reset_prompt',
                    'A new password for {name}. Their sessions end, and they are told at their next sign-in that you reset it.',
                    { name: _iso(person.name) }),
                inputType: 'password',
            });
            if (password === null || password === undefined) return;
            try {
                const resp = await fetch('/api/people/' + encodeURIComponent(person.id) + '/reset', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: password }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    showToast(this._peopleError(data), 'error');
                    return;
                }
                showToast(_t('settings.people_reset_done', 'Password reset. {name} will be told.', { name: _iso(person.name) }), 'success');
            } catch (e) {
                showToast(this._peopleError(null), 'error');
            }
        },

        // ── Disable / enable (Spaces S2-07): their work waits while disabled ──
        async setPersonEnabled(person, enabled) {
            if (!enabled) {
                const ok = await window.kazmaConfirm({
                    title: _t('settings.people_disable_title', 'Disable {name}?', { name: _iso(person.name) }),
                    message: _t('settings.people_disable_confirm',
                        'They can no longer sign in, and their scheduled jobs and other work wait until you enable them again. Their spaces and data stay.'),
                    confirmText: _t('settings.people_disable', 'Disable'),
                });
                if (!ok) return;
            }
            try {
                await window.kazmaSave('/api/people/' + encodeURIComponent(person.id) + '/enabled',
                    { method: 'PUT', body: { enabled: !!enabled } });
            } catch (e) {
                showToast(this._peopleError(e && e.body), 'error');
                return;
            }
            showToast(enabled
                ? _t('settings.people_enabled_done', '{name} is enabled again.', { name: _iso(person.name) })
                : _t('settings.people_disabled_done', '{name} is disabled.', { name: _iso(person.name) }), 'success');
            await this.loadPeople();
        },

        // When a person last signed in (Spaces S2-10): a date in the page's
        // language, or "never".
        lastSignInText(person) {
            if (!person || !person.last_sign_in) return _t('settings.people_never_signed_in', 'Never');
            return window.KazmaFormat ? window.KazmaFormat.dateTime(person.last_sign_in) : person.last_sign_in;
        },

        sessionsText(person) {
            const n = Number((person && person.sessions) || 0);
            return window.kazmaCount('settings.people_sessions_open', n,
                { n: window.KazmaFormat ? window.KazmaFormat.number(n) : String(n) });
        },

        async endPersonSessions(person) {
            if (!person) return;
            const ok = await window.kazmaConfirm({
                title: _t('settings.people_end_sessions_title', 'Sign {name} out everywhere?', { name: _iso(person.name) }),
                message: _t('settings.people_end_sessions_confirm',
                    'Every session they have open ends, and they sign in again on each device. They are told the next time they sign in.'),
                confirmText: _t('settings.people_end_sessions', 'End sessions'),
            });
            if (!ok) return;
            try {
                await window.kazmaSave('/api/people/' + encodeURIComponent(person.id) + '/sessions/end', { method: 'POST' });
            } catch (e) {
                showToast(this._peopleError(e && e.body), 'error');
                return;
            }
            showToast(_t('settings.people_sessions_ended', '{name} is signed out everywhere.', { name: _iso(person.name) }),
                'success');
            await this.loadPeople();
        },

        async deletePerson(person) {
            const ok = await window.kazmaConfirm({
                title: _t('settings.people_delete_title', 'Delete {name}?', { name: _iso(person.name) }),
                message: _t('settings.people_delete_confirm',
                    'They can no longer sign in and leave every space. Their own space stays, closed, until it is erased.'),
                danger: true,
                confirmText: _t('settings.delete', 'Delete'),
            });
            if (!ok) return;
            try {
                const resp = await fetch('/api/people/' + encodeURIComponent(person.id), {
                    method: 'DELETE', credentials: 'same-origin',
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    showToast(this._peopleError(data), 'error');
                    return;
                }
                showToast(_t('settings.people_deleted', '{name} was deleted.', { name: _iso(person.name) }), 'success');
            } catch (e) {
                showToast(this._peopleError(null), 'error');
                return;
            }
            await this.loadPeople();
        },

        async createTenant() {
            if (!this.newTenant.id) {
                showToast(_t('settings.tenant_id_required', 'Tenant id required'), 'error');
                return;
            }
            try {
                const resp = await fetch('/api/saas/tenants', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify(this.newTenant),
                });
                const data = await resp.json();
                if (!resp.ok) {
                    showToast(data.error || _t('settings.failed', 'Failed'), 'error');
                    return;
                }
                showToast(_t('settings.tenant_added', 'Tenant added'), 'success');
                this.newTenant = { id: '', name: '' };
                await this.loadSaasAdmin();
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        async changePassword() {
            if (this.passwordForm.new_password !== this.passwordForm.confirm_password) {
                showToast(_t('settings.passwords_mismatch', 'Passwords do not match'), 'error');
                return;
            }
            if (this.passwordForm.new_password.length < 8) {
                showToast(_t('settings.password_min', 'Password must be at least 8 characters'), 'error');
                return;
            }
            try {
                const resp = await fetch('/api/settings/account/password', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ old_password: this.passwordForm.old_password, new_password: this.passwordForm.new_password }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    showToast(_t('settings.password_changed', 'Password changed'), 'success');
                    this.passwordForm = { old_password: '', new_password: '', confirm_password: '' };
                    this.loadAccount();
                } else {
                    // The route answers a code; the words are the page's.
                    const words = {
                        sign_in_secret: _t('settings.password_is_sign_in_secret',
                            "You signed in with the sign-in secret. It is KAZMA_SECRET in the install's .env file; change it there."),
                        current_password_incorrect: _t('settings.current_password_incorrect', 'The current password is not correct'),
                        password_too_short: _t('settings.password_min', 'Password must be at least 8 characters'),
                    };
                    showToast(words[data.error] || _t('settings.failed', 'Failed'), 'error');
                }
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        /* End one of the person's own sessions (Spaces S4-02): another
         * device signs in again. */
        async endMySession(session) {
            if (!session || !session.id) return;
            const ok = await window.kazmaConfirm({
                title: _t('sign_in.end_mine_title', 'End this session?'),
                message: _t('sign_in.end_mine_confirm', 'That device signs in again.'),
                confirmText: _t('sign_in.end', 'End'),
            });
            if (!ok) return;
            try {
                await window.kazmaSave('/api/account/sessions/' + encodeURIComponent(session.id) + '/end', { method: 'POST' });
            } catch (e) {
                showToast(_t('sign_in.err_failed', 'That did not work. Try again.'), 'error');
                return;
            }
            showToast(_t('sign_in.session_ended', 'That session has ended.'), 'success');
            await this.loadAccount();
        },

        async createToken() {
            if (!this.tokenName) { showToast(_t('settings.token_name_required', 'Token name required'), 'error'); return; }
            try {
                const resp = await fetch('/api/settings/account/tokens', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ name: this.tokenName, role: this.tokenRole || 'operator' }),
                });
                const data = await resp.json();
                const tok = data.token || '';
                this.tokenName = '';
                this.tokenRole = 'operator';
                this.lastCreatedToken = tok;
                await this.loadAccount();
                showToast(tok ? _t('settings.token_created_copy', 'Token created — copy it below (shown once)') : _t('settings.token_created', 'Token created'), tok ? 'success' : 'error');
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        tokenRoleLabel(role) {
            // A token made before tokens chose a role acts as an operator.
            const words = {
                viewer: _t('settings.role_viewer', 'viewer'),
                operator: _t('settings.role_operator', 'operator'),
                admin: _t('settings.role_admin', 'admin'),
            };
            return words[role || 'operator'] || words.viewer;
        },

        async copyLastToken() {
            if (!this.lastCreatedToken) return;
            try {
                await navigator.clipboard.writeText(this.lastCreatedToken);
                showToast(_t('settings.token_copied', 'Token copied to clipboard'), 'success');
            } catch (e) {
                showToast(_t('settings.copy_failed_manual', 'Copy failed — select the token and copy manually'), 'error');
            }
        },

        async copyTokenCurl() {
            if (!this.lastCreatedToken) return;
            const origin = window.location.origin;
            const cmd =
                'curl -s -H "Authorization: Bearer ' + this.lastCreatedToken + '" \\\n  ' +
                origin + '/api/memory/v2/health';
            try {
                await navigator.clipboard.writeText(cmd);
                showToast(_t('settings.curl_copied', 'curl example copied'), 'success');
            } catch (e) {
                showToast(_t('settings.copy_failed', 'Copy failed'), 'error');
            }
        },

        async revokeToken(tokenId) {
            if (!tokenId) {
                showToast(_t('settings.missing_token_id', 'Missing token id'), 'error');
                return;
            }
            if (!(await window.kazmaConfirm({
                title: _t('settings.ops.revoke_token', 'Revoke token'),
                message: _t('settings.ops.revoke_this_token_this_cannot', 'Revoke this token? This cannot be undone. Scripts using it will get 401.'),
                confirmText: _t('settings.ops.revoke', 'Revoke'),
                danger: true,
            }))) return;
            try {
                const id = String(tokenId);
                // Clear the one-time green “copy me” panel if it was this token.
                const doomed = (this.apiTokens || []).find(function(x) {
                    return String(x.id) === id;
                });
                if (doomed && this.lastCreatedToken && doomed.token_prefix
                    && this.lastCreatedToken.indexOf(doomed.token_prefix) === 0) {
                    this.lastCreatedToken = '';
                }

                const resp = await fetch(
                    `/api/settings/account/tokens/${encodeURIComponent(id)}?_=${Date.now()}`,
                    { method: 'DELETE', credentials: 'same-origin', cache: 'no-store' }
                );
                if (!resp.ok) {
                    const err = await resp.json().catch(function() { return {}; });
                    const detail = err.detail || err.error || ('HTTP ' + resp.status);
                    showToast(_t('settings.revoke_failed', 'Revoke failed: {error}', {error: detail}), 'error');
                    await this.loadAccount();
                    return;
                }
                // Force Alpine to drop the row immediately (new array ref).
                this.apiTokens = (this.apiTokens || []).filter(function(x) {
                    return String(x.id) !== id;
                });
                // Re-fetch with cache bust so a stale GET cannot resurrect the row.
                const tokens = await this._fetch('/api/settings/account/tokens?_=' + Date.now());
                this.apiTokens = Array.isArray(tokens) ? tokens.slice() : [];
                this.apiTokensLoaded = Array.isArray(tokens);
                showToast(_t('settings.token_revoked', 'Token revoked'), 'success');
            } catch (e) {
                showToast(_t('settings.revoke_failed', 'Revoke failed: {error}', {error: e.message}), 'error');
            }
        },

        async loadTools() {
            try {
                const data = await this._fetch('/api/settings/tools');
                this.tools = data || [];
                this.toolsLoaded = !!data;
            } catch (e) {
                this.tools = [];
                this.toolsLoaded = false;
            }
        },

        async toggleTool(toolName, enabled) {
            await window.kazmaSave(`/api/settings/tools/${encodeURIComponent(toolName)}/toggle`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled }),
            });
            await this.loadTools();
        },

        async testTool(toolName) {
            let args = {};
            try { args = JSON.parse(this.toolTestArgs); } catch { showToast(_t('settings.invalid_json_args', 'Invalid JSON arguments'), 'error'); return; }
            this.toolTestResult = null;
            try {
                const resp = await fetch(`/api/settings/tools/${encodeURIComponent(toolName)}/test`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ arguments: args }),
                });
                this.toolTestResult = await resp.json();
            } catch (e) {
                this.toolTestResult = { error: e.message };
            }
        },

        get filteredTools() {
            if (!this.toolSearch) return this.tools;
            const q = this.toolSearch.toLowerCase();
            return this.tools.filter(tool =>
                (tool.name || '').toLowerCase().includes(q) ||
                (tool.description || '').toLowerCase().includes(q) ||
                (tool.category || '').toLowerCase().includes(q)
            );
        },

        async loadLogs() {
            try {
                const data = await this._fetch(`/api/settings/system/logs?lines=${this.logLines}`);
                this.logs = data?.lines || [];
                this.logsLoaded = !!(data && Array.isArray(data.lines));
            } catch (e) {
                this.logs = [];
                this.logsLoaded = false;
            }
        },

        async loadDiagnostics() {
            try {
                this.diagnostics = await this._fetch('/api/settings/system/diagnostics') || {};
            } catch (e) {
                this.diagnostics = { error: e.message };
            }
        },

        async loadVaultStatus() {
            try {
                this.vaultStatus = await this._fetch('/api/settings/vault/status') || { enabled: false, secret_count: 0 };
            } catch (e) {
                this.vaultStatus = { enabled: false, secret_count: 0 };
            }
        },

        async loadLogging() {
            try {
                const data = await this._fetch('/api/settings/system/logging');
                if (data) {
                    this.logging = {
                        level: data.level || 'INFO',
                        format: data.format || 'text',
                        retention_days: data.retention_days || 7,
                    };
                }
            } catch (e) { /* keep defaults */ }
        },

        async loadSwarmRetention() {
            try {
                const data = await this._fetch('/api/settings/swarm/task-retention');
                if (data && typeof data.days === 'number') {
                    this.swarmRetention = {
                        days: data.days,
                        default: typeof data.default === 'number' ? data.default : 30,
                        max: typeof data.max === 'number' ? data.max : 3650,
                    };
                }
            } catch (e) { /* keep defaults */ }
        },

        async loadCheckpointRetention() {
            try {
                const data = await this._fetch('/api/settings/checkpoints/retention');
                if (data && typeof data.days === 'number') {
                    this.checkpointRetention = {
                        days: data.days,
                        source: data.source || 'default',
                        default: typeof data.default === 'number' ? data.default : 30,
                        max: typeof data.max === 'number' ? data.max : 3650,
                        keep_per_chat: typeof data.keep_per_chat === 'number' ? data.keep_per_chat : 200,
                        idle_keep: typeof data.idle_keep === 'number' ? data.idle_keep : 10,
                    };
                }
            } catch (e) { /* keep defaults */ }
        },

        async loadReportUrl() {
            const data = window.kazmaGetJson ? await window.kazmaGetJson('/api/support/settings') : null;
            if (data) this.reportUrl = { value: data.report_url || '', default: data.default || '', loaded: true };
        },

        async saveReportUrl() {
            this.reportUrlSaving = true;
            try {
                await window.kazmaSave('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key: 'support.report_url', value: String(this.reportUrl.value || '').trim(),
                        category: 'support' }),
                });
                showToast(_t('settings.report_url_saved', 'Saved: where problem reports go'), 'success');
            } catch (e) {
                showToast(e.message || _t('settings.save_failed', 'Save failed'), 'error');
            }
            this.reportUrlSaving = false;
        },

        async loadA2a() {
            const data = window.kazmaGetJson ? await window.kazmaGetJson('/api/a2a/status') : null;
            if (data) this.a2a = { enabled: !!data.enabled, card: data.card || '', endpoint: data.endpoint || '', loaded: true };
        },

        async saveA2a() {
            this.a2aSaving = true;
            try {
                await window.kazmaSave('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key: 'a2a.enabled', value: !!this.a2a.enabled, category: 'a2a' }),
                });
                showToast(_t('settings.a2a_saved', 'Saved: agent-to-agent'), 'success');
            } catch (e) {
                showToast(e.message || _t('settings.save_failed', 'Save failed'), 'error');
            }
            this.a2aSaving = false;
        },

        async loadTurnTimeout() {
            try {
                const data = await this._fetch('/api/settings/turn-timeout');
                if (data && typeof data.seconds === 'number') {
                    this.turnTimeout = {
                        seconds: data.seconds,
                        source: data.source || 'default',
                        max: typeof data.max === 'number' ? data.max : 86400,
                    };
                }
            } catch (e) { /* keep defaults */ }
        },

        async loadTurnSpending() {
            try {
                const data = await this._fetch('/api/settings/turn-spending');
                if (data && typeof data.tokens === 'number') {
                    this.turnSpending = {
                        tokens: data.tokens,
                        tokensMax: typeof data.tokens_max === 'number' ? data.tokens_max : 100000000,
                        dailyUsd: typeof data.daily_usd === 'number' ? data.daily_usd : 0,
                        dailyMax: typeof data.daily_max === 'number' ? data.daily_max : 1000000,
                    };
                }
            } catch (e) { /* keep defaults */ }
        },

        async loadContainerEngine() {
            try {
                const data = await this._fetch('/api/settings/containers');
                if (data && data.installed) {
                    this.containerEngine = {
                        engine: typeof data.engine === 'string' ? data.engine : '',
                        effective: data.effective || '',
                        loaded: true,
                        installed: { kazma: !!data.installed.kazma, desktop: !!data.installed.desktop },
                    };
                }
            } catch (e) { /* the card stays disabled: nothing loaded */ }
        },

        async loadChatReasoning() {
            try {
                const data = await this._fetch('/api/settings/chat-reasoning');
                if (data && typeof data.level === 'string') {
                    this.chatReasoning = {
                        level: data.level,
                        applies: typeof data.applies === 'boolean' ? data.applies : null,
                        provider: data.provider || '',
                        // What the provider takes of the level: 'all', 'off_only' (Qwen) or 'none'.
                        takes: typeof data.takes === 'string' ? data.takes : null,
                    };
                }
            } catch (e) { /* keep defaults */ }
        },

        async loadPackages() {
            this.pkgLoading = true;
            try {
                const data = await this._fetch('/api/system/packages');
                if (data) {
                    this.pkgCore = data.core || [];
                    this.pkgExtras = data.extras || [];
                    this.pkgTotal = data.total_installed || 0;
                    this.pkgPythonVer = data.python_version || '';
                    this.pkgDbBackend = data.db_backend || 'sqlite';
                    this.pkgDbUrlSet = !!data.db_url_set;
                    this.pkgMemory = data.memory || { status: '', summary: '', headline: '', layers: {}, issues: [] };
                    this.pkgInstall = data.install || { mode: null, at: null, chosen: 0, total: 0 };
                    this.pkgLoaded = true;
                }
            } catch (e) { /* silent */ }
            this.pkgLoading = false;
        },

        // "Installed as Express on 7 Nov 2026: 14 of 23 parts." -- or '' when
        // no install was recorded.
        pkgInstallLine() {
            const inst = this.pkgInstall || {};
            if (!inst.mode) return '';
            const mode = window.kazmaT('packages.mode_' + inst.mode, inst.mode);
            const when = inst.at && window.KazmaFormat
                ? window.KazmaFormat.date(new Date(Number(inst.at) * 1000)) : '';
            return window.kazmaCount('packages.installed_as', Number(inst.total) || 0,
                { mode: mode, date: when, chosen: inst.chosen });
        },

        // The parts Express gained after this install ran (their titles, as
        // the install names them), or '' -- `kazma setup install` adds them.
        pkgNewInExpress() {
            const parts = (this.pkgInstall && this.pkgInstall.new_in_express) || [];
            return parts.length ? parts.join(', ') : '';
        },

        // The parts this install chose that its last run could not put in
        // place (their titles), or '' -- `kazma setup install` finishes them.
        pkgNotInPlace() {
            const parts = (this.pkgInstall && this.pkgInstall.not_in_place) || [];
            return parts.length ? parts.join(', ') : '';
        },

        // An extra's state: "installed" only when its packages AND its part
        // are in place; a part that did not install is "part_missing" (a
        // warning), never green. The page words each from the catalog.
        pkgExtraBadge(extra) {
            if (extra.installed && extra.part_missing) return 'part_missing';
            if (extra.installed) return 'installed';
            if (extra.chosen === false) return 'not_chosen';
            if (extra.partial) return 'partial';
            return 'missing';
        },

        pkgBadgeClass(extra) {
            return {
                part_missing: 'badge-warning', installed: 'badge-success', not_chosen: 'badge-secondary',
                partial: 'badge-warning', missing: 'badge-danger',
            }[this.pkgExtraBadge(extra)];
        },

        get pkgMemoryLayerRows() {
            const layers = (this.pkgMemory && this.pkgMemory.layers) || {};
            const layerText = (k) => (typeof window.tOr === 'function' ? window.tOr(k, k) : k);
            const order = [
                ['embedder', 'packages.layer.embedder', 'Embedder'],
                ['vector_memory', 'packages.layer.vector_memory', 'Memory engine'],
                ['layer_l1', 'packages.layer.layer_l1', 'Facts'],
                ['layer_l2', 'packages.layer.layer_l2', 'Conversation memories'],
                ['layer_l3', 'packages.layer.layer_l3', 'Procedures and task queue'],
                ['layer_l4', 'packages.layer.layer_l4', 'Entities'],
                ['pkg_chromadb', null, 'chromadb'],
                ['pkg_st', null, 'sentence-transformers'],
                ['pkg_sqlite_vec', null, 'sqlite-vec'],
                ['per_turn_retrieval', 'packages.layer.per_turn_retrieval', 'Per-turn RAG'],
                ['auto_store', 'packages.layer.auto_store', 'Auto-store'],
                ['consolidation', 'packages.layer.consolidation', 'Consolidator'],
            ];
            const rows = [];
            for (const [id, i18nKey, fallback] of order) {
                const c = layers[id];
                if (!c) continue;
                let name = fallback;
                if (i18nKey) {
                    const loc = layerText(i18nKey);
                    if (loc && loc !== i18nKey) name = loc;
                }
                rows.push({
                    id,
                    // A package's row is named by the package: a name.
                    isName: !i18nKey,
                    name: name || c.name || fallback,
                    ok: !!c.ok,
                    status: c.status || (c.ok ? 'ok' : 'error'),
                    detail: c.detail || '',
                });
            }
            return rows;
        },

        get filteredPkgCore() {
            if (!this.pkgSearch) return this.pkgCore;
            var q = this.pkgSearch.toLowerCase();
            return this.pkgCore.filter(function(p) {
                return (p.name || '').toLowerCase().includes(q) ||
                       (p.description || '').toLowerCase().includes(q);
            });
        },

        copyPkgCmd(cmd) {
            if (navigator.clipboard) {
                navigator.clipboard.writeText(cmd);
                if (window.KazmaStream && window.KazmaStream.toast) {
                    window.KazmaStream.toast(_t('settings.ops.copied_to_clipboard', 'Copied to clipboard'), 'success', 2000);
                }
            }
        },

        async installExtra(extraName) {
            if (!extraName || this.pkgInstalling) return;
            const ok = window.kazmaConfirm
                ? await window.kazmaConfirm({
                    title: _t('settings.ops.install_optional_dependency', 'Install optional dependency'),
                    message: _t('settings.ops.install_extra_message', 'Install the "{name}" extra into this Python environment? This runs uv/pip in the background.', { name: extraName }),
                    confirmText: _t('settings.ops.install', 'Install'),
                })
                : await confirm(_t('settings.ops.install_extra_native', 'Install optional extra "{name}"?', { name: extraName }));
            if (!ok) return;

            this.pkgInstalling = extraName;
            this.pkgInstallMsg = '';
            this.pkgInstallOk = false;
            try {
                const res = await fetch('/api/system/install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ extra: extraName }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.status === 'error' || data.status === 'unavailable') {
                    this.pkgInstallOk = false;
                    this.pkgInstallMsg = data.message || data.detail || _t('settings.ops.install_failed_to_start', 'Install failed to start');
                    if (window.showToast) window.showToast(this.pkgInstallMsg, 'error', 4000);
                } else {
                    this.pkgInstallOk = true;
                    this.pkgInstallMsg = _t('settings.ops.installing_extra', 'Installing "{name}" in the background… Refresh this tab in a minute.', { name: extraName });
                    if (window.showToast) window.showToast(this.pkgInstallMsg, 'success', 4000);
                    // Poll status a few times then reload package list
                    this._pollInstallStatus(extraName);
                }
            } catch (e) {
                this.pkgInstallOk = false;
                this.pkgInstallMsg = e.message || _t('settings.ops.network_error', 'Network error');
            }
            this.pkgInstalling = '';
        },

        async _pollInstallStatus(extraName) {
            const self = this;
            let tries = 0;
            const tick = async () => {
                tries += 1;
                try {
                    const st = await self._fetch('/api/system/install/status');
                    if (st && (st.status === 'OK' || st.status === 'FAILED')) {
                        self.pkgInstallOk = st.status === 'OK';
                        self.pkgInstallMsg = st.status === 'OK'
                            ? _t('settings.ops.installed_extra', 'Installed "{name}". Reloading package list…', { name: extraName })
                            : _t('settings.ops.install_failed', 'Install failed: {error}', { error: st.error || _t('settings.ops.see_server_logs', 'see server logs') });
                        await self.loadPackages();
                        return;
                    }
                } catch (e) { /* ignore */ }
                if (tries < 20) setTimeout(tick, 3000);
            };
            setTimeout(tick, 2500);
        },

        async checkUpdates() {
            try {
                this.updateInfo = await this._fetch('/api/settings/system/updates');
            } catch (e) {
                this.updateInfo = { error: e.message };
            }
        },

        // A settings backup (kazma_core.settings_restore): every setting, keys
        // as references to this install's vault, never the keys themselves.
        async _downloadSettings(url, ext) {
            const resp = await fetch(url);
            // An error page saved as a backup looks like one until the day it
            // is needed.
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const blob = await resp.blob();
            const href = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = href;
            a.download = `kazma-settings-${new Date().toISOString().split('T')[0]}.${ext}`;
            a.click();
            URL.revokeObjectURL(href);
        },

        async createBackup() {
            try {
                await this._downloadSettings('/api/settings/system/backup', 'yaml');
                showToast(_t('settings.backup_downloaded', 'Backup downloaded'), 'success');
            } catch (e) {
                showToast(_t('settings.backup_failed_reason', 'Backup failed: {error}', {error: e.message}), 'error');
            }
        },

        async exportConfig() {
            const ext = this.exportFormat === 'json' ? 'json' : 'yaml';
            try {
                await this._downloadSettings(`/api/settings/export?format=${ext}`, ext);
                showToast(_t('settings.config_exported', 'Configuration exported'), 'success');
            } catch (e) {
                showToast(_t('settings.export_failed', 'Export failed: {error}', {error: e.message}), 'error');
            }
        },

        // Import/Export restores through the same preview as Restore backup.
        async importConfig() {
            const sections = this.importSelective ? this._sectionPrefixes(this.importSections) : null;
            if (sections && !sections.length) {
                showToast(_t('settings.select_a_section', 'Pick at least one section to import.'), 'error');
                return;
            }
            await this.restoreSettings(this.importData, {
                sections,
                source: _t('settings.restore_source_pasted', 'the text above'),
            });
        },

        // The settings key sections a picked section stands for.
        _sectionPrefixes(ids) {
            const groups = { model: ['models', 'providers', 'registry', 'llm'], skills: ['skills', 'agent_skills'] };
            const out = [];
            (ids || []).forEach((id) => (groups[id] || [id]).forEach((p) => { if (!out.includes(p)) out.push(p); }));
            return out;
        },

        handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (e) => { this.importData = e.target.result; };
            reader.readAsText(file);
        },

        async onRestoreFile(event) {
            const input = event.target;
            const file = input.files && input.files[0];
            input.value = ''; // the same file can be picked again
            if (!file) return;
            if (file.size > 10 * 1024 * 1024) {
                showToast(_t('settings.restore_too_large', 'The file is larger than a settings backup can be (10 MB).'), 'error');
                return;
            }
            await this.restoreSettings(await file.text(), { source: file.name });
        },

        _restoreNames(names, max) {
            const limit = max || 8;
            const shown = _iso(names.slice(0, limit).join(', '));
            return names.length > limit
                ? shown + ' ' + _t('settings.restore_and_more', '… and {count} more', { count: names.length - limit })
                : shown;
        },

        // The preview a restore shows before it writes: names and counts,
        // never a value.
        _restorePreviewText(plan, source) {
            const lines = [];
            const fmt = window.KazmaFormat;
            const made = plan.backup || {};
            if (source) lines.push(_t('settings.restore_from', 'From: {source}', { source: _iso(source) }));
            if (made.created_at) {
                lines.push(_t('settings.restore_backup_made', 'Backup made {date}.', {
                    date: _iso(fmt ? fmt.dateTime(made.created_at) : made.created_at),
                }));
            }
            if (made.kazma_version) {
                lines.push(_t('settings.restore_backup_version', 'Kazma version: {version}', { version: _iso(made.kazma_version) }));
            }
            lines.push('');
            lines.push(_t('settings.restore_will_change', 'Settings that will change: {count}', { count: plan.changed.length }));
            lines.push('  ' + this._restoreNames(plan.changed));
            Object.entries(plan.lists || {}).forEach(([list, d]) => {
                if (d.added && d.added.length) lines.push(_t('settings.restore_list_added', 'Back in {list}: {names}', { list: _iso(list), names: _iso(d.added.join(', ')) }));
                if (d.updated && d.updated.length) lines.push(_t('settings.restore_list_updated', 'Updated in {list}: {names}', { list: _iso(list), names: _iso(d.updated.join(', ')) }));
            });
            if (plan.keys_restored.length) {
                lines.push(_t('settings.restore_keys_back', "Keys brought back from this install's vault (nothing to enter): {count}", { count: plan.keys_restored.length }));
                lines.push('  ' + this._restoreNames(plan.keys_restored));
            }
            if (plan.keys_to_reenter.length) {
                lines.push(_t('settings.restore_keys_reenter', 'Keys this vault no longer has, to enter again in Settings: {count}', { count: plan.keys_to_reenter.length }));
                lines.push('  ' + this._restoreNames(plan.keys_to_reenter));
            }
            const kept = plan.kept || {};
            if (kept.keys) lines.push(_t('settings.restore_keys_kept', 'Keys you have now stay as they are: {count}', { count: kept.keys }));
            if ((kept.runtime || 0) + (kept.credentials || 0)) {
                lines.push(_t('settings.restore_state_kept', "Left as they are: {count} of Kazma's own records and sign-in records.", { count: (kept.runtime || 0) + (kept.credentials || 0) }));
            }
            if (kept.learned) lines.push(_t('settings.restore_learned_kept', 'What Kazma learned (its Soul) stays as it is now.'));
            if (kept.retired_defaults) lines.push(_t('settings.restore_retired_kept', 'Defaults Kazma changed since the backup keep their new value: {count}', { count: kept.retired_defaults }));
            const refused = Object.entries(plan.refused || {});
            if (refused.length) {
                lines.push(_t('settings.restore_refused', 'Not restored, the value is not allowed: {count}', { count: refused.length }));
                refused.forEach(([key, why]) => lines.push('  ' + _iso(key + ': ' + why)));
            }
            if (plan.unchanged) lines.push(_t('settings.restore_unchanged', 'Already as in the backup: {count}', { count: plan.unchanged }));
            lines.push('');
            lines.push(_t('settings.restore_nothing_deleted', 'Nothing is deleted, and you can undo this restore afterwards.'));
            return lines.join('\n');
        },

        async _offerRestartAfterRestore() {
            await this.restartServer({
                restartNeeded: () => true,
                setBusy: (v) => { this.restoring = v; },
                title: _t('settings.restore_restart_title', 'Restart Kazma now?'),
                message: _t('settings.restore_restart_message', 'Some settings are read when Kazma starts: providers, models, chat apps. Restart now so every part of Kazma uses the restored ones.'),
            });
        },

        // Preview, confirm, write: the server writes exactly the plan the
        // owner confirmed (its digest), or answers 409 with the plan as it is
        // now, which is shown again.
        async restoreSettings(text, opts = {}) {
            if (!text || !String(text).trim()) {
                showToast(_t('settings.paste_or_upload', 'Paste or upload config data'), 'error');
                return;
            }
            const url = (params) => {
                const q = new URLSearchParams(params);
                if (opts.sections && opts.sections.length) q.set('sections', opts.sections.join(','));
                return '/api/settings/system/restore?' + q.toString();
            };
            const post = (params) => window.kazmaSave(url(params), {
                method: 'POST',
                headers: { 'Content-Type': 'text/plain; charset=utf-8' },
                body: String(text),
            });
            this.restoring = true;
            try {
                let plan = (await post({ dry_run: 'true' })).plan;
                for (let shown = 0; shown < 3; shown++) {
                    if (!plan.changed.length) {
                        const lines = [_t('settings.restore_nothing_to_do', 'Your settings already match this backup; nothing to restore.')];
                        if (plan.keys_to_reenter.length) {
                            lines.push('');
                            lines.push(_t('settings.restore_keys_reenter', 'Keys this vault no longer has, to enter again in Settings: {count}', { count: plan.keys_to_reenter.length }));
                            lines.push('  ' + this._restoreNames(plan.keys_to_reenter));
                        }
                        await window.kazmaAlert({ title: _t('settings.restore_preview_title', 'Restore settings?'), message: lines.join('\n') });
                        return;
                    }
                    const ok = await window.kazmaConfirm({
                        title: _t('settings.restore_preview_title', 'Restore settings?'),
                        message: this._restorePreviewText(plan, opts.source),
                        confirmText: _t('settings.restore_confirm', 'Restore'),
                    });
                    if (!ok) return;
                    try {
                        const done = await post({ expect: plan.digest });
                        showToast(_t('settings.restore_done', 'Settings restored: {count}', { count: done.restored }), 'success');
                        await this.loadRestoreUndo();
                        if (done.restart_recommended) await this._offerRestartAfterRestore();
                        return;
                    } catch (e) {
                        if (e.status !== 409 || !e.body || !e.body.plan) throw e;
                        showToast(_t('settings.restore_changed_since', 'Your settings changed since the preview; here is the plan as it is now.'), 'info');
                        plan = e.body.plan;
                    }
                }
            } catch (e) {
                showToast(_t('settings.restore_failed', 'Restore failed: {error}', { error: e.message }), 'error');
            } finally {
                this.restoring = false;
            }
        },

        async loadRestoreUndo() {
            const data = await window.kazmaGetJson('/api/settings/system/restore/undo');
            this.restoreUndo = data && data.available ? data.plan : null;
        },

        async undoRestore() {
            this.restoring = true;
            try {
                const preview = await window.kazmaGetJson('/api/settings/system/restore/undo');
                const plan = preview && preview.available ? preview.plan : null;
                this.restoreUndo = plan;
                if (!plan) {
                    showToast(_t('settings.undo_nothing', 'There is no restore to undo.'), 'info');
                    return;
                }
                const fmt = window.KazmaFormat;
                const lines = [];
                if (plan.restored_at) lines.push(_t('settings.undo_restored_at', 'Restored {date}.', { date: _iso(fmt ? fmt.dateTime(plan.restored_at) : plan.restored_at) }));
                lines.push(_t('settings.undo_will_revert', 'Settings that go back to what they were: {count}', { count: plan.reverted.length }));
                if (plan.reverted.length) lines.push('  ' + this._restoreNames(plan.reverted));
                if (plan.changed_since.length) {
                    lines.push(_t('settings.undo_changed_since', 'Changed again since the restore, left as they are now: {count}', { count: plan.changed_since.length }));
                    lines.push('  ' + this._restoreNames(plan.changed_since));
                }
                if (plan.kept_keys.length) lines.push(_t('settings.undo_keys_kept', 'Keys the restore brought back stay: {count}', { count: plan.kept_keys.length }));
                if (!plan.reverted.length) {
                    await window.kazmaAlert({ title: _t('settings.undo_title', 'Undo the last restore?'), message: lines.join('\n') });
                    return;
                }
                const ok = await window.kazmaConfirm({
                    title: _t('settings.undo_title', 'Undo the last restore?'),
                    message: lines.join('\n'),
                    confirmText: _t('settings.undo_confirm', 'Undo'),
                });
                if (!ok) return;
                const done = await window.kazmaSave('/api/settings/system/restore/undo?expect=' + encodeURIComponent(plan.digest), { method: 'POST' });
                showToast(_t('settings.undo_done', 'Settings put back: {count}', { count: done.reverted }), 'success');
                this.restoreUndo = null;
                if (done.restart_recommended) await this._offerRestartAfterRestore();
            } catch (e) {
                showToast(_t('settings.undo_failed', 'Undo failed: {error}', { error: e.message }), 'error');
                await this.loadRestoreUndo();
            } finally {
                this.restoring = false;
            }
        },

        get offsiteActiveProviderLabel() {
            const labels = {
                google_drive: 'Google Drive',
                onedrive: 'OneDrive',
                webdav: 'WD MyCloud / NAS',
                ftp: 'FTP / NAS',
                s3: 'S3 / B2',
            };
            return labels[this.offsiteProvider] || '';
        },

        offsiteProviderStatus(name) {
            if (!this.offsiteConfig || !Array.isArray(this.offsiteConfig.providers)) return null;
            return this.offsiteConfig.providers.find(p => p.provider === name) || null;
        },

        selectOffsiteProvider(provider) {
            this.offsiteProvider = provider;
            this.offsiteTestResult = null;
        },

        connectGoogleDrive() {
            // Reuse the Gmail OAuth flow (now includes the drive.file scope).
            // After the callback we land back on Settings; loadOffsiteConfig
            // completes the provider selection.
            return this._startOffsiteSignIn('google_drive', '/api/email/oauth/gmail/start');
        },

        connectOneDrive() {
            return this._startOffsiteSignIn('onedrive', '/api/email/oauth/microsoft/start');
        },

        /* The sign-in starts with a POST (auth-guard.js kazmaStartSignIn,
           same tab); it was a link to a GET start until 2026-11-11. */
        async _startOffsiteSignIn(provider, url) {
            try { localStorage.setItem('kazma_offsite_connect_pending', provider); } catch (e) {}
            try {
                const data = await window.kazmaStartSignIn(url, {});
                if (data.ok) return;
                try { localStorage.removeItem('kazma_offsite_connect_pending'); } catch (e) {}
                showToast(window.kazmaSignInProblem(data, 'HTTP ' + data.status), 'error');
            } catch (e) {
                try { localStorage.removeItem('kazma_offsite_connect_pending'); } catch (err) {}
                showToast(e.message, 'error');
            }
        },

        async loadOffsiteConfig() {
            try {
                const resp = await fetch('/api/settings/backup/offsite');
                if (resp.ok) {
                    this.offsiteConfig = await resp.json();
                    this.offsiteProvider = this.offsiteConfig.provider || '';
                    this.offsiteEnabled = this.offsiteConfig.enabled;                    // Prefill credential fields from stored config
                    const w = this.offsiteConfig.webdav || {};
                    this.offsiteWebdavUrl = w.url || '';
                    this.offsiteWebdavUser = w.username || '';
                    this.offsiteWebdavPass = w.password_set ? '••••••••' : '';
                    const ftp = this.offsiteConfig.ftp || {};
                    this.offsiteFtpHost = ftp.host || '';
                    this.offsiteFtpPort = ftp.port || '21';
                    this.offsiteFtpUser = ftp.username || '';
                    this.offsiteFtpPass = ftp.password_set ? '••••••••' : '';
                    this.offsiteFtpPath = ftp.path || '';
                    const s = this.offsiteConfig.s3 || {};
                    this.offsiteS3Key = s.access_key || '';
                    this.offsiteS3Secret = s.secret_key_set ? '••••••••' : '';
                    this.offsiteS3Bucket = s.bucket || '';
                    this.offsiteS3Endpoint = s.endpoint || '';
                    this.offsiteS3Region = s.region || 'us-east-1';
                    const keep = Number(this.offsiteConfig.keep);
                    this.offsiteKeep = Number.isInteger(keep) && keep >= 0 ? keep : 7;

                    // OAuth round-trip: user just connected Google/MS from the
                    // backup card. Auto-select + save the provider, then toast.
                    // Based on the provider's live connection status (the
                    // email tab's callback handler strips email_oauth from the
                    // URL before we run, so the URL param can't be used).
                    let pending = null;
                    try { pending = localStorage.getItem('kazma_offsite_connect_pending'); } catch (e) {}
                    if (pending) {
                        try { localStorage.removeItem('kazma_offsite_connect_pending'); } catch (e) {}
                        const status = this.offsiteProviderStatus(pending);
                        if (status && status.connected) {
                            this.offsiteProvider = pending;
                            this.offsiteEnabled = true;
                            await this.saveOffsite();
                            if (pending === 'google_drive' && status.drive_ok === false) {
                                // OAuth succeeded but Drive itself is blocked —
                                // say exactly why instead of the green toast.
                                showToast(_t('settings.google_drive_failed', 'Google connected, but Drive access failed: {error}. Test the card for the fix steps.', {error: (status.drive_error || _t('settings.drive_run_test', 'run Test to diagnose'))}), 'error');
                            } else {
                                showToast(_t('settings.offsite_connected', '☁️ {provider} connected — offsite backup active', {provider: (this.offsiteActiveProviderLabel || pending)}), 'success');
                            }
                        } else {
                            showToast(_t('settings.cloud_connect_incomplete', 'Cloud connect did not complete — open the provider card and try again'), 'error');
                        }
                    }
                }
            } catch (e) { /* fail silently */ }
            // Backup retention (live effective value — env override wins server-side)
            try {
                const r = await fetch('/api/settings/backup/retention');
                if (r.ok) {
                    const d = await r.json();
                    this.backupRetention = Number(d.retention) || 7;
                }
            } catch (e) { /* fail silently */ }
        },

        async saveBackupRetention() {
            const n = Number(this.backupRetention);
            if (!n || n < 1) {
                showToast(_t('settings.retention_min', 'Backup retention must be at least 1'), 'error');
                return;
            }
            try {
                const resp = await fetch('/api/settings/backup/retention', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ retention: n }),
                });
                const data = await resp.json();
                if (!resp.ok || data.status === 'error' || data.ok === false) {
                    showToast(data.error || _t('settings.save_failed', 'Save failed'), 'error');
                } else {
                    showToast(window.kazmaCount('settings.retention_saved', n), 'success');
                }
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        // How many offsite copies Kazma keeps (backups.offsite.keep). Saved
        // through the single-setting route, which validates it: it works on an
        // install whose offsite copy goes through rclone, with no provider set.
        async saveOffsiteKeep() {
            const n = Number(this.offsiteKeep);
            if (!Number.isInteger(n) || n < 0) {
                showToast(_t('settings.offsite_keep_invalid', 'Offsite copies kept must be 0 (keep every copy) or more'), 'error');
                return;
            }
            try {
                const resp = await fetch('/api/settings/single', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ key: 'backups.offsite.keep', value: n, category: 'backups' }),
                });
                let data = {};
                try { data = await resp.json(); } catch (e) { /* not JSON */ }
                if (!resp.ok || data.status === 'error' || data.ok === false) {
                    showToast(data.detail || data.error || _t('settings.save_failed', 'Save failed'), 'error');
                } else if (n === 0) {
                    showToast(_t('settings.offsite_keep_all_saved', 'Kazma keeps every offsite copy'), 'success');
                } else {
                    showToast(window.kazmaCount('settings.offsite_keep_saved', n), 'success');
                }
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        // ── The backup folder on this computer (backup.folder_copy) ──────────
        async loadBackupFolder() {
            const data = await window.kazmaGetJson('/api/settings/backup/folder');
            if (!data) {
                this.backupFolderLoadFailed = true;
                return;
            }
            this.backupFolderLoadFailed = false;
            this._showBackupFolder(data);
            this.backupFolderEnabled = !!data.enabled;
            this.backupFolderPath = data.path || '';
            this.backupFolderKeep = Number.isInteger(data.keep) ? data.keep : 7;
            this.backupFolderLoaded = true;
        },

        _showBackupFolder(data) {
            this.backupFolder = {
                state: data.state || 'off',
                path: data.path || '',
                repo: data.repo || '',
                missing_since: data.missing_since || null,
            };
        },

        backupFolderStateText() {
            const f = this.backupFolder || {};
            if (f.state === 'missing') {
                const since = f.missing_since ? window.KazmaFormat.dateTime(Number(f.missing_since) * 1000) : '';
                return since
                    ? _t('settings.bkf.state_missing_since', '', { path: _iso(f.path), since: since })
                    : _t('settings.bkf.state_missing', '', { path: _iso(f.path) });
            }
            if (f.state === 'ready') return _t('settings.bkf.state_ready', '', { repo: _iso(f.repo) });
            if (f.state === 'new') return _t('settings.bkf.state_new', '', { path: _iso(f.path) });
            return _t('settings.bkf.state_off');
        },

        backupFolderProblemText(code, where) {
            const known = ['empty', 'device', 'relative', 'inside_data', 'missing', 'cannot_create', 'network', 'unreadable', 'no_folder'];
            if (known.indexOf(code) < 0) return _t('settings.bkf.problem_other', '', { code: code });
            if (code === 'missing' && where === 'browse') return _t('settings.bkf.problem_browse_missing');
            return _t('settings.bkf.problem_' + code);
        },

        backupFolderCheckText(c) {
            if (c.check === 'free') {
                const gb = Number(c.detail);
                return Number.isFinite(gb)
                    ? _t('settings.bkf.check_free', '', { size: window.KazmaFormat.number(gb) })
                    : _t('settings.bkf.check_free_unknown');
            }
            if (c.check === 'other_disk') return _t(c.ok ? 'settings.bkf.check_other_disk' : 'settings.bkf.check_same_disk');
            if (c.check === 'writable') {
                if (!c.ok) return _t('settings.bkf.check_not_writable');
                return _t(c.detail === 'made' ? 'settings.bkf.check_made' : 'settings.bkf.check_writable');
            }
            if (c.check === 'restic') return _t(c.ok ? 'settings.bkf.check_restic' : 'settings.bkf.check_no_restic');
            if (c.check === 'passphrase') return _t(c.ok ? 'settings.bkf.check_passphrase' : 'settings.bkf.check_no_passphrase');
            return c.check;
        },

        async saveBackupFolder() {
            const keep = Number(this.backupFolderKeep);
            if (!Number.isInteger(keep) || keep < 0) {
                showToast(_t('settings.bkf.keep_invalid'), 'error');
                return;
            }
            this.backupFolderSaving = true;
            try {
                const saved = await window.kazmaSave('/api/settings/backup/folder', {
                    method: 'PUT',
                    body: { enabled: !!this.backupFolderEnabled, path: this.backupFolderPath, keep: keep },
                });
                this._showBackupFolder(saved || {});
                showToast(_t('settings.bkf.saved'), 'success');
            } catch (e) {
                const detail = e && e.body && e.body.detail;
                const message = detail && typeof detail === 'object' && detail.code
                    ? this.backupFolderProblemText(detail.code, 'save')
                    : (typeof detail === 'string' ? detail : (e && e.message) || '');
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', { error: message }), 'error');
            } finally {
                this.backupFolderSaving = false;
            }
        },

        async testBackupFolder() {
            this.backupFolderTesting = true;
            this.backupFolderTest = null;
            try {
                const resp = await fetch('/api/settings/backup/folder/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ path: this.backupFolderPath }),
                });
                let data = null;
                try { data = await resp.json(); } catch (e) { data = null; }
                this.backupFolderTest = resp.ok && data ? data : { ok: false, problem: 'unreadable', checks: [] };
            } catch (e) {
                this.backupFolderTest = { ok: false, problem: 'unreadable', checks: [] };
            } finally {
                this.backupFolderTesting = false;
            }
        },

        async openBackupFolderBrowser() {
            this.backupFolderBrowsing = true;
            await this.browseBackupFolder(this.backupFolderPath || '');
        },

        async browseBackupFolder(path) {
            this.backupFolderBrowseLoaded = false;
            let data = null;
            try {
                data = await window.kazmaSave('/api/settings/backup/folder/browse', {
                    method: 'POST', body: { path: path || '' },
                });
            } catch (e) { data = null; }
            if (!data) {
                this.backupFolderBrowse = Object.assign({}, this.backupFolderBrowse, { problem: 'unreadable', folders: [] });
                this.backupFolderBrowseLoaded = true;
                return;
            }
            this.backupFolderBrowse = {
                path: data.path || '',
                parent: data.parent || '',
                folders: Array.isArray(data.folders) ? data.folders : [],
                drives: Array.isArray(data.drives) ? data.drives : [],
                more: !!data.more,
                problem: data.problem || '',
            };
            this.backupFolderBrowseLoaded = true;
        },

        backupFolderJoin(parent, name) {
            const sep = parent.indexOf('\\') >= 0 || /^[A-Za-z]:/.test(parent) ? '\\' : '/';
            return parent.endsWith(sep) ? parent + name : parent + sep + name;
        },

        useBrowsedBackupFolder() {
            if (!this.backupFolderBrowse.path || this.backupFolderBrowse.problem) return;
            this.backupFolderPath = this.backupFolderBrowse.path;
            this.backupFolderBrowsing = false;
            this.backupFolderTest = null;
        },

        _offsiteCredsPayload() {
            return {
                provider: this.offsiteProvider,
                enabled: this.offsiteEnabled,
                webdav_url: this.offsiteWebdavUrl,
                webdav_username: this.offsiteWebdavUser,
                webdav_password: this.offsiteWebdavPass === '••••••••' ? '' : this.offsiteWebdavPass,
                ftp_host: this.offsiteFtpHost,
                ftp_port: this.offsiteFtpPort,
                ftp_username: this.offsiteFtpUser,
                ftp_password: this.offsiteFtpPass === '••••••••' ? '' : this.offsiteFtpPass,
                ftp_path: this.offsiteFtpPath,
                s3_access_key: this.offsiteS3Key,
                s3_secret_key: this.offsiteS3Secret === '••••••••' ? '' : this.offsiteS3Secret,
                s3_bucket: this.offsiteS3Bucket,
                s3_endpoint: this.offsiteS3Endpoint,
                s3_region: this.offsiteS3Region,
            };
        },

        async saveOffsite() {
            if (!this.offsiteProvider) return;
            this.offsiteSaved = false;
            try {
                const resp = await fetch('/api/settings/backup/offsite', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this._offsiteCredsPayload()),
                });
                if (resp.ok) {
                    this.offsiteSaved = true;
                    setTimeout(() => { this.offsiteSaved = false; }, 3000);
                    // Refresh statuses so the ✓ badge appears
                    await this.loadOffsiteConfig();
                } else {
                    let data = {};
                    try { data = await resp.json(); } catch (e) { /* not JSON */ }
                    showToast(data.detail || data.error || _t('settings.save_failed', 'Save failed'), 'error');
                }
            } catch (e) {
                console.error('saveOffsite failed:', e);
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        async testOffsite() {
            if (!this.offsiteProvider) return;
            this.offsiteTesting = true;
            this.offsiteTestResult = null;
            try {
                const resp = await fetch('/api/settings/backup/offsite/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this._offsiteCredsPayload()),
                });
                this.offsiteTestResult = await resp.json();
            } catch (e) {
                this.offsiteTestResult = { ok: false, error: String(e) };
            } finally {
                this.offsiteTesting = false;
            }
        },

        async runBackup() {
            this.backupRunning = true;
            this.backupResult = null;
            this.backupProgressText = _t('settings.ops.backup_starting', 'Starting…');
            this.backupProgressPhase = 'starting';
            try {
                const resp = await fetch('/api/backup/now', { method: 'POST' });
                const data = await resp.json();
                if (!data.ok) {
                    const p = data.progress || {};
                    if (p.phase && !['idle', 'done', 'error'].includes(p.phase)) {
                        // A backup started elsewhere (24h scheduler, another
                        // tab) is genuinely running — attach to its progress
                        // instead of leaving the user with a bare rejection.
                        this.backupProgressPhase = p.phase;
                        this.backupProgressText = p.detail || p.phase;
                        if (!this._backupPollId) {
                            this._backupPollId = setInterval(() => this._pollBackup(), 2000);
                        }
                        return;
                    }
                    this.backupResult = { ok: false, error: data.error || 'Failed to start' };
                    this.backupRunning = false;
                    return;
                }
                // Poll for progress. Clear any prior interval first — a
                // re-entered runBackup used to stack parallel poll loops.
                if (this._backupPollId) clearInterval(this._backupPollId);
                this._backupPollId = setInterval(() => this._pollBackup(), 2000);
            } catch (e) {
                this.backupResult = { ok: false, error: e.message };
                this.backupRunning = false;
            }
        },

        async syncBackupState() {
            // Detect a backup already in flight when the tab is opened (started
            // by the 24h scheduler or another session) so the progress bar
            // reflects reality instead of a later "already running" surprise.
            try {
                const resp = await fetch('/api/backup/status');
                if (!resp.ok) return;
                const data = await resp.json();
                if (data.phase && !['idle', 'done', 'error'].includes(data.phase)) {
                    this.backupRunning = true;
                    this.backupResult = null;
                    this.backupProgressPhase = data.phase;
                    this.backupProgressText = data.detail || data.phase;
                    if (!this._backupPollId) {
                        this._backupPollId = setInterval(() => this._pollBackup(), 2000);
                    }
                }
            } catch (e) { /* status endpoint unavailable */ }
        },

        async _pollBackup() {
            try {
                const resp = await fetch('/api/backup/status');
                const data = await resp.json();
                this.backupProgressPhase = data.phase || '';
                this.backupProgressText = data.detail || data.phase || '';
                if (data.phase === 'done') {
                    clearInterval(this._backupPollId);
                    this._backupPollId = null;
                    this.backupRunning = false;
                    this.backupResult = data.result || { ok: true };
                    if (this.backupResult.ok) {
                        showToast(window.kazmaCount('settings.backup_complete', this.backupResult.databases_ok || 0, {mb: this.backupResult.total_size_mb}), 'success');
                    }
                    await this.loadBackupList();
                } else if (data.phase === 'error') {
                    clearInterval(this._backupPollId);
                    this._backupPollId = null;
                    this.backupRunning = false;
                    this.backupResult = { ok: false, error: data.error || data.detail };
                    showToast(_t('settings.backup_failed_reason', 'Backup failed: {error}', {error: (data.error || data.detail)}), 'error');
                }
            } catch (e) { /* keep polling */ }
        },

        async loadBackupList() {
            try {
                const resp = await fetch('/api/backup/list');
                const data = await resp.json();
                this.backupList = (data.backups || []).map(function (b) {
                    b.archiving = false; // client-only transient; server provides archived
                    return b;
                });
                this.backupListLoaded = resp.ok && Array.isArray(data.backups);
            } catch (e) {
                this.backupList = [];
                this.backupListLoaded = false;
            }
        },

        async deleteBackup(dirName) {
            if (!(await window.kazmaConfirm({
                title: _t('settings.ops.delete_backup', 'Delete backup'),
                message: _t('settings.ops.delete_backup_message', 'Delete backup {date}? This cannot be undone.', {
                    date: window.KazmaFormat ? window.KazmaFormat.dateTime(parseInt(dirName)) : new Date(parseInt(dirName) * 1000).toLocaleString(),
                }),
                confirmText: _t('settings.ops.delete', 'Delete'), danger: true,
            }))) return;
            try {
                const resp = await fetch('/api/backup/' + encodeURIComponent(dirName), { method: 'DELETE' });
                const data = await resp.json();
                if (data.ok) {
                    showToast(_t('settings.backup_deleted', 'Backup deleted'), 'success');
                    await this.loadBackupList();
                } else {
                    showToast(_t('settings.delete_failed', 'Delete failed') + (data.error ? ': ' + data.error : ''), 'error');
                }
            } catch (e) {
                showToast(_t('settings.delete_failed', 'Delete failed') + ': ' + e.message, 'error');
            }
        },

        async archiveBackup(dirName) {
            // If already archived, trigger download.
            var item = this.backupList.find(function (b) { return b.dir === dirName; });
            if (item && item.archived) {
                window.open('/api/backup/' + encodeURIComponent(dirName) + '/download', '_blank');
                return;
            }
            if (item) item.archiving = true;
            try {
                const resp = await fetch('/api/backup/' + encodeURIComponent(dirName) + '/archive', { method: 'POST' });
                const data = await resp.json();
                if (data.ok) {
                    if (item) { item.archived = true; item.archiving = false; }
                    showToast(_t('settings.archived_mb', 'Archived: {mb} MB. Click Download to save.', {mb: data.size_mb}), 'success');
                    // Auto-trigger download.
                    window.open('/api/backup/' + encodeURIComponent(dirName) + '/download', '_blank');
                } else {
                    if (item) item.archiving = false;
                    showToast(_t('settings.archive_failed', 'Archive failed: {error}', {error: (data.error || '')}), 'error');
                }
            } catch (e) {
                if (item) item.archiving = false;
                showToast(_t('settings.archive_failed', 'Archive failed: {error}', {error: e.message}), 'error');
            }
        },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
