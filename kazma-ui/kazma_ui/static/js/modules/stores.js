// ── Kazma modules/stores.js ──
// Alpine.js global stores. Registered on alpine:init so they exist before
// any component initializes. Imported by the app.js entry module.

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

            /**
             * Open a modal.
             * @param {Object} opts - { title, body, size, actions, onClose }
             *   onClose is invoked when the modal is dismissed via overlay
             *   click or Escape (i.e. without an explicit action button).
             */
            show(opts = {}) {
                this.title = opts.title || '';
                this.body = opts.body || '';
                this.size = opts.size || 'md';
                this.actions = opts.actions || [];
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
                const danger = opts.danger !== false;
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
                        body: `<p class="confirm-message">${escapedMsg}</p>`,
                        size: 'sm',
                        onClose: function () { settle(false); },
                        actions: [
                            { label: cancelText, variant: 'btn-secondary', close: true, handler: function () { settle(false); } },
                            { label: confirmText, variant: danger ? 'btn-danger' : 'btn-primary', close: true, handler: function () { settle(true); } },
                        ],
                    });
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

        // ── 3. Search Store ────────────────────────────────────────────
        Alpine.store('search', {
            open: false,
            query: '',
            results: [],
            loading: false,

            toggle() { this.open = !this.open; },
            close() { this.open = false; this.query = ''; this.results = []; },
        });

        // ── 4. Notifications Store ─────────────────────────────────────
        Alpine.store('notifications', {
            open: false,
            count: 0,
            items: [],
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
