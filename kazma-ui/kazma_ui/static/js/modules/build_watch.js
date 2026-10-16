// ═══════════════════════════════════════════════════════════════════
// Kazma build watch — tells an open tab that Kazma was updated.
//
// A tab keeps running the page code it loaded. After a deploy the server
// runs a newer build while an open tab still runs the old scripts against
// it; the owner asked for a notice (2026-10-16) after a fix to the chat page
// reached the server but not a tab opened before the deploy.
//
// The page knows the build that rendered it (`<meta name="kazma-build">`,
// base.html); /health/live says the build the server runs now. When they
// differ, one toast per tab offers a Refresh. The build is the git commit:
// templates and the translation catalog change with server code too, so a
// refresh is the right answer after any deploy -- a restart of the same
// build changes nothing and shows nothing.
// ═══════════════════════════════════════════════════════════════════

/** How often a visible tab asks. Becoming visible also asks. */
const CHECK_EVERY_MS = 5 * 60 * 1000;

/**
 * True when this page was rendered by another build than the server runs.
 * An unknown build on either side (no git at the server) never notifies.
 */
export function buildNoticeDue(pageBuild, serverBuild) {
    const page = String(pageBuild || '').trim();
    const server = String(serverBuild || '').trim();
    if (!page || !server || page === 'unknown' || server === 'unknown') return false;
    return page !== server;
}

/** Start watching. Returns the watcher (also on window.KazmaBuildWatch). */
export function startBuildWatch() {
    const meta = document.querySelector('meta[name="kazma-build"]');
    const pageBuild = meta ? String(meta.getAttribute('content') || '') : '';
    let noticed = false;

    async function check() {
        if (noticed || !pageBuild) return false;
        if (document.visibilityState && document.visibilityState !== 'visible') return false;
        if (typeof window.kazmaGetJson !== 'function' || typeof window.kazmaT !== 'function') return false;
        const live = await window.kazmaGetJson('/health/live');
        const serverBuild = live && live.build ? live.build.commit : '';
        if (!buildNoticeDue(pageBuild, serverBuild)) return false;
        const store = window.Alpine && window.Alpine.store ? window.Alpine.store('toast') : null;
        if (!store || typeof store.add !== 'function') return false;
        noticed = true;
        store.add(
            window.kazmaT('common.update_available', 'Kazma was updated. Refresh to load the new version.'),
            'info',
            0,
            { label: window.kazmaT('common.refresh', 'Refresh'), run: () => window.location.reload() },
        );
        return true;
    }

    document.addEventListener('visibilitychange', () => { check(); });
    setInterval(() => { check(); }, CHECK_EVERY_MS);
    const watcher = { check, pageBuild };
    window.KazmaBuildWatch = watcher;
    return watcher;
}
