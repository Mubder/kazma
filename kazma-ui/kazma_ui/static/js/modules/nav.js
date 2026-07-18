// ── Kazma modules/nav.js ──
// Soft navigation: swap #main-content, reinject page scripts, re-init Alpine.
// Failures always fall back to a full page load.
//
// Hard-reload (full) for SSE / heavy editors: /chat, /ide, /swarm.

export function initSoftNav() {
    const SOFT_NAV_ENABLED = true;
    // Leaving or entering these always full-reloads (SSE streams, CodeMirror, Mermaid).
    const HARD_RELOAD_ALWAYS = new Set(['/chat', '/ide', '/swarm']);
    const GLOBAL_LIBS = [
        '/static/js/app.js',
        '/static/js/htmx.min.js',
        '/static/js/alpine.min.js',
        '/static/js/icons.js',
    ];

    let navInFlight = null; // serialize soft-navs
    let softNavGeneration = 0;

    function targetKey(href) {
        try {
            const u = new URL(href, location.origin);
            return u.pathname + u.search;
        } catch (e) {
            return href;
        }
    }

    function pathOnly(href) {
        try {
            return new URL(href, location.origin).pathname;
        } catch (e) {
            return href;
        }
    }

    function needsHardReload(fromPath, toPath) {
        return HARD_RELOAD_ALWAYS.has(fromPath) || HARD_RELOAD_ALWAYS.has(toPath);
    }

    function updateActiveNav() {
        const path = location.pathname;
        document.querySelectorAll('.nav-link, .bottom-nav a').forEach((el) => {
            const href = el.getAttribute('href');
            if (!href) return;
            let elPath;
            try { elPath = new URL(href, location.origin).pathname; } catch (e) { return; }
            el.classList.toggle('active', elPath === path);
        });
    }

    function isGlobalLib(src) {
        if (!src) return false;
        return GLOBAL_LIBS.some((g) => src.endsWith(g) || src.includes(g + '?'));
    }

    function setNavigating(on) {
        try {
            document.documentElement.classList.toggle('kazma-soft-nav', !!on);
            document.body && document.body.classList.toggle('kazma-soft-nav', !!on);
        } catch (e) { /* ignore */ }
    }

    /**
     * Load page scripts in document order; wait for external src scripts.
     * Returns a Promise that resolves when all external scripts have loaded
     * (or failed — we still resolve so Alpine can init).
     */
    function reinjectPageScripts(doc) {
        document.querySelectorAll('script[data-kazma-page-script]').forEach((el) => el.remove());

        const pending = [];
        const scripts = Array.from(doc.querySelectorAll('script'));

        for (const s of scripts) {
            const src = s.getAttribute('src') || '';
            const type = (s.getAttribute('type') || '').toLowerCase();
            if (type === 'module' && src.includes('/static/js/app.js')) continue;
            if (isGlobalLib(src)) continue;

            // Skip pure i18n re-bootstrap if already present (avoids flicker)
            if (!src && s.textContent && s.textContent.includes('window.KAZMA_I18N')
                && window.KAZMA_I18N) {
                continue;
            }

            const ns = document.createElement('script');
            ns.setAttribute('data-kazma-page-script', '1');
            if (type) ns.type = type;

            if (src) {
                const sep = src.includes('?') ? '&' : '?';
                ns.src = src + sep + '_sn=' + Date.now();
                pending.push(new Promise((resolve) => {
                    ns.onload = () => resolve();
                    ns.onerror = () => resolve(); // don't block forever
                }));
            } else if (s.textContent && s.textContent.trim()) {
                ns.textContent = s.textContent;
            } else {
                continue;
            }
            document.body.appendChild(ns);
        }
        return Promise.all(pending);
    }

    function initAlpineOn(el) {
        if (!window.Alpine || typeof Alpine.initTree !== 'function') return;
        try {
            Alpine.initTree(el);
        } catch (e) {
            console.warn('[soft-nav] Alpine.initTree:', e);
        }
    }

    async function softNav(url) {
        const gen = ++softNavGeneration;
        setNavigating(true);
        try {
            const res = await fetch(url, {
                headers: { 'Kazma-Soft-Nav': 'true', 'Accept': 'text/html' },
                credentials: 'same-origin',
                redirect: 'follow',
            });
            // Auth gate: never inject JSON 401 or login fragment into the shell
            if (res.status === 401 || res.status === 403) {
                const next = encodeURIComponent(pathOnly(url) + (new URL(url, location.origin).search || ''));
                window.location.href = '/login?next=' + next;
                return;
            }
            if (res.redirected && /\/login(?:\?|$)/.test(res.url)) {
                window.location.href = res.url;
                return;
            }
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const ct = (res.headers.get('content-type') || '').toLowerCase();
            if (ct && !ct.includes('text/html') && !ct.includes('application/xhtml')) {
                throw new Error('non-HTML response (' + ct + ')');
            }
            const html = await res.text();
            if (gen !== softNavGeneration) return; // superseded
            // Guard against raw JSON error bodies mis-parsed as empty pages
            const trimmed = html.trim();
            if (trimmed.startsWith('{') && trimmed.includes('"detail"')) {
                throw new Error('JSON error body instead of HTML');
            }

            const doc = new DOMParser().parseFromString(html, 'text/html');
            const newMain = doc.querySelector('#main-content');
            const oldMain = document.querySelector('#main-content');
            if (!newMain || !oldMain) throw new Error('missing #main-content');

            if (window.Alpine && typeof Alpine.destroyTree === 'function') {
                try { Alpine.destroyTree(oldMain); } catch (e) { /* ignore */ }
            }

            oldMain.innerHTML = newMain.innerHTML;
            if (doc.title) document.title = doc.title;
            window.scrollTo(0, 0);

            // Wait for page scripts (settings.js, skills.js, …) before Alpine
            await reinjectPageScripts(doc);
            if (gen !== softNavGeneration) return;

            initAlpineOn(oldMain);
            // Second pass for deferred factories
            requestAnimationFrame(() => {
                if (gen === softNavGeneration) initAlpineOn(oldMain);
            });

            history.pushState({ kazmaSoft: true }, '', url);
            updateActiveNav();

            // Sanity: if page expected an Alpine root and it never bound, hard reload
            const needsAlpine = oldMain.querySelector('[x-data]');
            if (needsAlpine && window.Alpine) {
                await new Promise((r) => setTimeout(r, 80));
                if (gen !== softNavGeneration) return;
                // x-data nodes that never got _x_dataStack often mean init failed
                const unbound = Array.from(oldMain.querySelectorAll('[x-data]')).filter(
                    (n) => !n._x_dataStack && !n.__x
                );
                if (unbound.length > 0 && unbound.length === oldMain.querySelectorAll('[x-data]').length) {
                    throw new Error('Alpine did not bind any x-data roots');
                }
            }
        } finally {
            if (gen === softNavGeneration) setNavigating(false);
        }
    }

    function navigateTo(url, { forceFull } = {}) {
        const toPath = pathOnly(url);
        if (forceFull || needsHardReload(location.pathname, toPath)) {
            window.location.href = url;
            return;
        }
        if (!SOFT_NAV_ENABLED) {
            window.location.href = url;
            return;
        }
        // Serialize: chain after in-flight nav
        const run = () => softNav(url).catch((err) => {
            console.warn('[soft-nav] falling back to full load:', err);
            window.location.href = url;
        });
        navInFlight = (navInFlight || Promise.resolve()).then(run, run);
        return navInFlight;
    }

    document.addEventListener('click', (e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const a = e.target.closest('a');
        if (!a || !a.href) return;
        if (a.target === '_blank' || a.hasAttribute('download')) return;
        if (a.origin !== location.origin) return;
        if (a.hasAttribute('data-hard-nav')) return;
        const key = targetKey(a.href);
        if (key === targetKey(location.href)) return;
        const toPath = pathOnly(a.href);
        if (needsHardReload(location.pathname, toPath)) return;
        if (!SOFT_NAV_ENABLED) return;

        e.preventDefault();
        navigateTo(a.href);
    });

    window.addEventListener('popstate', () => {
        if (!SOFT_NAV_ENABLED || needsHardReload(location.pathname, location.pathname)) {
            window.location.reload();
            return;
        }
        softNav(location.pathname + location.search)
            .then(updateActiveNav)
            .catch(() => window.location.reload());
    });

    // Keyboard shortcuts — match sidebar kbd hints
    const NAV_SHORTCUTS = {
        '1': '/workspace',
        '2': '/chat',
        '3': '/dashboard',
        '4': '/skills',
        '5': '/mcp',
        '6': '/swarm',
    };

    document.addEventListener('keydown', (e) => {
        if (!(e.metaKey || e.ctrlKey)) return;
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;

        if (e.key === ',') {
            e.preventDefault();
            navigateTo('/settings');
            return;
        }
        if ((e.key === 'i' || e.key === 'I') && e.shiftKey) {
            e.preventDefault();
            window.location.href = '/ide';
            return;
        }
        const target = NAV_SHORTCUTS[e.key];
        if (target) {
            e.preventDefault();
            navigateTo(target);
        }
    });
}
