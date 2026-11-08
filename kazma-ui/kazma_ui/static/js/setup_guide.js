/* The first-run setup guide (/setup, templates/setup.html).
 *
 * Welcome, the model, optional extras, done. The model is a cloud provider
 * (its key, then the model chosen from the provider's own list) or one on
 * this computer or its network (Ollama or LM Studio, found by
 * /api/setup/local; another computer's server by its address,
 * /api/setup/local/address). Choosing it, with nothing saved until it
 * answered (providers stage 1): /api/setup/models lists the provider's
 * models with the key as typed, /api/setup/check sends the chosen model one
 * short message through the client Kazma's chat would use, and only then
 * /api/setup/bootstrap saves the provider and its key and makes the model
 * the one Kazma uses (model and provider together). Every word is the
 * catalog's (setup.*).
 */
(function () {
  'use strict';

  function tr(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  // The error codes the setup routes answer with, worded by the page.
  var CODES = ['api_key_required', 'unknown_provider', 'save_failed', 'not_configured', 'timeout',
    'bad_address', 'not_own_network', 'no_server', 'key_refused', 'not_found', 'rate_limited',
    'unreachable', 'empty', 'provider_error', 'request_refused', 'unusable_answer', 'failed',
    'key_undecryptable', 'no_base_url', 'blocked_url', 'bad_answer', 'no_model'];

  // A model list's HTTP refusal (``http_401``...) in the check's words.
  function httpCode(code) {
    var m = /^http_(\d+)$/.exec(String(code || ''));
    if (!m) return code;
    var status = Number(m[1]);
    if (status === 401 || status === 403) return 'key_refused';
    if (status === 404) return 'not_found';
    if (status === 429) return 'rate_limited';
    return status >= 500 ? 'provider_error' : 'request_refused';
  }

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
      lanAddress: '',
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

      codeText(code, detail) {
        var text = String(httpCode(code) || '');
        if (CODES.indexOf(text) >= 0) return tr('setup.err.' + text, detail || text);
        return detail || text;
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

      // A server on another computer of this network: found and saved as a
      // provider of its own (/api/setup/local/address), then listed as
      // Ollama is.
      async useLan() {
        var address = String(this.lanAddress || '').trim();
        if (!address) return;
        this.resetModel();
        this.busy = true;
        this.status = tr('setup.lan_looking', 'Looking for a model server there…');
        try {
          var found = await postJson('/api/setup/local/address', { address: address });
          if (!found.data.ok) {
            this.fail('setup.lan_failed', 'No server there:', this.codeText(found.data.error || found.status));
            return;
          }
          this.provider = String(found.data.provider || '');
        } catch (e) {
          this.fail('', '', tr('setup.err.network', 'Kazma did not answer. Is it still running?'));
          return;
        }
        this.busy = false;
        return this.list(this.provider, '');
      },

      async list(provider, key) {
        if (!provider) return;
        this.resetModel();
        this.busy = true;
        this.status = tr('setup.listing', 'Asking for the models…');
        try {
          // Listed with the key as typed: nothing is saved until the chosen
          // model answers (useModel).
          var found = await postJson('/api/setup/models', { provider: provider, api_key: key || '' });
          if (!found.ok || !found.data.ok) {
            this.fail('setup.list_failed', 'Could not list the models:',
              this.codeText(found.data.error || found.status, found.data.detail));
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
        var key = this.where === 'cloud' ? this.apiKey : '';
        try {
          // The check first, with the key as typed: a model that does not
          // answer leaves nothing saved and Kazma on the model it had.
          this.status = tr('setup.checking', 'Sending the model a short test message…');
          var check = await postJson('/api/setup/check', { provider: this.provider, api_key: key, model: model });
          if (!check.data.ok) {
            this.fail('setup.check_failed', 'The model did not answer:',
              this.codeText(check.data.error || check.status, check.data.detail));
            return;
          }
          this.status = tr('setup.saving', 'Saving…');
          var boot = await postJson('/api/setup/bootstrap', { provider: this.provider, api_key: key, model: model });
          if (boot.data.error || !boot.ok) {
            this.fail('setup.save_failed', 'Could not save:', this.codeText(boot.data.error || boot.status));
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
