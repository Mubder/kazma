// Boot + wiring + the window._v2g* export contract (compat shim).
// Split from memory_console.js (2026-08). ES-module entry point loaded via
// <script type="module">. Wires graph controls, exposes the 13 window._v2g*
// globals memory.js depends on, kicks off the graph load. memory.js unchanged.
import { state, dispatchGraphSelect, dispatchOpsSlots, dispatchOpsDone } from './state.js';
import {
  _v2gLoad, _v2gRenderFilters, _v2gReloadGraph, _v2gRenameNode,
  _v2gSelectEntity, _v2gSelectByBelief, _v2gNotifyList, _v2gSyncOpsBar,
  _v2gRepaint, _v2gOpsPredicate, _v2gDoLink, _v2gDoMerge, _v2gUnlinkBelief,
  _v2gCutHubLinks, _v2gCutEdges, _v2gEditBeliefById,
} from './graph.js';
// Wire graph callbacks back into console (avoids circular import).
import { setGraphCallbacks } from './graph.js';

    var refresh = document.getElementById('v2g-refresh');
    if (refresh) refresh.addEventListener('click', _v2gLoad);
    var search = document.getElementById('v2g-search');
    if (search) { var sto; search.addEventListener('input', function() { clearTimeout(sto); sto = setTimeout(_v2gApplyFilters, 250); }); }
    document.getElementById('v2g-path-query')?.addEventListener('click', _v2gApplyPathFromQuery);
    document.getElementById('v2g-export-png')?.addEventListener('click', _v2gExportPng);
    document.getElementById('v2g-export-svg')?.addEventListener('click', _v2gExportSvg);

    // Prevent canvas pointer capture from eating inspect-panel clicks
    var inspectEl = document.getElementById('v2g-inspect');
    if (inspectEl && !inspectEl._v2gPointerGuard) {
      inspectEl._v2gPointerGuard = true;
      ['pointerdown', 'mousedown', 'click', 'touchstart'].forEach(function(evName) {
        inspectEl.addEventListener(evName, function(ev) {
          ev.stopPropagation();
        });
      });
    }

    // Graph ops bar
    var linkBtn = document.getElementById('v2g-ops-link');
    if (linkBtn && !linkBtn._v2gWired) {
      linkBtn._v2gWired = true;
      linkBtn.addEventListener('click', function(ev) {
        ev.preventDefault();
        if (state._v2gOps.sourceId && state._v2gOps.targetId) {
          _v2gDoLink(state._v2gOps.sourceId, state._v2gOps.targetId, state._v2gOpsPredicate());
        } else if (state._v2gOps.mode === 'link') {
          state._v2gOps.mode = null;
          _v2gSyncOpsBar();
          _v2gToast('Link mode cancelled', 'info');
        } else {
          _v2gEnterMode('link');
        }
      });
    }
    var mergeBtn = document.getElementById('v2g-ops-merge');
    if (mergeBtn && !mergeBtn._v2gWired) {
      mergeBtn._v2gWired = true;
      mergeBtn.addEventListener('click', function(ev) {
        ev.preventDefault();
        if (state._v2gOps.sourceId && state._v2gOps.targetId) {
          _v2gDoMerge(state._v2gOps.sourceId, state._v2gOps.targetId);
        } else if (state._v2gOps.mode === 'merge') {
          state._v2gOps.mode = null;
          _v2gSyncOpsBar();
          _v2gToast('Merge mode cancelled', 'info');
        } else {
          _v2gEnterMode('merge');
        }
      });
    }
    var swapBtn = document.getElementById('v2g-ops-swap');
    if (swapBtn) {
      swapBtn.addEventListener('click', function() {
        var s = state._v2gOps.sourceId;
        state._v2gOps.sourceId = state._v2gOps.targetId;
        state._v2gOps.targetId = s;
        _v2gBroadcastSlots();
      });
    }
    var clearBtn = document.getElementById('v2g-ops-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', function() {
        _v2gClearSlots();
        _v2gToast('Slots cleared', 'info');
      });
    }
    var predEl = document.getElementById('v2g-ops-predicate');
    if (predEl) {
      predEl.addEventListener('change', function() { _v2gBroadcastSlots(); });
    }
    // List → graph slot sync (Entities form Src/Tgt)
    window.addEventListener('kazma:memory-ops-slots', function(ev) {
      var d = (ev && ev.detail) || {};
      // Avoid feedback loop: only apply if values differ from graph state
      // and the event is marked from list (fromList: true)
      if (!d.fromList) return;
      if (d.sourceId !== undefined) state._v2gOps.sourceId = d.sourceId || null;
      if (d.targetId !== undefined) state._v2gOps.targetId = d.targetId || null;
      if (d.predicate && predEl) predEl.value = d.predicate;
      _v2gSyncOpsBar();
      _v2gRepaint();
    });
    _v2gSyncOpsBar();
    var epToggle = document.getElementById('v2g-episode-overlay');
    if (epToggle) {
      epToggle.addEventListener('change', async function() {
        state._v2gShowEpisodes = !!epToggle.checked;
        if (state._v2gShowEpisodes && !state._v2gEpisodeNodes.length) await _v2gLoadEpisodes();
        _v2gLoad();
      });
    }
    // Keyboard a11y: pan / zoom / reset
    var canvas = document.getElementById('v2g-canvas');
    if (canvas && !canvas._v2gKeys) {
      canvas._v2gKeys = true;
      canvas.addEventListener('keydown', function(ev) {
        var step = 28;
        var handled = true;
        if (ev.key === 'ArrowLeft') state._v2gView.ox += step;
        else if (ev.key === 'ArrowRight') state._v2gView.ox -= step;
        else if (ev.key === 'ArrowUp') state._v2gView.oy += step;
        else if (ev.key === 'ArrowDown') state._v2gView.oy -= step;
        else if (ev.key === '+' || ev.key === '=') {
          state._v2gView.scale = Math.min(state._v2gMaxScale, state._v2gView.scale * 1.15);
        } else if (ev.key === '-' || ev.key === '_') {
          state._v2gView.scale = Math.max(state._v2gMinScale, state._v2gView.scale / 1.15);
        } else if (ev.key === 'Home') {
          state._v2gView = { scale: 1, ox: 0, oy: 0 };
        } else if (ev.key === 'Escape') {
          state._v2gSelectedId = null; state._v2gPathIds = {};
          state._v2gOps.mode = null; state._v2gOps.selectedEdgeIdx = -1;
          state._v2gHighlightSubj = null; state._v2gHighlightObj = null;
          _v2gSyncOpsBar();
        } else {
          handled = false;
        }
        if (handled) { ev.preventDefault(); _v2gRepaint(); }
      });
    }
    // Time slider + play/pause scrub
    var slider = document.getElementById('v2g-time-slider');
    var label = document.getElementById('v2g-time-label');
    var liveBtn = document.getElementById('v2g-time-live');
    var playBtn = document.getElementById('v2g-time-play');
    if (playBtn) playBtn.setAttribute('data-play-label', playBtn.textContent || 'Play');
    function _updateTimeLabel() {
      if (!slider || !label) return;
      var v = parseFloat(slider.value);
      if (v >= 99.5) label.textContent = 'Live (now)';
      else if (state._v2gTimeRange.max > 0) {
        var ts = state._v2gTimeRange.min + (v / 100) * (state._v2gTimeRange.max - state._v2gTimeRange.min);
        label.textContent = new Date(ts * 1000).toLocaleDateString();
      } else label.textContent = '—';
    }
    if (slider) {
      slider.addEventListener('input', _updateTimeLabel);
      slider.addEventListener('change', function() { _v2gStopPlay(); _v2gLoad(); });
    }
    if (liveBtn) liveBtn.addEventListener('click', function() {
      _v2gStopPlay();
      if (slider) slider.value = 100;
      if (label) label.textContent = 'Live (now)';
      _v2gLoad();
    });
    if (playBtn && slider) {
      playBtn.addEventListener('click', function() {
        if (state._v2gPreferReducedMotion) {
          // One-step instead of animation when user prefers reduced motion
          var v = parseFloat(slider.value);
          slider.value = String(Math.min(100, v + 10));
          _updateTimeLabel();
          _v2gLoad();
          return;
        }
        if (state._v2gPlayTimer) {
          _v2gStopPlay();
          return;
        }
        playBtn.textContent = playBtn.getAttribute('data-pause-label') || 'Pause';
        if (parseFloat(slider.value) >= 99) slider.value = '0';
        state._v2gPlayTimer = setInterval(function() {
          var cur = parseFloat(slider.value);
          if (cur >= 100) {
            _v2gStopPlay();
            return;
          }
          slider.value = String(Math.min(100, cur + 2));
          _updateTimeLabel();
          _v2gLoad();
        }, 400);
      });
    }
    // Resize debouncer
    var rto;
    window.addEventListener('resize', function() { clearTimeout(rto); rto = setTimeout(function() { if (state._v2gPts.length) _v2gRepaint(); }, 200); });
  }

  // Expose for Memory admin list ↔ graph bridge
  window._v2gLoad = _v2gLoad;
  window._v2gForceReload = _v2gReloadGraph;
  window._v2gRenameNode = _v2gRenameNode;
  window._v2gSelectEntity = _v2gSelectEntity;
  window._v2gSelectBelief = function(subj, obj, beliefId, opts) {
    opts = opts || {};
    var ok = _v2gSelectByBelief(subj, obj, beliefId);
    if (ok && opts.notify !== false) {
      _v2gNotifyList({
        type: 'belief',
        id: beliefId || null,
        subject: subj,
        object: obj,
      });
    }
    return ok;
  };
  window._v2gSetOpsSlots = function(src, tgt, pred) {
    if (src !== undefined) state._v2gOps.sourceId = src || null;
    if (tgt !== undefined) state._v2gOps.targetId = tgt || null;
    var predEl = document.getElementById('v2g-ops-predicate');
    if (pred && predEl) predEl.value = pred;
    _v2gSyncOpsBar();
    _v2gRepaint();
  };
  window._v2gGetOpsSlots = function() {
    return {
      sourceId: state._v2gOps.sourceId,
      targetId: state._v2gOps.targetId,
      predicate: state._v2gOpsPredicate(),
      mode: state._v2gOps.mode,
    };
  };
  window._v2gDoLink = _v2gDoLink;
  window._v2gDoMerge = _v2gDoMerge;
  window._v2gUnlinkBelief = _v2gUnlinkBelief;
  window._v2gCutHubLinks = _v2gCutHubLinks;
  window._v2gCutEdges = _v2gCutEdges;
  window._v2gEditBeliefById = _v2gEditBeliefById;

  try {
    _v2gRenderFilters();
    _v2gWireControls();
    _v2gLoad();
    setInterval(_v2gLoad, 30000);
  } catch (e) { /* V2 graph optional */ }

// Inject the console callbacks the graph needs (pollV2Health, loadV2Beliefs
// are defined in console.js, loaded next). Deferred so console.js can register.
import('./console.js').then(function(mod) {
  setGraphCallbacks({
    pollV2Health: mod.pollV2Health,
    loadV2Beliefs: mod.loadV2Beliefs,
  });
}).catch(function(){ /* console panel optional */ });
