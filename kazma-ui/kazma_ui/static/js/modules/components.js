// ── Kazma modules/components.js ──
// Root app + sidebar + system-alerts Alpine components.
// Re-exported onto `window` by the app.js entry so x-data and inline
// handlers keep working after the ESM migration.

// Keep <meta name="color-scheme"> / theme-color in lockstep with data-theme.
// iOS Safari paints its UA canvas from these metas (not from CSS html
// background). Listing both schemes, or leaving color-scheme: dark on
// :root, is what made Light theme show a dark-gray page on a phone.
const _THEME_COLOR = { light: '#ffffff', dark: '#0e1626' };

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

/* The owner's accent colour (Settings > Appearance), as the two theme
 * variants the stylesheet reads: kazma.css derives --accent and every
 * accent shade from --accent-chosen-dark / --accent-chosen-light, per
 * theme. Setting --accent itself on <html> would beat both theme blocks and
 * give light and dark the same, sometimes unreadable, colour.
 *
 * The rules are kazma_ui/accent_colors.py's (the server renders the same
 * variables on <html>, so a chosen accent paints on the first frame); the
 * two are held equal by tests/fixtures/accent_variants.json:
 *   dark  - the colour, lightened toward white in 5% steps until it stands
 *           3:1 off the dark wall and white or near-black ink reads 4.5:1
 *           on it; ink: the one that reads better.
 *   light - the colour, darkened toward navy in 5% steps until white ink
 *           on it reaches 4.5:1; ink: white. */
const _ACCENT_PROPS = [
    '--accent-chosen-dark', '--accent-chosen-dark-rgb', '--accent-chosen-dark-on',
    '--accent-chosen-light', '--accent-chosen-light-rgb', '--accent-chosen-light-on',
];
// Written by builds before 2026-11-09; cleared so they never pin a colour.
const _LEGACY_ACCENT_PROPS = ['--accent', '--accent-rgb', '--accent-subtle', '--accent-glow'];
const _DARK_WALL = [0x0e, 0x16, 0x26];
const _NAVY = [0x0c, 0x15, 0x26];
const _WHITE = [255, 255, 255];
const _DARK_INK = [0x04, 0x08, 0x15];

function _channel(c) {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function _luminance(rgb) {
    return 0.2126 * _channel(rgb[0]) + 0.7152 * _channel(rgb[1]) + 0.0722 * _channel(rgb[2]);
}
function _contrast(a, b) {
    const la = _luminance(a), lb = _luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
function _mix(a, b, t) {
    return a.map((x, i) => Math.floor(x + (b[i] - x) * t + 0.5));
}
function _hex(rgb) {
    return '#' + rgb.map((c) => c.toString(16).padStart(2, '0')).join('');
}

export function accentVariants(hex) {
    if (typeof hex !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(hex.trim())) return null;
    const v = hex.trim().toLowerCase();
    const base = [parseInt(v.slice(1, 3), 16), parseInt(v.slice(3, 5), 16), parseInt(v.slice(5, 7), 16)];
    let dark = base;
    for (let step = 1; step <= 20; step++) {
        if (_contrast(dark, _DARK_WALL) >= 3.0 &&
            Math.max(_contrast(dark, _WHITE), _contrast(dark, _DARK_INK)) >= 4.5) break;
        dark = _mix(base, _WHITE, step / 20);
    }
    const darkOn = _contrast(dark, _WHITE) >= _contrast(dark, _DARK_INK) ? '#ffffff' : '#040815';
    let light = base;
    for (let step = 1; step <= 20; step++) {
        if (_contrast(light, _WHITE) >= 4.5) break;
        light = _mix(base, _NAVY, step / 20);
    }
    return {
        '--accent-chosen-dark': _hex(dark),
        '--accent-chosen-dark-rgb': dark.join(', '),
        '--accent-chosen-dark-on': darkOn,
        '--accent-chosen-light': _hex(light),
        '--accent-chosen-light-rgb': light.join(', '),
        '--accent-chosen-light-on': '#ffffff',
    };
}

/* Apply the owner's accent to <html>, or clear it (empty / malformed =
 * the theme's own accent). Only these custom properties are touched: the
 * element's other inline styles (font size, --app-ivh) stay. */
export function applyAccentColor(hex) {
    const root = document.documentElement;
    const props = accentVariants(hex);
    _LEGACY_ACCENT_PROPS.forEach((name) => root.style.removeProperty(name));
    if (!props) {
        _ACCENT_PROPS.forEach((name) => root.style.removeProperty(name));
        return;
    }
    Object.keys(props).forEach((name) => root.style.setProperty(name, props[name]));
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
                    // The server rendered the chosen accent on <html>; this
                    // re-confirms it (or clears it when none is chosen).
                    if (d && d.accent_color_chosen) applyAccentColor(d.accent_color);
                    else if (d) applyAccentColor('');
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
        // "No model selected" only once /api/provider/active answered: it
        // was on every page for as long as the two reads took.
        activeModelLoaded: false,
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
                    // provider:model, as the picker's values are (stage 3).
                    this.activeModel = data.provider ? data.provider + ':' + data.model : data.model;
                    const store = Alpine.store('settings');
                    if (store) store.appearance.active_chat_model = data.model;
                }
                this.activeModelLoaded = true;
            } catch (e) { /* keep default */ }
        },

        async _fetchModelOptions() {
            // The one model list (/api/models/options, providers stage 3):
            // ``provider:model`` values, and the default it names. This
            // picker sets the install's default; a chat's own picker sets
            // that chat's model.
            try {
                const res = await fetch('/api/models/options');
                if (!res.ok) return;
                const data = await res.json();
                const options = Array.isArray(data && data.options) ? data.options : [];
                const groups = [];
                const byProvider = {};
                options.forEach(o => {
                    if (!byProvider[o.provider]) {
                        byProvider[o.provider] = { label: o.provider_label || o.provider, models: [] };
                        groups.push(byProvider[o.provider]);
                    }
                    byProvider[o.provider].models.push({ ref: o.ref, model: o.model });
                });
                this.modelOptions = groups;
                if (data && data.default) {
                    this.activeModel = data.default;
                    this.activeModelLoaded = true;
                }
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
                // The value sent (provider:model) stays selected: the
                // answer names the model without its provider.
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
                await window.kazmaSave('/api/settings/active_model', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: this.selectedModel })
                });
                window.dispatchEvent(new CustomEvent('model-changed', { detail: { model: this.selectedModel } }));
            } catch (e) {
                // The server kept its model: show that, and say why.
                console.error('Failed to save model', e);
                if (typeof window.showToast === 'function') {
                    window.showToast(window.kazmaT('common.ui.model_not_switched', 'Model not switched: {error}', { error: e.message }), 'error');
                }
                if (typeof this._fetchActiveModel === 'function') this._fetchActiveModel();
            }
        }
    };
}

// The alerts store: the header's notifications store, which polls
// /api/alerts/recent. The banner read the same endpoint on a poller of its
// own, every 10 s beside the store's 15 s, on every page (2026-09-26).
function alertsStore() {
    const A = (typeof window !== 'undefined') ? window.Alpine : null;
    return (A && typeof A.store === 'function') ? A.store('notifications') : null;
}

// The banner is for what needs attention. The bell keeps everything, news
// included: an INFO alert ("Crawl completed") took the yellow warning banner
// and kept it, since a finished run's alert stays in the bell's history
// (live 2026-10-31). A dismissal is remembered in this browser -- it lived in
// the page's memory, so every reload brought the banner back.
const BANNER_DISMISSED_KEY = 'kazma.banner.dismissed';
const BANNER_DISMISSED_MAX = 200;

function readDismissedAlerts() {
    try {
        const raw = window.localStorage && window.localStorage.getItem(BANNER_DISMISSED_KEY);
        const ids = JSON.parse(raw || '[]');
        return new Set(Array.isArray(ids) ? ids.filter((id) => typeof id === 'string') : []);
    } catch (e) {
        return new Set();  // a private window or blocked storage: dismiss for this page only
    }
}

function saveDismissedAlerts(ids) {
    try {
        window.localStorage.setItem(BANNER_DISMISSED_KEY, JSON.stringify([...ids].slice(-BANNER_DISMISSED_MAX)));
    } catch (e) { /* storage unavailable: the dismissal holds for this page */ }
}

function needsAttention(alert) {
    const severity = String((alert && alert.severity) || '').toUpperCase();
    return severity !== 'INFO' && severity !== 'SUCCESS';  // a success is a green toast
}

export function systemAlertsBanner() {
    return {
        installing: false,
        dismissedAlerts: readDismissedAlerts(),

        // Alerts that need attention and were not dismissed, newest first.
        get waitingAlerts() {
            const store = alertsStore();
            const alerts = (store && Array.isArray(store.items)) ? store.items : [];
            return alerts.filter((a) => a && needsAttention(a) && !this.dismissedAlerts.has(a.id))
                .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
        },

        get activeAlert() {
            return this.waitingAlerts[0] || null;
        },

        // An error is red, anything else that needs attention yellow.
        bannerColor() {
            const severity = String((this.activeAlert && this.activeAlert.severity) || '').toUpperCase();
            return severity === 'ERROR' || severity === 'CRITICAL' ? 'var(--danger)' : 'var(--warning)';
        },

        bannerTone() {
            const red = this.bannerColor() === 'var(--danger)';
            return {
                background: red ? 'rgba(239, 68, 68, 0.1)' : 'rgba(245, 158, 11, 0.1)',
                borderLeft: '4px solid ' + this.bannerColor(),
            };
        },

        // Behind the shown alert can wait a backlog (a day of failed test
        // crawls left twelve): one click clears what is waiting now; an alert
        // raised later still shows.
        dismissAll() {
            for (const alert of this.waitingAlerts) this.dismissedAlerts.add(alert.id);
            saveDismissedAlerts(this.dismissedAlerts);
        },

        refreshAlerts() {
            const store = alertsStore();
            if (store && typeof store.refresh === 'function') return store.refresh();
            return undefined;
        },

        dismissAlert() {
            const alert = this.activeAlert;
            if (alert) {
                this.dismissedAlerts.add(alert.id);
                saveDismissedAlerts(this.dismissedAlerts);
            }
        },

        // A banner may point at the page that fixes it (a model fallback ->
        // Settings -> Providers). Only this site's own paths: the server drops
        // anything else, and so does this, in case a payload ever carries one.
        alertLink() {
            const id = this.activeAlert && this.activeAlert.callback_id;
            if (typeof id !== 'string' || !id.startsWith('link:/') || id.startsWith('link://')) {
                return '';
            }
            return id.slice(5);
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
                    showToast(window.kazmaT('common.ui.st_install_started', 'Installation of sentence-transformers started asynchronously'), 'success');
                    // Poll again immediately
                    setTimeout(() => this.refreshAlerts(), 3000);
                } else {
                    showToast(window.kazmaT('common.ui.install_start_failed', 'Failed to start installation'), 'error');
                }
            } catch (err) {
                console.error('[SystemAlertsBanner] Install failed:', err);
                showToast(window.kazmaT('common.ui.install_start_failed', 'Failed to start installation'), 'error');
            } finally {
                this.installing = false;
            }
        }
    };
}
