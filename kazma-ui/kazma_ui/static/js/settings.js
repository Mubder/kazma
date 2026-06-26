/* Kazma Settings — Alpine.js app for settings management */

function settingsApp() {
    return {
        tab: 'model',
        showKey: false,
        showTelegramToken: false,
        testing: false,
        testResult: null,
        model: {
            base_url: '',
            api_key: '',
            model: '',
            max_tokens: 4096,
            temperature: 0.7,
            timeout: 60
        },
        agent: {
            name: '',
            language: 'ar',
            system_prompt: ''
        },
        cost: {
            max_cost: 0.50,
            silence_window: 300
        },
        connectors: {
            telegram_token: '',
            telegram_allowed_users: '',
            discord_token: '',
            slack_token: '',
            slack_app_token: ''
        },

        init() {
            // Load settings from server
            this.loadSettings();
        },

        async loadSettings() {
            try {
                var resp = await fetch('/api/settings');
                var data = await resp.json();
                if (data.model) Object.assign(this.model, data.model);
                if (data.agent) Object.assign(this.agent, data.agent);
                if (data.cost) Object.assign(this.cost, data.cost);
                if (data.connectors) Object.assign(this.connectors, data.connectors);
            } catch (e) {
                console.error('Failed to load settings:', e);
            }
        },

        async saveModel() {
            var updates = Object.entries(this.model).map(function([k, v]) {
                return { key: 'llm.' + k, value: v, category: 'model' };
            });
            await this.save(updates);
        },

        async saveAgent() {
            var updates = Object.entries(this.agent).map(function([k, v]) {
                return { key: 'agent.' + k, value: v, category: 'agent' };
            });
            await this.save(updates);
        },

        async saveCost() {
            var updates = Object.entries(this.cost).map(function([k, v]) {
                return { key: 'cost.' + k, value: v, category: 'cost' };
            });
            await this.save(updates);
        },

        async saveConnectors() {
            var updates = [
                { key: 'connectors.telegram.token', value: this.connectors.telegram_token, category: 'connectors' },
                { key: 'connectors.telegram.allowed_users', value: this.connectors.telegram_allowed_users, category: 'connectors' },
                { key: 'connectors.discord.token', value: this.connectors.discord_token, category: 'connectors' },
                { key: 'connectors.slack.token', value: this.connectors.slack_token, category: 'connectors' },
                { key: 'connectors.slack.app_token', value: this.connectors.slack_app_token, category: 'connectors' },
            ];
            await this.save(updates);
            showToast('Connectors saved. Restart gateway to apply.', 'success');
        },

        async save(updates) {
            try {
                var resp = await fetch('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updates)
                });
                if (resp.ok) {
                    showToast('Settings saved', 'success');
                }
            } catch (e) {
                showToast('Failed to save: ' + e.message, 'error');
            }
        },

        async testModel() {
            this.testing = true;
            this.testResult = null;
            try {
                var resp = await fetch('/api/settings/test-model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.model)
                });
                this.testResult = await resp.json();
            } catch (e) {
                this.testResult = { success: false, error: e.message };
            }
            this.testing = false;
        },

        async importYaml(event) {
            var file = event.target.files[0];
            if (!file) return;
            var text = await file.text();
            try {
                var resp = await fetch('/api/settings/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'text/yaml' },
                    body: text
                });
                var result = await resp.json();
                showToast('Imported ' + result.imported + ' settings', 'success');
                this.loadSettings();
            } catch (e) {
                showToast('Import failed: ' + e.message, 'error');
            }
        },

        async resetSettings() {
            if (!confirm('Reset all settings to defaults? This cannot be undone.')) return;
            try {
                await fetch('/api/settings/reset', { method: 'POST' });
                showToast('Settings reset to defaults', 'success');
                this.loadSettings();
            } catch (e) {
                showToast('Reset failed: ' + e.message, 'error');
            }
        }
    };
}
