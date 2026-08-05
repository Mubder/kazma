// Boot + the window._v2g* export contract (compat shim).
// Split from memory_console.js (2026-08). ES-module entry (<script type=module>).
// Exposes the 13 window._v2g* globals memory.js depends on + boots the graph.
// _v2gWireControls lives in graph.js (imported); the export block + boot call here.
import { state, dispatchGraphSelect, dispatchOpsSlots, dispatchOpsDone } from './state.js';
import {
  _v2gLoad, _v2gRenderFilters, _v2gWireControls,
  _v2gReloadGraph, _v2gRenameNode, _v2gSelectEntity, _v2gSelectBelief,
  _v2gSelectByBelief,
  _v2gNotifyList, _v2gSyncOpsBar, _v2gRepaint, _v2gOpsPredicate,
  _v2gDoLink, _v2gDoMerge, _v2gUnlinkBelief, _v2gCutHubLinks, _v2gCutEdges,
  _v2gEditBeliefById,
} from './graph.js';
import { setGraphCallbacks } from './graph.js';

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

// Inject console callbacks the graph needs (avoids circular import).
import('./console.js').then(function(mod) {
  setGraphCallbacks({ pollV2Health: mod.pollV2Health, loadV2Beliefs: mod.loadV2Beliefs });
}).catch(function(){ /* console panel optional */ });
