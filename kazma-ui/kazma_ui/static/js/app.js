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
window.KazmaAPI = KazmaAPI;
window.KazmaUtils = KazmaUtils;
window.showToast = showToast;
window.showModal = showModal;
window.closeModal = closeModal;

// ── Boot ──
registerStores();   // registers Alpine stores on alpine:init
initSoftNav();      // progressive-enhancement client-side nav
