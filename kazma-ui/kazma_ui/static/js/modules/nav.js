// ── Kazma modules/nav.js ──
// Soft navigation: swap .page-body, reinject page scripts in order, wait for
// Alpine factories + page init, then initTree.
// Failures always fall back to a full page load.
//
// Soft-nav is on for every page. Failures fall back to a full load.
// Chat / IDE / Swarm register window.kazmaOnSoftNavLeave to abort SSE
// and destroy the editor before the next page binds.
//
// First-click empty pages: a whitelist used to skip companions
// (memory_console.js, dash_lists.js, voice.js, mermaid, CodeMirror).
// Second click on the same nav item is a full reload, so it looked
// "fixed". isSoftNavPageScript() is the single gate now.
//
// Settings (and any page whose factory is not already on window) has a
// second trap: <html> is x-data="kazmaApp()", so Alpine's MutationObserver
// inits the swapped .page-body BEFORE page scripts run. settingsApp() is
// still undefined → Alpine binds {} and stamps _x_marker → later initTree
// skips the tree. Pause the observer across the swap, then bind after
// scripts. Destroy+rebind clears a stale empty marker if one landed.

const GLOBAL_LIB_PATHS = [
    '/static/js/app.js',
    '/static/js/htmx.min.js',
    '/static/js/alpine.min.js',
    '/static/js/icons.js',
    '/static/js/auth-guard.js',
    '/static/js/bidi.js',
];

/** True for a script the incoming page owns and soft-nav must re-run. */
export function isSoftNavPageScript(src) {
    if (!src) return false;
    const path = String(src).split('?')[0];
    if (GLOBAL_LIB_PATHS.some((g) => path.endsWith(g))) return false;
    if (path.includes('/static/js/modules/')) return false;
    // documents.js, memory_console.js, dash_lists.js, voice.js, mermaid, …
    if (path.includes('/static/js/')) return true;
    if (/codemirror/i.test(path)) return true;
    return false;
}

export function initSoftNav() {
    const SOFT_NAV_ENABLED = true;

    const HARD_RELOAD_ALWAYS = new Set([]);

    const GLOBAL_LIBS = GLOBAL_LIB_PATHS;

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

        // Sync the shell class (e.g. `is-chat`): it is stamped server-side
        // per route (base.html) and drives header visibility + immersive
        // body CSS. Without this, soft-navigating away from /chat left
        // `is-chat` stuck on `.app-layout` — header hidden on EVERY page
        // and chat's padding/overflow rules leaking (audit P0-3).
        const newLayout = doc.querySelector('.app-layout');
        const oldLayout = document.querySelector('.app-layout');
        if (newLayout && oldLayout && newLayout.className !== oldLayout.className) {
            oldLayout.className = newLayout.className;
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
        if (!el || !(el._x_dataStack || el.__x)) return false;
        // The PRESENCE of _x_dataStack proves Alpine initialized the node.
        // Bare `x-data` (no expression) is a legitimate pattern — Alpine
        // gives it a zero-key data object — and treating those as "unbound"
        // made every soft-nav into /chat throw and fall back to a full
        // reload (audit P0-4). Real failures have NO stack at all. (The old
        // zero-key "empty bind" heuristic is deliberately gone.)
        return true;
    }

    function pageLevelXDataRoots(root) {
        if (!root) return [];
        return Array.from(root.querySelectorAll('[x-data]')).filter((el) => {
            const ancestor = el.parentElement && el.parentElement.closest('[x-data]');
            return !ancestor || !root.contains(ancestor);
        });
    }

    function unboundAlpineRoots(root) {
        if (!root) return [];
        return pageLevelXDataRoots(root).filter((n) => !isAlpineBound(n));
    }

    function pauseAlpineMutations() {
        if (window.Alpine && typeof Alpine.stopObservingMutations === 'function') {
            Alpine.stopObservingMutations();
        }
    }

    function resumeAlpineMutations() {
        if (window.Alpine && typeof Alpine.startObservingMutations === 'function') {
            Alpine.startObservingMutations();
        }
    }

    function rebindAlpineRoot(el) {
        destroyAlpineOn(el);
        initAlpineOn(el);
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
                // The script tag holds `window.KAZMA_I18N = <json>;` from the
                // server. Parse the payload instead of executing the tag as
                // code (new Function) — same-origin but an eval-equivalent
                // that turns any reflected-content bug into code execution
                // (audit finding).
                const text = i18nScript.textContent.trim();
                const m = /^\s*window\.KAZMA_I18N\s*=\s*/.exec(text);
                if (m) {
                    const payload = text.slice(m[0].length).replace(/;\s*$/, '');
                    window.KAZMA_I18N = JSON.parse(payload);
                }
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
            return isSoftNavPageScript(src);
        });

        for (const s of pageScripts) {
            const src = s.getAttribute('src') || '';
            // Monaco / mermaid are sticky globals — reloading them mid-session
            // resets the constructor. Skip if the previous page already loaded them.
            if (/monaco-editor/i.test(src) && window.monaco) continue;
            if (/codemirror/i.test(src) && window.CodeMirror) continue;
            if (/mermaid\.min/i.test(src) && window.mermaid) continue;
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
        let sawLoading = false;
        while (Date.now() - start < timeoutMs) {
            const roots = pageLevelXDataRoots(pageBody);
            if (!roots.length) return true;

            let allReady = true;
            for (const el of roots) {
                // Bare `x-data` roots ARE bound (zero-key stack) — gating on
                // isEmptyAlpineBind here re-introduced the P0-4 reload loop.
                if (!isAlpineBound(el)) {
                    allReady = false;
                    break;
                }
                try {
                    const data = Alpine.$data(el);
                    if (data && data.loading === true) {
                        sawLoading = true;
                        allReady = false;
                        break;
                    }
                    // Don't treat "loading never started" as ready for 300ms —
                    // Settings x-init sets loading=true after the first tick.
                    if (data && 'loading' in data && data.loading !== true && !sawLoading
                            && (Date.now() - start) < 300) {
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

        // Always destroy+init. A MutationObserver pass that ran before
        // page scripts may have bound {} and stamped _x_marker — initTree
        // alone would skip those nodes.
        const roots = pageLevelXDataRoots(pageBody);
        if (roots.length === 0) {
            initAlpineOn(pageBody);
        } else {
            for (const root of roots) {
                rebindAlpineRoot(root);
            }
        }

        await nextFrame();
        if (gen !== softNavGeneration) return;

        // Retry unbound / empty-{} roots
        for (let attempt = 0; attempt < 5; attempt++) {
            if (gen !== softNavGeneration) return;
            const unbound = unboundAlpineRoots(pageBody);
            if (unbound.length === 0) break;
            await sleep(40 + attempt * 30);
            await waitForFactories(factories, 500);
            for (const root of unbound) {
                rebindAlpineRoot(root);
            }
        }

        const still = unboundAlpineRoots(pageBody);
        if (still.length > 0) {
            const exprs = still.map((n) => n.getAttribute('x-data')).join('; ');
            throw new Error('Alpine did not bind page roots: ' + exprs);
        }

        // Wait for async x-init (settings loading flag) or hard-fail
        const pageReady = await waitForPageReady(pageBody, 8000);
        if (!pageReady) {
            throw new Error('page component init stuck (loading)');
        }
    }

    function teardownLiveSockets() {
        try {
            if (typeof window.kazmaOnSoftNavLeave === 'function') {
                window.kazmaOnSoftNavLeave();
            }
        } catch (e) { /* ignore */ }
        const held = window.__kazmaEventSources;
        if (Array.isArray(held)) {
            held.forEach((src) => {
                try { src.close(); } catch (e) { /* ignore */ }
            });
            window.__kazmaEventSources = [];
        }
    }

    function mergePageHead(doc) {
        document.querySelectorAll('[data-kazma-soft-head]').forEach((el) => el.remove());
        if (!doc.head) return;
        Array.from(doc.head.children).forEach((el) => {
            const tag = (el.tagName || '').toLowerCase();
            if (tag === 'style') {
                const clone = el.cloneNode(true);
                clone.setAttribute('data-kazma-soft-head', '1');
                document.head.appendChild(clone);
                return;
            }
            if (tag === 'link' && (el.getAttribute('rel') || '') === 'stylesheet') {
                const href = el.getAttribute('href') || '';
                if (!href || /kazma(\.v5)?\.css/i.test(href)) return;
                const exists = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).some(
                    (l) => (l.getAttribute('href') || '').split('?')[0] === href.split('?')[0],
                );
                if (exists) return;
                const clone = el.cloneNode(true);
                clone.setAttribute('data-kazma-soft-head', '1');
                document.head.appendChild(clone);
            }
        });
    }

    function runIncomingBodyScripts(doc) {
        // {% block scripts %} lives after .page-body. Re-run those inlines
        // (hash deep-links, page I18N) that runInlinePageScripts never sees.
        if (!doc.body) return;
        const scripts = Array.from(doc.body.querySelectorAll('script')).filter((s) => {
            if (s.getAttribute('src')) return false;
            if (s.closest('.page-body')) return false;
            const type = (s.getAttribute('type') || '').toLowerCase();
            if (type && type !== 'text/javascript' && type !== 'application/javascript') {
                return false;
            }
            const text = (s.textContent || '').trim();
            if (!text) return false;
            if (text.includes('window.KAZMA_I18N')) return false;
            return true;
        });
        for (const s of scripts) {
            try {
                const ns = document.createElement('script');
                ns.textContent = s.textContent;
                ns.setAttribute('data-kazma-inline-rerun', '1');
                document.body.appendChild(ns);
            } catch (e) {
                console.warn('[soft-nav] body inline script re-run failed:', e);
            }
        }
    }

    function runInlinePageScripts(root) {
        if (!root) return;
        const scripts = Array.from(root.querySelectorAll('script')).filter((s) => {
            if (s.getAttribute('src')) return false;
            const type = (s.getAttribute('type') || '').toLowerCase();
            if (type && type !== 'text/javascript' && type !== 'application/javascript') {
                return false;
            }
            const text = (s.textContent || '').trim();
            if (!text) return false;
            if (text.includes('window.KAZMA_I18N')) return false;
            return true;
        });
        for (const s of scripts) {
            try {
                const ns = document.createElement('script');
                ns.textContent = s.textContent;
                ns.setAttribute('data-kazma-inline-rerun', '1');
                s.replaceWith(ns);
            } catch (e) {
                console.warn('[soft-nav] inline script re-run failed:', e);
            }
        }
    }

    async function softNav(url) {
        const gen = ++softNavGeneration;
        setNavigating(true);
        teardownLiveSockets();
        let alpinePaused = false;
        try {
            const res = await fetch(url, {
                headers: { 'Kazma-Soft-Nav': 'true', 'Accept': 'text/html' },
                credentials: 'same-origin',
                redirect: 'follow',
            });
            // NOTE: the global fetch wrapper (auth-guard.js) now owns the
            // session-expired → /login redirect for 401/403. Just bail here;
            // the wrapper already fired the redirect.
            if (res.status === 401 || res.status === 403) {
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

            // Pause Alpine BEFORE the innerHTML swap. <html> is x-data, so
            // the document MutationObserver would otherwise init the new
            // tree on the first await (script load) while factories are
            // still missing, bind {}, and stamp _x_marker.
            pauseAlpineMutations();
            alpinePaused = true;
            try {
                if (!newBody || !oldBody) {
                    destroyAlpineOn(oldMain);
                    oldMain.innerHTML = newMain.innerHTML;
                    if (doc.title) document.title = doc.title;
                    window.scrollTo(0, 0);
                    mergePageHead(doc);
                    await reinjectPageScripts(doc);
                    runInlinePageScripts(oldMain);
                    runIncomingBodyScripts(doc);
                    if (gen !== softNavGeneration) return;
                    await bindPageAlpine(oldMain, gen);
                } else {
                    destroyAlpineOn(oldBody);
                    oldBody.innerHTML = newBody.innerHTML;
                    syncChrome(doc);
                    window.scrollTo(0, 0);

                    mergePageHead(doc);
                    await reinjectPageScripts(doc);
                    runInlinePageScripts(oldBody);
                    runIncomingBodyScripts(doc);
                    if (gen !== softNavGeneration) return;

                    await bindPageAlpine(oldBody, gen);
                }
            } finally {
                resumeAlpineMutations();
                alpinePaused = false;
            }

            if (gen !== softNavGeneration) return;
            history.pushState({ kazmaSoft: true }, '', url);
            updateActiveNav();
        } finally {
            if (alpinePaused) resumeAlpineMutations();
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
        // Ctrl+K — global search (the ONE registry; chat's old local
        // Ctrl+K focused the session search and fought this — audit P1-1).
        if (e.key === 'k' || e.key === 'K') {
            e.preventDefault();
            try {
                if (window.Alpine && Alpine.store('search')) Alpine.store('search').toggle();
            } catch (err) { /* search store not ready */ }
            return;
        }
        // Ctrl+N — page-aware new chat: on /chat start a fresh session
        // in place; anywhere else, navigate to chat (which boots fresh).
        if (e.key === 'n' || e.key === 'N') {
            e.preventDefault();
            if (location.pathname === '/chat'
                && window.KazmaChat && typeof window.KazmaChat.newSession === 'function') {
                window.KazmaChat.newSession();
            } else {
                window.location.href = '/chat';
            }
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
