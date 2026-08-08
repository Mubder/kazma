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
      try {
        const resp = await fetch('/api/agents/status');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        this.agent = data;
      } catch (err) {
        console.error('[AgentsPage] status fetch failed:', err);
      }
    },

    async fetchToolHistory() {
      try {
        const resp = await fetch('/api/agents/tools?limit=50');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        // Newest first
        this.toolHistory = (data.tools || []).reverse();
      } catch (err) {
        console.error('[AgentsPage] tool history fetch failed:', err);
      }
    },

    async fetchReasoning() {
      try {
        const resp = await fetch('/api/agents/reasoning?limit=50');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        // Newest first
        this.reasoningSteps = (data.steps || []).reverse();
      } catch (err) {
        console.error('[AgentsPage] reasoning fetch failed:', err);
      }
    },

    async control(action) {
      if (action !== 'start' && action !== 'stop') return;
      this.loadingAction = true;
      try {
        const resp = await fetch('/api/agents/' + action, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'ok') {
          this.agent.running = data.running;
          if (window.KazmaStream) {
            KazmaStream.toast(
              action === 'start' ? 'Agent started' : 'Agent stopped',
              'success',
              3000,
            );
          }
        } else {
          const msg = data.message || 'Action failed';
          if (window.KazmaStream) {
            KazmaStream.toast(msg, 'error', 5000);
          }
        }
      } catch (err) {
        console.error('[AgentsPage] control failed:', err);
        if (window.KazmaStream) {
          KazmaStream.toast('Failed to ' + action + ' agent', 'error', 5000);
        }
      } finally {
        this.loadingAction = false;
        // Refresh state after the action
        await this.fetchStatus();
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
        if (window.KazmaStream) KazmaStream.toast('Failed to switch personality', 'error', 5000);
      }
    },
  };
}
