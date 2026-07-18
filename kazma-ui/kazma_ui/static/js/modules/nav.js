// ── Kazma modules/nav.js ──
// Soft navigation: swap .page-body (not the whole chrome), reinject page
// scripts in order, wait for Alpine factories, then initTree.
// Failures always fall back to a full page load.
//
// Hard-reload (full) for SSE / heavy editors: /chat, /ide, /swarm.
//
// Why .page-body (not #main-content)?
//   #main-content also holds the header + system-alerts banner. Swapping the
//   whole thing re-inits chrome Alpine roots (always succeed) while page
//   factories like settingsApp() may still be missing — the old "all unbound"
//   check then passed and left a header-only shell. Second click worked because
//   the page script was already on window.

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

    function sleep(ms) {
        return new Promise((r) => setTimeout(r, ms));
    }

    function nextFrame() {
        return new Promise((r) => requestAnimationFrame(() => r()));
    }

    /**
     * Update document title + header chrome from the fetched page without
     * destroying the header Alpine tree.
     */
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

    /**
     * Extract Alpine factory names used by x-data="foo()" on a subtree.
     * Skips object literals like x-data="{ open: false }".
     */
    function extractFactoryNames(root) {
        if (!root) return [];
        const names = [];
        root.querySelectorAll('[x-data]').forEach((el) => {
            const expr = (el.getAttribute('x-data') || '').trim();
            // Match foo( or foo () — not { ... }
            const m = expr.match(/^([A-Za-z_$][\w$]*)\s*\(/);
            if (m) names.push(m[1]);
        });
        return [...new Set(names)];
    }

    /**
     * Wait until every named factory is a function on window.
     * Page scripts define settingsApp / agentsPage / skillsApp etc. globally.
     */
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
     * Load page scripts in document order (async=false) and wait for each.
     * Also refreshes i18n payload when present in the fetched document.
     */
    async function reinjectPageScripts(doc) {
        // Drop scripts from the previous soft-nav page
        document.querySelectorAll('script[data-kazma-page-script]').forEach((el) => el.remove());

        // Refresh i18n from the new page (keys differ slightly per render)
        const i18nScript = Array.from(doc.querySelectorAll('script')).find(
            (s) => !s.src && s.textContent && s.textContent.includes('window.KAZMA_I18N'),
        );
        if (i18nScript && i18nScript.textContent) {
            try {
                // eslint-disable-next-line no-new-func
                new Function(i18nScript.textContent)();
            } catch (e) {
                console.warn('[soft-nav] i18n refresh failed:', e);
            }
        }

        const scripts = Array.from(doc.querySelectorAll('script'));
        const pageScripts = scripts.filter((s) => {
            const src = s.getAttribute('src') || '';
            const type = (s.getAttribute('type') || '').toLowerCase();
            if (type === 'module' && src.includes('/static/js/app.js')) return false;
            if (isGlobalLib(src)) return false;
            // i18n handled above
            if (!src && s.textContent && s.textContent.includes('window.KAZMA_I18N')) return false;
            if (!src && !(s.textContent && s.textContent.trim())) return false;
            if (!src && !s.textContent) return false;
            // Skip empty src-less style-adjacent junk
            return true;
        });

        // Sequential load preserves providers.js → models.js → settings.js order.
        // Dynamically inserted scripts are async by default; force ordered exec.
        for (const s of pageScripts) {
            const src = s.getAttribute('src') || '';
            const type = (s.getAttribute('type') || '').toLowerCase();
            const ns = document.createElement('script');
            ns.setAttribute('data-kazma-page-script', '1');
            if (type) ns.type = type;

            if (src) {
                const sep = src.includes('?') ? '&' : '?';
                // cache-bust so a soft-nav always gets the latest page script
                const fullSrc = src + sep + '_sn=' + Date.now();
                await new Promise((resolve) => {
                    // Must set async=false BEFORE src for ordered execution
                    ns.async = false;
                    ns.onload = () => resolve();
                    ns.onerror = () => {
                        console.warn('[soft-nav] script failed to load:', fullSrc);
                        resolve();
                    };
                    ns.src = fullSrc;
                    document.body.appendChild(ns);
                });
            } else if (s.textContent && s.textContent.trim()) {
                ns.textContent = s.textContent;
                document.body.appendChild(ns);
            }
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
     * Init Alpine on the page body, retrying if any x-data root stays unbound.
     */
    async function bindPageAlpine(pageBody, gen) {
        if (!window.Alpine) {
            // Alpine is defer-loaded; wait briefly on first paint races
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

        initAlpineOn(pageBody);
        await nextFrame();
        if (gen !== softNavGeneration) return;
        initAlpineOn(pageBody);

        // Retry loop for flaky first bind (x-init races, etc.)
        for (let attempt = 0; attempt < 5; attempt++) {
            if (gen !== softNavGeneration) return;
            const unbound = unboundAlpineRoots(pageBody);
            if (unbound.length === 0) return;
            await sleep(40 + attempt * 30);
            // Re-check factories in case a late script redefined them
            await waitForFactories(factories, 500);
            initAlpineOn(pageBody);
        }

        const still = unboundAlpineRoots(pageBody);
        if (still.length > 0) {
            const exprs = still.map((n) => n.getAttribute('x-data')).join('; ');
            throw new Error('Alpine did not bind page roots: ' + exprs);
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
            const newBody = doc.querySelector('.page-body');
            const oldBody = document.querySelector('.page-body');
            // Fall back to full main swap only if shell is missing .page-body
            const newMain = doc.querySelector('#main-content');
            const oldMain = document.querySelector('#main-content');
            if (!newMain || !oldMain) throw new Error('missing #main-content');
            if (!newBody || !oldBody) {
                // Legacy / unexpected shell — full main swap
                destroyAlpineOn(oldMain);
                oldMain.innerHTML = newMain.innerHTML;
                if (doc.title) document.title = doc.title;
                window.scrollTo(0, 0);
                await reinjectPageScripts(doc);
                if (gen !== softNavGeneration) return;
                await bindPageAlpine(oldMain, gen);
            } else {
                // Preferred path: keep header/alerts chrome, swap page body only
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
