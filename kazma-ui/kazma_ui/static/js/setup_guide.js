/* The first-run setup guide (/setup, templates/setup.html).
 *
 * Welcome, the model, optional extras, done. The model is a cloud provider
 * (its key, then the model chosen from the provider's own list) or one on
 * this computer (Ollama or LM Studio, found by /api/setup/local). Choosing
 * it: /api/setup/provider saves the provider and its key, the existing
 * /api/providers/{name}/discover lists its models, /api/setup/bootstrap
 * makes the chosen model the one Kazma uses (model and provider together),
 * and /api/setup/check sends it one short message through the client
 * Kazma's chat uses. Every word is the catalog's (setup.*).
 */
(function () {
  'use strict';

  function tr(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  // The error codes the setup routes answer with, worded by the page.
  var CODES = ['api_key_required', 'unknown_provider', 'save_failed', 'not_configured', 'timeout'];

  async function postJson(url, body) {
    var resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    var data = {};
    try { data = (await resp.json()) || {}; } catch (e) { data = {}; }
    return { ok: resp.ok, status: resp.status, data: data };
  }

  window.setupGuide = function setupGuide() {
    return {
      loaded: false,
      loadFailed: false,
      ready: false,
      step: 1,
      lang: String(document.documentElement.lang || 'en').slice(0, 2),
      presets: [],
      activeProvider: '',
      activeModel: '',
      where: '',
      provider: '',
      apiKey: '',
      servers: [],
      localLoading: false,
      models: [],
      listed: false,
      model: '',
      busy: false,
      status: '',
      error: '',
      errorLead: '',
      tested: false,

      async init() {
        this.loadFailed = false;
        var getJson = window.kazmaGetJson;
        var state = getJson ? await getJson('/api/setup/status') : null;
        if (!state) {
          this.loadFailed = true;
          return;
        }
        this.presets = Array.isArray(state.presets) ? state.presets : [];
        this.ready = state.ready === true;
        this.activeProvider = String(state.active_provider || '');
        this.activeModel = String(state.active_model || '');
        var cloud = this.cloudPresets();
        var active = cloud.find(function (p) { return p.id === state.active_provider; });
        this.provider = active ? active.id : (cloud.length ? cloud[0].id : '');
        this.loaded = true;
      },

      stepClass(n) {
        if (n === this.step) return 'is-current';
        return n < this.step ? 'is-done' : '';
      },

      go(n) {
        this.step = n;
        this.error = '';
        this.errorLead = '';
        try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (e) { /* no smooth scroll */ }
      },

      activeLabel() {
        return [this.activeProvider, this.activeModel].filter(Boolean).join(' · ');
      },

      setLang(next) {
        if (next === this.lang) return;
        try { localStorage.setItem('kazma-lang', next); } catch (e) { /* storage blocked */ }
        var secure = window.location.protocol === 'https:' ? ';secure' : '';
        document.cookie = 'kazma-lang=' + next + ';path=/;max-age=31536000;samesite=lax' + secure;
        window.location.reload();
      },

      // The providers a key alone sets up (custom endpoints, Azure and
      // Bedrock are Settings' own).
      cloudPresets() {
        return this.presets.filter(function (p) { return p.guided; });
      },

      preset(id) {
        return this.presets.find(function (p) { return p.id === id; }) || null;
      },

      keyUrl() {
        var p = this.preset(this.provider);
        var url = p && p.key_url ? String(p.key_url) : '';
        return /^https:\/\//.test(url) ? url : '';
      },

      choose(where) {
        this.where = where;
        this.resetModel();
        if (where === 'local') this.findLocal();
      },

      providerChanged() {
        this.resetModel();
      },

      resetModel() {
        this.models = [];
        this.listed = false;
        this.model = '';
        this.tested = false;
        this.error = '';
        this.errorLead = '';
      },

      fail(leadKey, leadText, detail) {
        this.errorLead = leadKey ? tr(leadKey, leadText) : '';
        this.error = detail || '';
        this.busy = false;
        this.status = '';
      },

      codeText(code) {
        var text = String(code || '');
        if (CODES.indexOf(text) >= 0) return tr('setup.err.' + text, text);
        return text;
      },

      async findLocal() {
        this.localLoading = true;
        this.servers = [];
        var getJson = window.kazmaGetJson;
        var found = getJson ? await getJson('/api/setup/local') : null;
        this.servers = found && Array.isArray(found.servers) ? found.servers : [];
        this.localLoading = false;
        if (!found) this.fail('', '', tr('setup.err.network', 'Kazma did not answer. Is it still running?'));
      },

      connect() {
        return this.list(this.provider, this.apiKey);
      },

      useLocal(id) {
        this.provider = id;
        return this.list(id, '');
      },

      async list(provider, key) {
        if (!provider) return;
        this.resetModel();
        this.busy = true;
        this.status = tr('setup.listing', 'Asking for the models…');
        try {
          var saved = await postJson('/api/setup/provider', { provider: provider, api_key: key || '' });
          if (!saved.data.ok) {
            this.fail('setup.save_failed', 'Could not save:', this.codeText(saved.data.error || saved.status));
            return;
          }
          var found = await postJson('/api/providers/' + encodeURIComponent(provider) + '/discover', {});
          if (!found.ok || !found.data.ok) {
            this.fail('setup.list_failed', 'Could not list the models:',
              String(found.data.detail || found.data.error || found.status));
            return;
          }
          this.models = Array.isArray(found.data.models) ? found.data.models.map(String) : [];
          this.listed = true;
          var p = this.preset(provider);
          var wanted = provider === this.activeProvider && this.activeModel ? this.activeModel
            : (p && p.default_model) || '';
          this.model = this.models.indexOf(wanted) >= 0 ? wanted : (this.models[0] || '');
          if (!this.models.length && this.where === 'local') {
            this.fail('', '', tr('setup.no_local_models', 'This server has no models yet.'));
            return;
          }
        } catch (e) {
          this.fail('', '', tr('setup.err.network', 'Kazma did not answer. Is it still running?'));
          return;
        }
        this.busy = false;
        this.status = '';
      },

      modelsFound() {
        var n = this.models.length;
        return window.kazmaCount ? window.kazmaCount('setup.models_found', n) : '';
      },

      async useModel() {
        var model = String(this.model || '').trim();
        if (!model || !this.provider) return;
        this.tested = false;
        this.error = '';
        this.errorLead = '';
        this.busy = true;
        this.status = tr('setup.saving', 'Saving…');
        try {
          var boot = await postJson('/api/setup/bootstrap', {
            provider: this.provider,
            api_key: this.where === 'cloud' ? this.apiKey : '',
            model: model,
          });
          if (boot.data.error || !boot.ok) {
            this.fail('setup.save_failed', 'Could not save:', this.codeText(boot.data.error || boot.status));
            return;
          }
          this.status = tr('setup.checking', 'Sending the model a short test message…');
          var check = await postJson('/api/setup/check', { provider: this.provider, model: model });
          if (!check.data.ok) {
            this.fail('setup.check_failed', 'The model did not answer:',
              this.codeText(check.data.error || check.status));
            return;
          }
        } catch (e) {
          this.fail('', '', tr('setup.err.network', 'Kazma did not answer. Is it still running?'));
          return;
        }
        this.activeProvider = this.provider;
        this.activeModel = model;
        this.ready = true;
        this.tested = true;
        this.apiKey = '';
        this.busy = false;
        this.status = '';
      },
    };
  };
})();
