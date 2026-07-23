// ── Kazma modules/nav.js ──
// Soft navigation: swap .page-body, reinject page scripts in order, wait for
// Alpine factories + page init, then initTree.
// Failures always fall back to a full page load.
//
// Full navigation (not soft) for:
//   - SSE / heavy editors: /chat, /ide, /swarm
//   - Pages with dedicated Alpine apps + external JS: /settings, /agents,
//     /skills, /mcp — soft-nav left these stuck on "Loading…" / empty shells
//     because script reinject + x-init races. Full load is reliable.

export function initSoftNav() {
    const SOFT_NAV_ENABLED = true;

    // Always full document navigation (enter OR leave).
    // /dashboard uses large inline init (sessions + memory board); soft-nav
    // reinjects dashboard.js but historically skipped that inline block →
    // Session Management stuck on skeleton until F5.
    const HARD_RELOAD_ALWAYS = new Set([
        '/chat',
        '/ide',
        '/swarm',
        '/settings',
        '/dashboard',
        '/agents',
        '/skills',
        '/mcp',
        '/replay',
        '/research',
    ]);

    const GLOBAL_LIBS = [
        '/static/js/app.js',
        '/static/js/htmx.min.js',
        '/static/js/alpine.min.js',
        '/static/js/icons.js',
    ];

    // Only these classic page bundles are re-injected on soft-nav.
    // (Keeps importmap / module / alpine out of the reinject loop.)
    const PAGE_SCRIPT_RE = /\/static\/js\/(?:providers|models|settings|agents|skills|mcp|dashboard|workspace|streaming|hitl_approval|replay|research)\.js(?:\?|$)/i;

    let navInFlight = null;
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

    function sleep(ms) {
        return new Promise((r) => setTimeout(r, ms));
    }

    function nextFrame() {
        return new Promise((r) => requestAnimationFrame(() => r()));
    }

    function syncChrome(doc) {
        if (doc.title) document.title = doc.title;

        const newTitle = doc.querySelector('.header-title');
        const oldTitle = document.querySelector('.header-title');
        if (newTitle && oldTitle) {
            oldTitle.textContent = newTitle.textContent;
        }

        const newCrumbs = doc.querySelector('.breadcrumbs');
        const oldCrumbs = document.querySelector('.breadcrumbs');
        if (newCrumbs && oldCrumbs) {
            oldCrumbs.innerHTML = newCrumbs.innerHTML;
        }
    }

    function extractFactoryNames(root) {
        if (!root) return [];
        const names = [];
        root.querySelectorAll('[x-data]').forEach((el) => {
            const expr = (el.getAttribute('x-data') || '').trim();
            const m = expr.match(/^([A-Za-z_$][\w$]*)\s*\(/);
            if (m) names.push(m[1]);
        });
        return [...new Set(names)];
    }

    async function waitForFactories(names, timeoutMs = 4000) {
        if (!names.length) return true;
        const start = Date.now();
        while (Date.now() - start < timeoutMs) {
            if (names.every((n) => typeof window[n] === 'function')) return true;
            await sleep(16);
        }
        const missing = names.filter((n) => typeof window[n] !== 'function');
        console.warn('[soft-nav] factories still missing:', missing);
        return false;
    }

    function isAlpineBound(el) {
        return !!(el && (el._x_dataStack || el.__x));
    }

    function unboundAlpineRoots(root) {
        if (!root) return [];
        return Array.from(root.querySelectorAll('[x-data]')).filter((n) => !isAlpineBound(n));
    }

    /**
     * Load only page bundles (settings.js, agents.js, …) in order.
     * Skips importmap, ES modules, Alpine, HTMX, icons.
     */
    async function reinjectPageScripts(doc) {
        document.querySelectorAll('script[data-kazma-page-script]').forEach((el) => el.remove());

        // Refresh i18n from the new page
        const i18nScript = Array.from(doc.querySelectorAll('script')).find(
            (s) => !s.getAttribute('src') && s.textContent && s.textContent.includes('window.KAZMA_I18N'),
        );
        if (i18nScript && i18nScript.textContent) {
            try {
                // eslint-disable-next-line no-new-func
                new Function(i18nScript.textContent)();
            } catch (e) {
                console.warn('[soft-nav] i18n refresh failed:', e);
            }
        }

        const pageScripts = Array.from(doc.querySelectorAll('script')).filter((s) => {
            const src = s.getAttribute('src') || '';
            const type = (s.getAttribute('type') || '').toLowerCase();
            if (!src) return false;
            if (type === 'module' || type === 'importmap') return false;
            if (isGlobalLib(src)) return false;
            if (s.hasAttribute('data-kazma-page-script') || s.hasAttribute('data-page-script')) return true;
            return PAGE_SCRIPT_RE.test(src);
        });

        for (const s of pageScripts) {
            const src = s.getAttribute('src') || '';
            const fullSrc = src.includes('?')
                ? src + '&_sn=' + Date.now()
                : src + '?_sn=' + Date.now();

            await new Promise((resolve) => {
                const ns = document.createElement('script');
                ns.setAttribute('data-kazma-page-script', '1');
                ns.async = false;
                let settled = false;
                const done = () => {
                    if (settled) return;
                    settled = true;
                    resolve();
                };
                ns.onload = done;
                ns.onerror = () => {
                    console.warn('[soft-nav] script failed to load:', fullSrc);
                    done();
                };
                // Append first, then set src (most reliable load order across browsers)
                document.body.appendChild(ns);
                ns.src = fullSrc;
                // Safety: never hang soft-nav on a stuck script tag
                setTimeout(done, 8000);
            });
        }
    }

    function initAlpineOn(el) {
        if (!el || !window.Alpine || typeof Alpine.initTree !== 'function') return;
        try {
            Alpine.initTree(el);
        } catch (e) {
            console.warn('[soft-nav] Alpine.initTree:', e);
        }
    }

    function destroyAlpineOn(el) {
        if (!el || !window.Alpine || typeof Alpine.destroyTree !== 'function') return;
        try {
            Alpine.destroyTree(el);
        } catch (e) { /* ignore */ }
    }

    /**
     * After Alpine binds, wait for page components that expose `loading`
     * (e.g. settingsApp) to finish init. If still stuck, throw → full reload.
     */
    async function waitForPageReady(pageBody, timeoutMs = 3000) {
        const start = Date.now();
        while (Date.now() - start < timeoutMs) {
            const roots = Array.from(pageBody.querySelectorAll('[x-data]'));
            if (!roots.length) return true;

            let allReady = true;
            for (const el of roots) {
                if (!isAlpineBound(el)) {
                    allReady = false;
                    break;
                }
                try {
                    const data = Alpine.$data(el);
                    // settingsApp uses loading=true during init()
                    if (data && data.loading === true) {
                        allReady = false;
                        break;
                    }
                } catch (e) {
                    allReady = false;
                    break;
                }
            }
            if (allReady) return true;
            await sleep(40);
        }
        return false;
    }

    async function bindPageAlpine(pageBody, gen) {
        if (!window.Alpine) {
            const start = Date.now();
            while (!window.Alpine && Date.now() - start < 3000) {
                await sleep(30);
            }
            if (!window.Alpine) throw new Error('Alpine not available');
        }

        const factories = extractFactoryNames(pageBody);
        const ready = await waitForFactories(factories);
        if (!ready) {
            throw new Error('page factories not ready: ' + factories.join(', '));
        }
        if (gen !== softNavGeneration) return;

        // Bind each x-data root explicitly (more reliable than only walking the container)
        const roots = Array.from(pageBody.querySelectorAll('[x-data]'));
        if (roots.length === 0) {
            initAlpineOn(pageBody);
        } else {
            for (const root of roots) {
                if (!isAlpineBound(root)) {
                    initAlpineOn(root);
                }
            }
        }

        await nextFrame();
        if (gen !== softNavGeneration) return;

        // Retry unbound roots
        for (let attempt = 0; attempt < 5; attempt++) {
            if (gen !== softNavGeneration) return;
            const unbound = unboundAlpineRoots(pageBody);
            if (unbound.length === 0) break;
            await sleep(40 + attempt * 30);
            await waitForFactories(factories, 500);
            for (const root of unbound) {
                initAlpineOn(root);
            }
        }

        const still = unboundAlpineRoots(pageBody);
        if (still.length > 0) {
            const exprs = still.map((n) => n.getAttribute('x-data')).join('; ');
            throw new Error('Alpine did not bind page roots: ' + exprs);
        }

        // Wait for async x-init (settings loading flag) or hard-fail
        const pageReady = await waitForPageReady(pageBody, 3500);
        if (!pageReady) {
            throw new Error('page component init stuck (loading)');
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
            if (gen !== softNavGeneration) return;
            const trimmed = html.trim();
            if (trimmed.startsWith('{') && trimmed.includes('"detail"')) {
                throw new Error('JSON error body instead of HTML');
            }

            const doc = new DOMParser().parseFromString(html, 'text/html');
            const newBody = doc.querySelector('.page-body');
            const oldBody = document.querySelector('.page-body');
            const newMain = doc.querySelector('#main-content');
            const oldMain = document.querySelector('#main-content');
            if (!newMain || !oldMain) throw new Error('missing #main-content');

            if (!newBody || !oldBody) {
                destroyAlpineOn(oldMain);
                oldMain.innerHTML = newMain.innerHTML;
                if (doc.title) document.title = doc.title;
                window.scrollTo(0, 0);
                await reinjectPageScripts(doc);
                if (gen !== softNavGeneration) return;
                await bindPageAlpine(oldMain, gen);
            } else {
                destroyAlpineOn(oldBody);
                oldBody.innerHTML = newBody.innerHTML;
                syncChrome(doc);
                window.scrollTo(0, 0);

                await reinjectPageScripts(doc);
                if (gen !== softNavGeneration) return;

                await bindPageAlpine(oldBody, gen);
            }

            if (gen !== softNavGeneration) return;
            history.pushState({ kazmaSoft: true }, '', url);
            updateActiveNav();
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
        // Same URL: force a real reload so "click again" never no-ops on a stuck shell
        if (key === targetKey(location.href)) {
            if (needsHardReload(pathOnly(a.href), pathOnly(a.href))) {
                e.preventDefault();
                window.location.reload();
            }
            return;
        }
        const toPath = pathOnly(a.href);
        if (needsHardReload(location.pathname, toPath)) {
            // Let the browser do a full navigation (do not soft-nav)
            return;
        }
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

    const NAV_SHORTCUTS = {
        '1': '/workspace',
        '2': '/chat',
        '3': '/dashboard',
        '4': '/skills',
        '5': '/mcp',
        '6': '/swarm',
        '7': '/replay',
        '8': '/research',
    };

    document.addEventListener('keydown', (e) => {
        if (!(e.metaKey || e.ctrlKey)) return;
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;

        if (e.key === ',') {
            e.preventDefault();
            window.location.href = '/settings';
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
            if (needsHardReload(location.pathname, target)) {
                window.location.href = target;
            } else {
                navigateTo(target);
            }
        }
    });
}
