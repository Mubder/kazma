
// ── Font size persistence ─────────────────────────────────────────
(function() {
  var saved = localStorage.getItem('kazma-font-size') || 'md';
  document.body.classList.add('font-' + saved);
  window.setKazmaFont = function(size) {
    document.body.classList.remove('font-sm', 'font-md', 'font-lg');
    document.body.classList.add('font-' + size);
    localStorage.setItem('kazma-font-size', size);
  };
})();
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
            this.show({
                title,
                body: `<p style="color: var(--text-secondary); line-height: 1.6;">${message}</p>`,
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
});

// ── 5. Root App Component ──────────────────────────────────────────
function kazmaApp() {
    return {
        theme: 'dark',
        lang: 'ar',
        sidebarCollapsed: false,

        init() {
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
            document.cookie = 'kazma-lang=' + newLang + ';path=/;max-age=31536000;samesite=lax';
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
        // Active model name, fetched from /api/provider/active on init.
        // Falls back to the server-rendered (config.default_model) value
        // or 'gpt-4o-mini' when the fetch fails or returns an empty model.
        activeModel: '',

        init() {
            this.fetchActiveModel();
        },

        async fetchActiveModel() {
            try {
                const res = await fetch('/api/provider/active', {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'same-origin',
                });
                if (!res.ok) return;
                const data = await res.json();
                if (data && data.model) {
                    this.activeModel = data.model;
                }
            } catch (err) {
                // Network or parse error — keep the fallback display
                console.warn('[sidebar] Could not fetch active model:', err);
            }
        },

        toggleSidebar() {
            // Delegates to root app via Alpine
            const appEl = document.querySelector('[x-data*="kazmaApp"]');
            if (appEl && appEl.__x) {
                appEl.__x.$data.sidebarCollapsed = !appEl.__x.$data.sidebarCollapsed;
                localStorage.setItem('kazma-sidebar-collapsed', appEl.__x.$data.sidebarCollapsed);
            }
            // Fallback: dispatch event
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
