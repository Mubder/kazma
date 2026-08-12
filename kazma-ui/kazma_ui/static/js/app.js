// ═══════════════════════════════════════════════════════════════════
// Kazma App.js — ES module entry point.
// Imports the focused modules below and re-exposes the symbols that
// templates (x-data) and classic page scripts rely on as `window.*`
// globals, so the ESM migration is behavior-preserving.
// ═══════════════════════════════════════════════════════════════════

import { registerStores } from './modules/stores.js';
import {
    kazmaApp,
    sidebarComponent,
    sidebarModel,
    systemAlertsBanner,
    syncDocumentColorScheme,
} from './modules/components.js';
import {
    KazmaAPI,
    KazmaUtils,
    showToast,
    showModal,
    closeModal,
} from './modules/util.js';
import { initSoftNav } from './modules/nav.js';

// ── Preserve legacy globals consumed by templates + classic scripts ──
window.kazmaApp = kazmaApp;
window.sidebarComponent = sidebarComponent;
window.sidebarModel = sidebarModel;
window.systemAlertsBanner = systemAlertsBanner;
window.syncDocumentColorScheme = syncDocumentColorScheme;
window.KazmaAPI = KazmaAPI;
window.KazmaUtils = KazmaUtils;
window.showToast = showToast;
window.showModal = showModal;
window.closeModal = closeModal;

// ── Boot ──
registerStores();   // registers Alpine stores on alpine:init
initSoftNav();      // progressive-enhancement client-side nav

// ── Phone keyboard: keep the composer visible via visualViewport ──
// iOS Safari does NOT shrink layout viewport when the soft keyboard opens,
// so a position:sticky composer gets covered. visualViewport reports the
// real visible height; we mirror it into --app-ivh (px) and the chat
// container/mobile shell consume it. Desktop (no visualViewport API or a
// height equal to window) is a no-op. See handoff P2c.
(function initPhoneViewport() {
    const vv = window.visualViewport;
    if (!vv) return;
    const apply = () => {
        document.documentElement.style.setProperty('--app-ivh', `${vv.height}px`);
    };
    apply();
    vv.addEventListener('resize', apply, { passive: true });
    vv.addEventListener('scroll', apply, { passive: true });
})();
