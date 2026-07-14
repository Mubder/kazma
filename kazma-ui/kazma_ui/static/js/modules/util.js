// ── Kazma modules/util.js ──
// Shared API + formatting + UI helpers. Re-exported onto `window` by the
// app.js entry so classic page scripts (mcp.js, settings.js, etc.) keep
// calling them as globals.

export const KazmaAPI = {
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

// ── Toast Helper (global convenience) ───────────────────────────
export function showToast(message, type = 'info', duration) {
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

// ── Modal Helper (global convenience) ───────────────────────────
export function showModal(opts) {
    if (window.Alpine && Alpine.store('modal')) {
        Alpine.store('modal').show(opts);
    }
}

export function closeModal() {
    if (window.Alpine && Alpine.store('modal')) {
        Alpine.store('modal').close();
    }
}

// ── Format Utilities ───────────────────────────────────────────
export const KazmaUtils = {
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
