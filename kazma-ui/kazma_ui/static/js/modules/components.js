// ── Kazma modules/components.js ──
// Root app + sidebar + system-alerts Alpine components.
// Re-exported onto `window` by the app.js entry so x-data and inline
// handlers keep working after the ESM migration.

export function kazmaApp() {
    return {
        theme: 'dark',
        lang: 'ar',
        sidebarCollapsed: false,
        mobileNavOpen: false,
        mobileChatSidebarOpen: false,
        fontSize: 14,

        // Arabic needs ~15% larger base font for readability.
        effectiveFontSize() {
            var base = this.fontSize || 14;
            return this.lang === 'ar' ? Math.round(base * 1.15) : base;
        },

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

            // Auto-close mobile drawers when resizing back to desktop.
            window.addEventListener('resize', () => {
                if (window.innerWidth > 768) {
                    this.mobileNavOpen = false;
                    this.mobileChatSidebarOpen = false;
                }
            });
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

        // ── Mobile navigation drawer ──
        toggleMobileNav() {
            this.mobileNavOpen = !this.mobileNavOpen;
        },
        closeMobileNav() {
            this.mobileNavOpen = false;
        },
        toggleMobileChatSidebar() {
            this.mobileChatSidebarOpen = !this.mobileChatSidebarOpen;
        },
        closeMobileChatSidebar() {
            this.mobileChatSidebarOpen = false;
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

export function sidebarComponent() {
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

export function sidebarModel() {
    return {
        selectedModel: '',
        providers: [],
        async init() {
            try {
                const resp = await fetch('/api/providers');
                const data = await resp.json();
                this.providers = Array.isArray(data) ? data : (data.providers || []);
            } catch (e) { console.error('Failed to load providers', e); }
            // Load saved model
            try {
                const r = await fetch('/api/settings');
                const s = await r.json();
                if (s && s.model && s.model.default) this.selectedModel = s.model.default;
            } catch (e) { }
        },
        async saveModel() {
            if (!this.selectedModel) return;
            try {
                await fetch('/api/settings/active_model', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: this.selectedModel })
                });
                window.dispatchEvent(new CustomEvent('model-changed', { detail: { model: this.selectedModel } }));
            } catch (e) { console.error('Failed to save model', e); }
        }
    };
}

export function systemAlertsBanner() {
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
