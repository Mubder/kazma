// ── Kazma modules/components.js ──
// Root app + sidebar + system-alerts Alpine components.
// Re-exported onto `window` by the app.js entry so x-data and inline
// handlers keep working after the ESM migration.

// Keep <meta name="color-scheme"> / theme-color in lockstep with data-theme.
// iOS Safari paints its UA canvas from these metas (not from CSS html
// background). Listing both schemes, or leaving color-scheme: dark on
// :root, is what made Light theme show a dark-gray page on a phone.
const _THEME_COLOR = { light: '#f0f4fa', dark: '#0e1626' };

export function syncDocumentColorScheme(theme) {
    const scheme = theme === 'dark' ? 'dark' : 'light';
    const root = document.documentElement;
    if (root.getAttribute('data-theme') !== scheme) {
        root.setAttribute('data-theme', scheme);
    }
    _setMeta('color-scheme', scheme);
    _setMeta('supported-color-schemes', scheme);
    let canvas = _THEME_COLOR[scheme];
    try {
        const computed = getComputedStyle(root).getPropertyValue('--bg').trim();
        if (computed) canvas = computed;
    } catch (_) { /* computed style unavailable in some test harnesses */ }
    _setMeta('theme-color', canvas);
}

function _setMeta(name, content) {
    let el = document.querySelector('meta[name="' + name + '"]');
    if (!el) {
        el = document.createElement('meta');
        el.setAttribute('name', name);
        document.head.appendChild(el);
    }
    el.setAttribute('content', content);
}

export function kazmaApp() {
    return {
        theme: 'dark',
        lang: 'ar',
        sidebarCollapsed: false,
        mobileNavOpen: false,
        mobileChatSidebarOpen: false,
        fontSize: 14,

        // Same default for EN and AR. The Settings slider is the
        // operator's size control — do not re-introduce a language
        // multiplier here (it used to scale Arabic by 1.15×).
        effectiveFontSize() {
            return this.fontSize || 14;
        },

        init() {
            // Restore font size from localStorage (synchronous). The Alpine
            // $persist plugin is not bundled, so persistence is handled here.
            const storedSize = localStorage.getItem('kazma-font-size');
            if (storedSize) this.fontSize = Number(storedSize);

            // Persist font size to localStorage on every change.
            this.$watch('fontSize', (v) => localStorage.setItem('kazma-font-size', v));

            // Sync from backend (authoritative — overrides the local cache).
            // Theme is server-authoritative so a user's choice persists across
            // every device/browser, not just this browser's localStorage.
            fetch('/api/settings/appearance')
                .then(r => r.json())
                .then(d => {
                    if (d && d.font_size) this.fontSize = d.font_size;
                    // 'auto' resolves to the OS preference (SSR guessed dark;
                    // correct it here so an Auto user on a Light OS doesn't
                    // see a one-frame dark flash stay). light/dark apply as-is.
                    let resolved = null;
                    if (d && d.theme === 'auto') {
                        try {
                            resolved = (window.matchMedia &&
                                window.matchMedia('(prefers-color-scheme: dark)').matches)
                                ? 'dark' : 'light';
                        } catch (_) { resolved = null; }
                    } else if (d && (d.theme === 'light' || d.theme === 'dark')) {
                        resolved = d.theme;
                    }
                    if (resolved && resolved !== this.theme) {
                        this.theme = resolved;
                        this._applyTheme();
                    }
                })
                .catch(() => {});

            // Adopt the SSR-rendered theme (base.html renders the server-stored
            // appearance.theme onto <html data-theme="...">) as the starting
            // point. It is already correct on the very first paint, so adopting
            // it avoids any flash; the appearance fetch below reconfirms. The
            // server value is authoritative — browser localStorage is legacy.
            const ssrTheme = document.documentElement.getAttribute('data-theme');
            this.theme = (ssrTheme === 'light' || ssrTheme === 'dark') ? ssrTheme : 'light';
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
            // Persist to the account (server) so the choice travels across
            // every device/browser, not just this one. Fire-and-forget; the
            // local apply above already gives instant feedback.
            try {
                fetch('/api/settings/appearance', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({ theme: this.theme }),
                }).catch(() => {});
            } catch (_) { /* theme still works locally */ }
        },

        toggleLanguage() {
            // Switch between 'ar' and 'en', persist, then reload for SSR pickup.
            // Before reloading, check whether any long-running work is in
            // flight (a Knowledge crawl, a chat SSE stream, a swarm task…).
            // Reloading mid-job silently orphans the UI handle to the job
            // (the server-side task keeps running but the page loses its
            // polling timer).  Warn the user and let them back out.
            const inflight = (typeof window.kazmaHasInflightWork === 'function')
                ? window.kazmaHasInflightWork()
                : false;
            if (inflight) {
                // Defer the confirm so the click handler settles; kazmaConfirm
                // is Promise-based and backed by the unified modal.
                (async () => {
                    let proceed = true;
                    if (window.kazmaConfirm) {
                        // Dialog shows in the user's CURRENT language (this.lang),
                        // not the target — they haven't switched yet. The previous
                        // ternary branches were swapped (AR users got English).
                        const isAr = this.lang === 'ar';
                        proceed = await window.kazmaConfirm({
                            title: isAr ? 'تبديل اللغة؟' : 'Switch language?',
                            message: isAr
                                ? 'هناك مهمة قيد التشغيل (زحف أو توليد). تبديل اللغة يعيد تحميل الصفحة ويقطع التقدّم المباشر، رغم أن المهمة على الخادم تستمر. متابعة؟'
                                : 'Work is in progress (a crawl or generation). Switching the language reloads the page and disconnects the live progress, though the server-side task keeps running. Continue?',
                            confirmText: isAr ? 'بدّل على أي حال' : 'Switch anyway',
                            cancelText: isAr ? 'إلغاء' : 'Cancel',
                        });
                    }
                    if (proceed) this._doLanguageSwitch();
                })();
            } else {
                this._doLanguageSwitch();
            }
        },

        _doLanguageSwitch() {
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
            syncDocumentColorScheme(this.theme);
        },

        _handleKeyboard(e) {
            const meta = e.metaKey || e.ctrlKey;

            // Ctrl+B — Toggle sidebar
            if (meta && e.key === 'b') {
                e.preventDefault();
                this.toggleSidebar();
            }

            // Escape — Close modals/search
            if (e.key === 'Escape') {
                if (Alpine.store('search').open) {
                    Alpine.store('search').close();
                } else if (Alpine.store('modal').open) {
                    Alpine.store('modal').close();
                }
            }

            // NOTE: navigation shortcuts (Ctrl+K/N/1-8/,) live ONLY in
            // modules/nav.js — this used to carry a second (hard-load)
            // registry, and the two raced: Ctrl+1 landed on the wrong page,
            // Ctrl+2-6 double-navigated (UI audit P1-1).
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
            const previous = this.activeModel;
            this.activeModel = model;
            const store = Alpine.store('settings');
            if (store) store.appearance.active_chat_model = model;
            // Notify other components immediately (optimistic)
            document.dispatchEvent(new CustomEvent('model-changed', { detail: model }));
            try {
                const res = await fetch('/api/settings/active_model', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ active_model: model }),
                });
                const data = await res.json();
                if (data && (data.status === 'error' || data.ok === false)) {
                    this.activeModel = previous;
                    if (store) store.appearance.active_chat_model = previous;
                    document.dispatchEvent(new CustomEvent('model-changed', { detail: previous || '' }));
                    const msg = data.error || data.error_code || 'Model switch failed';
                    if (window.showToast) window.showToast(msg, 'error', 4000);
                    else console.warn('[sidebar]', msg);
                    return;
                }
                if (data && data.active_model) {
                    this.activeModel = data.active_model;
                    if (store) store.appearance.active_chat_model = data.active_model;
                }
            } catch (e) {
                console.warn('[sidebar] Failed to sync model:', e);
                this.activeModel = previous;
            }
        },

        toggleSidebar() {
            // Robust root lookup: the old `[x-data*="kazmaApp"]` substring
            // selector broke if any template mentioned the name elsewhere
            // (UI audit P2). The app root is the <html> element itself.
            const appEl = document.documentElement.hasAttribute('x-data')
                ? document.documentElement
                : document.querySelector('html[x-data], body [x-data]');
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
