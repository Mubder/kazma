// ── Kazma modules/stores.js ──
// Alpine.js global stores. Registered on alpine:init so they exist before
// any component initializes. Imported by the app.js entry module.

import { KAZMA_SEARCH_PAGES } from './search_pages.js';

export function registerStores() {
    document.addEventListener('alpine:init', () => {
        Alpine.store('toast', {
            items: [],
            _counter: 0,

            /**
             * Show a toast notification.
             * @param {string} message - Toast message
             * @param {string} type - 'success' | 'error' | 'warning' | 'info'
             * @param {number} duration - Auto-dismiss in ms (default 5000)
             */
            add(message, type = 'info', duration = 5000) {
                const id = ++this._counter;
                this.items.push({ id, message, type, duration });
                if (duration > 0) {
                    setTimeout(() => this.dismiss(id), duration);
                }
                return id;
            },

            success(message, duration) { return this.add(message, 'success', duration); },
            error(message, duration) { return this.add(message, 'error', duration || 8000); },
            warning(message, duration) { return this.add(message, 'warning', duration || 6000); },
            info(message, duration) { return this.add(message, 'info', duration); },

            dismiss(id) {
                this.items = this.items.filter(t => t.id !== id);
            },

            clear() {
                this.items = [];
            },
        });

        // ── 2. Modal Store ─────────────────────────────────────────────
        Alpine.store('modal', {
            open: false,
            title: '',
            body: '',
            size: 'md',
            actions: [],
            // Input support (for kazmaPrompt). When `input` is truthy, the
            // modal renders a text field bound to `inputValue`.
            input: null,        // null = no input; a string = placeholder text
            inputValue: '',
            inputType: 'text',

            /**
             * Open a modal.
             * @param {Object} opts - { title, body, size, actions, onClose, input, inputValue, inputType }
             *   onClose is invoked when the modal is dismissed via overlay
             *   click or Escape (i.e. without an explicit action button).
             */
            show(opts = {}) {
                this.title = opts.title || '';
                this.body = opts.body || '';
                this.size = opts.size || 'md';
                this.actions = opts.actions || [];
                this.input = opts.input !== undefined ? opts.input : null;
                this.inputValue = opts.inputValue !== undefined ? opts.inputValue : '';
                this.inputType = opts.inputType || 'text';
                this._onClose = opts.onClose || null;
                this.open = true;
            },

            close() {
                this.open = false;
                if (this._onClose) {
                    const cb = this._onClose;
                    this._onClose = null;
                    cb();
                }
                // Reset after transition
                setTimeout(() => {
                    this.title = '';
                    this.body = '';
                    this.actions = [];
                    this.input = null;
                    this.inputValue = '';
                    this.inputType = 'text';
                }, 200);
            },

            /**
             * Quick confirm dialog (callback style).
             * @param {string} title
             * @param {string} message
             * @param {Function} onConfirm
             */
            confirm(title, message, onConfirm) {
                // Escape message to prevent XSS via HTML injection
                var entityMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
                var escapedMsg = String(message).replace(/[&<>"']/g, function (c) { return entityMap[c]; });
                this.show({
                    title,
                    body: `<p style="color: var(--text-secondary); line-height: 1.6;">${escapedMsg}</p>`,
                    size: 'sm',
                    actions: [
                        { label: 'Cancel', variant: 'btn-secondary' },
                        { label: 'Confirm', variant: 'btn-primary', handler: onConfirm },
                    ],
                });
            },

            /**
             * Promise-based confirm dialog — replaces native window.confirm.
             * Resolves true on Confirm, false on Cancel / overlay / Escape.
             * @param {Object} opts - { title, message, confirmText, cancelText, danger }
             * @returns {Promise<boolean>}
             */
            confirmAsync(opts = {}) {
                const title = opts.title || 'Confirm';
                const message = opts.message || '';
                const confirmText = opts.confirmText || 'Confirm';
                const cancelText = opts.cancelText || 'Cancel';
                // Explicit false disables danger styling; default remains true.
                const danger = opts.danger !== false;
                const entityMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
                const escapedMsg = String(message).replace(/[&<>"']/g, function (c) { return entityMap[c]; });
                const self = this;
                return new Promise(function (resolve) {
                    let settled = false;
                    const settle = function (val) {
                        if (settled) return;
                        settled = true;
                        // Clear onClose so modal.close() after an action button
                        // does not overwrite settle(true) with settle(false).
                        self._onClose = null;
                        resolve(val);
                    };
                    self.show({
                        title: title,
                        body: `<p class="confirm-message">${escapedMsg}</p>`,
                        size: 'sm',
                        onClose: function () { settle(false); },
                        actions: [
                            {
                                label: cancelText,
                                variant: 'btn-secondary',
                                close: true,
                                handler: function () { settle(false); },
                            },
                            {
                                label: confirmText,
                                variant: danger ? 'btn-danger' : 'btn-primary',
                                close: true,
                                handler: function () { settle(true); },
                            },
                        ],
                    });
                });
            },

            /**
             * Promise-based prompt dialog — replaces native window.prompt.
             * Resolves the entered string on confirm, null on Cancel /
             * overlay / Escape (matching native semantics).
             * @param {Object} opts - { title, message, label, placeholder, defaultValue, confirmText, cancelText }
             *   The input ALWAYS renders; placeholder falls back to label,
             *   then message.
             * @returns {Promise<string|null>}
             */
            promptAsync(opts = {}) {
                const title = opts.title || 'Input';
                const message = opts.message || '';
                const confirmText = opts.confirmText || 'OK';
                const cancelText = opts.cancelText || 'Cancel';
                const entityMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
                const escapedMsg = String(message).replace(/[&<>"']/g, function (c) { return entityMap[c]; });
                const self = this;
                return new Promise(function (resolve) {
                    let settled = false;
                    const settle = function (val) {
                        if (settled) return;
                        settled = true;
                        resolve(val);
                    };
                    self.show({
                        title: title,
                        body: escapedMsg ? `<p class="confirm-message">${escapedMsg}</p>` : '',
                        size: 'sm',
                        // A PROMPT always renders its input. The old
                        // `opts.placeholder || ''` left `input` falsy for
                        // callers that pass label/message/defaultValue only
                        // (session Rename) — the dialog opened with NO
                        // textbox at all (reproduced 2026-08-26).
                        input: opts.placeholder || opts.label || opts.message || ' ',
                        inputValue: opts.defaultValue || '',
                        onClose: function () { settle(null); },
                        actions: [
                            { label: cancelText, variant: 'btn-secondary', close: true, handler: function () { settle(null); } },
                            { label: confirmText, variant: 'btn-primary', close: true, handler: function () { settle(self.inputValue); } },
                        ],
                    });
                    // Autofocus the input once the modal is in the DOM.
                    setTimeout(function () {
                        const inp = document.querySelector('.modal-input');
                        if (inp) { inp.focus(); inp.select(); }
                    }, 60);
                });
            },
        });

        // Global promise-based confirm — drop-in replacement for window.confirm().
        // Usage: if (!(await window.kazmaConfirm({ message: 'Delete?', danger: true }))) return;
        window.kazmaConfirm = function (opts) {
            // Allow a plain string message for ergonomics.
            if (typeof opts === 'string') opts = { message: opts };
            if (window.Alpine && Alpine.store('modal')) {
                return Alpine.store('modal').confirmAsync(opts || {});
            }
            // Fallback if Alpine hasn't booted yet (shouldn't happen on user action).
            return Promise.resolve(window.confirm(opts && opts.message ? opts.message : ''));
        };

        // Global promise-based alert — drop-in replacement for window.alert().
        // Resolves when the user dismisses the styled modal (OK / overlay / Escape).
        // Usage: await window.kazmaAlert({ title: 'Error', message: errMsg, variant: 'btn-danger' });
        window.kazmaAlert = function (opts) {
            if (typeof opts === 'string') opts = { message: opts };
            opts = opts || {};
            const entityMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
            const escapedMsg = String(opts.message || '').replace(/[&<>"']/g, function (c) { return entityMap[c]; });
            if (window.Alpine && Alpine.store('modal')) {
                const self = Alpine.store('modal');
                return new Promise(function (resolve) {
                    let settled = false;
                    const settle = function () {
                        if (settled) return;
                        settled = true;
                        resolve();
                    };
                    self.show({
                        title: opts.title || 'Notice',
                        body: `<p class="confirm-message">${escapedMsg}</p>`,
                        size: opts.size || 'sm',
                        onClose: settle,
                        actions: [
                            { label: opts.okText || 'OK', variant: opts.variant || 'btn-primary', close: true, handler: settle },
                        ],
                    });
                });
            }
            // Fallback if Alpine hasn't booted yet.
            return Promise.resolve(window.alert(opts.message || ''));
        };

        // Global promise-based prompt — drop-in replacement for window.prompt().
        // Resolves the entered string on OK, null on Cancel / overlay / Escape.
        // Usage: const name = await window.kazmaPrompt({ title: 'New file', message: 'Path:', defaultValue: 'x.py' });
        window.kazmaPrompt = function (opts) {
            if (typeof opts === 'string') opts = { message: opts };
            if (window.Alpine && Alpine.store('modal')) {
                return Alpine.store('modal').promptAsync(opts || {});
            }
            // Fallback if Alpine hasn't booted yet.
            return Promise.resolve(window.prompt(opts && opts.message ? opts.message : ''));
        };

        // ═══════════════════════════════════════════════════════════════
        // GLOBAL OVERRIDES — kill native browser dialogs everywhere.
        //
        // Every window.confirm() / window.alert() / window.prompt() call
        // across the entire codebase (current AND future) is intercepted
        // and routed through the styled Kazma modal. No more unstyled
        // browser dialogs.
        //
        // IMPORTANT: these overrides are ASYNC (return Promises). Code that
        // uses them synchronously must be converted to async/await. The
        // original native functions are preserved as _nativeConfirm etc.
        // for the Alpine-not-ready fallback path.
        // ═══════════════════════════════════════════════════════════════
        const _nativeConfirm = window.confirm.bind(window);
        const _nativeAlert = window.alert.bind(window);
        const _nativePrompt = window.prompt.bind(window);

        window.confirm = function (message) {
            // If a string is passed, wrap it. If kazmaConfirm is available,
            // delegate to the styled modal.
            return window.kazmaConfirm(
                typeof message === 'string' ? { message: message, danger: true } : message
            ).catch(function () { return false; });
        };

        window.alert = function (message) {
            return window.kazmaAlert(
                typeof message === 'string' ? { message: message } : message
            ).catch(function () {});
        };

        window.prompt = function (message, defaultValue) {
            var opts = typeof message === 'string'
                ? { message: message, defaultValue: defaultValue || '' }
                : message;
            return window.kazmaPrompt(opts).catch(function () { return null; });
        };

        // ── 3. Search Store ────────────────────────────────────────────
        // Global overlay search (Ctrl+K / header icon). Searches static
        // pages + chat sessions, with ↑/↓/Enter keyboard navigation.
        // The overlay DOM lives in base.html; this store owns the state.
        Alpine.store('search', {
            open: false,
            query: '',
            results: [],       // flat list of {kind:'session'|'page', title, subtitle, href}
            loading: false,
            searched: false,
            hovered: 0,

            _pages: KAZMA_SEARCH_PAGES,

            toggle() {
                this.open = !this.open;
                if (this.open) this._focusInput();
            },

            close() {
                this.open = false;
                this.query = '';
                this.results = [];
                this.searched = false;
                this.loading = false;
                this.hovered = 0;
            },

            next() {
                if (this.results.length) this.hovered = (this.hovered + 1) % this.results.length;
            },

            prev() {
                if (this.results.length) {
                    this.hovered = (this.hovered - 1 + this.results.length) % this.results.length;
                }
            },

            go() {
                var r = this.results[this.hovered] || this.results[0];
                if (!r) return;
                this.close();
                window.location.href = r.href;
            },

            _focusInput() {
                var self = this;
                setTimeout(function () {
                    var input = document.querySelector('.search-overlay input[type="text"]');
                    if (input) { input.focus(); input.select(); }
                    if (self.query) self.doSearch();
                }, 50);
            },

            async doSearch() {
                var q = (this.query || '').trim().toLowerCase();
                this.searched = true;
                this.hovered = 0;
                if (!q) { this.results = []; this.loading = false; return; }
                this.loading = true;
                var matches = [];

                this._pages.forEach(function (p) {
                    if (p.title.toLowerCase().includes(q) || p.href.toLowerCase().includes(q)) {
                        matches.push({ kind: 'page', title: p.title, subtitle: p.href, href: p.href });
                    }
                });

                try {
                    var res = await fetch('/api/chat/sessions', {
                        credentials: 'same-origin',
                        headers: { 'Accept': 'application/json' },
                    });
                    if (res.ok) {
                        var list = await res.json();
                        (Array.isArray(list) ? list : []).forEach(function (s) {
                            var title = s.title || s.session_id || '';
                            var sid = s.session_id || '';
                            if (title.toLowerCase().includes(q) || sid.toLowerCase().includes(q)) {
                                matches.push({
                                    kind: 'session',
                                    title: title,
                                    subtitle: (s.platform || 'web') + ' \u00B7 ' + (s.message_count || 0) + ' msgs',
                                    href: '/chat?s=' + encodeURIComponent(sid),
                                });
                            }
                        });
                    }
                } catch (e) { /* degrades to pages-only on failure */ }

                this.results = matches;
                this.loading = false;
            },
        });

        // ── 4. Notifications Store ─────────────────────────────────────
        Alpine.store('notifications', {
            open: false,
            count: 0,
            items: [],
            error: '',

            init() {
                this.refresh();
                try {
                    setInterval(() => this.refresh(), 15000);
                } catch (e) { /* no window timers in some tests */ }
            },

            async refresh() {
                try {
                    const res = await fetch('/api/alerts/recent', {
                        headers: { 'Accept': 'application/json' },
                        credentials: 'same-origin',
                    });
                    if (!res.ok) {
                        this.error = 'alerts unavailable';
                        return;
                    }
                    const alerts = await res.json();
                    this.items = Array.isArray(alerts) ? alerts : [];
                    this.count = this.items.length;
                    this.error = '';
                } catch (e) {
                    this.error = 'alerts unavailable';
                }
            },
        });

        // ── 5. Settings Store ──────────────────────────────────────────
        Alpine.store('settings', {
            appearance: {
                active_chat_model: '',
            },
            _modelOptions: [],  // [{ label, models: [] }] provider groups

            init() {
                // Hydrate active model from backend
                this._hydrateActiveModel();
                this._loadModelOptions();
            },

            async _hydrateActiveModel() {
                try {
                    const res = await fetch('/api/provider/active', {
                        headers: { 'Accept': 'application/json' },
                        credentials: 'same-origin',
                    });
                    if (!res.ok) return;
                    const data = await res.json();
                    if (data && data.model) {
                        this.appearance.active_chat_model = data.model;
                    }
                } catch (e) { /* keep default */ }
            },

            async _loadModelOptions() {
                try {
                    const res = await fetch('/api/providers');
                    if (!res.ok) return;
                    const providers = await res.json();
                    if (!Array.isArray(providers)) return;
                    this._modelOptions = providers
                        .filter(p => p.enabled)
                        .map(p => {
                            const disc = p.discovered_models || [];
                            const manual = p.models || [];
                            const models = [...new Set([...disc, ...manual])].filter(Boolean);
                            return { label: p.display_name || p.name, models };
                        }).filter(g => g.models.length > 0);
                } catch (e) { /* keep empty */ }
            },
        });
    });
}
