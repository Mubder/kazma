/* Kazma Skills — Alpine.js app for skills management */

function skillsApp() {
    // Text built here: the catalog's text in the page's language, else the
    // English given; {name} placeholders filled from vars.
    function _k(key, en, vars) {
        if (typeof window.kazmaT === 'function') return window.kazmaT(key, en, vars);
        var s = en;
        if (vars) for (var v in vars) s = s.split('{' + v + '}').join(String(vars[v]));
        return s;
    }
    return {
        tab: 'installed',
        hubQuery: '',
        hubResults: [],
        marketQuery: '',
        marketResults: [],
        marketSearching: false,
        validatePath: '',
        validateResult: null,
        agentSkillSource: '',
        installing: false,

        async installAgentSkill() {
            var source = (this.agentSkillSource || '').trim();
            if (!source) {
                showToast(_k('skills.ui.enter_repo', 'Enter owner/repo or a GitHub URL'), 'error');
                return;
            }
            this.installing = true;
            try {
                var resp = await fetch('/api/skills/install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: source })
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    showToast(result.message || _k('skills.ui.installed', 'Skill installed'), 'success');
                    this.agentSkillSource = '';
                    location.reload();
                } else {
                    showToast(_k('skills.ui.install_failed_error', 'Install failed: {error}', { error: result.error || '' }), 'error');
                }
            } catch (e) {
                showToast(_k('skills.ui.install_failed', 'Install failed'), 'error');
            } finally {
                this.installing = false;
            }
        },

        async toggleSkill(skillId, enabled) {
            try {
                await window.kazmaSave('/api/skills/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: skillId, enabled: enabled })
                });
                showToast(enabled ? 'Skill enabled' : 'Skill disabled', 'success');
            } catch (e) {
                showToast(_k('skills.ui.toggle_failed', 'Failed to toggle skill'), 'error');
            }
        },

        async uninstallSkill(skillId) {
            if (!(await window.kazmaConfirm({
                title: _k('skills.ui.uninstall_title', 'Uninstall skill'),
                message: _k('skills.ui.uninstall_message', 'Uninstall this skill? This cannot be undone.'),
                confirmText: _k('skills.ui.uninstall', 'Uninstall'),
                danger: true,
            }))) return;
            try {
                const body = await window.kazmaSave('/api/skills/uninstall', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: skillId })
                });
                // Only "ok" removed something: "not_found" used to toast
                // "Skill uninstalled" over a skill that was still there.
                if (!body || body.status !== 'ok') {
                    showToast(_k('skills.ui.nothing_uninstalled', 'Nothing was uninstalled: {reason}', { reason: (body && (body.error || body.status)) || _k('skills.ui.no_answer', 'no answer') }), 'error');
                    return;
                }
                showToast(_k('skills.ui.uninstalled', 'Skill uninstalled'), 'success');
                location.reload();
            } catch (e) {
                showToast(e && e.message ? _k('skills.ui.uninstall_failed_error', 'Failed to uninstall: {error}', { error: e.message }) : _k('skills.ui.uninstall_failed', 'Failed to uninstall'), 'error');
            }
        },

        async installSkill(skillId) {
            try {
                var resp = await fetch('/api/skills/install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: skillId })
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    showToast(_k('skills.ui.installed', 'Skill installed'), 'success');
                    location.reload();
                } else {
                    showToast(_k('skills.ui.install_failed_error', 'Install failed: {error}', { error: result.error || '' }), 'error');
                }
            } catch (e) {
                showToast(_k('skills.ui.install_failed', 'Install failed'), 'error');
            }
        },

        async searchHub() {
            if (!this.hubQuery.trim()) {
                this.hubResults = [];
                return;
            }
            try {
                var resp = await fetch('/api/skills/hub/search?q=' + encodeURIComponent(this.hubQuery));
                this.hubResults = await resp.json();
            } catch (e) {
                console.error('Hub search failed:', e);
            }
        },

        async searchMarketplace() {
            var q = (this.marketQuery || '').trim();
            if (!q) {
                this.marketResults = [];
                return;
            }
            this.marketSearching = true;
            try {
                var resp = await fetch('/api/skills/marketplace/search?q=' + encodeURIComponent(q));
                this.marketResults = await resp.json();
            } catch (e) {
                console.error('Marketplace search failed:', e);
                this.marketResults = [];
            } finally {
                this.marketSearching = false;
            }
        },

        async installFromMarket(source) {
            if (!source) return;
            this.installing = source;
            try {
                var resp = await fetch('/api/skills/install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: source })
                });
                var result = await resp.json();
                if (result.status === 'ok') {
                    showToast(result.message || _k('skills.ui.installed', 'Skill installed'), 'success');
                    location.reload();
                } else {
                    showToast(_k('skills.ui.install_failed_error', 'Install failed: {error}', { error: result.error || '' }), 'error');
                }
            } catch (e) {
                showToast(_k('skills.ui.install_failed', 'Install failed'), 'error');
            } finally {
                this.installing = false;
            }
        },

        async validateSkill() {
            if (!this.validatePath.trim()) return;
            try {
                var resp = await fetch('/api/skills/validate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: this.validatePath })
                });
                this.validateResult = await resp.json();
            } catch (e) {
                this.validateResult = { passed: false, errors: [e.message] };
            }
        }
    };
}
if (typeof window !== "undefined") window.skillsApp = skillsApp;
