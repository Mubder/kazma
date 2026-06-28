/**
 * Models Manager — Model registry, defaults, comparison, and usage tracking.
 * Used by the Models tab in Settings.
 */

const ModelsManager = {
    _cache: [],

    /**
     * Fetch available models from the backend.
     * @param {string} provider - Provider key (e.g. 'openai', 'all')
     * @param {string} base_url - Optional override
     * @param {string} api_key - Optional API key
     * @returns {Promise<Object>} { models, provider, online }
     */
    async discover(provider = 'all', base_url = '', api_key = '') {
        let url = `/api/models?provider=${encodeURIComponent(provider)}`;
        if (base_url) url += `&base_url=${encodeURIComponent(base_url)}`;
        if (api_key) url += `&api_key=${encodeURIComponent(api_key)}`;
        try {
            const resp = await fetch(url);
            const data = await resp.json();
            this._cache = data.models || [];
            return data;
        } catch (e) {
            console.error('[Models] Discovery failed:', e);
            return { models: [], provider, online: false, error: e.message };
        }
    },

    /**
     * Get the model registry (all known models with metadata).
     * @returns {Promise<Array>}
     */
    async getRegistry() {
        try {
            const resp = await fetch('/api/settings/models/registry');
            if (!resp.ok) return [];
            return await resp.json();
        } catch (e) {
            console.error('[Models] Registry load failed:', e);
            return [];
        }
    },

    /**
     * Get model defaults per task type.
     * @returns {Promise<Object>} { chat, code, summarize, translate }
     */
    async getDefaults() {
        try {
            const resp = await fetch('/api/settings/models/defaults');
            if (!resp.ok) return {};
            return await resp.json();
        } catch (e) {
            return {};
        }
    },

    /**
     * Set the default model for a task type.
     * @param {string} taskType - 'chat', 'code', 'summarize', 'translate'
     * @param {string} modelName
     * @returns {Promise<Object>}
     */
    async setDefault(taskType, modelName) {
        const resp = await fetch('/api/settings/models/defaults', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_type: taskType, model_name: modelName }),
        });
        return await resp.json();
    },

    /**
     * Get token usage stats per model.
     * @returns {Promise<Object>} { model_name: { tokens_in, tokens_out, cost, requests } }
     */
    async getUsage() {
        try {
            const resp = await fetch('/api/settings/models/usage');
            if (!resp.ok) return {};
            return await resp.json();
        } catch (e) {
            return {};
        }
    },

    /**
     * Run model comparison — same prompt across multiple models.
     * @param {string} prompt
     * @param {Array<string>} models
     * @param {number} temperature
     * @param {number} maxTokens
     * @returns {Promise<Array<Object>>} [{ model, response, latency_ms, tokens, error }]
     */
    async compare(prompt, models, temperature = 0.7, maxTokens = 256) {
        const resp = await fetch('/api/settings/models/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt, models, temperature, max_tokens: maxTokens }),
        });
        return await resp.json();
    },

    /**
     * Get cached models from last discovery.
     * @returns {Array<string>}
     */
    getCached() {
        return this._cache;
    },
};

window.ModelsManager = ModelsManager;
