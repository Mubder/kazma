/** Settings mixin: providerSetup — the guided "Add a provider".
 *
 * Choose the provider, its address, the key, check it, use it. What is typed
 * is never lost: a click outside asks, and a half-done setup is resumed. The
 * key and every other secret stay in this page's memory until the save (or
 * the message the person sends to test it); the resumable draft holds none.
 */
(function (root) {
    "use strict";
    // Text built here: the catalog's text in the page's language, else the
    // English given; {name} placeholders filled from vars.
    function _k(key, en, vars) {
        var f = root && root.kazmaT;
        if (typeof f === "function") return f(key, en, vars);
        var s = en;
        if (vars) for (var v in vars) s = s.split("{" + v + "}").join(String(vars[v]));
        return s;
    }

    var STEPS = ["provider", "address", "key", "check", "use"];
    var DRAFT_KEY = "kazma.providerSetup.draft";

    /** What the resumable draft keeps. Never a key, a secret, a header or
     *  what was typed in the search box (a key may have been pasted there). */
    var DRAFT_FIELDS = ["preset", "step", "endpoint", "customUrl", "googleMode", "region", "apiVersion",
        "projectId", "location", "name", "displayName", "models", "model", "kind", "urlAsTyped", "values"];

    var Logic = {
        STEPS: STEPS,
        DRAFT_FIELDS: DRAFT_FIELDS,

        empty() {
            return {
                open: false, step: 1, resumed: false, query: "", preset: "", endpoint: "", customUrl: "", values: {},
                googleMode: "ai_studio", region: "", apiVersion: "", projectId: "", location: "",
                apiKey: "", awsKeyId: "", awsSecret: "", awsToken: "", showKey: false,
                name: "", displayName: "", models: "", model: "", kind: "chat", urlAsTyped: false, headerRows: [],
                baseUrl: "", problems: [], same: [], addTo: "", separate: false, keyHost: "", cleared: "",
                busy: "", found: null, findError: "", test: null, saved: "", savedLabel: "", error: "",
                shown: {}, shownQuery: "", makeDefault: "", backup: "", backups: [],
            };
        },

        /** The catalog's rows for *query*, by group, empty groups left out. */
        groups(catalog, order, query) {
            var q = String(query || "").trim().toLowerCase();
            var rows = (catalog || []).filter(function (r) {
                if (!q) return true;
                return (r.name + " " + r.id + " " + (r.base_url || "")).toLowerCase().indexOf(q) !== -1;
            });
            return (order || []).map(function (id) {
                return { id: id, rows: rows.filter(function (r) { return (r.group || "api") === id; }) };
            }).filter(function (g) { return g.rows.length; });
        },

        /** The providers whose keys look like *text*, or []. */
        guess(text, shapes) {
            var key = String(text || "").trim();
            if (key.length < 8 || /\s/.test(key)) return [];
            for (var i = 0; i < (shapes || []).length; i++) {
                if (new RegExp(shapes[i].pattern).test(key)) return shapes[i].providers.slice();
            }
            return [];
        },

        /** *value* as the field takes it. */
        fieldOk(field, value, fields) {
            var rule = (fields || {})[field];
            return !!rule && new RegExp("^(?:" + rule.pattern + ")$").test(String(value || "").trim());
        },

        /** An endpoint's address with the typed values in it. */
        fill(template, values) {
            return String(template || "").replace(/\{([a-z_]{1,40})\}/g, function (whole, name) {
                var v = String((values || {})[name] || "").trim();
                return v || whole;
            });
        },

        /** The two letters of a provider's tile. */
        monogram(name) {
            var words = String(name || "").replace(/[^A-Za-z0-9 ]/g, " ").trim().split(" ").filter(Boolean);
            if (!words.length) return "?";
            return (words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)).toUpperCase();
        },

        /** One of the theme's category colours, the same for a provider
         *  everywhere. */
        tone(id) {
            var sum = 0;
            String(id || "").split("").forEach(function (c) { sum += c.charCodeAt(0); });
            return (sum % 6) + 1;
        },

        /** A name no row has: the preset's, unless a row that is set up has
         *  it (a catalog row nobody set up is filled in). */
        freeName(base, rows) {
            var taken = {};
            var wanted = String(base || "").toLowerCase();
            (rows || []).forEach(function (r) {
                var name = String(r.name || "").toLowerCase();
                var setUp = !!r.enabled || (!!r.api_key && r.api_key !== "—");
                if (setUp || name !== wanted) taken[name] = true;
            });
            if (!wanted || !taken[wanted]) return base;
            for (var i = 2; ; i++) {
                if (!taken[wanted + "-" + i]) return base + "-" + i;
            }
        },

        /** True when a row that is set up has *name*, in any case, or
         *  answers to it since a merge. */
        nameTaken(name, rows) {
            var wanted = String(name || "").trim().toLowerCase();
            return (rows || []).some(function (r) {
                if ((r.aliases || []).some(function (a) { return String(a).trim().toLowerCase() === wanted; })) return true;
                return String(r.name || "").trim().toLowerCase() === wanted
                    && (!!r.enabled || (!!r.api_key && r.api_key !== "—"));
            });
        },

        /** The host an address names, "" when it names none. */
        hostOf(url) {
            try { return new URL(String(url || "").trim()).host.toLowerCase(); } catch (e) { return ""; }
        },

        /** Text that may hold a key: a known key's shape, or a long run of
         *  letters and digits no name or model id is. */
        mayHoldKey(text, shapes) {
            return String(text || "").split(/[\s,;/?#&=@]+/).some(function (part) {
                if (Logic.guess(part, shapes).length) return true;
                return /^[A-Za-z0-9_.-]{24,}$/.test(part) && /[A-Za-z0-9]{20,}/.test(part) && /\d/.test(part) && /[A-Za-z]/.test(part);
            });
        },

        /** The draft a half-done setup is resumed from; null when there is
         *  nothing worth keeping. An address with a password or a query in it
         *  is not kept, nor any value that may be a key put in a wrong box. */
        draft(setup, shapes) {
            if (!setup || !setup.preset || setup.saved) return null;
            var out = {};
            DRAFT_FIELDS.forEach(function (f) {
                out[f] = typeof setup[f] === "string" && Logic.mayHoldKey(setup[f], shapes) ? "" : setup[f];
            });
            out.values = {};
            Object.keys(setup.values || {}).forEach(function (f) {
                if (!Logic.mayHoldKey(setup.values[f], shapes)) out.values[f] = setup.values[f];
            });
            if (/[@?#]/.test(String(out.customUrl || ""))) out.customUrl = "";
            // The key is not kept: back to its step, where it is typed again.
            out.step = Math.min(Number(setup.step) || 1, 3);
            return out;
        },

        restore(draft, catalog) {
            if (!draft || typeof draft !== "object") return null;
            var known = (catalog || []).some(function (r) { return r.id === draft.preset; });
            if (!known) return null;
            var setup = Logic.empty();
            DRAFT_FIELDS.forEach(function (f) { if (draft[f] !== undefined && draft[f] !== null) setup[f] = draft[f]; });
            if (!setup.values || typeof setup.values !== "object") setup.values = {};
            // The address is looked at again (a row may have been added since).
            setup.step = Math.min(Math.max(Number(setup.step) || 1, 1), 2);
            setup.resumed = true;
            return setup;
        },
    };
    root.ProviderSetup = Logic;

    root.KazmaSettingsMixins = root.KazmaSettingsMixins || {};
    root.KazmaSettingsMixins.providerSetup = function () {
        return {
            setup: Logic.empty(),
            setupData: { groups: [], fields: {}, regions: {}, key_shapes: [] },
            _setupTicket: 0,

            // ── open, close, resume ─────────────────────────────────

            async openProviderSetup() {
                if (!this.setupData.groups.length) {
                    const data = await this._fetch('/api/providers/setup');
                    if (data && Array.isArray(data.groups)) this.setupData = data;
                }
                if (!this.setup.preset || this.setup.saved) {
                    let draft = null;
                    try { draft = JSON.parse(root.localStorage.getItem(DRAFT_KEY) || 'null'); } catch (e) { draft = null; }
                    this.setup = Logic.restore(draft, ProvidersManager.catalog) || Logic.empty();
                } else {
                    this.setup.resumed = true;
                }
                this.setup.open = true;
                setTimeout(() => {
                    const el = root.document && root.document.getElementById(this.setup.step === 1 ? 'setup-search' : 'setup-next');
                    if (el) el.focus();
                }, 50);
            },

            /** Keeps the draft in step with the dialog (x-effect). */
            keepSetupDraft() {
                const draft = Logic.draft(this.setup, this.setupData.key_shapes);
                if (!this.setup.open) return;
                try {
                    if (draft) root.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
                    else root.localStorage.removeItem(DRAFT_KEY);
                } catch (e) { /* storage refused: the setup is kept while the page is open */ }
            },

            _forgetSetupDraft() {
                try { root.localStorage.removeItem(DRAFT_KEY); } catch (e) { /* storage refused: nothing was kept */ }
            },

            startSetupOver() {
                this._setupTicket++;
                this._forgetSetupDraft();
                this.setup = Object.assign(Logic.empty(), { open: true });
            },

            async closeProviderSetup() {
                const s = this.setup;
                if (s.saved || (!s.preset && !s.query.trim())) {
                    this._dropSetup();
                    return;
                }
                const answer = await root.KazmaDialogGuard.ask({
                    title: _k('settings.setup.close_title', 'Close the setup?'),
                    message: _k('settings.setup.close_body',
                        'What you chose is kept, so you can go on later. A key you pasted is written nowhere: it is kept only until you leave or reload this page.'),
                    confirmText: _k('settings.setup.close_keep', 'Close for now'),
                    cancelText: _k('settings.setup.close_stay', 'Go on'),
                    checkbox: _k('settings.setup.close_discard', 'Discard what I typed'),
                    danger: false,
                });
                if (!answer || !answer.ok) return;
                if (answer.checked) this._dropSetup();
                else { this._setupTicket++; s.busy = ''; s.open = false; }
            },

            _dropSetup() {
                this.startSetupOver();
                this.setup.open = false;
            },

            // ── what the dialog shows ───────────────────────────────

            setupSteps() {
                return STEPS.map((id, i) => ({
                    id: id, n: i + 1, label: _k('settings.setup.step_' + id, id),
                    state: i + 1 === this.setup.step ? 'current' : (i + 1 < this.setup.step ? 'done' : 'ahead'),
                }));
            },

            setupPreset() {
                return ProvidersManager.getPreset(this.setup.preset) || {};
            },

            setupGroups() {
                const pasted = this.setupGuess(this.setup.query).length > 0;
                return Logic.groups(ProvidersManager.catalog, this.setupData.groups, pasted ? '' : this.setup.query);
            },

            setupGroupLabel(id) {
                return _k('settings.setup.group_' + id, id);
            },

            setupMonogram(row) { return Logic.monogram(row.name); },
            setupTone(row) { return 'ps-tile-' + Logic.tone(row.id); },

            /** A provider's name as the dialog says it. */
            setupName(id) {
                if (id === 'custom') return _k('settings.hub.custom_endpoint', 'Custom endpoint');
                const row = ProvidersManager.getPreset(id);
                return row ? row.name : id;
            },

            setupGuess(text) {
                return Logic.guess(text, this.setupData.key_shapes);
            },

            /** The other providers a typed key looks like; [] when it fits
             *  the one chosen. */
            setupKeyMismatch() {
                const like = this.setupGuess(this.setup.apiKey);
                if (!like.length || like.indexOf(this.setup.preset) !== -1 || this.setup.preset === 'custom') return [];
                return like;
            },

            setupIsVertex() {
                return this.setupPreset().form === 'google' && this.setup.googleMode === 'vertex_ai';
            },

            setupEndpoints() {
                const preset = this.setupPreset();
                if (!preset.id || preset.form === 'bedrock' || preset.form === 'google') return [];
                const own = (preset.endpoints || []).map(e => ({
                    id: e.id, fields: e.fields || [], label: _k('settings.setup.endpoint_' + e.id, e.id),
                    url: Logic.fill(e.base_url, this.setup.values),
                    shown: Logic.fill(e.base_url, this.setup.values).replace(/\{[a-z_]{1,40}\}/g, '…'),
                }));
                if (!own.length && preset.base_url && preset.form !== 'local') {
                    own.push({
                        id: 'official', fields: [], label: _k('settings.setup.endpoint_official', 'The official address'),
                        url: preset.base_url, shown: preset.base_url,
                    });
                }
                own.push({
                    id: 'custom', fields: [], url: '', shown: '',
                    label: preset.form === 'local'
                        ? _k('settings.setup.endpoint_local', 'Its address')
                        : _k('settings.setup.endpoint_custom', 'Another address'),
                });
                return own;
            },

            setupEndpoint() {
                return this.setupEndpoints().find(e => e.id === this.setup.endpoint) || null;
            },

            setupEndpointFields() {
                const chosen = this.setupEndpoint();
                return chosen ? chosen.fields : [];
            },

            /** The fields the step asks beside the address. */
            setupFormFields() {
                const preset = this.setupPreset();
                if (preset.form === 'google' && !this.setupIsVertex()) return [];
                const model = { api_version: 'apiVersion', region: 'region', project_id: 'projectId', location: 'location' };
                return (preset.form_fields || []).map(f => ({ id: f.id, required: !!f.required, model: model[f.id] }));
            },

            setupFieldLabel(id) {
                return _k('settings.setup.field_' + id, id);
            },

            setupFieldExample(id) {
                return ((this.setupData.fields || {})[id] || {}).example || '';
            },

            setupFieldOptions(id) {
                const list = ((this.setupData.fields || {})[id] || {}).options;
                return (list && this.setupData.regions[list]) || [];
            },

            setupProblem(field) {
                const p = this.setup.problems.find(x => x.field === field);
                if (!p) return '';
                return p.code === 'missing'
                    ? _k('settings.setup.problem_missing', 'This is needed.')
                    : _k('settings.setup.problem_malformed', "This does not look right. Check it against the provider's page.");
            },

            /** The address as it will be used ("" when the client makes its
             *  own: Bedrock, Google). */
            setupAddress() {
                const chosen = this.setupEndpoint();
                if (!chosen) return '';
                return chosen.id === 'custom' ? this.setup.customUrl.trim() : chosen.url;
            },

            setupKeyPage() {
                const preset = this.setupPreset();
                const own = (preset.endpoints || []).find(e => e.id === this.setup.endpoint);
                return (own && own.key_page) || preset.key_page || preset.docs || '';
            },

            /** The host a key link goes to, so the person sees where. */
            setupKeyHost() {
                try { return new URL(this.setupKeyPage()).host; } catch (e) { return ''; }
            },

            /** The key is needed before anything is asked of the provider. */
            setupNeedsKey() {
                const form = this.setupPreset().form;
                return form !== 'local' && form !== 'bedrock' && !this.setupIsVertex() && this.setup.preset !== 'custom';
            },

            // ── choosing ────────────────────────────────────────────

            /** *ofKey*: the provider a typed key looks like was pressed, so the
             *  key is its. A key typed for one provider is never carried to
             *  another otherwise: it would be sent to that one's address. */
            chooseSetupProvider(id, ofKey) {
                const s = this.setup;
                const preset = ProvidersManager.getPreset(id);
                if (!preset) return;
                const pasted = this.setupGuess(s.query).length ? s.query.trim() : (ofKey === true ? s.apiKey : '');
                this._setupTicket++;
                if (s.preset !== id) {
                    const name = id === 'custom' ? '' : Logic.freeName(id, this.hubProviders);
                    Object.assign(s, Logic.empty(), {
                        open: true, preset: id, name: name, displayName: id === 'custom' ? '' : preset.name,
                        endpoint: preset.form === 'local' || id === 'custom' ? 'custom'
                            : ((preset.endpoints || [])[0] || { id: 'official' }).id,
                        customUrl: preset.form === 'local' ? preset.base_url : '',
                        model: preset.default_model || '',
                        location: preset.form === 'google' ? 'us-central1' : '',
                    });
                }
                s.apiKey = pasted || s.apiKey;
                s.query = '';
                s.step = 2;
            },

            /** An answer about what was typed before is not one about this. */
            setupChanged() {
                this._setupTicket++;
                Object.assign(this.setup, {
                    problems: [], same: [], addTo: '', separate: false, found: null, findError: '', test: null,
                    error: '', busy: '', baseUrl: '',
                });
            },

            /** To the key's step. A key or header typed for another host does
             *  not go with it. */
            _toSetupKey() {
                const s = this.setup;
                const host = Logic.hostOf(s.baseUrl);
                if (s.keyHost && s.keyHost !== host && (s.apiKey || s.awsSecret || s.headerRows.length)) {
                    s.cleared = s.keyHost;
                    Object.assign(s, { apiKey: '', awsKeyId: '', awsSecret: '', awsToken: '', headerRows: [] });
                    this.setupKeyChanged();
                } else if (!s.keyHost || s.keyHost === host) {
                    s.cleared = '';
                }
                s.keyHost = host;
                s.step = 3;
            },

            /** The key changed: what it was checked with is gone, not where
             *  it goes. */
            setupKeyChanged() {
                this._setupTicket++;
                if (this.setup.apiKey) this.setup.cleared = '';
                Object.assign(this.setup, { found: null, findError: '', test: null, error: '', busy: '' });
            },

            setupBack() {
                const s = this.setup;
                if (s.step > 1 && s.step < 5) { s.step--; s.error = ''; s.busy = ''; this._setupTicket++; }
            },

            addSetupHeader() { this.setup.headerRows.push({ name: '', value: '' }); },
            removeSetupHeader(i) { this.setup.headerRows.splice(i, 1); this.setupKeyChanged(); },

            // ── what is sent ────────────────────────────────────────

            /** The row as typed: what the check, the list and the save take. */
            setupBody() {
                const s = this.setup;
                const preset = this.setupPreset();
                const headers = {};
                s.headerRows.forEach(row => {
                    const n = String(row.name || '').trim();
                    if (n) headers[n] = String(row.value || '');
                });
                return {
                    name: s.addTo || s.name.trim(), display_name: s.displayName.trim() || s.name.trim(),
                    base_url: s.baseUrl || this.setupAddress(), preset: s.preset === 'custom' ? '' : s.preset,
                    api_key: s.apiKey.trim(), kind: s.kind, url_as_typed: s.urlAsTyped, headers: headers,
                    api_version: preset.form === 'azure' ? s.apiVersion.trim() : '',
                    region: preset.form === 'bedrock' ? s.region.trim() : '',
                    aws_access_key_id: preset.form === 'bedrock' ? s.awsKeyId.trim() : '',
                    aws_secret_access_key: preset.form === 'bedrock' ? s.awsSecret.trim() : '',
                    aws_session_token: preset.form === 'bedrock' ? s.awsToken.trim() : '',
                    google_mode: preset.form === 'google' ? s.googleMode : '',
                    project_id: this.setupIsVertex() ? s.projectId.trim() : '',
                    location: this.setupIsVertex() ? s.location.trim() : '',
                };
            },

            /** Posts *body*; {data, ok, status}, or null for an answer about
             *  something typed before. */
            async _setupPost(url, body, busy) {
                const s = this.setup;
                const ticket = ++this._setupTicket;
                s.busy = busy;
                s.error = '';
                let out = { data: {}, ok: false, status: 0 };
                try {
                    const resp = await fetch(url, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                        body: JSON.stringify(body),
                    });
                    let data = {};
                    try { data = (await resp.json()) || {}; } catch (e) { data = {}; }
                    out = { data: data, ok: resp.ok, status: resp.status };
                } catch (e) {
                    out = { data: { code: 'unreachable_kazma' }, ok: false, status: 0 };
                }
                if (ticket !== this._setupTicket) return null;
                s.busy = '';
                return out;
            },

            setupError(answer) {
                const data = answer.data || {};
                const detail = data.detail && typeof data.detail === 'object' ? data.detail : {};
                const code = String(data.error_code || data.code || detail.code || '');
                if (code === 'unreachable_kazma') return _k('settings.setup.err_kazma', 'Kazma did not answer. Try again.');
                const said = data.error || detail.message || (typeof data.detail === 'string' ? data.detail : '') || ('HTTP ' + answer.status);
                return code ? _k('settings.provider_check.' + code, said) : said;
            },

            // ── step by step ────────────────────────────────────────

            async setupNext() {
                const s = this.setup;
                if (s.busy) return;
                if (s.step === 1 && s.preset) s.step = 2;
                else if (s.step === 2) await this._setupLook();
                else if (s.step === 3) await this._setupFind();
                else if (s.step === 4) await this.saveProviderSetup();
                else if (s.step === 5) await this.finishProviderSetup();
            },

            /** The next button can do its step. */
            canSetupNext() {
                const s = this.setup;
                if (s.busy) return false;
                if (s.step === 1) return !!s.preset;
                if (s.step === 2) return !(s.same.length && !s.addTo && !s.separate);
                if (s.step === 4) return this.canSaveSetup();
                return true;
            },

            setupNextLabel() {
                const s = this.setup;
                if (s.step === 3) {
                    return s.busy === 'find' ? _k('settings.setup.finding', 'Finding…') : _k('settings.setup.find', 'Find the models');
                }
                if (s.step === 4) {
                    if (s.busy === 'save') return _k('settings.saving', 'Saving…');
                    return s.addTo ? _k('settings.setup.add_key_to', 'Add the key to {name}', { name: s.addTo }) : _k('settings.save', 'Save');
                }
                if (s.step === 5) return _k('settings.setup.finish', 'Done');
                return _k('settings.setup.next', 'Next');
            },

            async _setupLook() {
                const s = this.setup;
                if (s.same.length) {
                    if (s.addTo || s.separate) this._toSetupKey();  // asked and answered
                    return;
                }
                const body = this.setupBody();
                ['api_key', 'headers', 'aws_access_key_id', 'aws_secret_access_key', 'aws_session_token']
                    .forEach(f => { delete body[f]; });
                body.endpoint = s.endpoint;
                body.values = s.values;
                body.base_url = s.endpoint === 'custom' || s.endpoint === 'official' ? this.setupAddress() : '';
                const answer = await this._setupPost('/api/providers/setup/look', body, 'look');
                if (!answer) return;
                if (!answer.ok) { s.error = this.setupError(answer); return; }
                s.problems = answer.data.problems || [];
                if (s.problems.length) return;
                s.baseUrl = answer.data.base_url || '';
                s.same = answer.data.same || [];
                if (!s.same.length) this._toSetupKey();
            },

            useSetupRow(row) {
                Object.assign(this.setup, { addTo: row.name, separate: false });
                this._toSetupKey();
            },

            /** A row that takes no key here: one on this network, or one whose
             *  key is the environment's. It is shown instead. */
            setupRowTakesKey(row) {
                return !row.env_key && !(row.local && this.setupPreset().form === 'local');
            },

            showSetupRow(row) {
                this._dropSetup();
                this.hubSelectedProvider = row.name;
            },

            makeSetupSeparate() {
                const s = this.setup;
                s.separate = true;
                s.addTo = '';
                s.name = Logic.freeName(s.name || s.preset, this.hubProviders);
                this._toSetupKey();
            },

            async _setupFind() {
                const s = this.setup;
                if (!s.addTo && !s.name.trim()) {
                    s.error = _k('settings.setup.need_name', 'Give the provider a name.');
                    return;
                }
                if (!s.addTo && Logic.nameTaken(s.name, this.hubProviders)) {
                    s.error = _k('settings.provider_check.name_taken', 'There is already a provider with this name. Give this one another.');
                    return;
                }
                if (s.addTo && !s.apiKey.trim()) {
                    s.error = _k('settings.setup.need_key', 'Paste the key first.');
                    return;
                }
                if (this.setupNeedsKey() && !s.apiKey.trim() && !s.headerRows.some(r => String(r.value || '').trim())) {
                    s.error = _k('settings.setup.need_key', 'Paste the key first.');
                    return;
                }
                const typed = s.models.split(',').map(m => m.trim()).filter(Boolean);
                const answer = await this._setupPost('/api/providers/setup/models',
                    Object.assign(this.setupBody(), { models: typed }), 'find');
                if (!answer) return;
                if (!answer.ok) { s.error = this.setupError(answer); return; }
                const models = answer.data.models || [];
                if (!models.length && answer.data.refused) { s.error = this.setupError(answer); return; }
                s.findError = models.length || !answer.data.error_code ? '' : this.setupError(answer);
                s.found = models;
                const ids = models.map(m => m.id);
                const preferred = this.setupPreset().default_model || '';
                if (ids.indexOf(s.model) === -1) {
                    if (typed.length) s.model = typed[0];
                    else if (ids.length) s.model = ids.indexOf(preferred) !== -1 ? preferred : ids[0];
                }
                s.test = null;
                s.step = 4;
            },

            setupFoundLabel() {
                const n = (this.setup.found || []).length;
                return root.kazmaCount ? root.kazmaCount('settings.setup.models_found', n) : String(n);
            },

            /** The chosen model's listed price, before any message. */
            setupListedPrice() {
                const row = (this.setup.found || []).find(m => m.id === this.setup.model);
                return row ? row.price : null;
            },

            /** One message, sent when the person presses it. */
            async sendSetupTest() {
                const s = this.setup;
                if (!s.model.trim()) { s.error = _k('settings.setup.need_model', 'Choose or type a model to try.'); return; }
                s.test = null;
                const answer = await this._setupPost('/api/providers/check',
                    Object.assign(this.setupBody(), { model: s.model.trim() }), 'test');
                if (!answer) return;
                const data = answer.data || {};
                s.test = {
                    ok: !!data.success, speech: data.error_code === 'speech_provider',
                    ms: data.chat_ms, answer: data.answer || '', thinking: !!data.thinking, price: data.price || this.setupListedPrice(),
                    error: data.success ? '' : this.setupError(answer),
                };
                if (s.test.speech) s.kind = 'speech';
            },

            setupAnsweredLabel() {
                const t = this.setup.test || {};
                return _k('settings.setup.answered_in', 'Answered in {time}', { time: (t.ms || 0) + ' ms' });
            },

            /** What the test found, and what stopped a step: worded here, in
             *  the page's language (setupError). */
            setupTestWords() { return this.setup.test ? this.setup.test.error : ''; },
            setupErrorWords() { return this.setup.error; },

            /** A model's price in the page's words. */
            setupPriceLabel(price) {
                const known = !!price && price.input !== null && price.input !== undefined;
                if (!known) return _k('settings.setup.price_unknown', 'Price: not known. You can set it under Models.');
                if (price.source === 'local') return _k('settings.setup.price_free', 'Free: a server on your network.');
                const vars = { input: price.input, output: price.output, cached: price.cached_input };
                return price.cached_input === null || price.cached_input === undefined
                    ? _k('settings.setup.price_is', 'Price per million tokens: input ${input}, output ${output}', vars)
                    : _k('settings.setup.price_is_cached',
                        'Price per million tokens: input ${input}, cached input ${cached}, output ${output}', vars);
            },

            canSaveSetup() {
                const t = this.setup.test;
                return !!t && (!!t.ok || !!t.speech);
            },

            async saveSetupAnyway() {
                const ok = await root.kazmaConfirm({
                    title: _k('settings.save_untested_title', 'Save without a working test?'),
                    message: _k('settings.save_untested_body',
                        'The test did not get an answer from this provider. Kazma saves it as it is; chat will fail on it until it answers.'),
                    confirmText: _k('settings.save_untested', 'Save anyway'),
                    danger: true,
                });
                if (ok) await this.saveProviderSetup(true);
            },

            async saveProviderSetup(anyway) {
                const s = this.setup;
                if (anyway !== true && !this.canSaveSetup()) return;
                let answer;
                if (s.addTo) {
                    answer = await this._setupPost('/api/providers/' + encodeURIComponent(s.addTo) + '/keys',
                        { api_key: s.apiKey.trim(), base_url: s.baseUrl }, 'save');
                } else {
                    const typed = s.models.split(',').map(m => m.trim()).filter(Boolean);
                    answer = await this._setupPost('/api/providers', Object.assign(this.setupBody(), {
                        create: true, enabled: true, models: typed.length ? typed : (s.model.trim() ? [s.model.trim()] : []),
                    }), 'save');
                }
                if (!answer) return;
                if (!answer.ok || answer.data.error) { s.error = this.setupError(answer); return; }
                const name = s.addTo || s.name.trim();
                let label = _k('settings.setup.saved', '{name} is saved.', { name: s.displayName.trim() || name });
                if (s.addTo) {
                    label = answer.data.added === false
                        ? _k('settings.setup.key_had', '{name} already has this key.', { name: name })
                        : _k('settings.setup.key_added', 'The key is added to {name}.', { name: name });
                } else if (answer.data.switched_off === 'no_key') {
                    label = _k('settings.provider_saved_off', 'The provider is saved, but left off: it has no API key.');
                }
                // Secrets have no more use here.
                Object.assign(s, { saved: name, savedLabel: label, apiKey: '', awsKeyId: '', awsSecret: '', awsToken: '',
                    headerRows: [], step: 5, busy: 'after' });
                this._forgetSetupDraft();
                try {
                    await this._afterSetupSaved(name);
                } catch (e) {
                    console.error('[ProviderSetup] what the provider offers could not be read:', e);
                }
                s.busy = '';
            },

            /** What the saved provider offers: its models, the backups. */
            async _afterSetupSaved(name) {
                const s = this.setup;
                if (s.kind !== 'speech' && s.found && s.found.length && !s.findError) {
                    try {
                        await root.kazmaSave('/api/providers/' + encodeURIComponent(name) + '/discover', { method: 'POST' });
                    } catch (e) {
                        // Saved all the same: the models typed or tried are its list.
                        console.warn('[ProviderSetup] the saved provider did not list its models:', e);
                    }
                }
                await this.loadHubProviders();
                this.hubSelectedProvider = name;
                const row = this.hubProviders.find(p => p.name === name);
                const chosen = (row && row.selected_models) || [];
                const all = this.setupShownList();
                const shown = {};
                all.forEach(m => { shown[m] = chosen.length ? chosen.indexOf(m) !== -1 : (all.length <= 12 || m === s.model); });
                s.shown = shown;
                await this.loadSetupBackups();
            },

            /** Backups of another vendor than the model that will be the
             *  default: the one in use, or this provider's. */
            async loadSetupBackups() {
                const s = this.setup;
                const beside = s.makeDefault ? '?beside=' + encodeURIComponent(s.saved) : '';
                const backup = await this._fetch('/api/settings/failover-backup' + beside);
                s.backups = ((backup && backup.suggestions) || []).filter(b => b.model);
                if (!s.backups.some(b => b.model === s.backup)) s.backup = '';
            },

            setupShownList() {
                const row = this.hubProviders.find(p => p.name === this.setup.saved);
                if (!row || row.kind === 'speech') return [];
                const list = (row.all_models && row.all_models.length ? row.all_models : row.discovered_models) || [];
                return list.map(String);
            },

            setupShownFiltered() {
                const q = this.setup.shownQuery.trim().toLowerCase();
                return this.setupShownList().filter(m => !q || m.toLowerCase().indexOf(q) !== -1).slice(0, 200);
            },

            setupChosenModels() {
                return this.setupShownList().filter(m => !!this.setup.shown[m]);
            },

            async finishProviderSetup() {
                const s = this.setup;
                const name = s.saved;
                s.busy = 'finish';
                s.error = '';
                try {
                    const all = this.setupShownList();
                    const chosen = this.setupChosenModels();
                    if (all.length) {
                        await root.kazmaSave('/api/providers/' + encodeURIComponent(name) + '/select-models', {
                            method: 'POST', body: { models: chosen.length === all.length ? [] : chosen },
                        });
                    }
                    if (s.makeDefault) {
                        await root.kazmaSave('/api/settings/active_model', {
                            method: 'PUT', body: { active_model: name + ':' + s.makeDefault },
                        });
                    }
                    if (s.backup) {
                        const now = await this._fetch('/api/settings/failover-backup');
                        if (!now || !Array.isArray(now.chain)) throw new Error(_k('settings.setup.err_kazma', 'Kazma did not answer. Try again.'));
                        // First of the backups: those already there that can answer stay behind it.
                        const chain = [s.backup].concat(now.chain.filter(b => b.usable && b.model !== s.backup).map(b => b.model));
                        await root.kazmaSave('/api/settings/agent/nonstop', {
                            method: 'PUT', body: { failover_enabled: true, failover_chain: chain.slice(0, 8).join(', ') },
                        });
                    }
                } catch (e) {
                    s.busy = '';
                    s.error = _k('settings.setup.finish_failed', 'Not everything was saved: {error}', { error: e.message });
                    return;
                }
                this._dropSetup();
                showToast(_k('settings.setup.done', 'The provider is ready.'), 'success');
                try {
                    await this.loadHubProviders();
                    await this.loadModelDetails();
                    await this.loadProviderTidy();
                } catch (e) {
                    console.error('[ProviderSetup] the page could not be refreshed:', e);
                }
            },

            // ── tidying rows that repeat each other ─────────────────

            tidy: { open: false, merges: [], unused: [], picked: {}, busy: false, loaded: false, failed: false },

            async loadProviderTidy() {
                const data = await this._fetch('/api/providers/tidy');
                const ok = !!(data && Array.isArray(data.merges));
                this.tidy.failed = !ok;
                this.tidy.merges = ok ? data.merges : [];
                this.tidy.unused = ok ? (data.unused || []) : [];
                this.tidy.loaded = ok;
                const picked = {};
                // A row that is off but holds a key is the owner's to tick: its
                // key would be put back in use.
                this.tidy.merges.forEach(g => g.merge.forEach(m => { if (m.enabled || !m.keys) picked[m.name] = true; }));
                this.tidy.picked = picked;
            },

            /** What merging changes, said before it is done. */
            tidyMergeNote(g) {
                return _k('settings.setup.tidy_note',
                    'Work that named a merged row uses {name} from now on: its own key first, the merged ones when that fails.',
                    { name: g.display_name });
            },

            tidyCount() {
                return this.tidy.merges.reduce((n, g) => n + g.merge.length, 0) + this.tidy.unused.length;
            },

            tidyLabel() {
                return root.kazmaCount ? root.kazmaCount('settings.setup.tidy_count', this.tidyCount()) : '';
            },

            tidyWhy(code) {
                return _k('settings.setup.tidy_why_' + code, code);
            },

            tidyKeys(row) {
                return root.kazmaCount ? root.kazmaCount('settings.setup.tidy_keys', row.keys || 0) : '';
            },

            tidyPickedCount() {
                return Object.keys(this.tidy.picked).filter(n => this.tidy.picked[n]).length;
            },

            async runProviderTidy() {
                const merge = [];
                this.tidy.merges.forEach(g => g.merge.forEach(m => { if (this.tidy.picked[m.name]) merge.push(m.name); }));
                const remove = this.tidy.unused.map(u => u.name).filter(n => !!this.tidy.picked[n]);
                if (!merge.length && !remove.length) return;
                if (!(await root.kazmaConfirm({
                    title: _k('settings.setup.tidy_title', 'Tidy the provider list'),
                    message: _k('settings.setup.tidy_confirm',
                        'Merged: {merge}. Removed: {remove}. Every key moves to the row that stays. This cannot be undone.',
                        { merge: merge.join(', ') || '—', remove: remove.join(', ') || '—' })
                        + this.tidy.merges.filter(g => g.merge.some(m => !!this.tidy.picked[m.name]))
                            .map(g => '\n' + this.tidyMergeNote(g)).join(''),
                    confirmText: _k('settings.setup.tidy_do', 'Tidy'),
                    danger: true,
                }))) return;
                this.tidy.busy = true;
                let saved = false;
                try {
                    await root.kazmaSave('/api/providers/tidy', { method: 'POST', body: { merge: merge, remove: remove } });
                    saved = true;
                } catch (e) {
                    const code = e && e.body && e.body.detail && e.body.detail.code;
                    showToast(_k('settings.setup.tidy_failed', 'Nothing was changed: {error}',
                        { error: code ? this.tidyWhy(code) : e.message }), 'error');
                }
                this.tidy.busy = false;
                if (saved) {
                    showToast(_k('settings.setup.tidy_done', 'The list is tidy.'), 'success');
                    this.tidy.open = false;
                }
                await this.loadHubProviders();
                await this.loadProviderTidy();
            },
        };
    };
})(typeof window !== "undefined" ? window : globalThis);
