/* Kazma Skills — Alpine.js app for skills management */

function skillsApp() {
    return {
        tab: 'installed',
        hubQuery: '',
        hubResults: [],
        validatePath: '',
        validateResult: null,
        agentSkillSource: '',
        installing: false,

        async installAgentSkill() {
            var source = (this.agentSkillSource || '').trim();
            if (!source) {
                showToast('Enter owner/repo or a GitHub URL', 'error');
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
                    showToast(result.message || 'Skill installed', 'success');
                    this.agentSkillSource = '';
                    location.reload();
                } else {
                    showToast('Install failed: ' + (result.error || ''), 'error');
                }
            } catch (e) {
                showToast('Install failed', 'error');
            } finally {
                this.installing = false;
            }
        },

        async toggleSkill(skillId, enabled) {
            try {
                await fetch('/api/skills/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: skillId, enabled: enabled })
                });
                showToast(enabled ? 'Skill enabled' : 'Skill disabled', 'success');
            } catch (e) {
                showToast('Failed to toggle skill', 'error');
            }
        },

        async uninstallSkill(skillId) {
            if (!(await window.kazmaConfirm({
                title: 'Uninstall skill',
                message: 'Uninstall this skill? This cannot be undone.',
                confirmText: 'Uninstall',
                danger: true,
            }))) return;
            try {
                await fetch('/api/skills/uninstall', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: skillId })
                });
                showToast('Skill uninstalled', 'success');
                location.reload();
            } catch (e) {
                showToast('Failed to uninstall', 'error');
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
                    showToast('Skill installed', 'success');
                    location.reload();
                } else {
                    showToast('Install failed: ' + (result.error || ''), 'error');
                }
            } catch (e) {
                showToast('Install failed', 'error');
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
