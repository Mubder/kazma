// V2 belief topology graph — the canvas subsystem.
// Split from memory_console.js (2026-08). All _v2g* canvas code lives here;
// shared mutable state lives in state.js (imported by reference). The two
// console callbacks (pollV2Health, loadV2Beliefs) are injected via
// setGraphCallbacks to avoid a circular import.
import { state, dispatchGraphSelect, dispatchOpsSlots, dispatchOpsDone } from './state.js';

const graphCallbacks = { pollV2Health: null, loadV2Beliefs: null };
export function setGraphCallbacks(cb) { Object.assign(graphCallbacks, cb); }

  export function _v2gFindNodeIndex(key) {
    if (key == null || key === '') return -1;
    var k = String(key);
    var kLow = k.toLowerCase();
    var slug = _v2gSlugify(k);
    for (var i = 0; i < state._v2gPts.length; i++) {
      var pid = String(state._v2gPts[i].id || '');
      var plabel = String(state._v2gPts[i].fullLabel || state._v2gPts[i].label || '').toLowerCase();
      var pname = String(state._v2gPts[i].name || '').toLowerCase();
      if (pid === k || pid.toLowerCase() === kLow || pid === slug) return i;
      if (plabel === kLow || pname === kLow) return i;
    }
    return -1;
  }

  export function _v2gZoomToIndex(idx) {
    if (idx < 0 || !state._v2gPts[idx]) return;
    var p = state._v2gPts[idx];
    var size = _v2gCanvasSize();
    if (size) {
      state._v2gView.scale = 2.5;
      state._v2gView.ox = size.w / 2 - p.x * 2.5;
      state._v2gView.oy = size.h / 2 - p.y * 2.5;
    }
    _v2gHeated();
    _v2gRepaint();
  }

  export function _v2gNotifyList(detail) {
    try {
      window.dispatchEvent(new CustomEvent('kazma:memory-graph-select', { detail: detail || {} }));
    } catch (e) { /* ignore */ }
  }

  export function _v2gSelectByBelief(subj, obj, beliefId) {
    if (!state._v2gPts.length) return false;
    var objIdx = _v2gFindNodeIndex(obj);
    var subjIdx = _v2gFindNodeIndex(subj);
    var targetIdx = objIdx >= 0 ? objIdx : subjIdx;
    if (targetIdx < 0) return false;
    var p = state._v2gPts[targetIdx];
    state._v2gSelectedId = p.id;
    state._v2gHighlightSubj = subjIdx >= 0 ? state._v2gPts[subjIdx].id : null;
    state._v2gHighlightObj = objIdx >= 0 ? state._v2gPts[objIdx].id : null;
    _v2gInspect(p);
    _v2gZoomToIndex(targetIdx);
    return true;
  }

  /** Focus a node by entity id (from Entities table). Returns true if found. */
  export function _v2gSelectEntity(entityId, opts) {
    opts = opts || {};
    var id = String(entityId || opts.graphId || '').trim();
    if (!id) return false;
    // Self person shells (ent_* User/Mubder) map to hub id=user on the canvas
    var focusId = String(opts.graphId || id).trim();
    var tryIds = [focusId, id];
    if (opts.isSelf || opts.graphId === 'user') {
      tryIds.unshift('user');
    }
    // Clear search filter that may hide the node
    var searchEl = document.getElementById('v2g-search');
    if (searchEl && searchEl.value) {
      searchEl.value = '';
      _v2gApplyFilters();
    }
    var idx = -1;
    for (var t = 0; t < tryIds.length && idx < 0; t++) {
      idx = _v2gFindNodeIndex(tryIds[t]);
    }
    // Match by display name (e.g. list says Mubder, hub labeled Mubder)
    if (idx < 0 && opts.name) {
      var want = String(opts.name).toLowerCase();
      for (var i = 0; i < state._v2gPts.length; i++) {
        var dn = _v2gDisplayName(state._v2gPts[i]).toLowerCase();
        if (dn === want) { idx = i; break; }
      }
    }
    if (idx < 0) {
      // Node may be outside current filter — clear entity-type filters once
      var hadFilter = Object.keys(state._v2gFilters.entity || {}).length > 0;
      if (hadFilter) {
        state._v2gFilters.entity = {};
        _v2gRenderFilters();
        _v2gApplyFilters();
        for (var t2 = 0; t2 < tryIds.length && idx < 0; t2++) {
          idx = _v2gFindNodeIndex(tryIds[t2]);
        }
      }
    }
    // Soft-inject hub/self node if still missing (empty person shell)
    if (idx < 0 && (opts.isSelf || focusId === 'user' || id === 'user')) {
      var label = opts.name || 'You';
      state._v2gRawNodes = state._v2gRawNodes || [];
      var exists = false;
      for (var r = 0; r < state._v2gRawNodes.length; r++) {
        if (state._v2gRawNodes[r] && String(state._v2gRawNodes[r].id) === 'user') {
          state._v2gRawNodes[r].name = label;
          state._v2gRawNodes[r].isHub = true;
          exists = true;
          break;
        }
      }
      if (!exists) {
        state._v2gRawNodes.push({
          id: 'user',
          name: label,
          type: 'person',
          beliefCount: 0,
          isHub: true,
          isHighStakes: true,
        });
      }
      state._v2gStructSig = '';
      state._v2gLabelSig = '';
      _v2gApplyFilters();
      idx = _v2gFindNodeIndex('user');
    }
    if (idx < 0) return false;
    var p = state._v2gPts[idx];
    state._v2gSelectedId = p.id;
    state._v2gHighlightSubj = null;
    state._v2gHighlightObj = null;
    _v2gInspect(p);
    _v2gZoomToIndex(idx);
    if (opts.notify !== false) {
      _v2gNotifyList({ type: 'entity', id: p.id, name: _v2gDisplayName(p) });
    }
    return true;
  }

  function _v2gSlugify(s) {
    return String(s || '').trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  }

  function _esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  // Wire V2 controls + boot
  try {
    const v2Refresh = document.getElementById('v2-refresh-btn');
    if (v2Refresh) v2Refresh.addEventListener('click', () => { (graphCallbacks.pollV2Health ? graphCallbacks.pollV2Health() : Promise.resolve()); (graphCallbacks.loadV2Beliefs ? graphCallbacks.loadV2Beliefs(document.getElementById('v2-belief-search') : Promise.resolve()).value); });
    const v2Search = document.getElementById('v2-belief-search');
    if (v2Search) {
      let _v2STo;
      v2Search.addEventListener('input', () => { clearTimeout(_v2STo); _v2STo = setTimeout(() => (graphCallbacks.loadV2Beliefs ? graphCallbacks.loadV2Beliefs(v2Search.value) : Promise.resolve()), 250); });
    }
    (graphCallbacks.pollV2Health ? graphCallbacks.pollV2Health() : Promise.resolve());
    (graphCallbacks.loadV2Beliefs ? graphCallbacks.loadV2Beliefs('') : Promise.resolve());
    setInterval(pollV2Health, 5000);
  } catch (e) { /* V2 panel optional */ }

  // ══ V2 BELIEF TOPOLOGY GRAPH ══════════════════════════════════
  // Self-contained force-directed canvas for the V2 belief graph.
  // Separate _v2g* namespace — does NOT touch the L2 _kg* state.
  // Features: entity nodes (colored by type, sized by belief count),
  // belief edges (colored by predicate_type, dashed if superseded),
  // high-stakes red halo, bi-temporal time slider, filter toggles.

  var state._v2gPts = [], state._v2gEdges = [], state._v2gIds = {}, state._v2gStructSig = '', state._v2gLabelSig = '';
  var state._v2gView = { scale: 1, ox: 0, oy: 0 };
  // User-dragged positions survive layout rebuilds / 30s refresh / filter retune.
  // pinned: physics does not pull the node back after the user places it.
  var state._v2gPosCache = {};
  var state._v2gAlpha = 0, state._v2gAnim = null, state._v2gDrag = null, state._v2gHover = -1, state._v2gSelectedId = null;
  var state._v2gCap = 80, state._v2gNodeBaseR = 7;
  var state._v2gMinScale = 0.3, state._v2gMaxScale = 4;
  var state._v2gTimeRange = { min: 0, max: 0 };
  var state._v2gFilters = { entity: {}, predicate: {} };
  // Cache the last full dataset so client-side filters don't need a re-fetch
  var state._v2gRawNodes = [], state._v2gRawLinks = [];
  // Graph-native ops: source/target slots + pick modes (link | merge)
  // Shared with the Entities list via kazma:memory-ops-slots events.
  var state._v2gOps = {
    sourceId: null,
    targetId: null,
    mode: null, // null | 'link' | 'merge'
    selectedEdgeIdx: -1,
  };

  // Palette follows site tokens (cyan accent + blue secondary).
  // The "user" node is intentionally warm amber so it stands out.
  function _v2gCss(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (e) { return fallback; }
  }
  function _v2gTheme() {
    var accent = _v2gCss('--accent', '#22d3ee');
    var accentLight = _v2gCss('--accent-light', '#67e8f9');
    var secondary = _v2gCss('--secondary', '#3b82f6');
    var secondaryLight = _v2gCss('--secondary-light', '#93c5fd');
    var user = _v2gCss('--warning', '#f59e0b');
    return {
      accent: accent,
      accentLight: accentLight,
      secondary: secondary,
      secondaryLight: secondaryLight,
      user: user,
      // Soft type variation — all stay in site blue/cyan family
      type: {
        person: secondary,
        tool: accent,
        concept: accentLight,
        location: secondaryLight,
        project: '#38bdf8',
        entity: '#7dd3fc',
      },
      pred: {
        functional: accent,
        set: secondaryLight,
        state: '#38bdf8',
      },
    };
  }
  // Legacy maps rebuilt from theme (filters / legend)
  function _v2gTypeColors() {
    var t = _v2gTheme();
    return Object.assign({ user: t.user }, t.type);
  }
  function _v2gPredColors() {
    return _v2gTheme().pred;
  }
  // Keep symbols for older call sites that read the maps directly
  var _V2G_TYPE_COLORS = _v2gTypeColors();
  var _V2G_PRED_COLORS = _v2gPredColors();
  function _v2gRefreshPalette() {
    _V2G_TYPE_COLORS = _v2gTypeColors();
    _V2G_PRED_COLORS = _v2gPredColors();
  }
  function _v2gIsUser(p) {
    if (!p) return false;
    if (p.isHub || p.isUser) return true;
    var id = String(p.id || '').toLowerCase().trim();
    if (id === 'user' || id === 'you' || id === 'me') return true;
    // Display name can be "Mubder" while id stays user — already handled by isUser.
    // Leak self shells: ent_* / person named You/User (not collapsed onto hub).
    var name = String(p.name || p.fullLabel || p.label || '').toLowerCase().trim();
    if (name === 'you' || name === 'user' || name === 'me') return true;
    return false;
  }

  /** True if neighbor is the memory hub (You/Mubder) — used for Cut hub / shortcut banner. */
  function _v2gIsHubNeighbor(other) {
    if (!other) return false;
    if (_v2gIsUser(other)) return true;
    // Fallback: only one hub-colored center exists; match by id user even if flags missing
    var id = String(other.id || '').toLowerCase();
    if (id === 'user') return true;
    return false;
  }

  // Display label for a node. Id stays canonical (user, shipx); name is the
  // user-editable brand/label. Hub defaults to "You" until renamed (e.g. Mubder).
  function _v2gDisplayName(p) {
    if (!p) return '';
    var raw = String(p.name || p.fullLabel || p.label || p.id || '').trim();
    // Strip legacy "You (user)" fullLabel if still present as sole source
    if (/^you\s*\(user\)$/i.test(raw)) raw = 'You';
    if (_v2gIsUser(p)) {
      var low = raw.toLowerCase();
      if (!raw || low === 'user' || low === 'you' || low === 'me') return 'You';
      return raw; // e.g. Mubder / Kazma
    }
    return raw || String(p.id || '');
  }
  function _v2gNodeColor(p) {
    var t = _v2gTheme();
    if (_v2gIsUser(p)) return t.user;
    return (t.type[p.type] || t.accent);
  }
  function _v2gHexAlpha(hex, a) {
    // #rgb / #rrggbb → rgba()
    if (!hex || hex[0] !== '#') return hex;
    var h = hex.slice(1);
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return hex;
    var r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }

  function _v2gSX(wx) { return wx * state._v2gView.scale + state._v2gView.ox; }
  function _v2gSY(wy) { return wy * state._v2gView.scale + state._v2gView.oy; }
  function _v2gWX(sx) { return (sx - state._v2gView.ox) / state._v2gView.scale; }
  function _v2gWY(sy) { return (sy - state._v2gView.oy) / state._v2gView.scale; }
  function _v2gFont(px) {
    var fam = 'sans-serif'; var p = document.getElementById('v2g-canvas-wrap');
    if (p) { var v = getComputedStyle(p).fontFamily; if (v && String(v).indexOf('var(') === -1) fam = v; }
    return px + 'px ' + fam;
  }

  function _v2gCanvasSize() {
    var wrap = document.getElementById('v2g-canvas-wrap');
    var canvas = document.getElementById('v2g-canvas');
    if (!wrap || !canvas) return null;
    var rect = wrap.getBoundingClientRect();
    var w = Math.max(320, Math.floor(rect.width));
    var h = Math.max(240, Math.floor(rect.height));
    var dpr = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
    canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
    var ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: w, h: h };
  }

  function _v2gIsPinned(pt) {
    return !!(pt && (pt.pinned || (state._v2gPosCache[pt.id] && state._v2gPosCache[pt.id].pinned)));
  }

  function _v2gRememberPos(pt) {
    if (!pt || pt.id == null) return;
    state._v2gPosCache[pt.id] = {
      x: pt.x,
      y: pt.y,
      pinned: !!pt.pinned,
    };
  }

  // Force sim step: repulsion + spring + gravity + collision
  function _v2gStep(W, H) {
    var n = state._v2gPts.length; if (!n) return;
    var cx = W / 2, cy = H / 2;
    // Repulsion (Coulomb) — O(n²) but capped at state._v2gCap nodes
    for (var i = 0; i < n; i++) {
      for (var j = i + 1; j < n; j++) {
        var a = state._v2gPts[i], b = state._v2gPts[j];
        var dx = b.x - a.x, dy = b.y - a.y;
        var d2 = dx * dx + dy * dy + 0.01;
        var d = Math.sqrt(d2);
        var force = 900 * state._v2gAlpha / d2;
        var fx = (dx / d) * force, fy = (dy / d) * force;
        // Pinned / dragged nodes are fixed anchors (user layout sticks)
        if (!(state._v2gDrag && state._v2gDrag.idx === i) && !_v2gIsPinned(a)) { a.vx -= fx; a.vy -= fy; }
        if (!(state._v2gDrag && state._v2gDrag.idx === j) && !_v2gIsPinned(b)) { b.vx += fx; b.vy += fy; }
      }
    }
    // Spring (Hooke) along edges, weighted by confidence
    for (var e = 0; e < state._v2gEdges.length; e++) {
      var ed = state._v2gEdges[e]; var A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
      if (!A || !B) continue;
      var dx = B.x - A.x, dy = B.y - A.y;
      var d = Math.sqrt(dx * dx + dy * dy + 0.01);
      var targetLen = 70;
      var k = 0.04 * (0.5 + (ed.confidence || 0.5));
      var f = (d - targetLen) * k * state._v2gAlpha;
      var fx = (dx / d) * f, fy = (dy / d) * f;
      if (!(state._v2gDrag && state._v2gDrag.idx === ed.a) && !_v2gIsPinned(A)) { A.vx += fx; A.vy += fy; }
      if (!(state._v2gDrag && state._v2gDrag.idx === ed.b) && !_v2gIsPinned(B)) { B.vx -= fx; B.vy -= fy; }
    }
    // Gravity + collision-aware integration
    var margin = 30;
    for (var p = 0; p < n; p++) {
      var pt = state._v2gPts[p];
      if (state._v2gDrag && state._v2gDrag.idx === p) { pt.vx = 0; pt.vy = 0; continue; }
      if (_v2gIsPinned(pt)) {
        // Stay exactly where the user left it (refresh cache continuously)
        pt.vx = 0; pt.vy = 0;
        _v2gRememberPos(pt);
        continue;
      }
      pt.vx += (cx - pt.x) * 0.004 * state._v2gAlpha;
      pt.vy += (cy - pt.y) * 0.004 * state._v2gAlpha;
      if (pt.x < margin) pt.vx += (margin - pt.x) * 0.02 * state._v2gAlpha;
      if (pt.x > W - margin) pt.vx -= (pt.x - (W - margin)) * 0.02 * state._v2gAlpha;
      if (pt.y < margin) pt.vy += (margin - pt.y) * 0.02 * state._v2gAlpha;
      if (pt.y > H - margin) pt.vy -= (pt.y - (H - margin)) * 0.02 * state._v2gAlpha;
      pt.vx *= 0.82; pt.vy *= 0.82;
      var sp = Math.sqrt(pt.vx * pt.vx + pt.vy * pt.vy);
      if (sp > 14) { pt.vx = pt.vx / sp * 14; pt.vy = pt.vy / sp * 14; }
      pt.x += pt.vx; pt.y += pt.vy;
    }
    state._v2gAlpha *= 0.985;
    if (state._v2gAlpha < 0.004) state._v2gAlpha = 0;
  }

  function _v2gHeated() { state._v2gAlpha = Math.max(state._v2gAlpha, 0.2); }

  function _v2gDrawCanvas(nodes, links) {
    var size = _v2gCanvasSize(); if (!size) return;
    var ctx = size.ctx, W = size.w, H = size.h;
    var empty = document.getElementById('v2g-empty');
    var canvas = document.getElementById('v2g-canvas');
    var wrap = document.getElementById('v2g-canvas-wrap');
    if (!nodes || !nodes.length) {
      ctx.clearRect(0, 0, W, H);
      if (empty) { empty.style.display = 'flex'; empty.style.flexDirection = 'column'; }
      state._v2gPts = []; state._v2gEdges = []; state._v2gIds = {};
      state._v2gStructSig = ''; state._v2gLabelSig = '';
      if (state._v2gAnim) { cancelAnimationFrame(state._v2gAnim); state._v2gAnim = null; }
      return;
    }
    if (empty) empty.style.display = 'none';
    var nsIn = nodes.slice(0, state._v2gCap);
    // Dedupe by id before indexing. Server should already skip virtual
    // nodes whose id collides with a real entity (e.g. shipx as both
    // entity and belief object). Prefer non-virtual / higher beliefCount.
    var byId = {};
    nsIn.forEach(function(nd) {
      var id = nd && nd.id != null ? String(nd.id) : '';
      if (!id) return;
      var prev = byId[id];
      if (!prev) { byId[id] = nd; return; }
      var prevVirt = !!prev.isVirtual;
      var curVirt = !!nd.isVirtual;
      if (prevVirt && !curVirt) { byId[id] = nd; return; }
      if (!prevVirt && curVirt) return;
      if ((nd.beliefCount || 0) > (prev.beliefCount || 0)) byId[id] = nd;
    });
    var ns = Object.keys(byId).map(function(k) { return byId[k]; });
    function _nodeDisplay(nd) {
      var rawName = String(nd.name || nd.id || '').trim();
      var isUser = !!nd.isHub || String(nd.id || '').toLowerCase() === 'user';
      var display = rawName;
      if (isUser) {
        var low = rawName.toLowerCase();
        if (!rawName || low === 'user' || low === 'you' || low === 'me') display = 'You';
        // else keep branded name (Mubder, Kazma, …)
      }
      return { rawName: rawName, display: display, isUser: isUser };
    }
    var idKey = ns.map(function(nd) { return String(nd.id); }).join('\0');
    var labelKey = ns.map(function(nd) {
      var d = _nodeDisplay(nd);
      return String(nd.id) + '=' + d.display + (nd.isVirtual ? 'v' : '');
    }).join('|');
    var structSig = ns.length + ':' + ((links && links.length) || 0) + ':' + idKey.slice(0, 600);
    var labelSig = labelKey.slice(0, 1200);
    // Rename-only refresh: same topology, new display names — update labels
    // in place so the graph reflects list renames without resetting layout.
    if (structSig === state._v2gStructSig && state._v2gPts.length && labelSig !== state._v2gLabelSig) {
      var nameById = {};
      ns.forEach(function(nd) { nameById[nd.id] = nd; });
      state._v2gPts.forEach(function(p) {
        var nd = nameById[p.id];
        if (!nd) return;
        var d = _nodeDisplay(nd);
        p.name = d.rawName;
        p.label = d.display.slice(0, 22);
        p.fullLabel = d.display;
        p.isVirtual = !!nd.isVirtual;
        p.isHighStakes = !!nd.isHighStakes;
        p.type = nd.type || p.type;
      });
      state._v2gLabelSig = labelSig;
      if (state._v2gSelectedId) {
        for (var si = 0; si < state._v2gPts.length; si++) {
          if (state._v2gPts[si].id === state._v2gSelectedId) { _v2gInspect(state._v2gPts[si]); break; }
        }
      }
      _v2gRepaint();
    } else if (structSig !== state._v2gStructSig) {
      var keepSel = state._v2gSelectedId;
      var keepView = { scale: state._v2gView.scale, ox: state._v2gView.ox, oy: state._v2gView.oy };
      var hadLayout = state._v2gPts.length > 0;
      // Snapshot live positions before rebuild so a data refresh does not
      // fling user-arranged nodes back to the spiral layout.
      state._v2gPts.forEach(function(p) { _v2gRememberPos(p); });
      state._v2gStructSig = structSig;
      state._v2gLabelSig = labelSig;
      state._v2gIds = {};
      state._v2gPts = ns.map(function(nd, i) {
        state._v2gIds[nd.id] = i;
        var ang = i * 2.39996, r = 20 + (i % 6) * 24;
        var bc = nd.beliefCount || 1;
        var d = _nodeDisplay(nd);
        var rad = d.isUser ? 8 : r;
        var cached = state._v2gPosCache[nd.id];
        var x = W / 2 + Math.cos(ang) * rad;
        var y = H / 2 + Math.sin(ang) * rad;
        var pinned = false;
        if (cached && typeof cached.x === 'number' && typeof cached.y === 'number') {
          x = cached.x; y = cached.y;
          pinned = !!cached.pinned;
        }
        return {
          x: x, y: y,
          vx: 0, vy: 0, id: nd.id,
          name: d.rawName,
          label: d.display.slice(0, 22),
          fullLabel: d.display,
          type: nd.type || 'entity',
          isUser: d.isUser,
          isHub: !!nd.isHub || d.isUser,
          isHighStakes: !!nd.isHighStakes,
          r: (d.isUser ? state._v2gNodeBaseR + 4 : (nd.isEpisode ? state._v2gNodeBaseR - 1 : state._v2gNodeBaseR)) + Math.min(8, Math.sqrt(bc) * 1.5),
          isVirtual: !!nd.isVirtual,
          isEpisode: !!nd.isEpisode,
          pinned: pinned,
        };
      });
      state._v2gEdges = [];
      (links || []).forEach(function(l) {
        var ai = state._v2gIds[l.source];
        var bi = state._v2gIds[l.target];
        if (ai !== undefined && bi !== undefined) {
          state._v2gEdges.push({
            a: ai,
            b: bi,
            // Belief id (from graph API `id`) — required for edit/unlink
            beliefId: l.id || l.belief_id || null,
            label: String(l.label || l.predicate || '').slice(0, 18),
            fullLabel: String(l.label || l.predicate || ''),
            objectText: String(l.object_text || ''),
            sourceId: l.source,
            targetId: l.target,
            type: l.type || 'set',
            confidence: l.confidence || 0.5,
            superseded: !!l.superseded,
          });
        }
      });
      // Drop selected edge if topology rebuilt without it
      if (state._v2gOps.selectedEdgeIdx >= state._v2gEdges.length) state._v2gOps.selectedEdgeIdx = -1;
      // Keep pan/zoom if we already had a layout; only reset on first paint.
      if (hadLayout) {
        state._v2gView = keepView;
        // Mild reheat so free (unpinned) nodes settle around pinned anchors
        state._v2gAlpha = Math.max(state._v2gAlpha, 0.12);
      } else {
        state._v2gView = { scale: 1, ox: 0, oy: 0 };
        state._v2gAlpha = 1;
      }
      state._v2gSelectedId = keepSel && state._v2gIds[keepSel] !== undefined ? keepSel : null;
    }
    _v2gBindPointer(canvas, wrap);
    if (!state._v2gAnim) _v2gTick();
  }

  function _v2gTick() {
    var size = _v2gCanvasSize(); if (!size) { state._v2gAnim = null; return; }
    var ctx = size.ctx, W = size.w, H = size.h;
    if (state._v2gAlpha > 0) _v2gStep(W, H);
    _v2gPaint(ctx, W, H);
    // Keep a low-FPS idle loop for You/user breath + high-stakes pulse
    var idle = false;
    for (var i = 0; i < state._v2gPts.length; i++) {
      if (_v2gIsUser(state._v2gPts[i]) || state._v2gPts[i].isHighStakes) { idle = true; break; }
    }
    if (state._v2gAlpha > 0 || state._v2gDrag || idle) {
      state._v2gAnim = requestAnimationFrame(_v2gTick);
    } else {
      state._v2gAnim = null;
    }
  }

  export function _v2gRepaint() { if (!state._v2gAnim) state._v2gAnim = requestAnimationFrame(_v2gTick); }

  function _v2gPaint(ctx, W, H) {
    _v2gRefreshPalette();
    var theme = _v2gTheme();
    ctx.clearRect(0, 0, W, H);
    // Soft ambient glow (site accent)
    var amb = ctx.createRadialGradient(W * 0.5, H * 0.42, 8, W * 0.5, H * 0.5, Math.max(W, H) * 0.55);
    amb.addColorStop(0, _v2gHexAlpha(theme.accent, 0.07));
    amb.addColorStop(0.45, _v2gHexAlpha(theme.secondary, 0.03));
    amb.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = amb;
    ctx.fillRect(0, 0, W, H);

    // Edges — accent family, gradient along the link when connected to You
    // Wider hit targets via thicker stroke when zoomed out (pick still uses _v2gHitEdge)
    ctx.font = _v2gFont(10);
    for (var e = 0; e < state._v2gEdges.length; e++) {
      var ed = state._v2gEdges[e]; var A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
      if (!A || !B) continue;
      var ax = _v2gSX(A.x), ay = _v2gSY(A.y), bx = _v2gSX(B.x), by = _v2gSY(B.y);
      var hot = state._v2gSelectedId && (A.id === state._v2gSelectedId || B.id === state._v2gSelectedId);
      var edgeSelected = e === state._v2gOps.selectedEdgeIdx;
      var beliefHot = state._v2gHighlightSubj && state._v2gHighlightObj &&
        ((A.id === state._v2gHighlightSubj && B.id === state._v2gHighlightObj) ||
         (A.id === state._v2gHighlightObj && B.id === state._v2gHighlightSubj));
      var pcolor = _V2G_PRED_COLORS[ed.type] || theme.accent;
      var touchesUser = _v2gIsUser(A) || _v2gIsUser(B);
      ctx.lineCap = 'round';
      var pathHot = !!ed.pathHot || (state._v2gPathIds[A.id] && state._v2gPathIds[B.id]);
      // Source/target slot rings on edges between ops endpoints
      var opsEdge = (state._v2gOps.sourceId && state._v2gOps.targetId &&
        ((A.id === state._v2gOps.sourceId && B.id === state._v2gOps.targetId) ||
         (A.id === state._v2gOps.targetId && B.id === state._v2gOps.sourceId)));
      if (edgeSelected || beliefHot || pathHot) {
        ctx.strokeStyle = edgeSelected ? '#fbbf24' : theme.accentLight;
        ctx.lineWidth = edgeSelected ? 3.6 : (pathHot ? 3.0 : 3.4);
        ctx.shadowColor = _v2gHexAlpha(edgeSelected ? '#fbbf24' : theme.accent, 0.55); ctx.shadowBlur = 10;
      } else if (opsEdge) {
        ctx.strokeStyle = theme.user; ctx.lineWidth = 2.6;
        ctx.shadowColor = _v2gHexAlpha(theme.user, 0.45); ctx.shadowBlur = 8;
      } else if (hot) {
        ctx.strokeStyle = theme.accent; ctx.lineWidth = 2.3;
        ctx.shadowColor = _v2gHexAlpha(theme.accent, 0.4); ctx.shadowBlur = 8;
      } else if (ed.superseded) {
        ctx.strokeStyle = 'rgba(148,163,184,0.28)'; ctx.lineWidth = 1;
        ctx.shadowBlur = 0;
      } else {
        var grad = ctx.createLinearGradient(ax, ay, bx, by);
        if (touchesUser) {
          grad.addColorStop(0, _v2gHexAlpha(theme.user, 0.75));
          grad.addColorStop(1, _v2gHexAlpha(theme.accent, 0.55));
        } else {
          grad.addColorStop(0, _v2gHexAlpha(pcolor, 0.55));
          grad.addColorStop(1, _v2gHexAlpha(theme.accentLight, 0.4));
        }
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1 + (ed.confidence || 0.5) * 1.6;
        ctx.shadowBlur = 0;
      }
      if (ed.superseded) ctx.setLineDash([4, 3]); else ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
      ctx.setLineDash([]);
      ctx.shadowBlur = 0;
      // Arrowhead
      var ang = Math.atan2(by - ay, bx - ax);
      var ah = 6;
      ctx.beginPath();
      ctx.moveTo(bx, by);
      ctx.lineTo(bx - ah * Math.cos(ang - 0.4), by - ah * Math.sin(ang - 0.4));
      ctx.lineTo(bx - ah * Math.cos(ang + 0.4), by - ah * Math.sin(ang + 0.4));
      ctx.closePath();
      ctx.fillStyle = hot || beliefHot ? theme.accentLight : (ed.superseded ? 'rgba(148,163,184,0.4)' : _v2gHexAlpha(pcolor, 0.85));
      ctx.fill();
      // Label
      if (state._v2gView.scale > 0.7 && ed.label) {
        var mx = (ax + bx) / 2, my = (ay + by) / 2;
        var tw = ctx.measureText(ed.label).width;
        ctx.fillStyle = 'rgba(10,15,20,0.82)';
        ctx.beginPath();
        // rounded pill
        var px = mx - tw / 2 - 5, py = my - 9, pw = tw + 10, ph = 15, pr = 4;
        ctx.moveTo(px + pr, py); ctx.arcTo(px + pw, py, px + pw, py + ph, pr);
        ctx.arcTo(px + pw, py + ph, px, py + ph, pr); ctx.arcTo(px, py + ph, px, py, pr);
        ctx.arcTo(px, py, px + pw, py, pr); ctx.closePath(); ctx.fill();
        ctx.strokeStyle = _v2gHexAlpha(theme.accent, 0.2); ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = hot || beliefHot ? theme.accentLight : 'rgba(200,220,235,0.95)';
        ctx.textAlign = 'center'; ctx.fillText(ed.label, mx, my + 3);
      }
    }
    // Nodes
    var showLabels = state._v2gView.scale > 0.55;
    ctx.font = _v2gFont(11);
    var tNow = Date.now();
    for (var i = 0; i < state._v2gPts.length; i++) {
      var p = state._v2gPts[i];
      var x = _v2gSX(p.x), y = _v2gSY(p.y);
      var r = p.r;
      var isHover = (i === state._v2gHover), isSel = (p.id === state._v2gSelectedId);
      var isUser = _v2gIsUser(p);
      var onPath = !!state._v2gPathIds[p.id];
      if (isSel || isHover || onPath) r += 3;
      if (isUser) r += 1;
      var color = p.isEpisode ? _v2gHexAlpha(theme.secondary, 0.55) : _v2gNodeColor(p);
      // Soft outer glow
      var glowR = r + (isUser || onPath ? 10 : 7);
      var glow = ctx.createRadialGradient(x, y, r * 0.2, x, y, glowR);
      glow.addColorStop(0, _v2gHexAlpha(color, isUser || isSel || onPath ? 0.5 : 0.22));
      glow.addColorStop(1, _v2gHexAlpha(color, 0));
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(x, y, glowR, 0, 2 * Math.PI); ctx.fill();
      if (onPath) {
        ctx.beginPath(); ctx.arc(x, y, r + 6, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.accentLight; ctx.lineWidth = 2; ctx.stroke();
      }
      // High-stakes red halo (above glow)
      if (p.isHighStakes) {
        var pulse = 0.5 + 0.5 * Math.sin(tNow / 400);
        ctx.beginPath(); ctx.arc(x, y, r + 5 + pulse * 2, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(239,68,68,' + (0.4 + pulse * 0.3) + ')'; ctx.lineWidth = 2; ctx.stroke();
      }
      // User: gentle amber breath
      if (isUser) {
        var up = 0.5 + 0.5 * Math.sin(tNow / 650);
        ctx.beginPath(); ctx.arc(x, y, r + 5 + up * 2.5, 0, 2 * Math.PI);
        ctx.strokeStyle = _v2gHexAlpha(theme.user, 0.35 + up * 0.25); ctx.lineWidth = 2; ctx.stroke();
      }
      // Selected ring (accent)
      if (isSel) {
        ctx.beginPath(); ctx.arc(x, y, r + 5, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.accentLight; ctx.lineWidth = 2.2; ctx.stroke();
      }
      // Ops source / target rings (cyan = src, blue = tgt)
      if (state._v2gOps.sourceId && p.id === state._v2gOps.sourceId) {
        ctx.beginPath(); ctx.arc(x, y, r + 7, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.accent; ctx.lineWidth = 2.4; ctx.stroke();
      }
      if (state._v2gOps.targetId && p.id === state._v2gOps.targetId) {
        ctx.beginPath(); ctx.arc(x, y, r + 9, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.secondary; ctx.lineWidth = 2; ctx.setLineDash([3, 2]); ctx.stroke();
        ctx.setLineDash([]);
      }
      // Body — radial highlight for depth
      var body = ctx.createRadialGradient(x - r * 0.3, y - r * 0.35, 0, x, y, r);
      body.addColorStop(0, _v2gHexAlpha('#ffffff', isUser ? 0.45 : 0.28));
      body.addColorStop(0.35, color);
      body.addColorStop(1, _v2gHexAlpha(color, 0.85));
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = body;
      ctx.globalAlpha = p.isVirtual && !isSel && !isHover ? 0.72 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (p.isVirtual) {
        ctx.setLineDash([3, 2]);
        ctx.strokeStyle = _v2gHexAlpha(theme.accentLight, 0.55);
      } else {
        ctx.setLineDash([]);
        ctx.strokeStyle = isUser ? _v2gHexAlpha('#fff7ed', 0.55) : 'rgba(255,255,255,0.28)';
      }
      ctx.lineWidth = isUser ? 1.6 : 1;
      ctx.stroke();
      ctx.setLineDash([]);
      // User center mark
      if (isUser && r > 8) {
        ctx.beginPath(); ctx.arc(x, y, Math.max(2.2, r * 0.22), 0, 2 * Math.PI);
        ctx.fillStyle = 'rgba(255,255,255,0.9)'; ctx.fill();
      }
      if (showLabels) {
        var lab = p.label;
        // Canvas font: weight + size/family
        ctx.font = (isUser ? '600 ' : '') + _v2gFont(isUser ? 12 : 11);
        // subtle label plate for readability
        var lw = ctx.measureText(lab).width;
        ctx.fillStyle = 'rgba(10,15,20,0.55)';
        ctx.fillRect(x - lw / 2 - 4, y + r + 3, lw + 8, 14);
        ctx.fillStyle = isUser ? theme.user : '#e6edf3';
        ctx.textAlign = 'center';
        ctx.fillText(lab, x, y + r + 13);
      }
    }
  }

  function _v2gHit(sx, sy) {
    for (var i = state._v2gPts.length - 1; i >= 0; i--) {
      var p = state._v2gPts[i];
      var dx = sx - _v2gSX(p.x), dy = sy - _v2gSY(p.y);
      if (dx * dx + dy * dy < (p.r + 4) * (p.r + 4)) return i;
    }
    return -1;
  }

  /** Distance from point to segment; used for edge pick (edit/unlink). */
  function _v2gDistToSeg(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1, dy = y2 - y1;
    var len2 = dx * dx + dy * dy;
    if (len2 < 1e-6) {
      var d0x = px - x1, d0y = py - y1;
      return Math.sqrt(d0x * d0x + d0y * d0y);
    }
    var t = ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    var qx = x1 + t * dx, qy = y1 + t * dy;
    var ex = px - qx, ey = py - qy;
    return Math.sqrt(ex * ex + ey * ey);
  }

  function _v2gHitEdge(sx, sy) {
    // Generous hit area so edges are easy to pick for edit/unlink
    var best = -1, bestD = 14;
    for (var e = 0; e < state._v2gEdges.length; e++) {
      var ed = state._v2gEdges[e];
      var A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
      if (!A || !B) continue;
      var d = _v2gDistToSeg(sx, sy, _v2gSX(A.x), _v2gSY(A.y), _v2gSX(B.x), _v2gSY(B.y));
      if (d < bestD) { bestD = d; best = e; }
    }
    return best;
  }

  // ── Graph ops helpers (link / merge / unlink / edit) ─────────────

  export function _v2gToast(msg, type) {
    if (window.showToast) window.showToast(msg, type || 'info');
  }

  async function _v2gConfirm(opts) {
    try {
      if (typeof window.kazmaConfirm === 'function') {
        return !!(await window.kazmaConfirm(opts || {}));
      }
      // window.confirm may be async (overridden by stores.js) — always await
      var native = window._nativeConfirm || window.confirm;
      var res = native.call(window, (opts && (opts.message || opts.title)) || 'Confirm?');
      return !!(await Promise.resolve(res));
    } catch (e) {
      return false;
    }
  }

  async function _v2gPrompt(opts) {
    try {
      if (typeof window.kazmaPrompt === 'function') {
        return await window.kazmaPrompt(opts || {});
      }
      var native = window._nativePrompt || window.prompt;
      var res = native.call(
        window,
        (opts && opts.message) || '',
        (opts && opts.defaultValue) || ''
      );
      return await Promise.resolve(res);
    } catch (e) {
      return null;
    }
  }

  async function _v2gApiJson(url, opts) {
    opts = opts || {};
    var resp = await fetch(url, {
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        Accept: 'application/json',
      },
      method: opts.method || 'GET',
      body: opts.body != null ? opts.body : undefined,
    });
    var data = {};
    var text = '';
    try {
      text = await resp.text();
      data = text ? JSON.parse(text) : {};
    } catch (e) {
      data = { ok: false, error: text ? text.slice(0, 160) : ('HTTP ' + resp.status) };
    }
    if (!resp.ok && data.ok == null) data.ok = false;
    if (!resp.ok && !data.error) data.error = 'HTTP ' + resp.status;
    data._status = resp.status;
    return data;
  }

  function _v2gShortId(id) {
    var s = String(id || '');
    return s.length > 18 ? s.slice(0, 16) + '…' : s;
  }

  export function _v2gSyncOpsBar() {
    var srcEl = document.getElementById('v2g-ops-source');
    var tgtEl = document.getElementById('v2g-ops-target');
    var hint = document.getElementById('v2g-ops-hint');
    var linkBtn = document.getElementById('v2g-ops-link');
    var mergeBtn = document.getElementById('v2g-ops-merge');
    if (srcEl) {
      srcEl.textContent = 'src: ' + (state._v2gOps.sourceId ? _v2gShortId(state._v2gOps.sourceId) : '—');
      srcEl.title = state._v2gOps.sourceId || 'Source entity';
      srcEl.style.borderColor = state._v2gOps.sourceId ? 'var(--accent,#22d3ee)' : 'var(--border-subtle)';
    }
    if (tgtEl) {
      tgtEl.textContent = 'tgt: ' + (state._v2gOps.targetId ? _v2gShortId(state._v2gOps.targetId) : '—');
      tgtEl.title = state._v2gOps.targetId || 'Target entity';
      tgtEl.style.borderColor = state._v2gOps.targetId ? 'var(--secondary,#3b82f6)' : 'var(--border-subtle)';
    }
    if (hint) {
      if (state._v2gOps.mode === 'link') {
        hint.textContent = state._v2gOps.sourceId
          ? 'Link mode: click the target node…'
          : 'Link mode: click the source node…';
        hint.style.color = 'var(--accent,#22d3ee)';
      } else if (state._v2gOps.mode === 'merge') {
        hint.textContent = state._v2gOps.sourceId
          ? 'Merge mode: click the target (survivor)…'
          : 'Merge mode: click the source (will be retired)…';
        hint.style.color = 'var(--warning,#f59e0b)';
      } else if (state._v2gOps.sourceId && state._v2gOps.targetId) {
        hint.textContent = 'Ready — press Link or Merge, or click an edge to edit/unlink.';
        hint.style.color = 'var(--text-secondary)';
      } else {
        hint.textContent = 'Click node → inspect. Click edge → edit/unlink. Link: set src+tgt or use pick mode.';
        hint.style.color = 'var(--text-muted)';
      }
    }
    if (linkBtn) {
      linkBtn.classList.toggle('btn-primary', state._v2gOps.mode === 'link' || !!(!state._v2gOps.mode && state._v2gOps.sourceId && state._v2gOps.targetId));
      linkBtn.textContent = state._v2gOps.mode === 'link' ? 'Linking…' : 'Link';
    }
    if (mergeBtn) {
      mergeBtn.classList.toggle('btn-primary', state._v2gOps.mode === 'merge');
      mergeBtn.textContent = state._v2gOps.mode === 'merge' ? 'Merging…' : 'Merge';
    }
  }

  export function _v2gBroadcastSlots() {
    try {
      window.dispatchEvent(new CustomEvent('kazma:memory-ops-slots', {
        detail: {
          sourceId: state._v2gOps.sourceId,
          targetId: state._v2gOps.targetId,
          predicate: (document.getElementById('v2g-ops-predicate') || {}).value || 'related_to',
        },
      }));
    } catch (e) { /* ignore */ }
    _v2gSyncOpsBar();
    _v2gRepaint();
  }

  export function _v2gSetSlot(which, id, opts) {
    opts = opts || {};
    if (!id) return;
    if (which === 'source') state._v2gOps.sourceId = id;
    else if (which === 'target') state._v2gOps.targetId = id;
    if (!opts.silent) _v2gBroadcastSlots();
  }

  export function _v2gClearSlots(opts) {
    opts = opts || {};
    state._v2gOps.sourceId = null;
    state._v2gOps.targetId = null;
    state._v2gOps.mode = null;
    if (!opts.keepEdge) state._v2gOps.selectedEdgeIdx = -1;
    _v2gBroadcastSlots();
  }

  export function _v2gEnterMode(mode) {
    state._v2gOps.mode = mode;
    // Soft-start: if no source yet, wait for first click; if source set, wait for target
    _v2gSyncOpsBar();
    _v2gToast(
      mode === 'link'
        ? 'Link mode: click source, then target'
        : 'Merge mode: click source (retire), then target (keep)',
      'info'
    );
  }

  function state._v2gOpsPredicate() {
    var el = document.getElementById('v2g-ops-predicate');
    var p = el ? String(el.value || '').trim() : '';
    return p || 'related_to';
  }

  export async function _v2gReloadGraph() {
    state._v2gStructSig = '';
    state._v2gLabelSig = '';
    await _v2gLoad();
  }

  export async function _v2gDoLink(src, tgt, pred) {
    src = String(src || '').trim();
    tgt = String(tgt || '').trim();
    pred = String(pred || state._v2gOpsPredicate()).trim() || 'related_to';
    if (!src || !tgt) {
      _v2gToast('Set source and target first', 'error');
      return false;
    }
    if (src === tgt) {
      _v2gToast('Source and target must differ', 'error');
      return false;
    }
    try {
      var data = await _v2gApiJson('/api/memory/v2/entities/link', {
        method: 'POST',
        body: JSON.stringify({ subject: src, predicate: pred, object: tgt }),
      });
      if (!data.ok) {
        _v2gToast(data.error || 'Link failed', 'error');
        console.warn('[v2g] link failed', data);
        return false;
      }
      _v2gToast(
        (data.already ? 'Already linked · ' : 'Linked ') +
          _v2gShortId(data.subject || src) + ' —' + pred + '→ ' + _v2gShortId(data.object || tgt),
        'success'
      );
      state._v2gOps.mode = null;
      _v2gSyncOpsBar();
      await _v2gReloadGraph();
      _v2gSelectEntity(data.object || tgt, { notify: false });
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'link', source: data.subject || src, target: data.object || tgt, beliefId: data.belief_id },
        }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Link failed: ' + (err && err.message ? err.message : err), 'error');
      console.warn('[v2g] link exception', err);
      return false;
    }
  }

  export async function _v2gDoMerge(src, tgt) {
    src = String(src || '').trim();
    tgt = String(tgt || '').trim();
    if (!src || !tgt) {
      _v2gToast('Set source and target first', 'error');
      return false;
    }
    if (src === tgt) {
      _v2gToast('Source and target must differ', 'error');
      return false;
    }
    var ok = await _v2gConfirm({
      title: 'Merge entities',
      message: 'Merge ' + src + ' into ' + tgt + '?\nBeliefs rewire to the target; source is retired.',
    });
    if (!ok) return false;
    try {
      var resp = await fetch('/api/memory/v2/entities/merge', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ source_id: src, target_id: tgt }),
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        _v2gToast(data.error || 'Merge failed', 'error');
        return false;
      }
      _v2gToast('Merged ' + _v2gShortId(src) + ' → ' + _v2gShortId(tgt), 'success');
      state._v2gOps.sourceId = null;
      state._v2gOps.targetId = tgt;
      state._v2gOps.mode = null;
      _v2gBroadcastSlots();
      await _v2gReloadGraph();
      _v2gSelectEntity(tgt, { notify: false });
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'merge', source: src, target: tgt } }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Merge failed', 'error');
      return false;
    }
  }

  /** Build SPO seed from a canvas edge for unlink API. */
  function _v2gEdgeSeed(ed) {
    if (!ed) return {};
    var A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
    var pred = ed.fullLabel || ed.label || '';
    return {
      subject: (A && A.id) || ed.sourceId || '',
      predicate: pred,
      object: ed.objectText || (B && B.id) || ed.targetId || '',
      objectText: ed.objectText || '',
      sourceId: (A && A.id) || ed.sourceId || '',
      targetId: (B && B.id) || ed.targetId || '',
      label: pred,
      fullLabel: pred,
      beliefId: ed.beliefId || null,
      edgeIdx: typeof ed._idx === 'number' ? ed._idx : -1,
    };
  }

  /** All edges touching a node id (with neighbor + hub flags). */
  function state._v2gEdgesForNode(nodeId) {
    var out = [];
    if (!nodeId) return out;
    for (var i = 0; i < state._v2gEdges.length; i++) {
      var ed = state._v2gEdges[i];
      var A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
      if (!A || !B) continue;
      if (A.id !== nodeId && B.id !== nodeId) continue;
      var other = A.id === nodeId ? B : A;
      var outbound = A.id === nodeId;
      out.push({
        idx: i,
        ed: ed,
        other: other,
        outbound: outbound,
        toHub: _v2gIsHubNeighbor(other),
        seed: _v2gEdgeSeed(ed),
      });
    }
    return out;
  }

  /**
   * Unlink (soft-invalidate) a belief edge.
   * @param {string|null} beliefId
   * @param {{subject?:string,predicate?:string,object?:string,objectText?:string}} [seed]
   * @param {{skipConfirm?:boolean,skipReload?:boolean,silent?:boolean}} [opts]
   */
  export async function _v2gUnlinkBelief(beliefId, seed, opts) {
    seed = seed || {};
    opts = opts || {};
    var subject = String(seed.subject || seed.sourceId || '').trim();
    var predicate = String(seed.predicate || seed.label || seed.fullLabel || '').trim();
    var object = String(seed.object || seed.objectText || seed.targetId || '').trim();
    if (!beliefId) beliefId = seed.beliefId || null;
    if (!beliefId && !(subject && predicate && object)) {
      if (!opts.silent) _v2gToast('Cannot cut — missing belief id and edge triple', 'error');
      return false;
    }
    if (!opts.skipConfirm) {
      var ok = await _v2gConfirm({
        title: 'Cut connection',
        message:
          'Remove this edge from active memory?\n' +
          (subject && predicate
            ? subject + ' —' + predicate + '→ ' + object
            : 'Soft-invalidate (recoverable via Hygiene).'),
        confirmText: 'Cut',
        danger: true,
      });
      if (!ok) return false;
    }
    try {
      // Prefer unified unlink (id + SPO fallback) so missing belief ids still work
      var data = await _v2gApiJson('/api/memory/v2/entities/unlink', {
        method: 'POST',
        body: JSON.stringify({
          belief_id: beliefId || null,
          subject: subject || null,
          predicate: predicate || null,
          object: object || null,
          object_text: seed.objectText || object || null,
        }),
      });
      if (!data.ok) {
        // Legacy fallback: direct invalidate by id
        if (beliefId) {
          data = await _v2gApiJson(
            '/api/memory/v2/beliefs/' + encodeURIComponent(beliefId) + '/invalidate',
            { method: 'POST', body: '{}' }
          );
        }
      }
      if (!data.ok) {
        if (!opts.silent) {
          _v2gToast(data.error || 'Cut failed', 'error');
          console.warn('[v2g] unlink failed', data, { beliefId: beliefId, seed: seed });
        }
        return false;
      }
      if (!opts.silent && !opts.skipReload) {
        _v2gToast(data.already ? 'Already cut' : 'Connection cut', 'success');
      }
      if (!opts.skipReload) {
        state._v2gOps.selectedEdgeIdx = -1;
        var insp = document.getElementById('v2g-inspect');
        if (insp) {
          insp.innerHTML = '<span style="color:var(--text-muted);">Connection cut. Click a node or edge.</span>';
        }
        await _v2gReloadGraph();
        try {
          if (typeof loadV2Beliefs === 'function') {
            (graphCallbacks.loadV2Beliefs ? graphCallbacks.loadV2Beliefs((document.getElementById('v2-belief-search') : Promise.resolve()) || {}).value || '');
          }
        } catch (e) { /* optional */ }
        try {
          window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
            detail: { op: 'unlink', beliefId: data.belief_id || beliefId },
          }));
        } catch (e2) { /* ignore */ }
      }
      return true;
    } catch (err) {
      if (!opts.silent) {
        _v2gToast('Cut failed: ' + (err && err.message ? err.message : err), 'error');
        console.warn('[v2g] unlink exception', err);
      }
      return false;
    }
  }

  /**
   * Cut several edges after a single confirm (hub-shortcut cleanups).
   * @param {Array<{beliefId?:string,seed?:object,ed?:object}>} items
   */
  export async function _v2gCutEdges(items, opts) {
    opts = opts || {};
    items = (items || []).filter(Boolean);
    if (!items.length) {
      _v2gToast('No edges to cut', 'info');
      return 0;
    }
    var msg =
      opts.message ||
      ('Cut ' + items.length + ' connection' + (items.length > 1 ? 's' : '') +
        ' from active memory?');
    var ok = await _v2gConfirm({
      title: opts.title || 'Cut connections',
      message: msg,
      confirmText: opts.confirmText || ('Cut ' + items.length),
      danger: true,
    });
    if (!ok) return 0;
    var n = 0;
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var seed = it.seed || (it.ed ? _v2gEdgeSeed(it.ed) : it);
      var bid = it.beliefId || seed.beliefId || (it.ed && it.ed.beliefId) || null;
      var done = await _v2gUnlinkBelief(bid, seed, {
        skipConfirm: true,
        skipReload: true,
        silent: true,
      });
      if (done) n++;
    }
    if (n > 0) {
      _v2gToast('Cut ' + n + ' connection' + (n > 1 ? 's' : ''), 'success');
      state._v2gOps.selectedEdgeIdx = -1;
      await _v2gReloadGraph();
      try {
        if (typeof loadV2Beliefs === 'function') {
          (graphCallbacks.loadV2Beliefs ? graphCallbacks.loadV2Beliefs((document.getElementById('v2-belief-search') : Promise.resolve()) || {}).value || '');
        }
      } catch (e) { /* optional */ }
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'unlink-batch', count: n },
        }));
      } catch (e2) { /* ignore */ }
    } else {
      _v2gToast('No edges were cut', 'error');
    }
    return n;
  }

  /** Cut every direct edge between this node and the memory hub (You/Mubder). */
  export async function _v2gCutHubLinks(nodeId) {
    var edges = state._v2gEdgesForNode(nodeId).filter(function(x) { return x.toHub; });
    if (!edges.length) {
      _v2gToast('No direct hub link on this node', 'info');
      return 0;
    }
    var otherLinks = state._v2gEdgesForNode(nodeId).filter(function(x) { return !x.toHub; });
    var hint = otherLinks.length
      ? '\n\nKeeps links to: ' +
        otherLinks
          .map(function(x) { return _v2gDisplayName(x.other); })
          .slice(0, 6)
          .join(', ') +
        (otherLinks.length > 6 ? '…' : '') +
        '\nUseful when the chain should be leaf → parent → hub (not leaf → hub).'
      : '';
    return _v2gCutEdges(
      edges.map(function(x) {
        return { beliefId: x.ed.beliefId, seed: x.seed, ed: x.ed };
      }),
      {
        title: 'Cut hub shortcut',
        message:
          'Remove ' +
          edges.length +
          ' direct link' +
          (edges.length > 1 ? 's' : '') +
          ' to the hub (You/Mubder)?' +
          hint,
        confirmText: 'Cut hub link' + (edges.length > 1 ? 's' : ''),
      }
    );
  }

  export async function _v2gEditBeliefById(beliefId, seed) {
    seed = seed || {};
    if (!beliefId) {
      _v2gToast('No belief id — cannot edit', 'error');
      return false;
    }
    // Prefer live detail so we edit the current triple
    var b = {
      id: beliefId,
      subject: seed.subject || '',
      predicate: seed.predicate || seed.label || '',
      object: seed.object || seed.objectText || '',
    };
    try {
      var r0 = await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId), {
        credentials: 'same-origin',
      });
      var d0 = await r0.json().catch(function() { return {}; });
      if (d0.ok && d0.belief) {
        b.subject = d0.belief.subject || b.subject;
        b.predicate = d0.belief.predicate || b.predicate;
        b.object = d0.belief.object || b.object;
      }
    } catch (e) { /* use seed */ }

    var object = await _v2gPrompt({
      title: 'Edit belief — object',
      message: 'Fact / object text. Cancel aborts.',
      defaultValue: b.object || '',
      confirmText: 'Next',
      placeholder: 'e.g. Paris',
    });
    if (object == null) return false;
    var predicate = await _v2gPrompt({
      title: 'Edit belief — predicate',
      message: 'Relation name (snake_case ok).',
      defaultValue: b.predicate || '',
      confirmText: 'Next',
      placeholder: 'lives_in',
    });
    if (predicate == null) return false;
    var subject = await _v2gPrompt({
      title: 'Edit belief — subject',
      message: 'Subject entity id.',
      defaultValue: b.subject || '',
      confirmText: 'Save',
      placeholder: 'user',
    });
    if (subject == null) return false;
    subject = String(subject).trim();
    predicate = String(predicate).trim();
    object = String(object).trim();
    if (!subject || !predicate || !object) {
      _v2gToast('Subject, predicate, and object are required', 'error');
      return false;
    }
    if (subject === b.subject && predicate === b.predicate && object === b.object) return false;
    try {
      var resp = await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId), {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ subject: subject, predicate: predicate, object: object }),
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        _v2gToast(data.error || 'Edit failed', 'error');
        return false;
      }
      _v2gToast('Belief updated', 'success');
      state._v2gOps.selectedEdgeIdx = -1;
      await _v2gReloadGraph();
      try {
        if (typeof loadV2Beliefs === 'function') (graphCallbacks.loadV2Beliefs ? graphCallbacks.loadV2Beliefs((document.getElementById('v2-belief-search') : Promise.resolve()) || {}).value || '');
      } catch (e2) { /* optional */ }
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'edit', beliefId: beliefId, subject: subject, object: object },
        }));
      } catch (e3) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Edit failed', 'error');
      return false;
    }
  }

  async function _v2gDeleteEntity(id) {
    if (!id || id === 'user') {
      _v2gToast('Cannot delete protected hub', 'error');
      return false;
    }
    var ok = await _v2gConfirm({
      title: 'Delete entity',
      message: 'Delete entity shell “' + id + '”? (Protected / non-empty may fail.)',
    });
    if (!ok) return false;
    try {
      var resp = await fetch('/api/memory/v2/entities/' + encodeURIComponent(id), {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        _v2gToast(data.error || 'Delete failed', 'error');
        return false;
      }
      _v2gToast('Deleted ' + _v2gShortId(id), 'success');
      if (state._v2gOps.sourceId === id) state._v2gOps.sourceId = null;
      if (state._v2gOps.targetId === id) state._v2gOps.targetId = null;
      _v2gBroadcastSlots();
      await _v2gReloadGraph();
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'delete', id: id } }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Delete failed', 'error');
      return false;
    }
  }

  export function _v2gHandleNodePick(p) {
    if (!p || p.isEpisode) return false;
    if (!state._v2gOps.mode) return false;
    if (!state._v2gOps.sourceId) {
      _v2gSetSlot('source', p.id);
      _v2gSyncOpsBar();
      return true;
    }
    if (p.id === state._v2gOps.sourceId) {
      _v2gToast('Pick a different node as target', 'info');
      return true;
    }
    _v2gSetSlot('target', p.id);
    var mode = state._v2gOps.mode;
    state._v2gOps.mode = null;
    _v2gSyncOpsBar();
    if (mode === 'link') {
      _v2gDoLink(state._v2gOps.sourceId, state._v2gOps.targetId, state._v2gOpsPredicate());
    } else if (mode === 'merge') {
      _v2gDoMerge(state._v2gOps.sourceId, state._v2gOps.targetId);
    }
    return true;
  }

  export function _v2gInspectEdge(edgeIdx) {
    var el = document.getElementById('v2g-inspect');
    if (!el || edgeIdx < 0 || !state._v2gEdges[edgeIdx]) return;
    var ed = state._v2gEdges[edgeIdx];
    var A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
    if (!A || !B) return;
    state._v2gOps.selectedEdgeIdx = edgeIdx;
    state._v2gSelectedId = null;
    state._v2gHighlightSubj = A.id;
    state._v2gHighlightObj = B.id;
    var pcolor = _V2G_PRED_COLORS[ed.type] || _v2gTheme().accent;
    var pred = ed.fullLabel || ed.label || '';
    var html = '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:6px;">';
    html += '<div style="font-weight:700;font-size:0.8rem;color:#fbbf24;word-break:break-word;flex:1;">Edge · belief</div>';
    html += '</div>';
    html += '<div style="font-size:0.74rem;line-height:1.45;margin-bottom:8px;color:var(--text-primary);">';
    html += '<b>' + _v2gEsc(_v2gDisplayName(A)) + '</b> ';
    html += '<span style="color:' + pcolor + ';">' + _v2gEsc(String(pred).replace(/_/g, ' ')) + '</span> ';
    html += '<b>' + _v2gEsc(_v2gDisplayName(B)) + '</b>';
    html += '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.65rem;margin-bottom:8px;font-family:var(--font-mono);">';
    if (ed.beliefId) html += 'id: ' + _v2gEsc(String(ed.beliefId).slice(0, 20));
    else html += 'id: (missing — unlink may fail)';
    html += ' · ' + _v2gEsc(ed.type || '?') + ' · conf ' + Math.round((ed.confidence || 0) * 100) + '%';
    if (ed.superseded) html += ' · superseded';
    html += '</div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">';
    html += '<button type="button" class="btn btn-sm btn-danger v2g-edge-act" data-act="unlink" style="font-size:0.65rem;padding:2px 8px;" title="Cut this edge">Cut</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="edit" style="font-size:0.65rem;padding:2px 8px;">Edit</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="src" style="font-size:0.65rem;padding:2px 8px;">Src←A</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="tgt" style="font-size:0.65rem;padding:2px 8px;">Tgt→B</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="list" style="font-size:0.65rem;padding:2px 8px;">In list</button>';
    html += '</div>';
    // Stash edge triple on the panel for delegated clicks (survives re-renders better)
    el.setAttribute('data-edge-belief-id', ed.beliefId || '');
    el.setAttribute('data-edge-subject', A.id || '');
    el.setAttribute('data-edge-target', B.id || '');
    el.setAttribute('data-edge-predicate', pred || '');
    el.setAttribute('data-edge-object', ed.objectText || B.id || '');
    el.innerHTML = html;
    el.querySelectorAll('.v2g-edge-act').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var act = btn.getAttribute('data-act');
        var seed = {
          subject: A.id,
          predicate: pred,
          object: ed.objectText || B.id,
          objectText: ed.objectText,
          sourceId: A.id,
          targetId: B.id,
          label: pred,
          fullLabel: pred,
        };
        if (act === 'edit') {
          _v2gEditBeliefById(ed.beliefId, seed);
        } else if (act === 'unlink') {
          _v2gUnlinkBelief(ed.beliefId, seed);
        } else if (act === 'src') {
          _v2gSetSlot('source', A.id);
        } else if (act === 'tgt') {
          _v2gSetSlot('target', B.id);
        } else if (act === 'list') {
          _v2gNotifyList({
            type: 'belief',
            id: ed.beliefId,
            subject: A.id,
            object: ed.objectText || B.id,
            scrollOps: true,
          });
        }
      });
    });
    _v2gRepaint();
  }

  export function _v2gBindPointer(canvas, wrap) {
    if (canvas._v2gBound) return; canvas._v2gBound = true;
    function evToCanvas(ev) {
      var rect = canvas.getBoundingClientRect();
      return { sx: ev.clientX - rect.left, sy: ev.clientY - rect.top };
    }
    canvas.addEventListener('pointerdown', function(ev) {
      var c = evToCanvas(ev); var idx = _v2gHit(c.sx, c.sy);
      if (idx >= 0) {
        var p = state._v2gPts[idx];
        // Link/merge pick mode: first/second node click assigns slots
        if (state._v2gOps.mode && !p.isEpisode) {
          state._v2gSelectedId = p.id;
          state._v2gOps.selectedEdgeIdx = -1;
          state._v2gHighlightSubj = null; state._v2gHighlightObj = null;
          _v2gInspect(p);
          _v2gHandleNodePick(p);
          canvas.setPointerCapture(ev.pointerId);
          canvas.style.cursor = 'pointer';
          _v2gHeated(); _v2gRepaint();
          return;
        }
        // Shift+click: soft-pick into source/target without a formal mode
        if (ev.shiftKey && !p.isEpisode) {
          if (!state._v2gOps.sourceId || (state._v2gOps.sourceId && state._v2gOps.targetId)) {
            state._v2gOps.targetId = null;
            _v2gSetSlot('source', p.id);
          } else {
            _v2gSetSlot('target', p.id);
          }
        }
        state._v2gDrag = {
          idx: idx,
          wx: _v2gWX(c.sx) - p.x,
          wy: _v2gWY(c.sy) - p.y,
          sx0: c.sx,
          sy0: c.sy,
          moved: false,
        };
        state._v2gSelectedId = p.id; _v2gInspect(p); canvas.setPointerCapture(ev.pointerId);
        canvas.style.cursor = 'grabbing';
        // Clear belief-click highlight when selecting a node directly
        state._v2gHighlightSubj = null; state._v2gHighlightObj = null;
        state._v2gOps.selectedEdgeIdx = -1;
        // Do NOT jump the page to the entities list on single click —
        // that blocks free explore/drag. List sync is double-click only.
      } else {
        // Prefer edge hit when not on a node (edit/unlink without leaving graph)
        var eidx = _v2gHitEdge(c.sx, c.sy);
        if (eidx >= 0) {
          _v2gInspectEdge(eidx);
          canvas.setPointerCapture(ev.pointerId);
          canvas.style.cursor = 'pointer';
          _v2gHeated(); _v2gRepaint();
          return;
        }
        state._v2gDrag = { pan: true, sx: c.sx, sy: c.sy, ox: state._v2gView.ox, oy: state._v2gView.oy };
        canvas.style.cursor = 'grabbing';
        // Clear selection + belief highlight on empty-space click
        state._v2gSelectedId = null; state._v2gHighlightSubj = null; state._v2gHighlightObj = null;
        state._v2gOps.selectedEdgeIdx = -1;
      }
      // Heat lightly so free nodes can settle; pinned nodes stay fixed.
      _v2gHeated(); _v2gRepaint();
    });
    canvas.addEventListener('pointermove', function(ev) {
      var c = evToCanvas(ev);
      if (state._v2gDrag) {
        if (state._v2gDrag.pan) {
          state._v2gView.ox = state._v2gDrag.ox + (c.sx - state._v2gDrag.sx);
          state._v2gView.oy = state._v2gDrag.oy + (c.sy - state._v2gDrag.sy);
        } else {
          var p = state._v2gPts[state._v2gDrag.idx];
          if (p) {
            if (Math.abs(c.sx - state._v2gDrag.sx0) + Math.abs(c.sy - state._v2gDrag.sy0) > 3) {
              state._v2gDrag.moved = true;
            }
            p.x = _v2gWX(c.sx) - state._v2gDrag.wx; p.y = _v2gWY(c.sy) - state._v2gDrag.wy;
            p.vx = 0; p.vy = 0;
          }
        }
        _v2gRepaint();
      } else {
        var idx = _v2gHit(c.sx, c.sy);
        var eHover = idx < 0 ? _v2gHitEdge(c.sx, c.sy) : -1;
        if (idx !== state._v2gHover) { state._v2gHover = idx; _v2gRepaint(); }
        canvas.style.cursor = (idx >= 0 || eHover >= 0) ? 'pointer' : 'grab';
        var tip = document.getElementById('v2g-tooltip');
        if (idx >= 0 && tip) {
          var p = state._v2gPts[idx];
          var tc = _v2gNodeColor(p);
          var tLabel = _v2gTitle(_v2gDisplayName(p));
          var modeHint = '';
          if (state._v2gOps.mode === 'link') modeHint = '<br><span style="color:var(--accent);">link pick</span>';
          else if (state._v2gOps.mode === 'merge') modeHint = '<br><span style="color:var(--warning);">merge pick</span>';
          tip.innerHTML = '<b style="color:' + tc + ';word-break:break-word;">' + _v2gEsc(tLabel) + '</b><br><span style="color:var(--text-muted);">' +
            (_v2gIsUser(p) ? 'you · center of memory' : ('type: ' + p.type)) +
            (p.isHighStakes ? ' · ⚠ high-stakes' : '') +
            (p.isVirtual ? ' · fact' : '') +
            (p.id && _v2gDisplayName(p) !== p.id ? ' · id: ' + _v2gEsc(String(p.id).slice(0, 24)) : '') +
            '</span>' + modeHint;
          tip.style.display = 'block';
          tip.style.borderColor = _v2gHexAlpha(tc, 0.35);
          var rect = canvas.getBoundingClientRect();
          tip.style.left = Math.min(c.sx + 12, rect.width - 200) + 'px';
          tip.style.top = (c.sy + 12) + 'px';
        } else if (eHover >= 0 && tip) {
          var edh = state._v2gEdges[eHover];
          var Ah = state._v2gPts[edh.a], Bh = state._v2gPts[edh.b];
          tip.innerHTML = '<b style="color:#fbbf24;">' + _v2gEsc((edh.fullLabel || edh.label || 'edge').replace(/_/g, ' ')) + '</b><br>' +
            '<span style="color:var(--text-muted);">' +
            _v2gEsc(Ah ? _v2gDisplayName(Ah) : '?') + ' → ' + _v2gEsc(Bh ? _v2gDisplayName(Bh) : '?') +
            '</span><br><span style="color:var(--text-muted);font-size:0.68rem;">click to edit / unlink</span>';
          tip.style.display = 'block';
          tip.style.borderColor = 'rgba(251,191,36,0.4)';
          var rect2 = canvas.getBoundingClientRect();
          tip.style.left = Math.min(c.sx + 12, rect2.width - 200) + 'px';
          tip.style.top = (c.sy + 12) + 'px';
        } else if (tip) { tip.style.display = 'none'; }
      }
    });
    canvas.addEventListener('pointerup', function(ev) {
      // After a real drag, pin the node so physics / refresh cannot yank it back.
      if (state._v2gDrag && !state._v2gDrag.pan && state._v2gDrag.moved && state._v2gPts[state._v2gDrag.idx]) {
        var placed = state._v2gPts[state._v2gDrag.idx];
        placed.pinned = true;
        placed.vx = 0; placed.vy = 0;
        _v2gRememberPos(placed);
      }
      state._v2gDrag = null; canvas.style.cursor = 'grab'; _v2gRepaint();
    });
    canvas.addEventListener('pointercancel', function() { state._v2gDrag = null; });
    canvas.addEventListener('wheel', function(ev) {
      ev.preventDefault(); var c = evToCanvas(ev);
      var factor = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
      var ns = Math.max(state._v2gMinScale, Math.min(state._v2gMaxScale, state._v2gView.scale * factor));
      if (ns === state._v2gView.scale) return;
      var wx = _v2gWX(c.sx), wy = _v2gWY(c.sy);
      state._v2gView.scale = ns; state._v2gView.ox = c.sx - wx * ns; state._v2gView.oy = c.sy - wy * ns;
      _v2gRepaint();
    }, { passive: false });
    canvas.addEventListener('dblclick', function(ev) {
      var c = evToCanvas(ev); var idx = _v2gHit(c.sx, c.sy);
      if (idx < 0) return;
      var p = state._v2gPts[idx];
      state._v2gSelectedId = p.id;
      _v2gInspect(p);
      // Double-click: jump to the matching row in the entities/beliefs list.
      // Single-click deliberately does not (keeps free explore + drag).
      if (!p.isEpisode) {
        _v2gNotifyList({
          type: 'entity',
          id: p.id,
          name: _v2gDisplayName(p),
          scrollOps: true,
        });
        try {
          var ops = document.getElementById('mem-tab-entities');
          if (ops) ops.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (e) { /* ignore */ }
      }
      _v2gRepaint();
    });
  }

  function _v2gEsc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  // Short heading for a node: the first " - " segment, or the first ~56
  // chars. Long belief texts ("Repository: X - Description: ...") get a
  // readable title instead of dumping the whole blob into the header.
  function _v2gTitle(s) {
    var t = String(s || '').trim();
    if (!t) return '';
    if (t.length <= 56) return t;
    var seg = t.split(' - ')[0].trim();
    if (seg && seg.length <= 56) return seg;
    var cut = t.slice(0, 56).replace(/\s+\S*$/, '');
    return (cut || t.slice(0, 56)) + '…';
  }

  // Full contents of a belief text, split into readable lines on " - ".
  function _v2gContents(s) {
    var t = String(s || '').trim();
    if (!t) return '';
    var segs = t.split(' - ');
    if (segs.length > 1) {
      return segs.map(function(seg) {
        var html = _v2gEsc(seg.trim());
        return '<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04);line-height:1.4;">' + html + '</div>';
      }).join('');
    }
    return '<div style="line-height:1.45;">' + _v2gEsc(t) + '</div>';
  }

  // Truncate a long neighbor label in belief rows so the selected node's
  // full text is not repeated next to itself (the full text lives in the
  // Contents block above).
  function _v2gShortLabel(s, n) {
    var t = String(s || '').trim();
    if (!t) return '';
    var lim = n || 64;
    return t.length > lim ? t.slice(0, lim) + '…' : t;
  }

  export function _v2gInspect(p) {
    var el = document.getElementById('v2g-inspect');
    if (!el || !p) return;
    try {
    _v2gRefreshPalette();
    state._v2gOps.selectedEdgeIdx = -1;
    var color = _v2gNodeColor(p);
    var fullName = _v2gDisplayName(p);
    var title = _v2gTitle(fullName);
    var html = '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:4px;">';
    html += '<div style="color:' + color + ';font-weight:700;font-size:0.82rem;word-break:break-word;flex:1;min-width:0;">' + _v2gEsc(title) + '</div>';
    html += '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.68rem;margin-bottom:6px;">';
    html += _v2gIsUser(p) ? 'you · memory hub' : ('type: ' + p.type);
    if (p.id) html += ' · id: <code style="font-size:0.65rem;">' + _v2gEsc(String(p.id)) + '</code>';
    if (p.isHighStakes) html += ' · <span style="color:#ef4444;">⚠ high-stakes</span>';
    if (p.isVirtual) html += ' · fact node';
    html += '</div>';
    // Collect connections once for cut-hub + list UI
    var nodeEdges = state._v2gEdgesForNode(p.id);
    var hubEdges = nodeEdges.filter(function(x) { return x.toHub; });
    var nonHubEdges = nodeEdges.filter(function(x) { return !x.toHub; });
    // Hub shortcut: leaf linked to hub AND to another node (should be leaf→parent→hub)
    var hubShortcut = !_v2gIsUser(p) && hubEdges.length > 0 && nonHubEdges.length > 0;

    // Node ops — always show Cut when there is anything to cut
    if (!p.isEpisode) {
      html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;">';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="src" style="font-size:0.65rem;padding:2px 8px;" title="Set as link/merge source">Src</button>';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="tgt" style="font-size:0.65rem;padding:2px 8px;" title="Set as link/merge target">Tgt</button>';
      html += '<button type="button" class="btn btn-sm btn-primary v2g-node-act" data-act="link-from" style="font-size:0.65rem;padding:2px 8px;" title="Start link from this node — click target next">Link→</button>';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="merge-from" style="font-size:0.65rem;padding:2px 8px;" title="Start merge from this node (will be retired)">Merge→</button>';
      // Always offer Cut hub when any hub edge exists (even without "shortcut" pattern)
      if (hubEdges.length && !_v2gIsUser(p)) {
        html += '<button type="button" class="btn btn-sm btn-danger v2g-node-act" data-act="cut-hub" style="font-size:0.65rem;padding:2px 8px;" title="Remove direct link(s) to You/Mubder — keep parent chain">Cut hub</button>';
      }
      if (nodeEdges.length >= 1 && !_v2gIsUser(p)) {
        html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="cut-all" style="font-size:0.65rem;padding:2px 8px;" title="Cut every edge on this node">Cut all (' + nodeEdges.length + ')</button>';
      }
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="rename" style="font-size:0.65rem;padding:2px 8px;" title="Change display name (id stays the same)">Rename</button>';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="list" style="font-size:0.65rem;padding:2px 8px;" title="Highlight in entities list">In list</button>';
      if (!_v2gIsUser(p) && !p.isVirtual) {
        html += '<button type="button" class="btn btn-sm btn-danger v2g-node-act" data-act="delete" style="font-size:0.65rem;padding:2px 8px;" title="Delete empty entity shell">Del</button>';
      }
      html += '</div>';
    }

    // Banner: true shortcut (hub + other), OR softer note when only hub-linked
    if (hubShortcut) {
      var parentNames = nonHubEdges
        .map(function(x) { return _v2gDisplayName(x.other); })
        .filter(Boolean);
      var uniqParents = [];
      parentNames.forEach(function(n) {
        if (uniqParents.indexOf(n) < 0) uniqParents.push(n);
      });
      html += '<div style="margin-bottom:8px;padding:8px 10px;border-radius:8px;border:1px solid rgba(245,158,11,0.45);background:rgba(245,158,11,0.12);font-size:0.7rem;line-height:1.4;color:#fcd34d;">';
      html += '<strong style="color:#fbbf24;">Hub shortcut</strong> — direct link to hub while also linked to ';
      html += '<b>' + _v2gEsc(uniqParents.slice(0, 4).join(', ')) + (uniqParents.length > 4 ? '…' : '') + '</b>.';
      html += '<div style="margin-top:4px;color:var(--text-muted);font-size:0.65rem;">Preferred: this → parent → hub (You/Mubder). Use <b>Cut hub</b> to drop the shortcut edge.</div>';
      html += '<button type="button" class="btn btn-sm btn-danger v2g-node-act" data-act="cut-hub" style="margin-top:6px;font-size:0.68rem;padding:3px 10px;">Cut hub link' + (hubEdges.length > 1 ? 's' : '') + '</button>';
      html += '</div>';
    } else if (hubEdges.length && !_v2gIsUser(p) && nonHubEdges.length === 0) {
      html += '<div style="margin-bottom:8px;padding:6px 10px;border-radius:8px;border:1px solid var(--border-subtle);background:rgba(255,255,255,0.03);font-size:0.68rem;color:var(--text-muted);">Linked only to hub. Use <b style="color:#f87171;">Cut</b> on the connection row to detach.</div>';
    }

    // Contents — the full text of this belief/entity, shown exactly once.
    if (fullName !== title) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">Contents</div>';
      html += '<div style="background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);border-radius:6px;padding:6px 8px;font-size:0.72rem;color:var(--text-secondary);word-break:break-word;max-height:200px;overflow-y:auto;margin-bottom:8px;">' + _v2gContents(fullName) + '</div>';
    }
    // Connections — one Cut per neighbor (easy topology cleanup)
    if (nodeEdges.length) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">Connections (' + nodeEdges.length + ') · Cut to detach</div>';
      html += '<div style="display:flex;flex-direction:column;gap:3px;max-height:260px;overflow-y:auto;">';
      for (var r = 0; r < nodeEdges.length; r++) {
        var row = nodeEdges[r];
        var ed = row.ed;
        var pcolor = _V2G_PRED_COLORS[ed.type] || _v2gTheme().accent;
        var predLabel = _v2gEsc((ed.fullLabel || ed.label || '').replace(/_/g, ' '));
        var neighLabel = _v2gEsc(_v2gShortLabel(_v2gDisplayName(row.other), 48));
        var dir = row.outbound ? '→' : '←';
        var hubBadge = row.toHub
          ? ' <span style="font-size:0.58rem;padding:1px 5px;border-radius:3px;background:rgba(245,158,11,0.2);color:#fbbf24;">hub</span>'
          : '';
        var rowBg = row.toHub && hubShortcut ? 'rgba(245,158,11,0.1)' : 'transparent';
        var rowBorder = row.toHub && hubShortcut ? 'rgba(245,158,11,0.35)' : 'transparent';
        html += '<div class="v2g-belief-row" data-edge-idx="' + row.idx + '" style="color:var(--text-secondary);line-height:1.35;font-size:0.72rem;word-break:break-word;padding:5px 6px;border-radius:6px;cursor:pointer;border:1px solid ' + rowBorder + ';background:' + rowBg + ';" onmouseover="this.style.background=\'rgba(251,191,36,0.1)\'" onmouseout="this.style.background=\'' + rowBg + '\'">';
        html += '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:6px;">';
        html += '<div style="min-width:0;flex:1;">';
        html += '<span style="color:var(--text-muted);">' + dir + '</span> <b>' + neighLabel + '</b>' + hubBadge;
        html += '<div style="font-size:0.62rem;color:var(--text-muted);margin-top:1px;"><span style="color:' + pcolor + ';">' + predLabel + '</span>' +
          (ed.superseded ? ' · superseded' : '') + '</div>';
        html += '</div>';
        html += '<div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0;" onclick="event.stopPropagation()">';
        html += '<button type="button" class="btn btn-sm btn-danger v2g-rel-act" data-act="cut" data-edge-idx="' + row.idx + '" style="font-size:0.62rem;padding:2px 8px;" title="Cut this edge only">Cut</button>';
        html += '<button type="button" class="btn btn-sm btn-secondary v2g-rel-act" data-act="edit" data-edge-idx="' + row.idx + '" style="font-size:0.6rem;padding:1px 6px;">Edit</button>';
        html += '</div></div></div>';
      }
      html += '</div>';
    } else {
      html += '<div style="color:var(--text-muted);font-size:0.7rem;">No direct beliefs — set as Src and Link→ another node to connect it.</div>';
    }
    el.innerHTML = html;

    el.querySelectorAll('.v2g-node-act').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var act = btn.getAttribute('data-act');
        if (act === 'src') {
          _v2gSetSlot('source', p.id);
          _v2gToast('Source = ' + _v2gShortId(p.id), 'info');
        } else if (act === 'tgt') {
          _v2gSetSlot('target', p.id);
          _v2gToast('Target = ' + _v2gShortId(p.id), 'info');
        } else if (act === 'link-from') {
          state._v2gOps.sourceId = p.id;
          state._v2gOps.targetId = null;
          state._v2gOps.mode = 'link';
          _v2gBroadcastSlots();
          _v2gToast('Link from ' + _v2gShortId(p.id) + ' — click target on graph', 'info');
        } else if (act === 'merge-from') {
          state._v2gOps.sourceId = p.id;
          state._v2gOps.targetId = null;
          state._v2gOps.mode = 'merge';
          _v2gBroadcastSlots();
          _v2gToast('Merge from ' + _v2gShortId(p.id) + ' — click survivor target', 'info');
        } else if (act === 'cut-hub') {
          _v2gCutHubLinks(p.id).then(function() {
            // Re-inspect node after graph reload if still present
            var idx = _v2gFindNodeIndex(p.id);
            if (idx >= 0) _v2gInspect(state._v2gPts[idx]);
          });
        } else if (act === 'cut-all') {
          var all = state._v2gEdgesForNode(p.id);
          _v2gCutEdges(
            all.map(function(x) {
              return { beliefId: x.ed.beliefId, seed: x.seed, ed: x.ed };
            }),
            {
              title: 'Cut all connections',
              message:
                'Detach “' +
                _v2gDisplayName(p) +
                '” from all ' +
                all.length +
                ' neighbor(s)? Node shell stays.',
              confirmText: 'Cut all',
            }
          ).then(function() {
            var idx2 = _v2gFindNodeIndex(p.id);
            if (idx2 >= 0) _v2gInspect(state._v2gPts[idx2]);
          });
        } else if (act === 'rename') {
          _v2gRenameNode(p);
        } else if (act === 'list') {
          _v2gNotifyList({ type: 'entity', id: p.id, name: _v2gDisplayName(p), scrollOps: true });
          var ops = document.getElementById('mem-tab-entities') || document.querySelector('[id^="mem-tab-"]');
          if (ops) {
            try { ops.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) {}
          }
        } else if (act === 'delete') {
          _v2gDeleteEntity(p.id);
        }
      });
    });

    el.querySelectorAll('.v2g-rel-act').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var eidx = parseInt(btn.getAttribute('data-edge-idx'), 10);
        var ed = state._v2gEdges[eidx];
        if (!ed) return;
        var act = btn.getAttribute('data-act');
        var seed = _v2gEdgeSeed(ed);
        if (act === 'edit') {
          _v2gEditBeliefById(ed.beliefId, seed);
        } else if (act === 'cut' || act === 'unlink') {
          _v2gUnlinkBelief(ed.beliefId, seed).then(function(ok) {
            if (ok) {
              var idx3 = _v2gFindNodeIndex(p.id);
              if (idx3 >= 0) _v2gInspect(state._v2gPts[idx3]);
            }
          });
        }
      });
    });

    el.querySelectorAll('.v2g-belief-row').forEach(function(row) {
      row.addEventListener('click', function(ev) {
        if (ev.target && ev.target.closest && ev.target.closest('button')) return;
        var eidx = parseInt(row.getAttribute('data-edge-idx'), 10);
        if (!isNaN(eidx)) _v2gInspectEdge(eidx);
      });
    });
    } catch (inspectErr) {
      console.error('[v2g] inspect failed', inspectErr);
      try {
        el.innerHTML = '<div style="color:#f87171;font-size:0.75rem;">Inspect error: ' +
          _v2gEsc(String(inspectErr && inspectErr.message ? inspectErr.message : inspectErr)) +
          '</div>';
      } catch (e2) { /* ignore */ }
    }
  }

  export async function _v2gRenameNode(p) {
    if (!p || p.isEpisode) return;
    var current = _v2gDisplayName(p);
    var msg = 'Display name for this node. The id stays "' + String(p.id) + '" so all beliefs keep linking correctly.';
    var name;
    if (window.kazmaPrompt) {
      name = await window.kazmaPrompt({
        title: 'Rename node',
        message: msg,
        defaultValue: current === 'You' && _v2gIsUser(p) ? 'You' : current,
        confirmText: 'Rename',
        placeholder: _v2gIsUser(p) ? 'e.g. Mubder or Kazma' : 'e.g. ShipX',
      });
    } else {
      name = window.prompt(msg, current);
    }
    if (name == null) return;
    name = String(name).trim();
    if (!name) {
      if (window.showToast) window.showToast('Name cannot be empty', 'error');
      return;
    }
    if (name === current) return;
    try {
      var resp = await fetch('/api/memory/v2/entities/' + encodeURIComponent(p.id) + '/rename', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ name: name }),
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        if (window.showToast) window.showToast(data.error || 'Rename failed', 'error');
        return;
      }
      if (window.showToast) window.showToast('Renamed to “' + name + '”', 'success');
      // Update cached raw node so filters don't flash old label
      for (var i = 0; i < (state._v2gRawNodes || []).length; i++) {
        if (state._v2gRawNodes[i] && state._v2gRawNodes[i].id === p.id) {
          state._v2gRawNodes[i].name = name;
          state._v2gRawNodes[i].isVirtual = false;
        }
      }
      // Soft label update (struct same, names changed)
      state._v2gLabelSig = '';
      _v2gApplyFilters();
      state._v2gSelectedId = p.id;
      for (var j = 0; j < state._v2gPts.length; j++) {
        if (state._v2gPts[j].id === p.id) {
          _v2gInspect(state._v2gPts[j]);
          break;
        }
      }
      _v2gNotifyList({ type: 'entity', id: p.id, name: name });
      _v2gRepaint();
    } catch (err) {
      if (window.showToast) window.showToast('Rename failed', 'error');
    }
  }

  // ── Filter logic (CLIENT-SIDE against cached data) ──
  // Filters don't re-fetch from the server — they filter the cached
  // state._v2gRawNodes/state._v2gRawLinks. This avoids the "empty graph on filter"
  // bug where the server-side entity_type filter found no matches.

  function _v2gBuildUrl() {
    var params = new URLSearchParams();
    // Only the time slider goes to the server (bi-temporal query)
    var slider = document.getElementById('v2g-time-slider');
    if (slider && state._v2gTimeRange.max > 0 && parseFloat(slider.value) < 100) {
      var frac = parseFloat(slider.value) / 100;
      var ts = state._v2gTimeRange.min + frac * (state._v2gTimeRange.max - state._v2gTimeRange.min);
      params.set('at', String(Math.floor(ts)));
    }
    var qs = params.toString();
    return '/api/memory/v2/graph' + (qs ? ('?' + qs) : '');
  }

  var state._v2gLastStats = {};

  function _v2gRenderTruncation(stats) {
    var el = document.getElementById('v2g-trunc-banner');
    if (!el) return;
    var truncI18n = (window.__DASH_MEM_I18N && window.__DASH_MEM_I18N.graphTrunc) || 'showing first {n} of {total} nodes';
    if (stats && stats.truncated && stats.total_nodes > (stats.nodes || 0)) {
      var msg = String(truncI18n)
        .replace('{n}', stats.nodes || 0)
        .replace('{total}', stats.total_nodes);
      el.textContent = msg + ' — filter to narrow the view.';
      el.style.display = 'block';
    } else {
      el.style.display = 'none';
    }
  }

  // Refresh the canvas's screen-reader label with live node/edge counts so
  // the graph state is announced (the canvas itself is non-text). a11y.
  function _v2gUpdateCanvasAria(stats) {
    var canvas = document.getElementById('v2g-canvas');
    if (!canvas) return;
    var nodes = (stats && stats.nodes) || 0;
    var links = (stats && stats.links) || 0;
    var focus = state._v2gSelectedId || '';
    var base = 'V2 belief topology graph. Arrow keys pan, plus minus zoom, Home resets. Click edges to edit or unlink beliefs.';
    canvas.setAttribute('aria-label', base + ' Currently showing ' + nodes + ' nodes and ' + links + ' edges.' + (focus ? ' Focused on ' + focus + '.' : ''));
  }

  export async function _v2gLoad() {
    try {
      var resp = await fetch(_v2gBuildUrl());
      var data = await resp.json();
      var stats = data.stats || {};
      state._v2gLastStats = stats;
      state._v2gRawNodes = data.nodes || [];
      state._v2gRawLinks = data.links || [];
      // Normalize link fields (neo4j probe may use predicate instead of label)
      state._v2gRawLinks.forEach(function(l) {
        if (!l.label && l.predicate) l.label = l.predicate;
        if (!l.type) l.type = 'set';
        if (!l.source && l.subject) l.source = l.subject;
        if (!l.target && l.object) l.target = l.object;
      });
      state._v2gRawNodes.forEach(function(n) {
        if (!n.name && n.label) n.name = n.label;
        if (!n.type) n.type = 'concept';
      });
      // Episode overlay — faint virtual nodes (not edges)
      if (state._v2gShowEpisodes && state._v2gEpisodeNodes.length) {
        var existing = {};
        state._v2gRawNodes.forEach(function(n) { existing[n.id] = true; });
        state._v2gEpisodeNodes.forEach(function(ep) {
          if (!existing[ep.id]) state._v2gRawNodes.push(ep);
        });
      }
      if (stats.earliest && stats.latest && stats.latest > stats.earliest) {
        state._v2gTimeRange = { min: stats.earliest, max: Math.max(stats.latest, Date.now() / 1000) };
      }
      _v2gRenderTruncation(stats);
      _v2gUpdateCanvasAria(stats);
      _v2gRenderFilters();
      _v2gApplyFilters();
    } catch (e) { /* silent */ }
  }

  function _v2gTypeCountsFromData() {
    var counts = {};
    (state._v2gRawNodes || []).forEach(function(n) {
      var t = n.type || 'concept';
      counts[t] = (counts[t] || 0) + 1;
    });
    return counts;
  }

  function _v2gPredCountsFromData() {
    var counts = {};
    (state._v2gRawLinks || []).forEach(function(l) {
      var t = l.type || 'set';
      counts[t] = (counts[t] || 0) + 1;
    });
    return counts;
  }

  function _v2gUpdateLegend(entCounts) {
    var leg = document.getElementById('v2g-legend');
    if (!leg) return;
    _v2gRefreshPalette();
    var theme = _v2gTheme();
    var hasUser = (state._v2gRawNodes || []).some(function(n) {
      return String(n.id || '').toLowerCase() === 'user';
    });
    var parts = [];
    if (hasUser) {
      parts.push(
        '<span title="You"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' +
        theme.user + ';margin-right:4px;box-shadow:0 0 6px ' + _v2gHexAlpha(theme.user, 0.5) +
        ';"></span>You</span>'
      );
    }
    var order = ['person', 'tool', 'concept', 'location', 'project', 'entity'];
    var keys = order.filter(function(k) { return (entCounts[k] || 0) > 0; });
    Object.keys(entCounts || {}).forEach(function(k) {
      if (keys.indexOf(k) < 0 && entCounts[k] > 0) keys.push(k);
    });
    if (!keys.length) keys = ['concept'];
    keys.forEach(function(k) {
      // person count includes user — still show type chip in accent family
      var c = _V2G_TYPE_COLORS[k] || theme.accent;
      var n = entCounts[k] || 0;
      parts.push(
        '<span><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' +
        c + ';margin-right:4px;box-shadow:0 0 5px ' + _v2gHexAlpha(c, 0.35) +
        ';"></span>' + k + (n ? ' <span style="opacity:0.7">(' + n + ')</span>' : '') + '</span>'
      );
    });
    parts.push('<span style="opacity:0.7;margin-left:2px;">· site accent</span>');
    leg.innerHTML = parts.join('');
  }

  export function _v2gApplyFilters() {
    // Apply client-side entity-type + predicate-type + search filters
    var activeEnt = Object.keys(state._v2gFilters.entity);
    var activePred = Object.keys(state._v2gFilters.predicate);
    var search = '';
    var searchEl = document.getElementById('v2g-search');
    if (searchEl) search = searchEl.value.trim().toLowerCase();

    var nodes = state._v2gRawNodes, links = state._v2gRawLinks;
    // LOD: hard-cap node count for paint performance (P2-4)
    var _LOD_MAX = 200;
    if (nodes.length > _LOD_MAX) {
      nodes = nodes.slice(0, _LOD_MAX);
      var keep = {};
      nodes.forEach(function(n) { keep[n.id] = true; });
      links = links.filter(function(e) {
        var s = e.source && (e.source.id || e.source);
        var t = e.target && (e.target.id || e.target);
        return keep[s] && keep[t];
      });
    }

    // Filter links by predicate type + search
    if (activePred.length || search) {
      links = links.filter(function(l) {
        if (activePred.length && activePred.indexOf(l.type) < 0) return false;
        if (search) {
          var hay = ((l.label || '') + ' ' + (l.source || '') + ' ' + (l.target || '') + ' ' + (l.object_text || '')).toLowerCase();
          if (hay.indexOf(search) < 0) return false;
        }
        return true;
      });
    }
    // Keep only nodes referenced by surviving links (+ search match on node names)
    var nodeIds = new Set();
    links.forEach(function(l) { nodeIds.add(l.source); nodeIds.add(l.target); });
    if (search) {
      nodes.forEach(function(n) {
        var nm = (n.name || n.label || n.id || '').toLowerCase();
        if (nm.indexOf(search) >= 0) nodeIds.add(n.id);
      });
    }
    // Filter nodes by entity type + membership
    nodes = nodes.filter(function(n) {
      if (activeEnt.length && activeEnt.indexOf(n.type) < 0) return false;
      // Keep nodes that are in a surviving link, OR all nodes if no link filter
      if (activePred.length || search) return nodeIds.has(n.id);
      return true;
    });
    // If entity-type filter is on, re-filter links to only those between surviving nodes
    if (activeEnt.length) {
      var keepIds = new Set(nodes.map(function(n) { return n.id; }));
      links = links.filter(function(l) { return keepIds.has(l.source) && keepIds.has(l.target); });
    }

    var sl = document.getElementById('v2g-stats-line');
    if (sl) {
      var st = state._v2gLastStats || {};
      var paint = st.paint_source || st.source || 'sqlite';
      var gprov = st.graph_provider || paint;
      var parts = [nodes.length + ' nodes · ' + links.length + ' beliefs'];
      parts.push('paint ' + paint);
      if (gprov === 'neo4j') {
        parts.push(st.graph_online ? 'neo4j dual-write online' : 'neo4j offline');
      }
      if (activeEnt.length || activePred.length || search) {
        parts.push('filtered from ' + state._v2gRawNodes.length);
      }
      sl.textContent = parts.join(' · ');
    }
    _v2gUpdateLegend(_v2gTypeCountsFromData());
    // Let _v2gDrawCanvas compare signatures. Clearing always forced a full
    // spiral re-layout and wiped user-dragged positions on every 30s poll /
    // filter pass. Positions are restored from state._v2gPosCache on rebuild.
    _v2gDrawCanvas(nodes, links);
  }

  export function _v2gRenderFilters() {
    var entBox = document.getElementById('v2g-filters-entity');
    var predBox = document.getElementById('v2g-filters-predicate');
    var entCounts = _v2gTypeCountsFromData();
    var predCounts = _v2gPredCountsFromData();
    // Prefer types present in data; always offer the core set so empty graphs stay usable
    var coreEnt = ['person', 'tool', 'concept', 'location', 'project'];
    var entTypes = coreEnt.slice();
    Object.keys(entCounts).forEach(function(k) {
      if (entTypes.indexOf(k) < 0) entTypes.push(k);
    });
    // Drop core types that never appear once we have data (except keep all until first load)
    if (state._v2gRawNodes.length) {
      entTypes = entTypes.filter(function(k) {
        return (entCounts[k] || 0) > 0 || !!state._v2gFilters.entity[k];
      });
      if (!entTypes.length) entTypes = coreEnt.slice();
    }
    var corePred = ['functional', 'set', 'state'];
    var predTypes = corePred.slice();
    Object.keys(predCounts).forEach(function(k) {
      if (predTypes.indexOf(k) < 0) predTypes.push(k);
    });
    if (state._v2gRawLinks.length) {
      predTypes = predTypes.filter(function(k) {
        return (predCounts[k] || 0) > 0 || !!state._v2gFilters.predicate[k];
      });
      if (!predTypes.length) predTypes = corePred.slice();
    }
    function makeToggle(label, group, key, color, count) {
      var id = 'v2g-ft-' + group + '-' + key;
      var active = !!state._v2gFilters[group][key];
      var cnt = (count != null && count > 0) ? ' <span style="opacity:0.7;font-family:var(--font-mono);">' + count + '</span>' : '';
      return '<label title="' + label + (count != null ? ': ' + count : '') + '" style="display:inline-flex;align-items:center;gap:4px;cursor:pointer;font-size:0.65rem;padding:2px 7px;border-radius:999px;border:1px solid ' + (active ? color : 'var(--border-subtle)') + ';background:' + (active ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.02)') + ';' + (active ? 'color:var(--text-primary);' : 'color:var(--text-muted);') + '">' +
             '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (active ? color : 'transparent') + ';border:1px solid ' + color + ';flex-shrink:0;"></span>' +
             '<input type="checkbox" id="' + id + '" ' + (active ? 'checked' : '') + ' style="display:none;">' + label + cnt + '</label>';
    }
    if (entBox) entBox.innerHTML = entTypes.map(function(t) {
      return makeToggle(t, 'entity', t, _V2G_TYPE_COLORS[t] || '#94a3b8', entCounts[t] || 0);
    }).join('');
    if (predBox) predBox.innerHTML = predTypes.map(function(t) {
      return makeToggle(t, 'predicate', t, _V2G_PRED_COLORS[t] || '#94a3b8', predCounts[t] || 0);
    }).join('');
    // Wire change handlers — multi-select (toggle each independently)
    document.querySelectorAll('[id^="v2g-ft-entity-"]').forEach(function(cb) {
      cb.addEventListener('change', function() {
        var key = cb.id.replace('v2g-ft-entity-', '');
        if (cb.checked) state._v2gFilters.entity[key] = true;
        else delete state._v2gFilters.entity[key];
        _v2gRenderFilters(); _v2gApplyFilters();
      });
    });
    document.querySelectorAll('[id^="v2g-ft-predicate-"]').forEach(function(cb) {
      cb.addEventListener('change', function() {
        var key = cb.id.replace('v2g-ft-predicate-', '');
        if (cb.checked) state._v2gFilters.predicate[key] = true;
        else delete state._v2gFilters.predicate[key];
        _v2gRenderFilters(); _v2gApplyFilters();
      });
    });
    // Active filter chips + reset button
    var chips = document.getElementById('v2g-active-filters');
    if (chips) {
      var all = Object.keys(state._v2gFilters.entity).map(function(k) { return { group: 'entity', key: k, label: 'entity:' + k }; })
        .concat(Object.keys(state._v2gFilters.predicate).map(function(k) { return { group: 'predicate', key: k, label: 'pred:' + k }; }));
      var html = all.map(function(c, idx) {
        return '<span data-fg="' + c.group + '" data-fk="' + c.key + '" style="font-size:0.62rem;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,0.15);color:#a5b4fc;cursor:pointer;">' + c.label + ' ✕</span>';
      }).join('');
      if (all.length) html += '<span id="v2g-reset-filters" style="font-size:0.62rem;padding:2px 6px;border-radius:4px;background:rgba(239,68,68,0.12);color:#f87171;cursor:pointer;margin-left:4px;">Reset all</span>';
      chips.innerHTML = html;
      chips.querySelectorAll('span[data-fg]').forEach(function(span) {
        span.addEventListener('click', function() {
          var g = span.getAttribute('data-fg');
          var k = span.getAttribute('data-fk');
          if (g && k && state._v2gFilters[g]) delete state._v2gFilters[g][k];
          _v2gRenderFilters(); _v2gApplyFilters();
        });
      });
      var reset = document.getElementById('v2g-reset-filters');
      if (reset) reset.addEventListener('click', function() {
        state._v2gFilters = { entity: {}, predicate: {} };
        var s = document.getElementById('v2g-search'); if (s) s.value = '';
        _v2gRenderFilters(); _v2gApplyFilters();
      });
    }
  }

  var state._v2gPlayTimer = null;
  var state._v2gPreferReducedMotion = false;
  try {
    state._v2gPreferReducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch (e) { state._v2gPreferReducedMotion = false; }

  export function _v2gStopPlay() {
    if (state._v2gPlayTimer) { clearInterval(state._v2gPlayTimer); state._v2gPlayTimer = null; }
    var playBtn = document.getElementById('v2g-time-play');
    if (playBtn) playBtn.textContent = playBtn.getAttribute('data-play-label') || 'Play';
  }

  var state._v2gPathIds = {};
  var state._v2gEpisodeNodes = [];
  var state._v2gShowEpisodes = false;

  export function _v2gApplyPathFromQuery() {
    var seeds = (state._v2gLastQuerySeeds || []).map(function(s) { return String(s).toLowerCase(); });
    if (!seeds.length) {
      var q = ((document.getElementById('v2g-search') || {}).value || (document.getElementById('v2-probe-input') || {}).value || '').trim();
      if (q) seeds = q.toLowerCase().split(/\s+/).filter(function(w) { return w.length > 2; });
    }
    state._v2gPathIds = {};
    var matched = 0;
    for (var i = 0; i < state._v2gPts.length; i++) {
      var p = state._v2gPts[i];
      var hay = ((p.id || '') + ' ' + (p.fullLabel || '') + ' ' + (p.label || '')).toLowerCase();
      for (var s = 0; s < seeds.length; s++) {
        if (seeds[s] && hay.indexOf(seeds[s]) >= 0) {
          state._v2gPathIds[p.id] = true;
          matched++;
          break;
        }
      }
    }
    // Also mark edges between path nodes
    for (var e = 0; e < state._v2gEdges.length; e++) {
      var ed = state._v2gEdges[e], A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
      if (A && B && state._v2gPathIds[A.id] && state._v2gPathIds[B.id]) ed.pathHot = true;
      else ed.pathHot = false;
    }
    if (window.showToast) {
      window.showToast(matched ? ('Path: highlighted ' + matched + ' nodes') : 'No matching nodes for query path', matched ? 'success' : 'info');
    }
    // Zoom to first path node
    for (var j = 0; j < state._v2gPts.length; j++) {
      if (state._v2gPathIds[state._v2gPts[j].id]) {
        state._v2gSelectedId = state._v2gPts[j].id;
        var size = _v2gCanvasSize();
        if (size) {
          state._v2gView.scale = 1.8;
          state._v2gView.ox = size.w / 2 - state._v2gPts[j].x * 1.8;
          state._v2gView.oy = size.h / 2 - state._v2gPts[j].y * 1.8;
        }
        break;
      }
    }
    _v2gHeated();
    _v2gRepaint();
  }

  export async function _v2gLoadEpisodes() {
    try {
      var resp = await fetch('/api/memory/v2/episodes?limit=30');
      var data = await resp.json();
      state._v2gEpisodeNodes = (data.episodes || []).map(function(ep) {
        return {
          id: 'ep:' + ep.id,
          name: (ep.preview || ep.id).slice(0, 40),
          type: 'entity',
          beliefCount: 1,
          isHighStakes: false,
          isVirtual: true,
          isEpisode: true,
          tier: ep.tier || '',
        };
      });
    } catch (e) {
      state._v2gEpisodeNodes = [];
    }
  }

  export function _v2gExportPng() {
    var canvas = document.getElementById('v2g-canvas');
    if (!canvas) return;
    try {
      var a = document.createElement('a');
      a.download = 'kazma-v2-topology.png';
      a.href = canvas.toDataURL('image/png');
      a.click();
      if (window.showToast) window.showToast('PNG downloaded', 'success');
    } catch (e) {
      if (window.showToast) window.showToast('PNG export failed', 'error');
    }
  }

  export function _v2gExportSvg() {
    var W = 800, H = 500;
    var size = _v2gCanvasSize();
    if (size) { W = size.w; H = size.h; }
    var parts = ['<?xml version="1.0" encoding="UTF-8"?>',
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">',
      '<rect width="100%" height="100%" fill="#0a0f14"/>'];
    for (var e = 0; e < state._v2gEdges.length; e++) {
      var ed = state._v2gEdges[e], A = state._v2gPts[ed.a], B = state._v2gPts[ed.b];
      if (!A || !B) continue;
      parts.push('<line x1="' + _v2gSX(A.x).toFixed(1) + '" y1="' + _v2gSY(A.y).toFixed(1) +
        '" x2="' + _v2gSX(B.x).toFixed(1) + '" y2="' + _v2gSY(B.y).toFixed(1) +
        '" stroke="#22d3ee" stroke-opacity="0.45" stroke-width="1.5"/>');
    }
    for (var i = 0; i < state._v2gPts.length; i++) {
      var p = state._v2gPts[i];
      var col = _v2gNodeColor(p);
      parts.push('<circle cx="' + _v2gSX(p.x).toFixed(1) + '" cy="' + _v2gSY(p.y).toFixed(1) +
        '" r="' + p.r + '" fill="' + col + '" fill-opacity="0.9"/>');
      parts.push('<text x="' + _v2gSX(p.x).toFixed(1) + '" y="' + (_v2gSY(p.y) + p.r + 12).toFixed(1) +
        '" fill="#e6edf3" font-size="11" text-anchor="middle" font-family="sans-serif">' +
        _v2gEsc(p.label || p.id).replace(/&/g, '&amp;') + '</text>');
    }
    parts.push('</svg>');
    var blob = new Blob([parts.join('')], { type: 'image/svg+xml' });
    var a = document.createElement('a');
    a.download = 'kazma-v2-topology.svg';
    a.href = URL.createObjectURL(blob);
    a.click();
    setTimeout(function() { URL.revokeObjectURL(a.href); }, 2000);
    if (window.showToast) window.showToast('SVG downloaded', 'success');
  }

