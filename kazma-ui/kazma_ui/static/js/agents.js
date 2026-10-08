/* ═══════════════════════════════════════════════════════
   Kazma Agents — Agent management & monitoring UI
   Shows agent status, state, tool history, reasoning steps
   Uses Alpine.js x-data component pattern
   ═══════════════════════════════════════════════════════ */

/**
 * Alpine.js component for the Agents page.
 * Loaded via x-data="agentsPage()" on the page container.
 */
function agentsPage() {
  return {
    agent: {
      name: 'kazma',
      running: false,
      agent_state: 'idle',
      session_count: 0,
      config: {},
      llm: {},
      tools: { count: 0, servers: 0, list: [] },
      metrics: { total_cost: '$0.0000', total_tokens: '0', total_llm_calls: 0, total_tool_calls: 0 },
      personality: 'default',
    },
    personalities: [],
    toolHistory: [],
    reasoningSteps: [],
    loadingAction: false,
    statusLoading: false,
    statusLoaded: false,
    statusError: false,
    statusUpdatedAt: '',
    _pollInterval: null,

    init() {
      // Load personality templates
      fetch('/api/settings/agent/personalities', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      }).then(r => r.json()).then(data => {
        if (Array.isArray(data)) this.personalities = data;
      }).catch(() => {});

      // Load current personality
      fetch('/api/settings/agent', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      }).then(r => r.json()).then(data => {
        if (data && data.personality) this.agent.personality = data.personality;
      }).catch(() => {});

      // Initial load
      this.refresh();

      // Poll for updates every 5 seconds
      this._pollInterval = setInterval(() => this.refresh(), 5000);
      window.kazmaOnSoftNavLeave = () => this.destroy();

      // Clean up on page unload
      window.addEventListener('beforeunload', () => this.destroy());
    },

    destroy() {
      if (this._pollInterval) {
        clearInterval(this._pollInterval);
        this._pollInterval = null;
      }
    },

    async refresh() {
      try {
        await Promise.all([
          this.fetchStatus(),
          this.fetchToolHistory(),
          this.fetchReasoning(),
        ]);
      } catch (err) {
        console.error('[AgentsPage] refresh failed:', err);
      }
    },

    async fetchStatus() {
      if (this.statusLoading) return;
      this.statusLoading = true;
      try {
        const data = await this._getJson('/api/agents/status');
        if (!data || typeof data.running !== 'boolean') throw new Error('Status unavailable');
        this.agent = data;
        this.statusLoaded = true;
        this.statusError = false;
        this.statusUpdatedAt = new Date().toLocaleString(window.KAZMA_LANG === 'ar' ? 'ar' : 'en');
      } catch (err) {
        this.statusError = true;
      } finally {
        this.statusLoading = false;
      }
    },

    async _getJson(url) {
      if (window.kazmaGetJson) return window.kazmaGetJson(url);
      try {
        const response = await fetch(url);
        return response.ok ? await response.json() : null;
      } catch (error) { return null; }
    },

    statusLabel() {
      if (!this.statusLoaded) return t(this.statusLoading ? 'agents.loading_status' : 'agents.unknown_status');
      if (this.statusError) return t('agents.stale_status');
      if (!this.agent.running) return t('agents.stopped');
      return t(this.agent.agent_state === 'idle' ? 'agents.ready' : this.agent.agent_state === 'thinking' ? 'agents.thinking' : 'agents.acting');
    },

    async fetchToolHistory() {
      try {
        const data = await this._getJson('/api/agents/tools?limit=50');
        if (!data) return;
        // Newest first
        this.toolHistory = (data.tools || []).reverse();
      } catch (err) {
      }
    },

    async fetchReasoning() {
      try {
        const data = await this._getJson('/api/agents/reasoning?limit=50');
        if (!data) return;
        // Newest first
        this.reasoningSteps = (data.steps || []).reverse();
      } catch (err) {
      }
    },


    async switchPersonality(name) {
      this.agent.personality = name;
      try {
        const resp = await fetch('/api/settings/agent', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          body: JSON.stringify({ personality: name }),
        });
        const data = await resp.json();
        if (window.KazmaStream) {
          const p = this.personalities.find(p => p.name === name);
          KazmaStream.toast(
            (window.KAZMA_LANG === 'ar' ? 'الشخصية: ' : 'Personality: ') + (p ? (p.display_name_ar && window.KAZMA_LANG === 'ar' ? p.display_name_ar : name) : name),
            'success', 3000
          );
        }
      } catch (err) {
        console.error('[AgentsPage] switchPersonality failed:', err);
        if (window.KazmaStream) KazmaStream.toast(window.kazmaT('agents.ui.personality_switch_failed', 'Failed to switch personality'), 'error', 5000);
      }
    },
  };
}
if (typeof window !== "undefined") window.agentsPage = agentsPage;
