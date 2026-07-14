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
             * @param {Object} opts - { title, body, size, actions }
             */
            show(opts = {}) {
                this.title = opts.title || '';
                this.body = opts.body || '';
                this.size = opts.size || 'md';
                this.actions = opts.actions || [];
                this.open = true;
            },

            close() {
                this.open = false;
                // Reset after transition
                setTimeout(() => {
                    this.title = '';
                    this.body = '';
                    this.actions = [];
                }, 200);
            },

            /**
             * Quick confirm dialog.
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
        });

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
