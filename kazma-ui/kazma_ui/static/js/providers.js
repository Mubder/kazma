/**
 * Providers Manager — Handles provider CRUD, health monitoring, and connection testing.
 * Used by the Services/Providers tab in Settings.
 */

// Assign to window so soft-nav can re-inject this file without
// "Identifier has already been declared" on top-level const.
var ProvidersManager = window.ProvidersManager = {
    /** Provider presets with default base URLs */
    PRESETS: {
        openai: { name: 'OpenAI', base_url: 'https://api.openai.com/v1', docs: 'https://platform.openai.com/api-keys' },
        anthropic: { name: 'Anthropic', base_url: 'https://api.anthropic.com/v1', docs: 'https://console.anthropic.com/keys' },
        deepseek: { name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', docs: 'https://platform.deepseek.com/api_keys' },
        google: { name: 'Google Gemini', base_url: 'https://generativelanguage.googleapis.com/v1beta', docs: 'https://aistudio.google.com/apikey' },
        xai: { name: 'xAI / Grok', base_url: 'https://api.x.ai/v1', docs: 'https://console.x.ai' },
        openrouter: { name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', docs: 'https://openrouter.ai/keys' },
        ollama: { name: 'Ollama (Local)', base_url: 'http://127.0.0.1:11434/v1', docs: '' },
        'lm-studio': { name: 'LM Studio (Local)', base_url: 'http://localhost:1234/v1', docs: '' },
        nvidia: { name: 'NVIDIA NIM', base_url: 'https://integrate.api.nvidia.com/v1', docs: 'https://build.nvidia.com' },
        custom: { name: 'Custom Endpoint', base_url: '', docs: '' },
    },

    /**
     * Load all providers from backend.
     * @returns {Promise<Array>} Provider list
     */
    async loadAll() {
        try {
            const resp = await fetch('/api/settings/providers');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            return await resp.json();
        } catch (e) {
            console.error('[Providers] Failed to load:', e);
            return [];
        }
    },

    /**
     * Add a new provider.
     * @param {Object} data - { name, display_name, base_url, api_key, models, enabled }
     * @returns {Promise<Object>} Result
     */
    async add(data) {
        const resp = await fetch('/api/settings/providers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return await resp.json();
    },

    /**
     * Delete a provider by name.
     * @param {string} name
     * @returns {Promise<Object>}
     */
    async remove(name) {
        const resp = await fetch(`/api/settings/providers/${encodeURIComponent(name)}`, {
            method: 'DELETE',
        });
        return await resp.json();
    },

    /**
     * Toggle provider enabled/disabled.
     * @param {string} name
     * @param {boolean} enabled
     * @returns {Promise<Object>}
     */
    async toggle(name, enabled) {
        const resp = await fetch(`/api/settings/providers/${encodeURIComponent(name)}/toggle`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
        return await resp.json();
    },

    /**
     * Test a provider connection.
     *
     * The backend now sends a real completion, not just a model-list query,
     * so the result carries three outcomes rather than two. A provider whose
     * model list answers while its chat 404s used to report as healthy — the
     * operator saw green and not one message ever reached it.
     *
     * @param {string} name
     * @returns {Promise<Object>} { success, reachable, chat_ok, latency_ms, chat_ms, chat_model, error }
     */
    async test(name) {
        const resp = await fetch(`/api/settings/providers/${encodeURIComponent(name)}/test`, {
            method: 'POST',
        });
        return await resp.json();
    },

    /**
     * Which of the three states a test result represents.
     * @param {Object} result
     * @returns {'working'|'chat_failing'|'unreachable'}
     */
    stateOf(result) {
        if (!result) return 'unreachable';
        if (result.success) return 'working';
        // Reached the provider, but it cannot actually answer a message.
        if (result.reachable && result.chat_ok === false) return 'chat_failing';
        return 'unreachable';
    },

    /**
     * One line an operator can act on, per state.
     * @param {Object} result
     * @returns {{label: string, tone: string, detail: string}}
     */
    describe(result) {
        const state = this.stateOf(result);
        if (state === 'working') {
            const ms = result.chat_ms != null ? `${result.chat_ms} ms` : '';
            const model = result.chat_model ? ` · ${result.chat_model}` : '';
            return { label: 'Working', tone: 'success', detail: `replied in ${ms}${model}` };
        }
        if (state === 'chat_failing') {
            return {
                label: 'Chat failing',
                tone: 'warning',
                detail: result.error || 'the model list answers, a real message does not',
            };
        }
        return { label: 'Unreachable', tone: 'danger', detail: result.error || 'no response' };
    },

    /**
     * Get health status for a provider.
     * @param {string} name
     * @returns {Promise<Object>}
     */
    async getHealth(name) {
        const resp = await fetch(`/api/settings/providers/${encodeURIComponent(name)}/health`);
        return await resp.json();
    },

    /**
     * Apply a preset to form fields.
     * @param {string} presetKey
     * @returns {Object|null} Preset data or null
     */
    getPreset(presetKey) {
        return this.PRESETS[presetKey] || null;
    },

    /**
     * Get all preset keys for dropdown.
     * @returns {Array<{key: string, name: string}>}
     */
    getPresetKeys() {
        return Object.entries(this.PRESETS).map(([key, val]) => ({ key, name: val.name }));
    },

    /**
     * Get status icon for a provider health state.
     * @param {string} status - 'healthy' | 'degraded' | 'down' | 'unknown'
     * @returns {string} SVG status dot
     */
    statusIcon(status) {
        // Use colored SVG circles instead of emoji for a crisp, premium look.
        // degraded == reachable but chat failing: the state that had no colour before.
        var colors = { healthy: 'var(--success)', degraded: 'var(--warning)', chat_failing: 'var(--warning)', down: 'var(--danger)', unknown: 'var(--text-muted)' };
        var color = colors[status] || colors.unknown;
        return '<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="' + color + '"/></svg>';
    },
};

// Make available globally
window.ProvidersManager = ProvidersManager;
