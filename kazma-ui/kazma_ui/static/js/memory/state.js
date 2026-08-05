// Shared mutable state for the memory console + V2 belief graph.
//
// Split from the old memory_console.js single-IIFE (2026-08). The original
// file closed ~40 `var`s over an IIFE that all 86 functions shared. ES
// modules can't share reassignable bindings across files, so every piece of
// shared state lives HERE, on one object exported by reference. All graph/
// console code imports this object and reads/writes its fields —
// `state._v2gPts = []` instead of a bare `_v2gPts = []`. The grep gate
// (no bare `_v2g(Pts|Edges|Ids|...)` outside `state.`) verifies nothing was
// missed. window._v2g* exports stay byte-identical for memory.js.

// i18n labels (window.__DASH_MEM_I18N injected by memory.html) + build stamp.
export const I18N = window.__DASH_MEM_I18N || window.I18N || {};
window.__KAZMA_MEMORY_CONSOLE_BUILD = 'es-modules-2026-08-05';

// The single shared-state object. Fields grouped by owning subsystem.
// Graph-engine state is reassigned frequently (see _v2gLoad / _v2gApplyFilters),
// so callers MUST go through `state.` — never hold a local alias to a field.
export const state = {
  // ── Graph model (reassigned on every load/filter) ────────────────────
  _v2gPts: [],
  _v2gEdges: [],
  _v2gIds: {},
  _v2gStructSig: '',
  _v2gLabelSig: '',
  _v2gView: { scale: 1, ox: 0, oy: 0 },
  _v2gPosCache: {},
  _v2gAlpha: 0,
  _v2gAnim: null,
  _v2gDrag: null,
  _v2gHover: -1,
  _v2gSelectedId: null,
  _v2gHighlightSubj: null,
  _v2gHighlightObj: null,
  _v2gCap: 80,
  _v2gNodeBaseR: 7,
  _v2gMinScale: 0.3,
  _v2gMaxScale: 4,
  _v2gTimeRange: { min: 0, max: 0 },
  _v2gFilters: { entity: {}, predicate: {} },
  _v2gRawNodes: [],
  _v2gRawLinks: [],
  _v2gOps: { sourceId: null, targetId: null, mode: null, selectedEdgeIdx: -1 },
  _v2gLastStats: {},
  _v2gPlayTimer: null,
  _v2gPreferReducedMotion: (() => {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) {
      return false;
    }
  })(),
  _v2gPathIds: {},
  _v2gEpisodeNodes: [],
  _v2gShowEpisodes: false,

  // ── Console / health / search / drawer state ─────────────────────────
  _memCompOpen: {},
  _memCompToggleAllWired: false,
  _openBeliefId: null,
  _v2gLastQuerySeeds: [],
};

// ── Event dispatch helpers (the kazma:memory-* contract memory.js relies on) ─
// Three custom events cross the graph↔list bridge; keep names/payloads stable.
export function dispatchGraphSelect(detail) {
  try {
    window.dispatchEvent(new CustomEvent('kazma:memory-graph-select', { detail }));
  } catch (_) { /* ignore */ }
}

export function dispatchOpsSlots(detail) {
  try {
    window.dispatchEvent(new CustomEvent('kazma:memory-ops-slots', { detail }));
  } catch (_) { /* ignore */ }
}

export function dispatchOpsDone(detail) {
  try {
    window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail }));
  } catch (_) { /* ignore */ }
}
