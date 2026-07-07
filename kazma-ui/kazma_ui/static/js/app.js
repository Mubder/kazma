
/**
 * ═══════════════════════════════════════════════════════════════════
 * Kazma App.js — Core Alpine.js stores, utilities, and keyboard shortcuts
 * ═══════════════════════════════════════════════════════════════════
 */

// ── 1. Toast Store ─────────────────────────────────────────────────
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
            var escapedMsg = String(message).replace(/[&<>"']/g, function(c) { return entityMap[c]; });
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

// ── 5. Root App Component ──────────────────────────────────────────
function kazmaApp() {
    return {
        theme: 'dark',
        lang: 'ar',
        sidebarCollapsed: false,
        fontSize: 14,

        init() {
            // Restore font size from localStorage (synchronous). The Alpine
            // $persist plugin is not bundled, so persistence is handled here.
            const storedSize = localStorage.getItem('kazma-font-size');
            if (storedSize) this.fontSize = Number(storedSize);

            // Persist font size to localStorage on every change.
            this.$watch('fontSize', (v) => localStorage.setItem('kazma-font-size', v));

            // Sync from backend (authoritative — overrides the local cache).
            fetch('/api/settings/appearance')
                .then(r => r.json())
                .then(d => { if (d && d.font_size) this.fontSize = d.font_size; })
                .catch(() => {});

            // Restore theme from localStorage
            const saved = localStorage.getItem('kazma-theme');
            if (saved) {
                this.theme = saved;
            } else {
                // Detect system preference
                this.theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
            }
            this._applyTheme();

            // Read current language from <html lang="..."> attribute (set server-side)
            this.lang = document.documentElement.lang || 'ar';

            // Restore sidebar state
            this.sidebarCollapsed = localStorage.getItem('kazma-sidebar-collapsed') === 'true';

            // Global keyboard shortcuts
            document.addEventListener('keydown', (e) => this._handleKeyboard(e));
        },

        toggleTheme() {
            this.theme = this.theme === 'dark' ? 'light' : 'dark';
            localStorage.setItem('kazma-theme', this.theme);
            this._applyTheme();
        },

        toggleLanguage() {
            // Switch between 'ar' and 'en', persist, then reload for SSR pickup
            const newLang = this.lang === 'ar' ? 'en' : 'ar';
            this.lang = newLang;
            // Store in localStorage
            localStorage.setItem('kazma-lang', newLang);
            // Store in cookie so server-side middleware reads it on next request
            var secureFlag = window.location.protocol === 'https:' ? ';secure' : '';
            document.cookie = 'kazma-lang=' + newLang + ';path=/;max-age=31536000;samesite=lax' + secureFlag;
            // Reload so server-side rendering picks up the new language
            window.location.reload();
        },

        toggleSidebar() {
            this.sidebarCollapsed = !this.sidebarCollapsed;
            localStorage.setItem('kazma-sidebar-collapsed', this.sidebarCollapsed);
        },

        _applyTheme() {
            document.documentElement.setAttribute('data-theme', this.theme);
        },

        _handleKeyboard(e) {
            const meta = e.metaKey || e.ctrlKey;

            // Ctrl+B — Toggle sidebar
            if (meta && e.key === 'b') {
                e.preventDefault();
                this.toggleSidebar();
            }

            // Ctrl+K — Search
            if (meta && e.key === 'k') {
                e.preventDefault();
                Alpine.store('search').toggle();
            }

            // Ctrl+N — New chat
            if (meta && e.key === 'n') {
                e.preventDefault();
                window.location.href = '/chat';
            }

            // Escape — Close modals/search
            if (e.key === 'Escape') {
                if (Alpine.store('search').open) {
                    Alpine.store('search').close();
                } else if (Alpine.store('modal').open) {
                    Alpine.store('modal').close();
                }
            }

            // Ctrl+1-6 — Navigate sections
            if (meta && e.key >= '1' && e.key <= '6') {
                e.preventDefault();
                const routes = ['/', '/chat', '/dashboard', '/skills', '/mcp', '/swarm'];
                const idx = parseInt(e.key) - 1;
                if (routes[idx]) window.location.href = routes[idx];
            }

            // Ctrl+, — Settings
            if (meta && e.key === ',') {
                e.preventDefault();
                window.location.href = '/settings';
            }
        },
    };
}

// ── 6. Sidebar Component ───────────────────────────────────────────
function sidebarComponent() {
    return {
        activeModel: '',
        modelOptions: [],

        async init() {
            // Fetch options first so the <select> has the <option>s
            // before we set activeModel, then fetch+set the active model.
            await this._fetchModelOptions();
            await this._fetchActiveModel();
            // Force Alpine to re-sync the <select> value now that
            // both options and activeModel are populated.
            this.$nextTick(() => {
                const sel = this.$el.querySelector('.sidebar-model-dropdown');
                if (sel && this.activeModel) sel.value = this.activeModel;
            });
            // Listen for model changes from chat or other components
            document.addEventListener('model-changed', (e) => {
                const model = e.detail || (e.target && e.target.value);
                if (model) {
                    this.activeModel = model;
                    this.$nextTick(() => {
                        const sel = this.$el.querySelector('.sidebar-model-dropdown');
                        if (sel) sel.value = model;
                    });
                }
            });
        },

        async _fetchActiveModel() {
            try {
                const res = await fetch('/api/provider/active', {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'same-origin',
                });
                if (!res.ok) return;
                const data = await res.json();
                if (data && data.model) {
                    this.activeModel = data.model;
                    const store = Alpine.store('settings');
                    if (store) store.appearance.active_chat_model = data.model;
                }
            } catch (e) { /* keep default */ }
        },

        async _fetchModelOptions() {
            try {
                const res = await fetch('/api/providers');
                if (!res.ok) return;
                const providers = await res.json();
                if (!Array.isArray(providers)) return;
                this.modelOptions = providers
                    .filter(p => p.enabled)
                    .map(p => {
                        // Use visible_models (user-selected subset or all if none selected)
                        const visible = p.visible_models || p.selected_models || [];
                        const disc = p.discovered_models || [];
                        const manual = p.models || [];
                        // Prefer visible_models; fall back to discovered+manual
                        let models;
                        if (visible && visible.length) {
                            models = visible;
                        } else {
                            models = [...new Set([...disc, ...manual])].filter(Boolean);
                        }
                        return { label: p.display_name || p.name, models };
                    }).filter(g => g.models.length > 0);
            } catch (e) { /* keep empty */ }
        },

        async onModelChange(event) {
            const model = event.target ? event.target.value : (event.detail || '');
            if (!model) return;
            this.activeModel = model;
            const store = Alpine.store('settings');
            if (store) store.appearance.active_chat_model = model;
            // Notify other components immediately (before the async PUT)
            document.dispatchEvent(new CustomEvent('model-changed', { detail: model }));
            try {
                const res = await fetch('/api/settings/active_model', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ active_model: model }),
                });
                const data = await res.json();
                if (data && data.active_model) {
                    this.activeModel = data.active_model;
                }
            } catch (e) {
                console.warn('[sidebar] Failed to sync model:', e);
            }
        },

        toggleSidebar() {
            const appEl = document.querySelector('[x-data*="kazmaApp"]');
            if (appEl && window.Alpine) {
                const data = Alpine.$data(appEl);
                if (data) {
                    data.sidebarCollapsed = !data.sidebarCollapsed;
                    localStorage.setItem('kazma-sidebar-collapsed', data.sidebarCollapsed);
                }
            }
            document.dispatchEvent(new CustomEvent('kazma:toggle-sidebar'));
        },
    };
}

// ── 7. API Utilities ───────────────────────────────────────────────
const KazmaAPI = {
    /**
     * Fetch wrapper with error handling and JSON parsing.
     * @param {string} url
     * @param {Object} opts - fetch options
     * @returns {Promise<any>}
     */
    async fetch(url, opts = {}) {
        const defaults = {
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
        };
        const config = { ...defaults, ...opts };
        if (opts.body && typeof opts.body === 'object') {
            config.body = JSON.stringify(opts.body);
        }

        try {
            const res = await fetch(url, config);
            if (!res.ok) {
                const text = await res.text().catch(() => res.statusText);
                throw new Error(`HTTP ${res.status}: ${text}`);
            }
            const contentType = res.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                return await res.json();
            }
            return await res.text();
        } catch (err) {
            console.error(`[KazmaAPI] ${url}:`, err);
            throw err;
        }
    },

    get(url) { return this.fetch(url); },
    post(url, body) { return this.fetch(url, { method: 'POST', body }); },
    put(url, body) { return this.fetch(url, { method: 'PUT', body }); },
    del(url) { return this.fetch(url, { method: 'DELETE' }); },
};

// ── 8. Toast Helper (global convenience) ───────────────────────────
function showToast(message, type = 'info', duration) {
    // Wait for Alpine to initialize
    if (window.Alpine && Alpine.store('toast')) {
        Alpine.store('toast').add(message, type, duration);
    } else {
        // Fallback: vanilla toast
        const container = document.querySelector('.toast-container');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        toast.style.cssText = 'position:relative;overflow:hidden;';
        container.appendChild(toast);
        setTimeout(() => toast.remove(), duration || 5000);
    }
}

// ── 9. Modal Helper (global convenience) ───────────────────────────
function showModal(opts) {
    if (window.Alpine && Alpine.store('modal')) {
        Alpine.store('modal').show(opts);
    }
}

function closeModal() {
    if (window.Alpine && Alpine.store('modal')) {
        Alpine.store('modal').close();
    }
}

// ── 10. Format Utilities ───────────────────────────────────────────
const KazmaUtils = {
    formatBytes(bytes, decimals = 1) {
        if (!bytes) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
    },

    formatDuration(ms) {
        if (!ms) return '0s';
        if (ms < 1000) return ms + 'ms';
        if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
        if (ms < 3600000) return Math.floor(ms / 60000) + 'm ' + Math.floor((ms % 60000) / 1000) + 's';
        return Math.floor(ms / 3600000) + 'h ' + Math.floor((ms % 3600000) / 60000) + 'm';
    },

    formatTime(date) {
        if (!date) return '';
        const d = new Date(date);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },

    timeAgo(date) {
        if (!date) return '';
        const seconds = Math.floor((new Date() - new Date(date)) / 1000);
        if (seconds < 60) return 'just now';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
        if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
        return Math.floor(seconds / 86400) + 'd ago';
    },

    copyToClipboard(text) {
        navigator.clipboard.writeText(text).then(() => {
            showToast('Copied to clipboard', 'success', 2000);
        }).catch(() => {
            showToast('Failed to copy', 'error');
        });
    },
};

// ── 11. Sidebar Model Selector Component ───────────────────────────
function sidebarModel() {
    return {
        selectedModel: '',
        providers: [],
        async init() {
            try {
                const resp = await fetch('/api/providers');
                const data = await resp.json();
                this.providers = Array.isArray(data) ? data : (data.providers || []);
            } catch(e) { console.error('Failed to load providers', e); }
            // Load saved model
            try {
                const r = await fetch('/api/settings');
                const s = await r.json();
                if (s && s.model && s.model.default) this.selectedModel = s.model.default;
            } catch(e) {}
        },
        async saveModel() {
            if (!this.selectedModel) return;
            try {
                await fetch('/api/settings/active_model', {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ model: this.selectedModel })
                });
                window.dispatchEvent(new CustomEvent('model-changed', { detail: { model: this.selectedModel } }));
            } catch(e) { console.error('Failed to save model', e); }
        }
    };
}

// ── 12. Global System Alerts Banner Component ─────────────────────
function systemAlertsBanner() {
    return {
        activeAlert: null,
        installing: false,
        pollInterval: null,
        dismissedAlerts: new Set(),

        init() {
            this.fetchAlerts();
            // Poll every 10 seconds
            this.pollInterval = setInterval(() => {
                this.fetchAlerts();
            }, 10000);
        },

        destroy() {
            if (this.pollInterval) {
                clearInterval(this.pollInterval);
            }
        },

        async fetchAlerts() {
            try {
                const res = await fetch('/api/alerts/recent');
                if (!res.ok) return;
                const alerts = await res.json();
                if (Array.isArray(alerts) && alerts.length > 0) {
                    // Find the most recent active alert that has not been dismissed
                    const validAlerts = alerts.filter(a => !this.dismissedAlerts.has(a.id));
                    if (validAlerts.length > 0) {
                        // Sort by timestamp desc to show the newest alert first
                        validAlerts.sort((a, b) => b.timestamp - a.timestamp);
                        this.activeAlert = validAlerts[0];
                    } else {
                        this.activeAlert = null;
                    }
                } else {
                    this.activeAlert = null;
                }
            } catch (err) {
                console.error('[SystemAlertsBanner] Failed to fetch alerts:', err);
            }
        },

        dismissAlert() {
            if (this.activeAlert) {
                this.dismissedAlerts.add(this.activeAlert.id);
                this.activeAlert = null;
            }
        },

        async installMl() {
            if (this.installing) return;
            this.installing = true;
            try {
                const res = await fetch('/api/system/install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ package_name: 'sentence-transformers' }),
                });
                if (res.ok) {
                    showToast('Installation of sentence-transformers started asynchronously', 'success');
                    // Poll again immediately
                    setTimeout(() => this.fetchAlerts(), 3000);
                } else {
                    showToast('Failed to start installation', 'error');
                }
            } catch (err) {
                console.error('[SystemAlertsBanner] Install failed:', err);
                showToast('Failed to start installation', 'error');
            } finally {
                this.installing = false;
            }
        }
    };
}

