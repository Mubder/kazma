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
    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.ops = function () {
        return {
        async saveAppearance() {
            this.saving = true;
            try {
                await fetch('/api/settings/appearance', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(this.appearance),
                });
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
                this._applyAccentColor(this.appearance.accent_color);
                showToast(_t('settings.appearance_saved', 'Appearance saved'), 'success');
            } catch (e) {
                showToast(_t('settings.save_failed', 'Save failed'), 'error');
            }
            this.saving = false;
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
            if (!hex || !/^#[0-9a-fA-F]{6}$/.test(hex)) return;
            const r = parseInt(hex.slice(1, 3), 16);
            const g = parseInt(hex.slice(3, 5), 16);
            const b = parseInt(hex.slice(5, 7), 16);
            const root = document.documentElement;
            root.style.setProperty('--accent', hex);
            root.style.setProperty('--accent-rgb', r + ', ' + g + ', ' + b);
            root.style.setProperty('--accent-subtle', 'rgba(' + r + ', ' + g + ', ' + b + ', 0.12)');
            root.style.setProperty('--accent-glow', 'rgba(' + r + ', ' + g + ', ' + b + ', 0.28)');
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
            this.shortcuts[action] = keys;
            await fetch('/api/settings/shortcuts', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, keys }),
            });
            this.shortcutConflicts = this.detectConflicts();
            showToast(_t('settings.shortcut_updated', 'Shortcut for "{action}" updated', {action: action}), 'success');
        },

        async resetShortcuts() {
            if (!(await window.kazmaConfirm({
                title: 'Reset shortcuts',
                message: 'Reset all shortcuts to defaults?',
                confirmText: 'Reset',
                danger: false,
            }))) return;
            await fetch('/api/settings/shortcuts/reset', { method: 'POST' });
            this.shortcuts = await this._fetch('/api/settings/shortcuts') || {};
            this.shortcutConflicts = [];
            showToast(_t('settings.shortcuts_reset', 'Shortcuts reset'), 'success');
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

        async loadAccount() {
            const [tokens, sessions] = await Promise.all([
                this._fetch('/api/settings/account/tokens?_=' + Date.now()),
                this._fetch('/api/settings/account/sessions?_=' + Date.now()),
            ]);
            // Always replace arrays so Alpine x-for sees a new reference.
            this.apiTokens = Array.isArray(tokens) ? tokens.slice() : [];
            this.sessions = Array.isArray(sessions) ? sessions.slice() : [];
            await this.loadSaasAdmin();
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
            if (!await confirm('Delete user ' + username + '?')) return;
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
                } else {
                    showToast(data.error || _t('settings.failed', 'Failed'), 'error');
                }
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
        },

        async createToken() {
            if (!this.tokenName) { showToast(_t('settings.token_name_required', 'Token name required'), 'error'); return; }
            try {
                const resp = await fetch('/api/settings/account/tokens', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ name: this.tokenName }),
                });
                const data = await resp.json();
                const tok = data.token || '';
                this.tokenName = '';
                this.lastCreatedToken = tok;
                await this.loadAccount();
                showToast(tok ? _t('settings.token_created_copy', 'Token created — copy it below (shown once)') : _t('settings.token_created', 'Token created'), tok ? 'success' : 'error');
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
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
            const origin = window.location.origin || 'http://127.0.0.1:9090';
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
                title: 'Revoke token',
                message: 'Revoke this token? This cannot be undone. Scripts using it will get 401.',
                confirmText: 'Revoke',
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
                showToast(_t('settings.token_revoked', 'Token revoked'), 'success');
            } catch (e) {
                showToast(_t('settings.revoke_failed', 'Revoke failed: {error}', {error: e.message}), 'error');
            }
        },

        async loadTools() {
            try {
                this.tools = await this._fetch('/api/settings/tools') || [];
            } catch (e) {
                this.tools = [];
            }
        },

        async toggleTool(toolName, enabled) {
            await fetch(`/api/settings/tools/${encodeURIComponent(toolName)}/toggle`, {
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
            return this.tools.filter(t =>
                (t.name || '').toLowerCase().includes(q) ||
                (t.description || '').toLowerCase().includes(q) ||
                (t.category || '').toLowerCase().includes(q)
            );
        },

        async loadLogs() {
            try {
                const data = await this._fetch(`/api/settings/system/logs?lines=${this.logLines}`);
                this.logs = data?.lines || [];
            } catch (e) {
                this.logs = [];
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
                }
            } catch (e) { /* silent */ }
            this.pkgLoading = false;
        },

        get pkgMemoryLayerRows() {
            const layers = (this.pkgMemory && this.pkgMemory.layers) || {};
            const t = (typeof this.t === 'function') ? this.t.bind(this) : (k) => k;
            const order = [
                ['embedder', 'packages.layer.embedder', 'Embedder'],
                ['vector_memory', 'packages.layer.vector_memory', 'VectorMemory'],
                ['layer_l1', 'packages.layer.layer_l1', 'L1 Chroma'],
                ['layer_l2', 'packages.layer.layer_l2', 'L2 Graph'],
                ['layer_l3', 'packages.layer.layer_l3', 'L3 FTS5'],
                ['layer_l4', 'packages.layer.layer_l4', 'L4 sqlite-vec'],
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
                    const loc = t(i18nKey);
                    if (loc && loc !== i18nKey) name = loc;
                }
                rows.push({
                    id,
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
                    window.KazmaStream.toast('Copied to clipboard', 'success', 2000);
                }
            }
        },

        async installExtra(extraName) {
            if (!extraName || this.pkgInstalling) return;
            const ok = window.kazmaConfirm
                ? await window.kazmaConfirm({
                    title: 'Install optional dependency',
                    message: `Install the "${extraName}" extra into this Python environment? This runs uv/pip in the background.`,
                    confirmText: 'Install',
                })
                : await confirm(`Install optional extra "${extraName}"?`);
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
                    this.pkgInstallMsg = data.message || data.detail || 'Install failed to start';
                    if (window.showToast) window.showToast(this.pkgInstallMsg, 'error', 4000);
                } else {
                    this.pkgInstallOk = true;
                    this.pkgInstallMsg = `Installing "${extraName}" in the background… Refresh this tab in a minute.`;
                    if (window.showToast) window.showToast(this.pkgInstallMsg, 'success', 4000);
                    // Poll status a few times then reload package list
                    this._pollInstallStatus(extraName);
                }
            } catch (e) {
                this.pkgInstallOk = false;
                this.pkgInstallMsg = e.message || 'Network error';
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
                            ? `Installed "${extraName}". Reloading package list…`
                            : (`Install failed: ${st.error || 'see server logs'}`);
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

        async createBackup() {
            try {
                const resp = await fetch('/api/settings/system/backup');
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `kazma-backup-${new Date().toISOString().split('T')[0]}.yaml`;
                a.click();
                URL.revokeObjectURL(url);
                showToast(_t('settings.backup_downloaded', 'Backup downloaded'), 'success');
            } catch (e) {
                showToast(_t('settings.backup_failed_reason', 'Backup failed: {error}', {error: e.message}), 'error');
            }
        },

        async systemReset() {
            if (!(await window.kazmaConfirm({
                title: 'Reset all settings',
                message: '[!]  This will reset ALL settings to defaults. Are you sure?',
                confirmText: 'Reset',
                danger: true,
            }))) return;
            if (!(await window.kazmaConfirm({
                title: 'Final confirmation',
                message: 'Reset everything? This cannot be undone.',
                confirmText: 'Reset everything',
                danger: true,
            }))) return;
            try {
                await fetch('/api/settings/reset', { method: 'POST' });
                showToast(_t('settings.system_reset_reloading', 'System reset complete. Reloading...'), 'success');
                setTimeout(() => location.reload(), 1500);
            } catch (e) {
                showToast(_t('settings.reset_failed', 'Reset failed: {error}', {error: e.message}), 'error');
            }
        },

        async exportConfig() {
            try {
                const url = `/api/settings/export?format=${this.exportFormat}`;
                const resp = await fetch(url);
                const blob = await resp.blob();
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `kazma-config.${this.exportFormat}`;
                a.click();
                showToast(_t('settings.config_exported', 'Configuration exported'), 'success');
            } catch (e) {
                showToast(_t('settings.export_failed', 'Export failed: {error}', {error: e.message}), 'error');
            }
        },

        async importConfig() {
            if (!this.importData.trim()) { showToast(_t('settings.paste_or_upload', 'Paste or upload config data'), 'error'); return; }
            this.saving = true;
            try {
                await fetch('/api/settings/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({
                        data: this.importData,
                        format: this.importFormat,
                        selective: this.importSelective,
                        sections: this.importSections,
                    }),
                });
                showToast(_t('settings.config_imported', 'Configuration imported. Reloading...'), 'success');
                setTimeout(() => location.reload(), 1500);
            } catch (e) {
                showToast(_t('settings.import_failed', 'Import failed: {error}', {error: e.message}), 'error');
            }
            this.saving = false;
        },

        handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (e) => {
                this.importData = e.target.result;
                this.importFormat = file.name.endsWith('.json') ? 'json' : 'yaml';
            };
            reader.readAsText(file);
        },

        async resetToDefaults() {
            if (!(await window.kazmaConfirm({
                title: 'Reset settings',
                message: 'Reset ALL settings to defaults? This cannot be undone.',
                confirmText: 'Reset',
                danger: true,
            }))) return;
            try {
                await fetch('/api/settings/reset', { method: 'POST' });
                showToast(_t('settings.settings_reset_reloading', 'Settings reset. Reloading...'), 'success');
                setTimeout(() => location.reload(), 1500);
            } catch (e) {
                showToast(_t('settings.reset_failed', 'Reset failed: {error}', {error: e.message}), 'error');
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
            try { localStorage.setItem('kazma_offsite_connect_pending', 'google_drive'); } catch (e) {}
            window.location.href = '/api/email/oauth/gmail/start';
        },

        connectOneDrive() {
            try { localStorage.setItem('kazma_offsite_connect_pending', 'onedrive'); } catch (e) {}
            window.location.href = '/api/email/oauth/microsoft/start';
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
                                showToast(_t('settings.google_drive_failed', 'Google connected, but Drive access failed: {error}. Test the card for the fix steps.', {error: (status.drive_error || 'run Test to diagnose')}), 'error');
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
                if (data.status === 'error') {
                    showToast(data.error || _t('settings.save_failed', 'Save failed'), 'error');
                } else {
                    showToast(_t('settings.retention_saved', 'Backup retention saved — keeping the newest {n} backups', {n: n}), 'success');
                }
            } catch (e) {
                showToast(_t('settings.failed_with_reason', 'Failed: {error}', {error: e.message}), 'error');
            }
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
                }
            } catch (e) {
                console.error('saveOffsite failed:', e);
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
            this.backupProgressText = 'Starting…';
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
                        showToast(_t('settings.backup_complete', 'Backup complete: {dbs} DBs, {mb} MB', {dbs: this.backupResult.databases_ok, mb: this.backupResult.total_size_mb}), 'success');
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
            } catch (e) {
                this.backupList = [];
            }
        },

        async deleteBackup(dirName) {
            if (!(await window.kazmaConfirm({
                title: 'Delete backup',
                message: 'Delete backup ' + new Date(parseInt(dirName) * 1000).toLocaleString() + '? This cannot be undone.',
                confirmText: 'Delete', danger: true,
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
