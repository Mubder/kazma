/**
 * Providers Manager — the provider page's data layer.
 *
 * Pure logic only: how to read a test result, what state to paint a card in,
 * which capability badges to show. Every HTTP call the page makes lives in
 * settings_hub.js against /api/providers; this file deliberately owns no
 * endpoint of its own, because owning a second one is what let the two halves
 * of this feature drift far enough that a whole phase shipped into a route
 * nobody called.
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

    // NOTE: loadAll / add / remove / toggle / test used to sit here, wrapping
    // /api/settings/providers. Nothing called them: the Settings page is built
    // on the hub* functions in settings_hub.js, which use /api/providers. They
    // were an SDK for a page that had stopped existing, and keeping a second
    // client in step with the first is precisely the cost this refactor already
    // paid once. What remains below is pure logic with no endpoint of its own.

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

    /** Display names for the capability keys the provider layer declares. */
    CAPABILITY_LABELS: {
        tools: 'Tools',
        streaming: 'Streaming',
        json_mode: 'JSON mode',
        vision: 'Vision',
    },

    /**
     * The state to paint a provider card in, from whatever evidence exists.
     *
     * A fresh test result wins. Failing that, the stored health from the last
     * test is used — which is why `chat_failing` is its own health value and
     * not folded into `degraded`: an operator who reloads the page must not
     * lose the difference between "cannot reach it" and "reaches it, cannot
     * use it".
     *
     * @param {Object} provider   The provider entry from /api/providers
     * @param {Object} [result]   A test result from this session, if any
     * @returns {'working'|'chat_failing'|'unreachable'|'untested'}
     */
    cardState(provider, result) {
        if (result) return this.stateOf(result);
        var health = (provider && provider.health) || '';
        if (health === 'healthy') return 'working';
        if (health === 'chat_failing') return 'chat_failing';
        if (health === 'down' || health === 'degraded') return 'unreachable';
        return 'untested';
    },

    /** Human label for a card state. */
    STATE_LABELS: {
        working: 'Working',
        chat_failing: 'Chat failing',
        unreachable: 'Unreachable',
        untested: 'Not tested',
    },

    /**
     * Capability badges for a provider entry.
     *
     * Three states, not two. `null` from the backend means NOT VERIFIED, and
     * it renders as its own thing — showing an unmeasured capability as "no"
     * would be an assumption wearing a checkmark, which is the failure mode
     * the capability schema exists to end.
     *
     * @param {Object} provider
     * @returns {Array<{key: string, label: string, state: 'yes'|'no'|'unknown', title: string}>}
     */
    capabilityBadges(provider) {
        var caps = (provider && provider.capabilities) || null;
        if (!caps || !caps.supports) return [];
        var supports = caps.supports;
        var self = this;
        return Object.keys(this.CAPABILITY_LABELS).map(function (key) {
            var value = supports[key];
            var state = value === true ? 'yes' : (value === false ? 'no' : 'unknown');
            var title = {
                yes: 'Measured against this provider and confirmed.',
                no: 'Measured against this provider and not supported.',
                unknown: 'Not verified. Nobody has measured this yet — run scripts/provider_conformance.py --live.',
            }[state];
            return { key: key, label: self.CAPABILITY_LABELS[key], state: state, title: title };
        });
    },

    /**
     * The declared wire facts worth showing next to a provider: how Kazma
     * talks to it, and what it calls the system turn. Both were guessed in
     * code until providers started 400ing over them.
     * @param {Object} provider
     * @returns {Array<{label: string, value: string}>}
     */
    wireFacts(provider) {
        var caps = (provider && provider.capabilities) || null;
        if (!caps) return [];
        var facts = [{ label: 'API', value: String(caps.api_style || 'openai') }];
        facts.push({ label: 'System turn', value: String(caps.system_role || 'system') });
        if (caps.max_context) {
            facts.push({ label: 'Context', value: String(caps.max_context) });
        }
        return facts;
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
     * The API version segment the provider's base URL declares, e.g. "v4".
     *
     * Shown because guessing it is what broke Google, Z.AI and Perplexity:
     * a URL ending in something other than /v1 had /v1 appended, every call
     * 404'd, and the page still reported the provider healthy. What the
     * preset declares is now what gets called, and the operator can see it.
     *
     * @param {Object} provider
     * @returns {string} the segment, or '' when the URL carries none
     */
    apiVersionOf(provider) {
        var url = String((provider && provider.base_url) || '').replace(/\/+$/, '');
        var match = url.match(/\/(v[0-9][A-Za-z0-9._-]*)$/);
        return match ? match[1] : '';
    },

    /**
     * How many providers sit in each state — the counters above the list.
     * @param {Array} providers
     * @returns {{working: number, chat_failing: number, unreachable: number, untested: number}}
     */
    stateCounts(providers) {
        var counts = { working: 0, chat_failing: 0, unreachable: 0, untested: 0 };
        (providers || []).forEach(function (p) {
            var state = ProvidersManager.cardState(p, p && p._test);
            if (counts[state] !== undefined) counts[state] += 1;
        });
        return counts;
    },

    /**
     * The last clause of a probe error, for the narrow meta column.
     *
     * The backend error is written to be read as a sentence in the alert —
     * "Reachable, but chat is failing. The model list answered in 358 ms; a
     * real completion returned: HTTP 404". In a right-aligned column that
     * ellipsises before reaching the only part that varies, which is the part
     * the operator needs.
     *
     * @param {string} error
     * @returns {string}
     */
    shortReason(error) {
        var text = String(error || '').trim();
        if (!text) return '';
        var marker = text.lastIndexOf('returned: ');
        if (marker !== -1) text = text.slice(marker + 'returned: '.length);
        text = text.trim();
        return text.length > 48 ? text.slice(0, 47) + '…' : text;
    },

    /**
     * The checks that were actually run, with their real numbers.
     *
     * Two, not a longer list: Kazma asks the model list and then sends one
     * real completion. Rendering a fuller conformance table here would mean
     * inventing rows nothing measured — the live probes that produce more
     * (system turn, tool round-trip) live in scripts/provider_conformance.py
     * and cost money, so they are not run from this page.
     *
     * @param {Object} result  a test result, or null before Test is pressed
     * @returns {Array<{key: string, label: string, state: 'ok'|'fail'|'idle', detail: string}>}
     */
    checksFor(result) {
        var idle = { state: 'idle', detail: '' };
        if (!result) {
            return [
                { key: 'models', label: 'model_list', state: 'idle', detail: '' },
                { key: 'chat', label: 'completion', state: 'idle', detail: '' },
            ];
        }
        var reachable = result.success || result.reachable === true;
        var models = reachable
            ? { state: 'ok', detail: result.latency_ms != null ? result.latency_ms + ' ms' : '' }
            : { state: 'fail', detail: result.error || '' };
        var chat = idle;
        if (result.success) {
            chat = {
                state: 'ok',
                detail: [
                    result.chat_ms != null ? result.chat_ms + ' ms' : '',
                    result.chat_model || '',
                ].filter(Boolean).join(' · '),
            };
        } else if (reachable) {
            chat = { state: 'fail', detail: this.shortReason(result.error) };
        }
        return [
            { key: 'models', label: 'model_list', state: models.state, detail: models.detail },
            { key: 'chat', label: 'completion', state: chat.state, detail: chat.detail },
        ];
    },

    /**
     * Get status icon for a provider health state.
     * @param {string} status - 'healthy' | 'degraded' | 'down' | 'unknown'
     * @returns {string} SVG status dot
     */
    statusIcon(status) {
        // Use colored SVG circles instead of emoji for a crisp, premium look.
        // degraded == reachable but chat failing: the state that had no colour before.
        var colors = {
            healthy: 'var(--success)', degraded: 'var(--warning)',
            chat_failing: 'var(--warning)', down: 'var(--danger)',
            unknown: 'var(--text-muted)',
            // Card states share the dot, so both vocabularies resolve here.
            working: 'var(--success)', unreachable: 'var(--danger)',
            untested: 'var(--text-muted)',
        };
        var color = colors[status] || colors.unknown;
        return '<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="' + color + '"/></svg>';
    },
};

// Make available globally
window.ProvidersManager = ProvidersManager;
