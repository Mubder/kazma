// ── Kazma modules/nav.js ──
// Soft navigation: intercept same-origin nav clicks, swap only
// #main-content, re-run page scripts, and re-init Alpine. Any failure
// falls back to a normal full page load, so the app can never end up
// in a broken state. /chat is excluded to protect the live SSE stream.

export function initSoftNav() {
    // Soft-nav (innerHTML swap + Alpine re-init) is fragile for server-rendered
    // Alpine pages — swapped content renders uninitialized/broken. Until a proper
    // SPA shell exists, navigation uses normal full page loads (reliable).
    // Flip to true once re-init is robust. /chat always does a full reload.
    const SOFT_NAV_ENABLED = false;
    const HARD_RELOAD = new Set(['/chat']); // protect SSE + chat.js lifecycle
    const GLOBAL_LIBS = ['/static/js/app.js', '/static/js/htmx.min.js', '/static/js/alpine.min.js'];

    function targetKey(href) {
        try {
            const u = new URL(href, location.origin);
            return u.pathname + u.search;
        } catch (e) {
            return href;
        }
    }

    // Sync the sidebar / bottom-nav active highlight with the current path.
    // The nav lives outside #main-content, so soft-nav must update it manually
    // (otherwise the old tab stays highlighted after a client-side swap).
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

    async function softNav(url) {
        const res = await fetch(url, {
            headers: { 'Kazma-Soft-Nav': 'true' },
            credentials: 'same-origin',
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const html = await res.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const newMain = doc.querySelector('#main-content');
        const oldMain = document.querySelector('#main-content');
        if (!newMain || !oldMain) throw new Error('missing #main-content');

        oldMain.innerHTML = newMain.innerHTML;
        if (doc.title) document.title = doc.title;
        window.scrollTo(0, 0);

        // Re-run page scripts (skip global libs that are already loaded).
        doc.querySelectorAll('script').forEach((s) => {
            const src = s.getAttribute('src');
            if (src && GLOBAL_LIBS.some((g) => src.endsWith(g))) return;
            const ns = document.createElement('script');
            if (src) {
                if (document.querySelector('script[src="' + src + '"]')) return;
                ns.src = src;
            } else if (s.textContent && s.textContent.trim()) {
                ns.textContent = s.textContent;
            } else {
                return;
            }
            document.body.appendChild(ns);
        });

        // Alpine's MutationObserver auto-inits new x-data nodes; nudge if available.
        if (window.Alpine && Alpine.initTree) {
            try { Alpine.initTree(oldMain); } catch (e) { /* already initialized */ }
        }
        history.pushState({ kazmaSoft: true }, '', url);
        // Highlight must sync AFTER the URL changes, or it re-lights the old tab.
        updateActiveNav();
    }

    document.addEventListener('click', (e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const a = e.target.closest('a');
        if (!a || !a.href) return;
        if (a.target === '_blank' || a.hasAttribute('download')) return;
        if (a.origin !== location.origin) return;
        const key = targetKey(a.href);
        if (key === targetKey(location.href)) return; // same page
        // Full reload for SSE/scripted pages to guarantee clean teardown/bind.
        if (HARD_RELOAD.has(new URL(a.href).pathname) || HARD_RELOAD.has(location.pathname)) return;
        // Soft-nav disabled: let the browser do a normal (reliable) full load.
        if (!SOFT_NAV_ENABLED) return;

        e.preventDefault();
        const url = a.href;
        softNav(url).catch((err) => {
            console.warn('[soft-nav] falling back to full load:', err);
            window.location.href = url;
        });
    });

    window.addEventListener('popstate', () => {
        if (!SOFT_NAV_ENABLED || HARD_RELOAD.has(location.pathname)) { window.location.reload(); return; }
        softNav(location.pathname + location.search)
            .then(updateActiveNav)
            .catch(() => window.location.reload());
    });

    // ── Keyboard shortcuts (⌘/Ctrl + 1-6 for nav, ⌘/Ctrl + , for settings) ──
    // The sidebar already shows these as kbd hints but they were never wired.
    const NAV_SHORTCUTS = {
        '1': '/workspace',
        '2': '/ide',
        '3': '/chat',
        '4': '/swarm',
        '5': '/agents',
        '6': '/dashboard',
    };

    document.addEventListener('keydown', (e) => {
        // Only trigger on ⌘/Ctrl + number keys, not when typing in an input.
        if (!(e.metaKey || e.ctrlKey)) return;
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;

        if (e.key === ',') {
            e.preventDefault();
            window.location.href = '/settings';
            return;
        }
        const target = NAV_SHORTCUTS[e.key];
        if (target) {
            e.preventDefault();
            window.location.href = target;
        }
    });
}
