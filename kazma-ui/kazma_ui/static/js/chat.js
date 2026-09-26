/* ═══════════════════════════════════════════════════════
   Kazma Chat — Full-featured chat interface
   Uses SSE streaming for real-time responses

   Section map (do not rewrite this file in one shot):
     sessions / sidebar  — loadSession, session list, search
     send / turn machine — sendMessage, abort, recovery
     SSE paint           — token deltas, turn_complete, HITL card
     composer / capacity — input, attach, voice, capacity bar
     slash catalog       — chat_slash.js (window.KAZMA_SLASH_COMMANDS)
   Navigation shortcuts live ONLY in modules/nav.js.
   ═══════════════════════════════════════════════════════ */

(function() {
  'use strict';
  var KS = window.KazmaStream;
  var chatSessionId = null;
  var currentMsgEl = null;
  /**
   * The answer this turn has produced so far, read from the document.
   *
   * This replaces `tokenAccum`, a module-level string that every paint
   * wrote and four decisions read. AGENTS.md §31B already forbade the
   * dual-PAINT ("Do not restore tokenAccum dual-paint (T-4)"); what was
   * left was subtler and is the same defect one level down — a second
   * answer to "has this turn produced anything", kept in a variable whose
   * lifetime nobody owned. It was zeroed in eleven places, and every
   * zeroing was somebody defending against a stale read: the empty-string
   * guard in _paintLiveTextNow exists because one transport's terminal
   * frame flushed after the other's endTurn had blanked it, and painted ""
   * over a finished reply (2026-09-02).
   *
   * The document cannot go stale that way. It is keyed by turn, a retired
   * turn's document is simply no longer the live one, and "what has this
   * turn said" has exactly one answer (invariant U06).
   */
  function _liveAnswerText() {
    var TD = window.KazmaTurnDocument;
    var doc = _docs[_liveTurnId] || _docs.live || null;
    return _answerFromDoc(TD, doc) || '';
  }
  var _liveTurnId = '';
  /** Turn ids retired by abortThenSend / Stop. Old SSE/WS tokens with these
   *  ids must not paint (or switch `_liveTurnId` back to the first bubble). */
  var _retiredTurnIds = [];
  var _RETIRED_CAP = 32;
  /** True after abortThenSend/Stop until session switch. SSE tokens often
   *  have no turn_id — they stay accepted (epoch-gated). Orphan WS/done
   *  frames without a turn_id must not dump into the new bubble. */
  var _supersededLive = false;
  var _docs = {};
  var activeStream = null;
  /** Live typing-indicator element for the current turn (cleared on abort). */
  // Track the last successfully-sent user message so the empty-turn
  // recovery can offer a one-click Retry instead of leaving the user
  // staring at "_No response received._" with no recourse. Reset on
  // every fresh sendMessage(); only the recovery path reads it.
  var lastSentUserText = '';
  var sessions = [];
  var messageReactions = {};
  var searchQuery = '';
  var showArchived = false;
  // Cumulative session usage (tokens/cost). Preferred source: the server's
  // session_tokens/session_cost payload keys; falls back to local accumulation
  // when only per-turn values arrive (old backends).
  var _sessionTotals = { tokens: 0, cost: 0 };
  // Per-turn usage captured from done/turn_complete — powers the turn
  // summary bar rendered by finalizeProgress().
  var _lastTurnStats = null;
  // Tool rows logged in the current turn's workbench (summary bar count).
  var _progressToolCount = 0;

  // DOM refs
  var messagesEl, inputEl, sendBtn, sessionListEl, searchInputEl;
  var costBadge, tokensBadge, contextBadge, charBadge;
  var modelSelectorEl;

  // Currently selected model (persisted in localStorage)
  var selectedModel = '';
  var MODEL_LS_KEY = 'kazma.selectedModel';
  var _activeWorkspaceId = '';

  function refreshWorkspaceId() {
    fetch('/api/workspaces')
      .then(function(r) { return r.ok ? r.json() : {}; })
      .then(function(data) {
        _activeWorkspaceId = (data && data.active_workspace_id) || '';
        try { window.__kazmaWorkspaceId = _activeWorkspaceId; } catch (e) { /* ignore */ }
      })
      .catch(function() { _activeWorkspaceId = ''; });
  }

  // Active chat session (persisted so a page refresh resumes the same session)
  var SESSION_LS_KEY = 'kazma.chatSessionId';

  function $(id) { return document.getElementById(id); }

  /** Localized CoT / Activity strings (injected from chat.html as window.CHAT_I18N). */
  function ti(key, fallback) {
    var m = window.CHAT_I18N || {};
    var v = m[key];
    return (v != null && String(v) !== '') ? String(v) : (fallback || key);
  }
  function tiFmt(key, fallback, vars) {
    var s = ti(key, fallback);
    if (vars) {
      Object.keys(vars).forEach(function(k) {
        s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), String(vars[k]));
      });
    }
    return s;
  }

  /** CLDR plural category: the same rule as i18n.get_arabic_plural_form /
   *  t_plural (six forms for Arabic, one/other for everything else). */
  function _pluralCategory(n) {
    var lang = String(document.documentElement.getAttribute('lang') || 'en')
      .slice(0, 2).toLowerCase();
    var x = Math.abs(Number(n) || 0);
    if (lang !== 'ar') return x === 1 ? 'one' : 'other';
    if (x === 0) return 'zero';
    if (x === 1) return 'one';
    if (x === 2) return 'two';
    var mod100 = Math.floor(x) % 100;
    if (mod100 >= 3 && mod100 <= 10) return 'few';
    if (mod100 >= 11 && mod100 <= 99) return 'many';
    return 'other';
  }

  /** A count with its noun, in the page language: "1 tool", "10 tools",
   *  Arabic dual and plural forms. THE way to print a count -- the forms
   *  come from the catalog (chat.<base>.<category>) via CHAT_I18N.plural.
   *  Hand-built "n + ' ' + noun" labels printed "1 approvals", "3 3 tools"
   *  and, through the wrong key, "1 step" for one tool (2026-09-24);
   *  tests/test_count_labels.py keeps them from coming back. */
  function tiCount(base, n, oneEn, otherEn) {
    var forms = (((window.CHAT_I18N || {}).plural) || {})[base] || {};
    var cat = _pluralCategory(n);
    var s = forms[cat] || forms.other || (Number(n) === 1 ? oneEn : otherEn);
    return String(s).replace(/\{n\}/g, String(n));
  }

  function generateSessionId() {
    try {
      if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    } catch (e) {}
    return 's-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  function persistSessionId() {
    try {
      if (chatSessionId) localStorage.setItem(SESSION_LS_KEY, chatSessionId);
      else localStorage.removeItem(SESSION_LS_KEY);
    } catch (e) {}
  }

  // ── Initialize ────────────────────────────────────────
  function init() {
    messagesEl = $('chat-messages');
    _installScrollPinTracker();
    inputEl = $('chat-input');
    sendBtn = $('send-btn');
    sessionListEl = $('session-list');
    searchInputEl = $('session-search');
    costBadge = $('session-cost');
    tokensBadge = $('session-tokens');
    contextBadge = $('context-size');
    charBadge = $('composer-chars');
    modelSelectorEl = $('model-selector');

    if (!messagesEl) return; // not on chat page

    _paintBuildBadge();

    // Input handlers
    if (inputEl) {
      inputEl.addEventListener('keydown', onInputKeydown);
      inputEl.addEventListener('input', onInputResize);
      // Ensure dir=auto is set even if the template cache is stale
      if (!inputEl.getAttribute('dir')) inputEl.setAttribute('dir', 'auto');
      syncInputBidi();
    }
    if (sendBtn) sendBtn.addEventListener('click', function() {
      var draft = (inputEl && inputEl.value || '').trim();
      if (isSteerOrAbortCommand(draft)) {
        sendMessage();
        return;
      }
      // Empty composer + generating → Stop. Typed follow-up → stop-and-send
      // (do not swallow the draft behind the Stop button).
      if (_isGenerating && !draft) { abortGeneration(); return; }
      if (_isGenerating && draft) { abortThenSend(); return; }
      sendMessage();
    });

    // Make the entire input box focus the text field (no dead zones).
    var inputWrapper = document.querySelector('.input-wrapper') || $('chat-drop-zone');
    if (inputWrapper && inputEl) {
      inputWrapper.addEventListener('click', function (e) {
        if (e.target.closest('button')) return; // let buttons do their job
        if (e.target.closest('.chat-attach-chip')) return;
        inputEl.focus();
      });
    }

    // Drag-and-drop attachments onto the composer
    setupChatDropZone();

    // Model selector
    if (modelSelectorEl) {
      modelSelectorEl.addEventListener('change', onModelChange);
    }

    // Listen for model changes from sidebar or other components
    document.addEventListener('model-changed', function(e) {
      var model = e.detail || (e.target && e.target.value) || '';
      if (model && model !== selectedModel) {
        selectedModel = model;
        if (modelSelectorEl) modelSelectorEl.value = model;
        try { localStorage.setItem(MODEL_LS_KEY, model); } catch(err) {}
      }
    });

    // New session button
    var newBtn = $('new-session-btn');
    if (newBtn) newBtn.addEventListener('click', newSession);

    // Session search
    if (searchInputEl) {
      searchInputEl.addEventListener('input', function() {
        searchQuery = this.value.toLowerCase();
        renderSessionList();
      });
    }

    // File upload (any type; multi-select + drag-drop)
    var fileInput = $('file-input');
    var attachBtn = $('attach-btn');
    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', function() { fileInput.click(); });
      fileInput.addEventListener('change', onFileSelected);
    }
    // Attachment chip remove (delegation)
    var attachStrip = $('chat-attachments');
    if (attachStrip) {
      attachStrip.addEventListener('click', function(e) {
        var btn = e.target.closest('[data-remove-attach]');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        removePendingAttachment(btn.getAttribute('data-remove-attach'));
      });
    }

    // Session list click delegation
    if (sessionListEl) {
      sessionListEl.addEventListener('click', function(e) {
        var item = e.target.closest('.session-item');
        if (!item) return;
        var sid = item.dataset.sessionId;
        if (sid) loadSession(sid);
      });
    }

    // Click anywhere outside an open kebab menu closes it
    document.addEventListener('click', function(e) {
      if (_openMenuId && !e.target.closest('.session-menu') && !e.target.closest('.session-more')) {
        _openMenuId = null;
        renderSessionList();
      }
    });

    function _modalOrOverlayOpen() {
      try {
        if (window.Alpine && window.Alpine.store) {
          var modalStore = window.Alpine.store('modal');
          if (modalStore && modalStore.open) return true;
          var searchStore = window.Alpine.store('search');
          if (searchStore && searchStore.open) return true;
        }
        if (typeof document !== 'undefined') {
          if (document.documentElement && document.documentElement.dataset.overlayOpen === '1') return true;
          var activeModal = document.querySelector('.search-overlay:not([style*="display: none"]), .modal-overlay:not([style*="display: none"]), dialog[open]');
          if (activeModal) return true;
        }
      } catch (e) { /* ignore */ }
      return false;
    }

    // Global Escape key — abort generation from anywhere on the page
    // (not just when the textarea has focus). Yields when a modal or search overlay is open.
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && _isGenerating) {
        if (_modalOrOverlayOpen()) return;
        e.preventDefault();
        abortGeneration();
      }
    });

    // Load the current session's messages if we have a session ID
    // (e.g., after page refresh or via the global search overlay ?s=)
    var initialSessionId = localStorage.getItem(SESSION_LS_KEY);
    try {
      var sParam = new URLSearchParams(window.location.search).get('s');
      if (sParam) initialSessionId = sParam;
    } catch (e) { /* ignore malformed URLs */ }
    if (initialSessionId) {
      chatSessionId = initialSessionId;
      // Persist so loadModels()'s loadSession(savedSid) honors a ?s= param
      // that differs from the last saved session.
      persistSessionId();
      // ONE boot painter: loadSession (via loadModels below) renders the
      // transcript and ends with the 'load' resync. The old extra boot-time
      // init resync here raced it — its journal attach painted
      // the final reply first, then loadSession's innerHTML wipe erased it
      // for the fetch duration before the transcript repainted it (the
      // "reply appears → vanishes 1-2s → reappears" refresh flicker,
      // 2026-09-01). Reconciliation still happens, just after render.
    }

    // Load available models for the model selector
    loadModels();
    refreshWorkspaceId();

    // Load sessions after models are loaded
    loadSessions();
    bindCapacityBar();
    refreshCapacity();

    // ── Turn Delivery V2: unconditional snapshot resync ──────────────
    // The browser throttles TIMERS in hidden tabs (≤1/min after ~5 min) but
    // never network callbacks — so recovery must not depend on timers.
    // resync() is gate-free and idempotent: fetch status + messages from the
    // durable SessionStore and make the UI match server truth. Fired on tab
    // visible / focus / pageshow / WS seq-gap / idle watchdog / init.
    // Replaces the old gated reconciler, the 3s "nuclear" poll and
    // the background-turn poller (all deleted).
    document.addEventListener('visibilitychange', function() {
      if (!document.hidden) {
        if (showArchived) loadArchivedSessions(); else loadSessions();
        if (chatSessionId) _resyncDelivery('visibility');
      }
    });
    // Some browsers (esp. mobile) fire pageshow/focus without visibilitychange.
    window.addEventListener('pageshow', function() {
      if (chatSessionId && !document.hidden) _resyncDelivery('pageshow');
    });
    window.addEventListener('focus', function() {
      if (chatSessionId && !document.hidden) _resyncDelivery('focus');
    });
  }

  /**
   * Unconditional authoritative resync — server is source of truth.
   * No debounce windows, no expectReply gating, no rendered-text matching:
   * idempotent by construction, so it never needs guarding (plan KD-5).
   */
  // Set per-send (sendMessage closure owns _dispatchSse); module-level
  // recovery paths (_resyncDelivery) re-attach the live stream through it.
  var _reopenSseRef = null;

  /**
   * Re-attach to the journal after an approval. ALWAYS.
   *
   * Every call site used to read `if (!activeStream)` first, which asks a
   * variable a question it cannot answer: the handle returned by KS.sse
   * exposes `abort()` and `lastEventId()` and nothing about liveness. A
   * stream the server closed on the HITL pause leaves that variable holding
   * a dead handle unless a terminal callback nulls it -- and those callbacks
   * are epoch-gated, so a superseded stream's `done` frame nulls nothing.
   *
   * Measured on the operator's install, 2026-09-14 15:44 UTC: approval
   * granted and POST 200 at :28.8, the tool ran, and the turn closed at
   * :43.3 with a 3,725-character reply -- with ZERO /api/chat/stream
   * requests after the approve. The guard held, the answer was written to a
   * journal nobody was reading, and the bubble kept the placeholder:
   * "The agent paused to ask for permission to run a tool."
   *
   * The guard sat on the wrong side of the trade. A redundant attach costs
   * one HTTP request and is idempotent -- it resumes from `last_event_id`,
   * and bumping the epoch gates whatever it superseded. A MISSED attach
   * costs the entire response. So: abort whatever we are holding, then
   * attach, unconditionally.
   */
  function _reattachAfterApproval(reason) {
    _startReconciler('approval');
    if (activeStream) {
      try { activeStream.abort(); } catch (eAb) { /* already dead */ }
      activeStream = null;
    }
    if (typeof _reopenSseRef === 'function') {
      try { _reopenSseRef(reason); } catch (eRe) { /* ignore */ }
    }
  }
  // Bounded re-attaches per turn — a journal-gap attach closes without a
  // terminal, and an unbounded resync→reattach cycle with the same invalid
  // cursor loops forever (the "is it still running?" stuck state).
  var _reopenCount = 0;
  var _REOPEN_MAX = 3;
  // Monotonic per-dispatch epoch: stale SSE dispatches (superseded by an
  // approval resume, cursor re-attach, or abort) must not paint or finalize.
  var _sseEpoch = 0;
  // Journal cursor + attach live outside sendMessage so a refresh can
  // re-tail a running turn (loadSession used to call a null _reopenSseRef).
  var _lastSeqSeen = 0;
  var _sseAttempts = 0;
  var _buildSseCallbacks = null;

  function _noteSeq() {
    if (activeStream && typeof activeStream.lastEventId === 'function') {
      var sid = Number(activeStream.lastEventId());
      if (sid > 0) _lastSeqSeen = sid;
    }
  }

  /**
   * How far THIS page has read the thread's journal when no stream of its
   * own has: the WebSocket's tracker, else its persisted cursor, else 0.
   *
   * `_lastSeqSeen` only follows SSE frames, so a tab that did not send the
   * turn -- or one just reloaded -- attached from seq 0 and replayed every
   * earlier turn of the thread through the projector: finished blocks went
   * back to "working" and showed an interim narration as their answer
   * (2026-09-26, the watching tab of a three-turn run).
   */
  function _pageDeliveryCursor() {
    try {
      var st = window.Alpine && Alpine.store && Alpine.store('agent');
      if (st && st._cursor && typeof st._cursor.last === 'function'
          && String(st.sessionId || '') === String(chatSessionId || '')) {
        var live = Number(st._cursor.last());
        if (live > 0) return live;
      }
      if (window.KazmaDeliveryCursor && chatSessionId) {
        var kept = Number(KazmaDeliveryCursor.loadPersisted(chatSessionId));
        if (kept > 0) return kept;
      }
    } catch (e) { /* no cursor is a cursor of 0 */ }
    return 0;
  }

  function _attachJournal(reason) {
    if (!chatSessionId) return;
    // A dead handle is not a live stream. Treating `activeStream` as
    // proof of liveness left a paused thread with nobody reading the
    // journal (2026-09-14).
    if (activeStream && !_streamIsLive()) {
      try { activeStream.abort(); } catch (eDead) { /* already dead */ }
      activeStream = null;
    }
    if (_streamIsLive() || _attachInFlight) return;
    var stream = window.KazmaStream;
    if (!stream || typeof stream.sse !== 'function') return;
    // An Approve click is a deliberate operator action, never a cursor
    // loop — it must never be rationed by the reopen budget. Same for a
    // thread that is still paused: exhausting the budget left a live
    // question with no stream. Reset and SAY SO when a decline happens.
    var waiveBudget = reason === 'approve-json' || reason === 'approve-409'
      || _awaitingApproval || _serverPaused || hasLiveGate();
    if (waiveBudget) {
      if (_reopenCount > 0) {
        console.warn('[KazmaChat] Reopen budget reset by ' + (reason || 'pause') + ' attach');
      }
      _reopenCount = 0;
    }
    if (_reopenCount >= _REOPEN_MAX) {
      console.warn('[KazmaChat] Attach declined — reopen budget exhausted (' + (reason || '?') + ')');
      return;
    }
    _reopenCount++;
    _attachInFlight = true;
    // The furthest this page has read on EITHER mouth. `_lastSeqSeen` lags:
    // no terminal callback records a seq, so after the previous turn's
    // attach it pointed before that turn's done -- and the replay then fed
    // the next turn's untagged frames to the previous turn's finished block.
    var cursor = Math.max(_lastSeqSeen || 0, _pageDeliveryCursor());
    console.warn('[KazmaChat] Attaching journal (' + (reason || '?') + ') from seq=' + cursor);
    try { noteTurnActivity(); } catch (eN) { /* ignore */ }
    var epoch = ++_sseEpoch;
    var callbacks = (typeof _buildSseCallbacks === 'function')
      ? _buildSseCallbacks(epoch)
      : _defaultAttachCallbacks(epoch);
    try {
      activeStream = stream.sse('/api/chat/stream', {
        session_id: chatSessionId,
        last_event_id: cursor,
        workspace_id: _activeWorkspaceId || '',
      }, callbacks);
    } finally {
      _attachInFlight = false;
    }
  }
  /**
   * A frame the stream could not apply: its handler threw, or its data was
   * not JSON. KazmaStream has already moved on to the next frame (one bad
   * frame used to stop the whole stream), so what that frame would have
   * painted comes back from server truth -- one resync, not one per frame.
   */
  var _frameErrorResyncAt = 0;
  function _onSseFrameError(type, err) {
    diag('sse-frame-error', String(type) + ': ' + String((err && err.message) || err || ''));
    var now = Date.now();
    if (now - _frameErrorResyncAt < 3000) return;
    _frameErrorResyncAt = now;
    try { _resyncDelivery('sse-frame-error'); } catch (eR) { /* never fatal */ }
  }

  function _defaultAttachCallbacks(epoch) {
    function _mine() { return epoch === _sseEpoch; }
    return {
      onFrameError: function(type, err) {
        if (!_mine()) return;
        _onSseFrameError(type, err);
      },
      onToken: function(data) {
        if (!_mine()) return;
        _noteSeq();
        applyTurnEvent({
          type: 'token',
          content: data.content,
          seq: data.seq,
          turn_id: data.turn_id || _liveTurnId,
          source: 'sse',
        });
      },
      onToolCall: function(data) {
        if (!_mine()) return;
        var inputs = data.inputs;
        if (typeof inputs === 'object') {
          try { inputs = JSON.stringify(inputs); } catch (e) { inputs = String(inputs); }
        }
        logProgress({
          kind: 'tool',
          title: data.tool_name || 'tool',
          detail: _toolDetailWithGist(_toolArgSummary(data.inputs), inputs),
          state: 'running',
          // The graph's own run id, so the LIVE row and the row the
          // server persisted are one row rather than two after a
          // refresh. activityToParts reads 'tool#<id>' back into
          // call_id, which is what partKey keys on.
          id: data.tool_call_id ? 'tool#' + data.tool_call_id : undefined,
        });
      },
      onToolResult: function(data) {
        if (!_mine()) return;
        logProgress({
          kind: 'tool',
          title: data.tool_name || 'tool',
          detail: _toolDetailWithGist(_toolResultSummary(data.result), data.result),
          state: 'done',
          // The graph's own run id, so the LIVE row and the row the
          // server persisted are one row rather than two after a
          // refresh. activityToParts reads 'tool#<id>' back into
          // call_id, which is what partKey keys on.
          id: data.tool_call_id ? 'tool#' + data.tool_call_id : undefined,
        });
      },
      onStatus: function(data) {
        if (!_mine()) return;
        _noteSeq();
        if ((data && data.status) === 'resync') {
          _lastSeqSeen = 0;
          _resyncDelivery('sse-gap');
        }
      },
      onHeartbeat: function(data) {
        // Journaled liveness frame — not epoch-gated (same rule as HITL):
        // a superseded stream's graph is the live graph.
        _noteSeq();
        // The heartbeat is the only frame carrying a SERVER-measured
        // elapsed. Without this the header would have to time the turn
        // itself, which is the client clock that printed "Done 0s" while
        // the graph was still working (plan §3).
        applyTurnEvent({
          type: 'turn_heartbeat',
          elapsed_s: (data && data.elapsed_s) || 0,
          seq: data && data.seq,
          turn_id: (data && data.turn_id) || _liveTurnId,
          source: 'sse',
        });
      },
      onApprovalRequired: function(data) {
        // HITL is not epoch-gated: a superseded stream's approval is still
        // the live question. Dropping it left the card only on Dashboard.
        // Replayed frames are history — registry reconciler paints pending.
        if (data && data.replay) return;
        if (_hitlAlreadyClaimed(data)) return;
        if (data && data.thread_id) _lastInterruptedThreadId = String(data.thread_id);
        pauseForApproval(data);
        _ingestFrameGateViews(data);
        applyTurnEvent({
          type: 'hitl',
          state: 'pending',
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
          view: (data && data.view) || undefined,
          turn_id: (data && data.turn_id) || _liveTurnId,
          source: 'sse',
        });
      },
      onHitl: function(data) {
        var st = String((data && data.state) || 'pending');
        // Replayed pending frames are history (ghost-card flash, 2026-09-03).
        if (st === 'pending' && data && data.replay) return;
        if (st === 'pending' && _hitlAlreadyClaimed(data)) return;
        if (st !== 'pending' && !_mine()) return;
        if (data && data.thread_id) _lastInterruptedThreadId = String(data.thread_id);
        if (st === 'pending') pauseForApproval(data);
        else _awaitingApproval = false;
        _ingestFrameGateViews(data);
        applyTurnEvent({
          type: 'hitl',
          state: st,
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
          view: (data && data.view) || undefined,
          turn_id: (data && data.turn_id) || _liveTurnId,
          source: 'sse',
        });
      },
      onDone: function(data) {
        if (!_mine()) return;
        activeStream = null;
        _ingestFrameGateViews(data);
        if (data && data.content) {
          applyTurnEvent({
            type: 'done',
            content: data.content,
            seq: data.seq,
            turn_id: data.turn_id || _liveTurnId,
            interrupted: !!(data && data.interrupted),
            source: 'done',
          });
          _forcePaintDoneContent(data.content);
        }
        if (hasLiveGate() || _awaitingApproval) {
          refreshSessionsSoon();
        } else {
          endTurn();
        }
        if (!data) {
          setTimeout(function() { _resyncDelivery('sse-truncated'); }, 400);
        }
      },
      onError: function() {
        if (!_mine()) return;
        activeStream = null;
        // HITL pause closes the HTTP body. That is catch-up, not a failed
        // turn — always resync. Skipping because `_awaitingApproval` left
        // the bubble on a frozen card with no journal tail.
        _resyncDelivery('sse-fail');
      }
    };
  }
  _reopenSseRef = _attachJournal;
  // True once THIS turn painted a real assistant reply — the "No response
  // received." fallback must never fire after a successful paint.
  var _turnPainted = false;

  // ── Send durability (outbox) ─────────────────────────────────────
  // A message whose POST never reached the server (restart / down) must
  // not vanish: it is parked in localStorage before dispatch, cleared on
  // the first streamed token (server got it), and restored with a Retry
  // affordance on the next load (2026-08-26 restart-mid-turn incident).
  function _outboxKey() { return 'kazma_outbox_' + (chatSessionId || ''); }
  function _outboxWrite(text) {
    try { localStorage.setItem(_outboxKey(), JSON.stringify({ text: text, ts: Date.now() })); } catch (e) { /* private mode */ }
  }
  function _outboxClear() {
    try { localStorage.removeItem(_outboxKey()); } catch (e) { /* ignore */ }
  }
  function _restoreUndeliveredOutbox(serverMessages) {
    try {
      var raw = localStorage.getItem(_outboxKey());
      if (!raw) return;
      var entry;
      try { entry = JSON.parse(raw); } catch (e) { _outboxClear(); return; }
      var text = String((entry && entry.text) || '').trim();
      if (!text) { _outboxClear(); return; }
      var msgs = serverMessages || [];
      for (var i = 0; i < msgs.length; i++) {
        if (msgs[i] && msgs[i].role === 'user'
            && String(msgs[i].content || '').trim() === text) {
          _outboxClear();  // the server did receive it after all
          return;
        }
      }
      lastSentUserText = text;  // Retry resends exactly this
      appendMessage('user', text);
      var notice = appendMessage(
        'assistant',
        '⚠️ _This message was **not delivered** — the server was down or restarting when you sent it. ' +
        'Tap retry to send it now._'
      );
      // Retry button injected as a real DOM element (post-render), not
      // markdown raw-HTML — the renderer only guarantees code-block
      // escaping (audit P2).
      try {
        var btn = document.createElement('button');
        btn.className = 'btn btn-sm btn-primary';
        btn.textContent = '↻ Retry';
        btn.addEventListener('click', function() {
          if (window.KazmaChat && typeof window.KazmaChat.retry === 'function') {
            window.KazmaChat.retry();
          }
        });
        (notice && notice.querySelector('.message-text')
          ? notice.querySelector('.message-text')
          : messagesEl).appendChild(btn);
      } catch (e) { /* ignore */ }
      scrollToBottom();
    } catch (e) { /* corrupt outbox — drop silently */ _outboxClear(); }
  }

  /** Running build identity in the sidebar footer (see /health/live build). */
  function _paintBuildBadge() {
    try {
      fetch('/health/live')
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(d) {
          var b = d && d.build;
          var el = document.getElementById('build-badge');
          if (!b || !el) return;
          var started = b.started_at
            ? new Date(b.started_at * 1000).toLocaleTimeString() : '';
          el.textContent = 'build ' + (b.commit || '?') + (started ? ' · up since ' + started : '');
        })
        .catch(function() { /* badge is best-effort */ });
    } catch (e) { /* ignore */ }
  }

  /**
   * Is the delivery stream actually able to deliver?
   *
   * `activeStream` being non-null is not an answer. The handle stays put
   * after the server closes the body unless a terminal callback nulls it,
   * and those callbacks are epoch-gated -- a superseded stream's `done`
   * frame nulls nothing. KazmaStream now reports `isClosed()` from inside
   * the stream, where it is a fact. Older handles without the getter fall
   * back to the old truthiness test.
   */
  function _streamIsLive() {
    if (!activeStream) return false;
    if (typeof activeStream.isClosed !== 'function') return true;
    return !activeStream.isClosed();
  }

  /* ── Delivery reconciler ────────────────────────────────────────────
   *
   * The one mechanism in this file that is not allowed to give up.
   *
   * Everything else that recovers a stuck turn asks a client-side question
   * first -- is the stream alive, is the card terminal, are we awaiting an
   * approval -- and skips the server when the answer is wrong. Each of
   * those guards has failed at least once, and each failure looks identical
   * to the operator: the answer exists on the server and the screen says
   * the agent is still waiting.
   *
   * 2026-09-14 is the case this was written for. An approval was granted,
   * the tool ran, a 3,725-character reply was persisted -- and the browser
   * made zero requests between the approve and the reply, because one
   * stale variable made every recovery path decline. Nothing was lost.
   * Nothing was delivered either.
   *
   * So this loop has NO guard on whether to ask. While a turn might still
   * be undelivered it asks the server, on a timer, and `_resyncDelivery`
   * paints whatever server truth turns out to be. It stops on one thing
   * only: the server saying the turn is over, twice in a row. Not on a
   * belief about the transport, not on a card's phase, not on a budget.
   *
   * It is deliberately convergent rather than correct-first-time: it does
   * not matter WHICH delivery path broke, because the next tick heals it.
   * That is the property worth having -- a bug in the stream becomes a few
   * seconds of delay instead of a lost answer.
   */
  var _RECONCILE_MS = 6000;
  var _RECONCILE_HIDDEN_MS = 30000;   // backoff, NOT a stop
  var _RECONCILE_IDLE_TICKS = 2;      // server must say idle twice
  var _reconcileTimer = null;
  var _reconcileIdleTicks = 0;

  function _turnMayBeUndelivered() {
    return !!(_isGenerating || _awaitingReply || _awaitingApproval
      || _serverGenerating || _serverPaused);
  }

  /** Idempotent: safe to call from every path that starts or resumes a turn. */
  function _startReconciler(reason) {
    _reconcileIdleTicks = 0;
    if (_reconcileTimer) return;
    _scheduleReconcile();
  }

  function _scheduleReconcile() {
    if (_reconcileTimer) return;
    var wait = (typeof document !== 'undefined' && document.hidden)
      ? _RECONCILE_HIDDEN_MS : _RECONCILE_MS;
    _reconcileTimer = setTimeout(_reconcileTick, wait);
  }

  function _reconcileTick() {
    _reconcileTimer = null;
    if (!chatSessionId) return;   // no session: there is nothing to deliver
    try { _resyncDelivery('reconcile'); } catch (eR) { /* never fatal */ }
    // Counted from the PREVIOUS resync's answer, which is the conservative
    // direction: it costs an extra tick, it cannot stop early.
    if (_turnMayBeUndelivered()) _reconcileIdleTicks = 0;
    else _reconcileIdleTicks += 1;
    if (_reconcileIdleTicks < _RECONCILE_IDLE_TICKS) _scheduleReconcile();
  }

  function _resyncDelivery(reason) {
    if (!chatSessionId) return;
    var sid = chatSessionId;
    // Race guard (audit P1-7): if the user sends a NEW message while this
    // resync's fetches are in flight, the stale "idle + durable assistant"
    // branch must not paint the previous reply into the new turn's bubble.
    var epochAtFetch = _sseEpoch;
    Promise.all([
      fetch('/api/chat/sessions/' + encodeURIComponent(sid) + '/status')
        .then(function(r) { return r.ok ? r.json() : null; })
        .catch(function() { return null; }),
      fetch('/api/chat/sessions/' + encodeURIComponent(sid) + '/messages')
        .then(function(r) { return r.ok ? r.json() : []; })
        .catch(function() { return []; }),
    ]).then(function(pair) {
      if (chatSessionId !== sid) return;
      if (_sseEpoch !== epochAtFetch) return; // a new turn started meanwhile
      var status = pair[0] || {};
      var messages = pair[1] || [];
      _ingestStatus(status);
      var generating = _serverGenerating;
      var paused = _serverPaused;
      var lastMsg = messages.length ? messages[messages.length - 1] : null;
      var liveHitl = _statusHasLiveHitl(status) || paused;

      // Registry answered authoritatively and the thread is idle → stamp
      // fossil live-button cards resolved (see _reconcileHitlCardsWithGates).
      if (!generating && !liveHitl) _reconcileHitlCardsWithGates();
      _rerenderHitlDocs();

      // Server idle (no live gate) after /abort or restart: do not keep the
      // composer locked on a fossil pending part. The next prompt is a turn.
      if (!generating && !liveHitl && _awaitingApproval) {
        _releaseHitlComposer('resync-idle');
      }
      if (liveHitl && !_awaitingApproval) {
        var liveView = _firstInteractiveView();
        pauseForApproval(liveView ? _payloadFromView(liveView) : null);
      }
      // Catch-up never skips because a card "looks" settled. Paint every
      // interactive view, then recover from the pending list even when
      // chrome is omitted or frozen.
      if (liveHitl) {
        _paintLiveGates();
        setTimeout(recoverMissedApproval, 0);
      }

      // This-turn durable (parts + interim text) even if the stream is live.
      // Previous-turn lastMsg is NOT this turn — skip it while paused so
      // we do not paint an older answer over a live question (2026-08-27).
      if (lastMsg && lastMsg.role === 'assistant' && _messageIsThisTurn(lastMsg)) {
        if (isPlanOnlyMessage(lastMsg.content)) {
          try { tryIngestPlanFromText(lastMsg.content); } catch (ePlan) { /* ignore */ }
        } else {
          applyTurnEvent({
            type: 'hydrate',
            source: 'resync',
            turn_id: lastMsg.turn_id || _liveTurnId,
            content: lastMsg.content,
            parts: lastMsg.parts,
            activity: lastMsg.activity,
            model: lastMsg.model || '',
            open: lastMsg.open,
            pending: lastMsg.pending,
            // The durable revision this snapshot covers. /status and
            // /messages are fetched in parallel and either can land late;
            // the document refuses a snapshot older than what it already
            // has (invariant U05).
            rev: lastMsg.rev,
            schema: lastMsg.schema,
          });
          if (!generating && !liveHitl && (lastMsg.content || '').trim()) {
            _forcePaintDoneContent(lastMsg.content);
          }
        }
      }

      // Still running server-side → keep waiting honestly AND re-attach a
      // live SSE stream from the journal cursor — but only when the stream
      // is genuinely DEAD. Aborting a healthy stream on every focus/visibility
      // trigger churned connections for no gain. This-turn durable already
      // projected above; this return must not skip that work.
      if (generating || liveHitl) {
        if (_streamIsLive()) {
          // A live stream owns this turn — NEVER abort it here. Aborting a
          // healthy stream forced a journal-cursor reopen whose replay
          // painted terminal segments, fragmenting one reply into multiple
          // bubbles each with its own "Writing reply…" row (2026-08-27
          // post-restart). The live stream IS the delivery path; a genuinely
          // dead stream is handled below (no activeStream → reopen).
          try {
            _setStatusStrip(paused
              ? ti('waiting_approval', 'Waiting for approval…')
              : ti('thinking', 'Kazma is thinking…'));
          } catch (e2) { /* ignore */ }
          return;
        }
        try {
          _setStatusStrip(paused
            ? ti('waiting_approval', 'Waiting for approval…')
            : ti('thinking', 'Kazma is thinking…'));
        } catch (e2) { /* ignore */ }
        // A stream that just closed (sse-fail / truncated) while paused
        // must not attach in this same tick: attach → close → resync
        // loops forever if the reopen budget is also waived. The
        // reconciler attaches on its timer. Visibility/focus/reconcile
        // still reopen a dead handle.
        var closedHere = reason === 'sse-fail' || reason === 'sse-truncated';
        if (liveHitl && closedHere) {
          return;
        }
        _awaitingReply = true;
        noteTurnActivity();
        if (_reopenSseRef) {
          try { _reopenSseRef('resync-' + (reason || '?')); } catch (e3) { /* ignore */ }
        }
        return;
      }

      // Server idle with a durable assistant answer → paint server truth.
      //
      // EXCEPT while a gate is genuinely waiting: the graph is paused on
      // HITL, so "idle + last durable reply" can be the PREVIOUS turn's
      // answer, and painting it over the live bubble swapped the visible
      // text for an older message on every app-switch (2026-08-27).
      //
      // "Genuinely" is any view.interactive. A fossil pending stamp, a
      // frozen card, or a dead stream handle cannot disarm this path.
      if (hasLiveGate()) return;

      // Idle: paint durable assistant text even if the row still carries a
      // leftover `pending` flag (detached persist wrote the answer, then
      // the client refreshed before close_reply_turn cleared the marker).
      // Plan-only rows are workbench chrome — never the answer.
      if (lastMsg && lastMsg.role === 'assistant' && isPlanOnlyMessage(lastMsg.content)) {
        try { tryIngestPlanFromText(lastMsg.content); } catch (ePlan) { /* ignore */ }
        return;
      }
      if (lastMsg && lastMsg.role === 'assistant' && (lastMsg.content || '').trim()) {
        applyTurnEvent({
          type: 'hydrate',
          source: 'resync',
          turn_id: lastMsg.turn_id || _liveTurnId,
          content: lastMsg.content,
          parts: lastMsg.parts,
          activity: lastMsg.activity,
          model: lastMsg.model || '',
          open: lastMsg.open,
          pending: lastMsg.pending,
          rev: lastMsg.rev,
          schema: lastMsg.schema,
        });
        // Refresh/pageshow must replace a leftover watchdog stamp with the
        // persisted reply (2026-09-08: replay showed "_No response received._"
        // while SessionStore already had the 1155-char answer).
        _forcePaintDoneContent(lastMsg.content);
        return;
      }

      // Idle with nothing to deliver → release the wait honestly.
      if (_awaitingReply || _isGenerating) {
        if (lastMsg && lastMsg.pending) return; // pending row may still flush
        _awaitingReply = false;
        if (_isGenerating) endTurn();
        else {
          _clearStatusStrip();
        }
      }
    }).catch(function() { /* transient network — the next trigger retries */ });
  }

  /**
   * Delivery rule (server → UI), Turn Delivery V2:
   *   SessionStore / seq-journaled terminal frames are source of truth.
   *   ALWAYS paint server truth into the open-turn assistant bubble.
   *   Deduplication lives UPSTREAM in the transports (seq-journaled WS/SSE,
   *   replay dedupe) — the painter never compares rendered text against
   *   desired content to decide whether to paint. The old
   *   fingerprint/≥90%-prefix "did it render?" heuristics were the root of
   *   the "no response until refresh" class and are gone.
   */

  /** /long /mission /unrestricted /yolo /plan — server answers without the graph. */
  function _isInstantCapacitySlash(text) {
    var h = String(text || '').trim().split(/\s+/)[0].toLowerCase();
    return h === '/long' || h === '/mission' || h === '/unrestricted'
      || h === '/yolo' || h === '/plan';
  }

  /** Render markdown to plain text for transcript dedupe on load. */
  function _plainFromMarkdown(md) {
    var s = String(md || '').trim();
    if (!s) return '';
    try {
      if (KS && KS.markdown) {
        var tmp = document.createElement('div');
        tmp.innerHTML = KS.markdown(s);
        return (tmp.textContent || '').replace(/\s+/g, ' ').trim();
      }
    } catch (e) { /* fall through */ }
    return s.replace(/\s+/g, ' ').trim();
  }

  /**
   * Collapse pathological assistant-only runs to one evolving row.
   *
   * One user turn should produce one assistant reply row. During delivery
   * glitches (multi-writer or replay drift), SessionStore may accumulate a
   * chain of consecutive assistant snapshots where each content is a growing
   * prefix of the next ("Schedule", "Schedule the", ...). Rendering that run
   * verbatim creates a fake CoT ladder that looks like many answers.
   *
   * This reducer keeps distinct assistant replies intact (different text with
   * no prefix relation) and only merges obviously related snapshots.
   */
  function _coalesceAssistantRuns(rows) {
    if (!Array.isArray(rows) || !rows.length) return rows || [];
    var out = [];
    rows.forEach(function(raw) {
      var msg = raw || {};
      var role = String(msg.role || '').toLowerCase();
      if (role !== 'assistant') { out.push(msg); return; }
      if (!out.length) { out.push(msg); return; }
      var prev = out[out.length - 1];
      var prevRole = String((prev && prev.role) || '').toLowerCase();
      if (prevRole !== 'assistant') { out.push(msg); return; }

      var p = String((prev && prev.content) || '').trim();
      var c = String(msg.content || '').trim();
      var sameTurn = !!(prev && prev.turn_id && msg.turn_id
        && String(prev.turn_id) === String(msg.turn_id));
      var related = (
        sameTurn ||
        !p || !c ||
        c.indexOf(p) === 0 ||
        p.indexOf(c) === 0 ||
        _plainFromMarkdown(p) === _plainFromMarkdown(c)
      );
      if (!related) { out.push(msg); return; }

      var merged = {};
      var k;
      for (k in prev) if (Object.prototype.hasOwnProperty.call(prev, k)) merged[k] = prev[k];
      for (k in msg) if (Object.prototype.hasOwnProperty.call(msg, k)) merged[k] = msg[k];
      // Prefer richer / later payload for related snapshots.
      if (!c && p) merged.content = prev.content;
      var prevParts = Array.isArray(prev.parts) ? prev.parts : [];
      var curParts = Array.isArray(msg.parts) ? msg.parts : [];
      if (prevParts.length > curParts.length) merged.parts = prevParts;
      var prevAct = Array.isArray(prev.activity) ? prev.activity : [];
      var curAct = Array.isArray(msg.activity) ? msg.activity : [];
      if (prevAct.length > curAct.length) merged.activity = prevAct;
      if (!msg.turn_id && prev.turn_id) merged.turn_id = prev.turn_id;
      out[out.length - 1] = merged;
    });
    return out;
  }

  /**
   * Assistant bubble for the open turn: the one after the last user message.
   * NEVER create a second assistant without a new user row (duplicate root cause).
   *
   * @param {boolean} [create=true] Pass false to LOOK ONLY — returns null
   *   instead of minting a bubble. A progress-only frame that minted one
   *   left a bare avatar + timestamp + reaction buttons with nothing in it
   *   sitting above the composer until the first token: the "empty plain
   *   bubble before streaming" every turn opened with.
   */
  function _assistantBubbleForOpenTurn(create) {
    var mayCreate = create !== false;
    if (!messagesEl) return mayCreate ? createAssistantMessage() : null;
    var msgs = messagesEl.querySelectorAll('.message-user, .message-assistant');
    var lastAsstAfterUser = null;
    for (var i = 0; i < msgs.length; i++) {
      // CoT can swallow a nested .message; that node is not the open turn.
      // Pinning it put the HITL card inside overflow:hidden / collapsed body
      // so the dashboard listed the gate and chat looked empty (2026-09-02).
      if (msgs[i].closest && msgs[i].closest('.agent-progress')) continue;
      if (msgs[i].classList.contains('message-user')) {
        lastAsstAfterUser = null;
      } else if (msgs[i].classList.contains('message-assistant')) {
        lastAsstAfterUser = msgs[i];
      }
    }
    if (lastAsstAfterUser) return lastAsstAfterUser;
    return mayCreate ? createAssistantMessage() : null;
  }

  /** Pin the open-turn assistant. NEVER `createAssistantMessage()` from
   *  progress/HITL/token paths — that minted the CoT ladder (one bubble per
   *  plan hop) after currentMsgEl was left null by historical-render. */
  function _pinLiveAssistantBubble(create) {
    var el = _assistantBubbleForOpenTurn(create);
    if (el) currentMsgEl = el;
    return el;
  }

  // ── Slash commands (catalog lives in chat_slash.js) ───────────
  var SLASH_COMMANDS = window.KAZMA_SLASH_COMMANDS || [
    { cmd: '/help', desc: 'List available slash commands' },
  ];

  function _cmdHead(text) {
    return String(text || '').trim().split(/\s+/)[0].toLowerCase();
  }

  function isAbortCommand(text) {
    return _cmdHead(text) === '/abort';
  }

  function isSteerCommand(text) {
    var h = _cmdHead(text);
    return h === '/steer' || h === '/steer!';
  }

  function isSteerOrAbortCommand(text) {
    return isSteerCommand(text) || isAbortCommand(text);
  }

  function steerBody(text) {
    var rest = (String(text || '').trim().split(/\s(.+)/)[1] || '').trim();
    // Leftover menu placeholders like "<text>" are not a real note.
    if (!rest || /^<[^>]+>$/.test(rest)) return '';
    return rest;
  }

  function currentThreadId() {
    if (!chatSessionId) return '';
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i] && sessions[i].session_id === chatSessionId) {
        return sessions[i].thread_id || chatSessionId;
      }
    }
    return chatSessionId;
  }

  /** Stop vs Send: a steer/abort draft in the box must be submittable. */
  function syncSendButtonForDraft() {
    if (!sendBtn) return;
    var draft = (inputEl && inputEl.value || '').trim();
    var canSendSteer = isSteerOrAbortCommand(draft);
    if (_isGenerating && canSendSteer) {
      sendBtn.disabled = false;
      sendBtn.classList.remove('stop-mode');
      sendBtn.title = isAbortCommand(draft)
        ? 'Abort the running task'
        : (steerBody(draft) ? 'Send steer (Enter)' : 'Type your steer, then Enter');
      sendBtn.innerHTML = _SEND_SVG;
      return;
    }
    if (_isGenerating) {
      sendBtn.disabled = false;
      sendBtn.classList.add('stop-mode');
      sendBtn.title = ti('stop_generation', 'Stop generation');
      sendBtn.innerHTML = _STOP_SVG;
      return;
    }
    sendBtn.classList.remove('stop-mode');
    sendBtn.innerHTML = _SEND_SVG;
    sendBtn.title = _awaitingApproval
      ? 'Send steer or command'
      : 'Send (Enter / Ctrl+Enter)';
  }

  function ensureSlashMenu() {
    var menu = document.getElementById('chat-slash-menu');
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'chat-slash-menu';
    menu.className = 'chat-slash-menu';
    menu.style.cssText =
      'display:none;position:absolute;bottom:100%;left:0;right:0;max-height:220px;' +
      'overflow:auto;background:var(--bg-elevated);border:1px solid var(--border);' +
      'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.25);z-index:50;margin-bottom:6px;';
    var wrapper = document.querySelector('.input-wrapper') || (inputEl && inputEl.parentElement);
    if (wrapper) {
      if (getComputedStyle(wrapper).position === 'static') wrapper.style.position = 'relative';
      wrapper.appendChild(menu);
    }
    return menu;
  }

  function hideSlashMenu() {
    var menu = document.getElementById('chat-slash-menu');
    if (menu) menu.style.display = 'none';
  }

  function showSlashMenu(filter) {
    var menu = ensureSlashMenu();
    var q = (filter || '/').toLowerCase();
    var matches = SLASH_COMMANDS.filter(function(c) {
      return c.cmd.indexOf(q) === 0 || c.cmd.indexOf(q.replace(/^\//, '')) >= 0;
    });
    if (!matches.length) { hideSlashMenu(); return; }
    menu.innerHTML = matches.map(function(c) {
      var insertAttr = c.insert
        ? ' data-insert="' + escapeHtml(c.insert) + '"'
        : '';
      return '<button type="button" class="chat-slash-item" data-cmd="' + escapeHtml(c.cmd) + '"' +
        insertAttr + ' ' +
        'style="display:flex;flex-direction:column;align-items:flex-start;width:100%;' +
        'padding:8px 12px;border:0;background:transparent;color:var(--text-primary);' +
        'cursor:pointer;text-align:left;border-bottom:1px solid var(--border-subtle);">' +
        '<code style="font-size:0.85rem;color:var(--accent);">' + escapeHtml(c.cmd) + '</code>' +
        '<span style="font-size:0.72rem;color:var(--text-muted);">' + escapeHtml(c.desc) + '</span>' +
        '</button>';
    }).join('');
    menu.style.display = 'block';
    menu.querySelectorAll('.chat-slash-item').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var insert = btn.getAttribute('data-insert');
        var cmd = btn.getAttribute('data-cmd') || '';
        hideSlashMenu();
        if (!inputEl) return;
        // /steer and /steer! queue a draft so the user can edit, then send.
        // Other complete commands (/help, /yolo, /abort) still send immediately.
        if (insert) {
          inputEl.value = insert;
          inputEl.focus();
          try { inputEl.setSelectionRange(insert.length, insert.length); } catch (e) {}
          onInputResize.call(inputEl);
          if (window.showToast) {
            window.showToast(
              'Steer queued — add your note, then Enter to apply.',
              'info', 2800);
          }
          return;
        }
        inputEl.value = cmd;
        inputEl.focus();
        sendMessage();
      });
      btn.addEventListener('mouseenter', function() {
        btn.style.background = 'var(--bg-surface)';
      });
      btn.addEventListener('mouseleave', function() {
        btn.style.background = 'transparent';
      });
    });
  }

  function _clearComposer() {
    if (!inputEl) return;
    inputEl.value = '';
    inputEl.style.height = 'auto';
    try { inputEl.setAttribute('dir', 'auto'); } catch (e) { /* ignore */ }
    syncInputBidi();
    updateComposerCharCount();
    syncSendButtonForDraft();
  }

  // ── Input handling ────────────────────────────────────
  function onInputKeydown(e) {
    // IME (Arabic/CJK): Enter confirms composition. Sending on that
    // keydown clears the box, then compositionend puts the committed
    // text back — the "sent message still sitting in the composer" bug.
    if (e.isComposing || e.keyCode === 229) return;
    // Enter (without Shift and without Ctrl) sends the message.
    // Ctrl+Enter also sends the message (so users who press Ctrl+Enter
    // from muscle-memory get the expected behaviour).
    if (e.key === 'Escape') {
      if (_modalOrOverlayOpen()) return;
      if (_isGenerating) { abortGeneration(); return; }
      hideSlashMenu();
      return;
    }
    // While generating: /steer|/abort go to the live turn; any other draft
    // stop-and-sends (Enter used to no-op, forcing a Stop click first).
    if (_isGenerating && e.key === 'Enter') {
      var draft = (inputEl && inputEl.value || '').trim();
      if (!e.shiftKey && isSteerOrAbortCommand(draft)) {
        e.preventDefault();
        hideSlashMenu();
        sendMessage();
        return;
      }
      if (!e.shiftKey && draft) {
        e.preventDefault();
        hideSlashMenu();
        abortThenSend();
        return;
      }
      if (!e.shiftKey) e.preventDefault();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      hideSlashMenu();
      sendMessage();
      return;
    }
    // Ctrl+Enter or Cmd+Enter sends the message
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      hideSlashMenu();
      sendMessage();
      return;
    }
    // Shift+Enter inserts a newline (default textarea behaviour — no preventDefault)
  }

  function onInputResize() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 200) + 'px';
    var v = (this.value || '');
    if (v.startsWith('/') && v.indexOf('\n') < 0 && v.length < 40) {
      showSlashMenu(v.toLowerCase());
    } else {
      hideSlashMenu();
    }
    // Per-content bidi: Arabic in an English UI needs rtl base + Arabic font
    syncInputBidi();
    // Composer character counter (live; reset on send)
    updateComposerCharCount();
    syncSendButtonForDraft();
  }

  /** Live character counter on the composer footer badge. */
  function updateComposerCharCount() {
    if (!charBadge || !inputEl) return;
    var n = (inputEl.value || '').length;
    charBadge.textContent = n ? String(n) : '';
    charBadge.classList.toggle('is-empty', n === 0);
    charBadge.hidden = n === 0;
  }

  function formatCompactCount(n) {
    n = Math.max(0, Number(n) || 0);
    if (n < 1000) return String(Math.round(n));
    if (n < 1000000) {
      var k = n / 1000;
      var digits = k < 10 ? 1 : (k < 100 ? 1 : 0);
      return k.toFixed(digits).replace(/\.0$/, '') + 'k';
    }
    return (n / 1000000).toFixed(2).replace(/\.?0+$/, '') + 'M';
  }

  /**
   * Keep the composer base direction in sync with typed content.
   * English UI (html dir=ltr) still must render Arabic input RTL so caret,
   * alignment, and mixed Latin/Arabic order are correct.
   */
  function syncInputBidi() {
    if (!inputEl) return;
    var v = inputEl.value || '';
    var hasAr = window.KazmaBidi
      ? KazmaBidi.hasArabic(v)
      : /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/.test(v);
    if (!v) {
      inputEl.setAttribute('dir', 'auto');
      inputEl.classList.remove('ar-input');
      return;
    }
    if (hasAr) {
      var arDom = window.KazmaBidi
        ? KazmaBidi.isArabicDominant(v)
        : true;
      // First strong char Arabic → rtl; mixed but Arabic present → auto
      // (browser picks from first strong). Dominant Arabic forces rtl.
      inputEl.setAttribute('dir', arDom ? 'rtl' : 'auto');
      inputEl.classList.add('ar-input');
    } else {
      inputEl.setAttribute('dir', 'ltr');
      inputEl.classList.remove('ar-input');
    }
  }

  // Original SVG icons for the send button (restored after Stop mode).
  var _SEND_SVG = '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
  var _STOP_SVG = '<svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
  /**
   * Single turn-state machine shared by SSE and WebSocket transports.
   *
   * Root cause of "must press Stop/ESC before I can chat again":
   * chat preferred the WebSocket bus when connected, called beginTurn()
   * (Stop pulses, Enter blocked), but never called endTurn() because SSE
   * onDone never fires on that path — and newSession used to leave the
   * flag set. Both transports must end every turn.
   */
  var _isGenerating = false;
  /** The operator asked to stop and the server has not answered yet.
   *  Produces "Stopping…" and NOTHING else: only the server can say a
   *  cancellation took (plan §7, "do not prematurely mark cancelled"). */
  var _stopRequested = false;
  var _awaitingApproval = false;
  var _serverGenerating = false;
  var _serverPaused = false;
  var _serverGateViews = [];
  var _serverGatesAuth = false;
  var _serverThreadId = '';
  var _attachInFlight = false;

  /** Drop HITL/turn client leftovers when switching sessions.
   *  Leftover `_serverGenerating=true` from a previous session's post-Approve
   *  resync painted the next session's pending card as already approved
   *  (2026-09-01). Grants stay server-side and thread-scoped; this is UI only. */
  function _isRetiredTurn(id) {
    if (!id) return false;
    return _retiredTurnIds.indexOf(String(id)) !== -1;
  }
  function _retireLiveTurn() {
    var id = String(_liveTurnId || '');
    if (id && id !== 'live' && _retiredTurnIds.indexOf(id) === -1) {
      _retiredTurnIds.push(id);
    }
    while (_retiredTurnIds.length > _RETIRED_CAP) _retiredTurnIds.shift();
    _supersededLive = true;
    _liveTurnId = '';
    _liveRenderEl = null;
    currentMsgEl = null;
  }

  function _syncPrefsSession() {
    var prefs = _turnPrefs();
    if (prefs) {
      try { prefs.setSession(chatSessionId || ''); } catch (e) { /* ignore */ }
    }
  }

  function _resetSessionTurnState() {
    _docs = {};
    // The turn→bubble registry belongs to the transcript on screen. Leaving
    // it populated across a session switch would let a turn id from the old
    // session resolve to a node that is about to be thrown away.
    var TVr = _turnView();
    if (TVr) TVr.releaseAll();
    _invariantSeen = {};
    _invariantResyncs = 0;
    _liveTurnId = '';
    _retiredTurnIds = [];
    _supersededLive = false;
    _serverGenerating = false;
    _serverPaused = false;
    _serverGateViews = [];
    _serverGatesAuth = false;
    _clearHitlOverlay();
    _serverThreadId = '';
    _lastInterruptedThreadId = '';
    _awaitingApproval = false;
    _clearStoreApproval();
    // Switching sessions is not the END of a turn — it is the ABSENCE of
    // one. forceEndTurn's 'done' frame left the card on screen for its
    // 1.6s retire animation, so a brand-new empty session flashed a
    // "Done" task card for a turn that never happened (2026-09-03).
  }

  /** Apply a /status payload. Join-before-paint: call this BEFORE TurnView
   *  renders hydrated history so live views exist on the first pass. */
  function _ingestStatus(status) {
    status = status || {};
    _serverGenerating = !!status.generating;
    _serverPaused = !!status.paused;
    // Merge, do not replace. /status builds gate_views from live_gates(),
    // whose LIVE_STATES are ("pending","claimed","resuming") — a gate that has
    // SETTLED is simply absent from the payload. A wholesale assign therefore
    // deleted every settled card on the next status poll, the slot plan lost
    // them, and they fell to keptTail UNDER the answer. That is the identical
    // 2026-09-20 sequential-card defect that _ingestFrameGateViews was fixed
    // for; this path kept the old assignment and so kept the bug, which is
    // why the merge now lives in one function instead of two.
    //
    // A status payload for a DIFFERENT thread replaces rather than merges:
    // carrying one thread's cards onto another is not a partial snapshot, it
    // is the wrong conversation.
    var incoming = Array.isArray(status.gate_views) ? status.gate_views : [];
    var sameThread =
      !status.thread_id ||
      !_serverThreadId ||
      String(status.thread_id) === String(_serverThreadId);
    _serverGateViews = sameThread ? _mergeGateViews(incoming) : incoming;
    _serverGatesAuth = !!status.gates_authoritative;
    if (status.thread_id) _serverThreadId = String(status.thread_id);
  }

  /** Merge a partial gate_views snapshot over what we already hold.
   *
   *  Incoming wins per id. Prior views the snapshot omits are KEPT unless
   *  they are still interactive — a card the server no longer lists as live
   *  has settled, and a settled card must stay on screen in its slot. An
   *  omitted *interactive* view is genuinely gone (superseded/aborted) and is
   *  dropped.
   *
   *  Shared by the /status poll and by journal HITL/done frames so the two
   *  cannot drift again. */
  function _mergeGateViews(incoming) {
    var by = {};
    var seen = {};
    var i, v, id, out;
    var prev = _serverGateViews || [];
    for (i = 0; i < prev.length; i++) {
      v = prev[i] || {};
      id = String(v.interrupt_id || v.gate_id || '');
      if (id) by[id] = v;
    }
    for (i = 0; i < incoming.length; i++) {
      v = incoming[i] || {};
      id = String(v.interrupt_id || v.gate_id || '');
      if (!id) continue;
      by[id] = v;
      seen[id] = true;
    }
    out = incoming.slice();
    for (id in by) {
      if (!Object.prototype.hasOwnProperty.call(by, id) || seen[id]) continue;
      v = by[id];
      if (v && v.interactive) continue;
      out.push(v);
    }
    return out;
  }

  /** Journal HITL/done frames carry gate_views. Do not touch generating/paused.
   *  Incoming overwrites by id. A partial snapshot must not drop prior
   *  non-pending views — that omitted the claimed cards from slotPlan and
   *  they fell to keptTail UNDER the answer (2026-09-20 sequential). */
  function _ingestFrameGateViews(data) {
    if (!data || !Array.isArray(data.gate_views)) return;
    _serverGateViews = _mergeGateViews(data.gate_views);
    _serverGatesAuth = true;
    try { _rerenderHitlDocs(); } catch (eGv) { /* ignore */ }
  }

  function _viewIsPending(v) {
    return !!(v && v.interactive === true);
  }

  function _firstInteractiveView() {
    var list = _serverGateViews || [];
    var i;
    for (i = 0; i < list.length; i++) {
      if (_viewIsPending(list[i])) return list[i];
    }
    return null;
  }

  /** Optimistic inflight until approve 200/409/4xx/catch or 15s→resync. */
  var _hitlOverlay = {};
  var _HITL_OVERLAY_MS = 15000;

  function _setHitlOverlay(iid, view) {
    iid = String(iid || '');
    if (!iid || !view) return;
    var prev = _hitlOverlay[iid];
    if (prev && prev.timer) clearTimeout(prev.timer);
    var timer = setTimeout(function () {
      _clearHitlOverlay(iid);
      try { _resyncDelivery('overlay-ttl'); } catch (eOv) { /* ignore */ }
    }, _HITL_OVERLAY_MS);
    _hitlOverlay[iid] = { view: view, timer: timer };
  }

  function _clearHitlOverlay(iid) {
    if (!iid) {
      var k;
      for (k in _hitlOverlay) {
        if (!Object.prototype.hasOwnProperty.call(_hitlOverlay, k)) continue;
        if (_hitlOverlay[k] && _hitlOverlay[k].timer) clearTimeout(_hitlOverlay[k].timer);
      }
      _hitlOverlay = {};
      return;
    }
    iid = String(iid);
    var rec = _hitlOverlay[iid];
    if (rec && rec.timer) clearTimeout(rec.timer);
    delete _hitlOverlay[iid];
  }

  function _mergeGateView(view) {
    if (!view || typeof view !== 'object') return;
    var iid = String(view.interrupt_id || view.gate_id || '');
    if (!iid) return;
    var list = (_serverGateViews || []).slice();
    var i, found = false;
    for (i = 0; i < list.length; i++) {
      var v = list[i] || {};
      if (String(v.interrupt_id || '') === iid || String(v.gate_id || '') === iid) {
        list[i] = view;
        found = true;
        break;
      }
    }
    if (!found) list.push(view);
    _serverGateViews = list;
  }

  function _applyApproveView(body, fallbackIid) {
    body = body || {};
    var iid = String(body.interrupt_id || (body.view && body.view.interrupt_id) || fallbackIid || '');
    _clearHitlOverlay(iid);
    if (body.view && typeof body.view === 'object') _mergeGateView(body.view);
    return body.view || null;
  }

  function _statusHasLiveHitl(status) {
    var views = (status && Array.isArray(status.gate_views))
      ? status.gate_views : (_serverGateViews || []);
    var i;
    for (i = 0; i < views.length; i++) {
      if (_viewIsPending(views[i])) return true;
    }
    return !!(status && status.paused);
  }

  /** Progress-idle failsafe — only fires when NO activity for IDLE ms (not wall-clock). */
  var _turnWatchdogTimer = null;
  /** Desync healer: if agent store is idle but Stop is still on, release. */
  var _turnSyncTimer = null;
  /** No tool/token/status for this long → unlock UI + start catch-up poller (NOT false Done). */
  var TURN_IDLE_WATCHDOG_MS = 5 * 60 * 1000;
  var _lastTurnActivityTs = 0;
  /** True once the server has emitted any frame (token/tool/status) this turn.
   *  Gates the desync healer (below) so it can't fire during the startup gap
   *  between beginTurn() and the first server frame — the cause of the false
   *  "Done · 1s" heading that sometimes flashed ~1.5s after sending a message. */
  var _serverActivitySeen = false;

  function _clearTurnTimers() {
    if (_turnWatchdogTimer) {
      clearTimeout(_turnWatchdogTimer);
      _turnWatchdogTimer = null;
    }
  }

  // ── Delivery wait flag ────────────────────────────────────────────────
  // Set on send and cleared only when server truth is on screen (paint or
  // resync). No interval polls it anymore — recovery is trigger-driven
  // (visibility/focus/resume/seq-gap) plus the idle watchdog below.
  var _awaitingReply = false;

  /** Call on every live frame (token/tool/status) so long multi-tool turns stay open. */
  function noteTurnActivity() {
    _lastTurnActivityTs = Date.now();
    _serverActivitySeen = true;
    if (_isGenerating && !_awaitingApproval) {
      _armTurnWatchdog();
    }
  }

  function _armTurnWatchdog() {
    _clearTurnTimers();
    _turnWatchdogTimer = setTimeout(function() {
      _turnWatchdogTimer = null;
      if (!_isGenerating) return;
      if (_awaitingApproval) {
        // Waiting on the operator is not idleness -- but this branch used
        // to `return` WITHOUT re-arming, unlike every other branch here.
        // One firing while an approval card was on screen disarmed the
        // watchdog for the rest of the turn. Re-arm; the reconciler runs
        // regardless, and this stays as a second net rather than a latch.
        _armTurnWatchdog();
        return;
      }
      var idleFor = Date.now() - (_lastTurnActivityTs || 0);
      if (idleFor < TURN_IDLE_WATCHDOG_MS - 500) {
        // Activity arrived after schedule — re-arm.
        _armTurnWatchdog();
        return;
      }
      // Idle too long (server heartbeats every ≤15s during active turns, so
      // this means the transport is dead or the server stalled). Do NOT
      // unlock or claim anything — ask the server what is true. Resync keeps
      // the turn open if generating; paints/unlocks if the turn ended.
      console.warn('[KazmaChat] Idle turn watchdog — reconciling with server truth');
      if (_progressEl) {
        var titleEl = _progressEl.querySelector('.agent-progress-title');
        if (titleEl) {
          titleEl.textContent = ti('still_working_bg', 'Still working in background\u2026');
        }
      }
      _resyncDelivery('idle-watchdog');
    }, TURN_IDLE_WATCHDOG_MS);
  }

  // ── Turn timing format ──────────────────────────────────────────────
  //
  // What stood here was the Live Task Card: a docked status bar above the
  // composer with its own phase machine, its own clock, its own stall
  // detector and its own step list. docs/plans/UNIFIED_TURN_BLOCK.md §3
  // removes it — one turn gets ONE status surface, and it lives in the
  // turn block (see _buildTurnHeader / modules/turn_presentation.js).
  //
  // Where each of its jobs went:
  //   phase, elapsed, counts, Stop  -> the turn header
  //   open/closed body              -> the activity disclosure, whose
  //                                    state is now a reader preference
  //                                    (modules/turn_preferences.js)
  //   step list                     -> the workbench, which already had one
  //   stall detection + resync      -> _reconcileTick, which was ALREADY
  //                                    polling every 6s while a turn might
  //                                    be undelivered. The bar ran a second
  //                                    recovery loop beside it with its own
  //                                    retry budget; all that added was a
  //                                    label, and that label is now the
  //                                    header's connection indicator.
  //
  /** Seconds as m:ss / h:mm:ss. Pure formatting, no clock of its own. */
  function _fmtMMSS(total) {
    var s = Math.max(0, Math.floor(Number(total) || 0));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    if (h) {
      return h + ':' + (m < 10 ? '0' : '') + m + ':' + (sec < 10 ? '0' : '') + sec;
    }
    return m + ':' + (sec < 10 ? '0' : '') + sec;
  }



  /**
   * A compact "what is it doing this to" for the card header: the first
   * meaningful scalar out of a tool's arguments. "Running file_search" tells
   * you far less than 'Running file_search "auth middleware"'.
   */
  var _TOOL_ARG_SKIP = { session_id: 1, thread_id: 1, workspace_id: 1, turn_id: 1, id: 1 };
  var _TOOL_ARG_PREFER = ['query', 'q', 'path', 'file', 'file_path', 'url', 'name',
    'command', 'cmd', 'pattern', 'text', 'prompt', 'title', 'to'];
  function _toolArgSummary(inputs) {
    var obj = inputs;
    if (typeof obj === 'string') {
      var s = obj.trim();
      if (!s) return '';
      if (s.charAt(0) === '{' || s.charAt(0) === '[') {
        try { obj = JSON.parse(s); } catch (eP) { return _toolQuote(s); }
      } else {
        return _toolQuote(s);
      }
    }
    if (!obj || typeof obj !== 'object') return '';
    if (Array.isArray(obj)) return obj.length ? _toolArgSummary(obj[0]) : '';
    var k, i;
    for (i = 0; i < _TOOL_ARG_PREFER.length; i++) {
      k = _TOOL_ARG_PREFER[i];
      if (typeof obj[k] === 'string' && obj[k].trim()) return _toolQuote(obj[k]);
      if (typeof obj[k] === 'number') return _toolQuote(String(obj[k]));
    }
    var keys = Object.keys(obj);
    for (i = 0; i < keys.length; i++) {
      k = keys[i];
      if (_TOOL_ARG_SKIP[k]) continue;
      var v = obj[k];
      if (typeof v === 'string' && v.trim()) return _toolQuote(v);
      if (typeof v === 'number' || typeof v === 'boolean') return _toolQuote(String(v));
    }
    return '';
  }
  function _toolQuote(s) {
    s = String(s).replace(/\s+/g, ' ').trim();
    if (!s) return '';
    return '“' + truncateStr(s, 48) + '”';
  }

  /**
   * Tool-step detail formatting lives in static/js/turn_detail.js: pure
   * functions with no DOM, so a test can execute them instead of only reading
   * them. chat.js is 7,700 lines inside an IIFE around a browser — logic that
   * can only be verified by reading it is logic that drifts.
   *
   * The wrappers degrade to the raw value if that file fails to load: a
   * missing gist is a cosmetic loss, and a step row that throws is not.
   */
  function _toolResultSummary(result) {
    var M = window.KazmaTurnDetail;
    return M ? M.resultSummary(result) : '';
  }
  function _toolDetailWithGist(gist, raw) {
    var M = window.KazmaTurnDetail;
    return M ? M.withGist(gist, raw) : String(raw == null ? '' : raw);
  }

  /** Alpine store liveness flag. Split out of _setStatusStrip so a turn can
   *  mark itself thinking WITHOUT stamping a text override on the card. */
  function _setStoreThinking(on, msg) {
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent')) {
        var st = Alpine.store('agent');
        st.isThinking = !!on;
        if (on && msg) st.statusMessage = msg;
      }
    } catch (e) { /* store not ready */ }
  }

  /** Legacy strip call sites route here — one surface, one writer. */
  function _setStatusStrip(msg) {
    // Store flag kept for WS liveness logic; it no longer owns any DOM.
    _setStoreThinking(true, msg);
  }
  function _clearStatusStrip() {
    _setStoreThinking(false);
  }

  function _directChildByClass(parent, cls) {
    if (!parent || !parent.children) return null;
    for (var i = 0; i < parent.children.length; i++) {
      if (parent.children[i].classList && parent.children[i].classList.contains(cls)) {
        return parent.children[i];
      }
    }
    return null;
  }

  function _bubbleContent(el) {
    if (!el) return null;
    if (el.classList && el.classList.contains('message-content')) return el;
    return _directChildByClass(el, 'message-content');
  }

  /**
   * Collapse every FINISHED workbench to its one-line summary.
   *
   * Called at the start of a turn, never at the end. Collapsing a panel
   * removes a few hundred pixels from above the reply; doing that at the
   * terminal frame yanked the just-painted answer up the screen (the
   * end-of-turn "flash"). At the start of the next turn the view is already
   * moving to the new user message, so the same shift is invisible.
   */
  function _collapseFinishedWorkbenches() {
    if (!messagesEl) return;
    var panels = messagesEl.querySelectorAll('.agent-progress.is-done');
    for (var i = 0; i < panels.length; i++) {
      var p = panels[i];
      if (p.classList.contains('is-collapsed')) continue;
      var bubble = p.closest('.message');
      if (bubble && bubble.querySelector('.hitl-approval-card button:not([disabled])')) continue;
      p.classList.add('is-collapsed');
      var chev = p.querySelector('.agent-progress-chevron');
      if (chev) chev.textContent = '▸';
      var hdr = p.querySelector('.agent-progress-header');
      if (hdr) hdr.setAttribute('aria-expanded', 'false');
    }
  }

  function _resetTurnState() {
    currentMsgEl = null;
    _liveTurnId = '';
    _turnPainted = false;
    _progressEl = null;
    _progressStepCount = 0;
    _progressToolCount = 0;
    _planItems = [];
    _planParsedFromText = false;
    _lastTurnStats = null;
    // Drop the previous turn's 'live' document. Otherwise the first
    // logProgress merges into leftover parts and _answerFromDoc paints
    // yesterday's reasoning over this turn's bubble — and if that node
    // is the You row, the sent text vanishes from the chip (2026-09-01).
    try {
      if (window.KazmaTurnDocument && typeof KazmaTurnDocument.empty === 'function') {
        _docs.live = KazmaTurnDocument.empty('live');
      } else {
        delete _docs.live;
      }
    } catch (eLive) {
      try { delete _docs.live; } catch (eDel) { /* ignore */ }
    }
    // Release any bubble a previous turn left carrying the placeholder id
    // (older builds stamped it; a restored transcript can carry it too).
    // While one exists, this turn's untagged frames would paint into it
    // instead of into this turn's own bubble.
    try {
      var _stale = messagesEl
        ? messagesEl.querySelectorAll('.message-assistant[data-turn-id="live"]')
        : [];
      for (var _si = 0; _si < _stale.length; _si++) {
        _stale[_si].removeAttribute('data-turn-id');
      }
    } catch (eStale) { /* ignore */ }
    // The registry twin of the strip above. A previous turn that broke
    // before the server named it is still bound under 'live'; left there,
    // this turn's frames resolve to that bubble (2026-09-24).
    var TVp = _turnView();
    if (TVp && typeof TVp.releasePlaceholder === 'function') TVp.releasePlaceholder();
  }

  /**
   * @param {{resume?: boolean}} [opts] `resume: true` continues the turn that
   *   is already on screen (HITL approve / deny) instead of starting a new
   *   one. A resume MUST NOT reset the workbench: the panel below belongs to
   *   this same turn and holds every step that led up to the approval card.
   *   Wiping it made the whole CoT vanish the instant you clicked Approve,
   *   leaving a lone "Thinking…" row above the answer.
   */
  function beginTurn(opts) {
    _stopRequested = false;
    _startHeaderTicker();
    var resume = !!(opts && opts.resume);
    _isGenerating = true;
    _awaitingApproval = false;
    // Tidy the transcript BEFORE this turn adds to it (see the function's
    // comment for why this cannot happen at the end of a turn). A resume is
    // not a new turn, so there is nothing new to tidy for.
    if (!resume) _collapseFinishedWorkbenches();
    _lastTurnActivityTs = Date.now();
    _serverActivitySeen = false;
    // Status strip shows the instant ANY turn starts (SSE, WS, or
    // approve-resume) — no longer dependent on WS frames arriving.
    // A resume is not a new card epoch (keeps elapsed/step).
    // Store flag only. Stamping a text override here painted "Kazma is
    // thinking\u2026" over the phase the line above just set \u2014 every approve
    // rendered as "\u21bb Kazma is thinking\u2026" instead of "Resuming after
    // approval". The card's own phase is the label.
    _setStoreThinking(true, ti('thinking', 'Kazma is thinking\u2026'));
    // Keep visibility recovery armed even if no token frames arrive before
    // the user switches tabs (WS can be silent for seconds at turn start).
    _armTurnWatchdog();
    // Fresh progress log for this turn. Previous bubbles keep their CoT
    // accordion — never strip another turn's panel. A resume keeps the
    // open bubble (HITL). A new user message detaches currentMsgEl so
    // logProgress opens a new assistant bubble.
    if (!resume) {
      currentMsgEl = null;
      _resetTurnState();
      logProgress({ kind: 'status', title: ti('thinking', 'Kazma is thinking\u2026'), state: 'running' });
    }
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.placeholder = ti('thinking_queue', 'Kazma is thinking\u2026 type to queue your next message');
    }
    hideSlashMenu();
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.classList.add('stop-mode');
      sendBtn.title = ti('stop_generation', 'Stop generation');
      sendBtn.innerHTML = _STOP_SVG;
    }
    syncSendButtonForDraft();
  }

  // ── Turn lifecycle diagnostics ───────────────────────────────────
  // Ring buffer of the last turn-lifecycle events. The 2026-08-26 "done in
  // 1s, no response, message never persisted" incident left no trace
  // anywhere — this makes the next one self-identifying:
  // window.KazmaChat.diagnostics() (or the console table dumped on error)
  // shows the exact dispatch/terminal sequence.
  var _diag = [];
  function diag(ev, detail) {
    try {
      _diag.push({ t: new Date().toISOString().slice(11, 23), e: ev, d: detail });
      if (_diag.length > 200) _diag.shift();
    } catch (e) { /* ignore */ }
  }
  function dumpDiagnostics() {
    var copy = _diag.slice();
    try { if (console.table) console.table(copy); else console.log(copy); } catch (e) { console.log(copy); }
    // Which turns the renderer believes it owns, and what the live turn's
    // document holds. The first question in every past incident here was
    // "which bubble did it think it was painting into?" — now answerable
    // without reading the DOM.
    try {
      var TVd = _turnView();
      var liveDoc = _docs[_liveTurnId] || null;
      console.log('[KazmaChat] render state', {
        liveTurnId: _liveTurnId,
        registry: TVd ? TVd.stats() : null,
        status: liveDoc ? liveDoc.status : '(no doc)',
        parts: liveDoc ? (liveDoc.parts || []).map(function (p) { return p.type; }) : [],
        liveGate: hasLiveGate(),
      });
    } catch (eR) { /* diagnostics must never throw */ }
    return copy;
  }

  function endTurn() {
    _stopRequested = false;
    _clearTurnTimers();
    _isGenerating = false;
    _awaitingApproval = false;
    // Clear the WS store's thinking/turnActive status. The WS reconnect
    // handler (ws_chat.py:580) sends "Reconnected — previous turn still
    // running…" which sets the store to thinking. Without clearing it here,
    // that indicator stays visible forever after the turn actually finishes.
    try {
      var _store = (window.Alpine && Alpine.store) ? Alpine.store('agent') : null;
      if (_store) { _store._turnActive = false; _store.isThinking = false; }
    } catch (e) { /* store not ready */ }
    // Honest summary: a turn that delivered no reply must not claim "Done".
    finalizeProgress(_turnPainted ? true : 'empty');
    // The card's last frame carries the SHAPE of what just happened
    // ("12 steps · 3 tools · 18.4s · 4.2k tokens") instead of a bare "Done"
    // that threw the counts away with the live panel.
    // Approve-resume used a local typing row that endTurn never saw, so
    // "Thinking…" stayed under a finished answer (2026-09-01).
    if (currentMsgEl) {
      var leftover = currentMsgEl.querySelectorAll('.kz-typing-row');
      for (var _ti = 0; _ti < leftover.length; _ti++) {
        if (leftover[_ti].parentNode) leftover[_ti].parentNode.removeChild(leftover[_ti]);
      }
    }
    _clearStatusStrip();
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.placeholder = 'Type a message or /yolo \u2026 (Enter to send)';
    }
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.classList.remove('stop-mode');
      sendBtn.title = 'Send (Enter / Ctrl+Enter)';
      sendBtn.innerHTML = _SEND_SVG;
    }
    syncSendButtonForDraft();
    // Stamp finish time on the open assistant meta if still empty-ish
    if (currentMsgEl) {
      var meta = currentMsgEl.querySelector('.message-meta time');
      if (meta) {
        var now = new Date();
        meta.setAttribute('datetime', now.toISOString());
        meta.textContent = formatMsgTime(now);
      }
    }
    // Finalize open assistant bubble so the next token starts a new one.
    currentMsgEl = null;
    // The finished bubble is no longer a live-paint target — a duplicate
    // terminal frame (second transport's done) must not find it here.
    _liveRenderEl = null;
    activeStream = null;
    // WS path never hit SSE onDone → session list used to stay stale until F5.
    // Refresh after every completed turn (debounced).
    if (!showArchived) refreshSessionsSoon();
  }

  /**
   * Hard reset used by new session / ESC / desync recovery.
   * Always clears Stop + Alpine thinking even if the server never sent idle.
   */
  function forceEndTurn() {
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent')) {
        var store = Alpine.store('agent');
        store.isThinking = false;
        store.activeNode = '';
        store.activeTool = null;
        store.pendingApproval = null;
        store._turnActive = false;
      }
    } catch (e) {}
    // endTurn already finalizes progress as stopped when we mark it first
    if (_progressEl) {
      var titleEl = _progressEl.querySelector('.agent-progress-title');
      if (titleEl) titleEl.textContent = 'Stopped';
    }
    endTurn();
  }

  function pauseForApproval(data) {
    // HITL: turn is paused. Keep the composer usable for /steer, /abort,
    // /long, /yolo — locking it was why steers vanished (incident 2026-08-16).
    _clearTurnTimers();
    // An approval can be granted from Telegram or Discord while this tab
    // only watches. The reconciler is what makes the answer show up here
    // anyway -- this tab never sees an approve response to react to.
    _startReconciler('hitl');
    _isGenerating = false;
    _awaitingApproval = true;
    // This tab saw the interrupt. Do NOT treat this flag as "already approved"
    // — _paintHitlFromDoc used to stamp "Approved — running…" on first paint
    // because pauseForApproval runs before the pending card is created.
    _serverPaused = true;
    _clearStatusStrip();
    // The card is the ONE surface while paused: it shows the awaiting
    // phase + the watchdog countdown (pause used to blank the strip and
    // leave dead air when the inline card was late — 2026-09-03).
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.placeholder = 'Approve above — or /steer /abort /long /yolo';
    }
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.classList.remove('stop-mode');
      sendBtn.title = 'Send steer or command';
      sendBtn.innerHTML = _SEND_SVG;
    }
    syncSendButtonForDraft();
    void data;
  }

  // Back-compat aliases used throughout this file.
  function disableInput() { beginTurn(); }
  function enableInput() { endTurn(); }

  function lockInputForApproval() {
    pauseForApproval(null);
  }

  function unlockInputForApproval() {
    endTurn();
  }

  function abortGeneration(opts) {
    opts = opts || {};
    _stopRequested = true;
    // Invalidate in-flight SSE immediately. abortThenSend used to wait up
    // to 1.5s for POST /stop with the old epoch still current, so tokens
    // kept painting the first bubble while the new CoT opened below
    // (2026-09-02 mid-turn send).
    _sseEpoch++;
    _retireLiveTurn();
    if (activeStream) {
      activeStream.abort();
      activeStream = null;
      if (!opts.silent && KS.toast) KS.toast('Generation stopped', 'info', 2000);
    }
    // The SSE turn runs detached server-side (refresh-safe) — aborting the
    // fetch alone would NOT stop the generation. Tell the server to cancel
    // the pump task so billing stops and the transcript persists as-is.
    var stopP = Promise.resolve();
    try {
      if (chatSessionId) {
        stopP = fetch('/api/chat/stop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: chatSessionId }),
          credentials: 'same-origin',
        }).then(function() {}).catch(function() { /* best-effort */ });
      }
    } catch (e) { /* best-effort */ }
    forceEndTurn();
    return stopP;
  }

  /** Stop the in-flight turn, then send whatever is in the composer. */
  function abortThenSend() {
    diag('abort-then-send');
    var p = abortGeneration({ silent: true });
    function go() { sendMessage(); }
    var raced = Promise.race([
      p && typeof p.then === 'function' ? p : Promise.resolve(),
      new Promise(function(resolve) { setTimeout(resolve, 1500); }),
    ]);
    raced.then(go, go);
  }

  // Heal desync: WS sets isThinking=false but missed chat.endTurn (or vice versa).
  // Runs cheaply; only acts when Stop is stuck while the bus reports idle.
  if (!_turnSyncTimer) {
    _turnSyncTimer = setInterval(function() {
      if (!_isGenerating || _awaitingApproval) return;
      // Don't heal before the server has emitted anything this turn. At turn
      // start the bus is still idle until the first status frame lands, so a
      // 1.5s tick landing in that gap would otherwise release the Stop lock and
      // paint a false "Done · 1s" heading while the turn is still running.
      if (!_serverActivitySeen) return;
      try {
        if (hasLiveGate()) return;
        if (!window.Alpine || !Alpine.store || !Alpine.store('agent')) return;
        var store = Alpine.store('agent');
        if (store.pendingApproval) return;
        // Reply already painted and the SSE fetch is gone — Stop was stuck
        // because WS still had isThinking from a leftover status frame.
        var sseDead = !_streamIsLive();
        var replyPainted = !!_liveAnswerText().trim();
        if (sseDead && replyPainted) {
          console.warn('[KazmaChat] Desync recovery: SSE ended with a painted reply — releasing Stop');
          endTurn();
          return;
        }
        if (store._turnActive || store.isThinking) return;
        // Bus is idle; chat still thinks a turn is running → release.
        console.warn('[KazmaChat] Desync recovery: releasing stuck generation lock');
        endTurn();
      } catch (e) {}
    }, 1500);
  }

  // ── File handling ─────────────────────────────────────
  // Pending attachments accumulated for the next send. Text files stay
  // client-side (inlined); binary files (images, PDFs, docs, etc.) are
  // uploaded to /api/chat/upload and referenced by the returned descriptor.
  // Chips render ABOVE the composer — never buried in the placeholder.
  var pendingText = '';
  var pendingTextName = '';
  var pendingUploads = []; // [{id, kind, mime, filename}]
  var _attachChipSeq = 0; // local ids for in-flight upload chips

  function _isTextFile(file) {
    var allowedTypes = [
      'text/plain', 'text/markdown', 'text/html', 'application/json',
      'text/csv', 'text/x-python', 'text/javascript', 'application/javascript',
      'text/css', 'text/xml', 'application/xml', 'text/yaml', 'application/x-yaml'
    ];
    var allowedExts = [
      '.txt', '.md', '.markdown', '.json', '.csv', '.py', '.js', '.ts',
      '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.sql', '.log',
      '.toml', '.ini', '.cfg', '.env', '.bash'
    ];
    var ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
    return allowedTypes.indexOf(file.type) !== -1 || allowedExts.indexOf(ext) !== -1;
  }

  function _defaultPlaceholder() {
    return ti('type_message', ti('placeholder', 'Type your message\u2026 (Enter to send)'));
  }

  function renderPendingAttachments() {
    var strip = $('chat-attachments');
    if (!strip) return;
    var chips = [];
    if (pendingTextName) {
      chips.push({
        key: 'text',
        name: pendingTextName,
        kind: 'text',
        uploading: false
      });
    }
    pendingUploads.forEach(function(u) {
      chips.push({
        key: 'up:' + (u.id || u._localId || u.filename),
        name: u.filename || u.id || 'file',
        kind: u.kind || 'file',
        uploading: !!u._uploading
      });
    });
    if (!chips.length) {
      strip.innerHTML = '';
      strip.hidden = true;
      return;
    }
    strip.hidden = false;
    var removeLabel = ti('remove_attachment', 'Remove attachment');
    strip.innerHTML = chips.map(function(c) {
      var icon = c.uploading ? '\u23F3' : '\uD83D\uDCCE';
      var status = c.uploading
        ? ' <span class="chat-attach-status">' + escapeHtml(ti('uploading', 'Uploading\u2026')) + '</span>'
        : '';
      return (
        '<div class="chat-attach-chip' + (c.uploading ? ' is-uploading' : '') + '" data-attach-key="' + escapeHtml(c.key) + '" title="' + escapeHtml(c.name) + '">' +
          '<span class="chat-attach-icon" aria-hidden="true">' + icon + '</span>' +
          '<span class="chat-attach-name" dir="auto">' + escapeHtml(c.name) + '</span>' +
          status +
          (c.uploading ? '' :
            '<button type="button" class="chat-attach-remove" data-remove-attach="' + escapeHtml(c.key) + '" title="' + escapeHtml(removeLabel) + '" aria-label="' + escapeHtml(removeLabel) + '">&times;</button>') +
        '</div>'
      );
    }).join('');
  }

  function removePendingAttachment(key) {
    if (!key) return;
    if (key === 'text') {
      pendingText = '';
      pendingTextName = '';
    } else if (key.indexOf('up:') === 0) {
      var id = key.slice(3);
      pendingUploads = pendingUploads.filter(function(u) {
        return String(u.id || u._localId || u.filename) !== id;
      });
    }
    renderPendingAttachments();
    if (inputEl && !pendingTextName && !pendingUploads.length) {
      inputEl.placeholder = _defaultPlaceholder();
    }
  }

  function clearPendingAttachments() {
    pendingText = '';
    pendingTextName = '';
    pendingUploads = [];
    renderPendingAttachments();
    if (inputEl) inputEl.placeholder = _defaultPlaceholder();
  }

  function attachFile(file) {
    if (!file) return;
    // Text files ≤ 1MB are still inlined client-side (cheap, no upload).
    if (_isTextFile(file) && file.size <= 1048576) {
      var reader = new FileReader();
      reader.onload = function(evt) {
        pendingText = evt.target.result;
        pendingTextName = file.name;
        KS.toast((ti('attached', 'Attached') + ': ' + file.name), 'info', 2500);
        renderPendingAttachments();
      };
      reader.onerror = function() {
        KS.toast('Failed to read ' + file.name, 'error', 3000);
      };
      reader.readAsText(file);
      return;
    }
    // Everything else (images, PDFs, docs, large text) is uploaded.
    if (file.size > 20 * 1024 * 1024) {
      KS.toast('File too large (max 20MB): ' + file.name, 'error', 3000);
      return;
    }
    var localId = 'local-' + (++_attachChipSeq);
    var placeholder = {
      id: '',
      _localId: localId,
      kind: (file.type || '').indexOf('image/') === 0 ? 'image' : 'file',
      mime: file.type || 'application/octet-stream',
      filename: file.name,
      _uploading: true
    };
    pendingUploads.push(placeholder);
    renderPendingAttachments();
    var fd = new FormData();
    fd.append('file', file);
    fetch('/api/chat/upload', { method: 'POST', body: fd })
      .then(function(r) {
        if (!r.ok) {
          return r.json().catch(function() { return {}; }).then(function(body) {
            var detail = (body && body.detail) || ('Upload failed (' + r.status + ')');
            throw new Error(typeof detail === 'string' ? detail : 'Upload failed (' + r.status + ')');
          });
        }
        return r.json();
      })
      .then(function(desc) {
        // Replace the in-flight chip with the server descriptor
        var idx = -1;
        for (var i = 0; i < pendingUploads.length; i++) {
          if (pendingUploads[i]._localId === localId) { idx = i; break; }
        }
        if (idx >= 0) {
          pendingUploads[idx] = {
            id: desc.id,
            kind: desc.kind || placeholder.kind,
            mime: desc.mime || placeholder.mime,
            filename: desc.filename || file.name
          };
        } else {
          pendingUploads.push(desc);
        }
        KS.toast((ti('attached', 'Attached') + ': ' + (desc.filename || file.name)), 'info', 2500);
        renderPendingAttachments();
      })
      .catch(function(err) {
        pendingUploads = pendingUploads.filter(function(u) {
          return u._localId !== localId;
        });
        renderPendingAttachments();
        KS.toast('Upload failed: ' + (err && err.message ? err.message : err), 'error', 3500);
      });
  }

  function attachFiles(fileList) {
    if (!fileList || !fileList.length) return;
    for (var i = 0; i < fileList.length; i++) {
      attachFile(fileList[i]);
    }
  }

  function onFileSelected(e) {
    var files = e.target.files;
    if (!files || !files.length) return;
    attachFiles(files);
    e.target.value = '';
  }

  function setupChatDropZone() {
    var zone = $('chat-input-area') || document.querySelector('.chat-input-area');
    if (!zone) return;
    var hint = $('chat-drop-hint');
    var dragDepth = 0;

    function hasFiles(e) {
      var dt = e.dataTransfer;
      if (!dt) return false;
      if (dt.types && typeof dt.types.indexOf === 'function') {
        return dt.types.indexOf('Files') !== -1;
      }
      if (dt.types) {
        for (var i = 0; i < dt.types.length; i++) {
          if (dt.types[i] === 'Files') return true;
        }
      }
      return !!(dt.files && dt.files.length);
    }

    function setDrag(active) {
      zone.classList.toggle('is-dragover', !!active);
      if (hint) {
        hint.hidden = !active;
        hint.setAttribute('aria-hidden', active ? 'false' : 'true');
      }
    }

    zone.addEventListener('dragenter', function(e) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth++;
      setDrag(true);
    });
    zone.addEventListener('dragover', function(e) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      setDrag(true);
    });
    zone.addEventListener('dragleave', function(e) {
      if (!hasFiles(e) && dragDepth === 0) return;
      e.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) setDrag(false);
    });
    zone.addEventListener('drop', function(e) {
      e.preventDefault();
      dragDepth = 0;
      setDrag(false);
      var files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length) attachFiles(files);
    });
    // Prevent the browser from navigating away if a file is dropped outside
    // the zone but still on the chat page (common accidental drops).
    var chatMain = document.querySelector('.chat-main');
    if (chatMain && chatMain !== zone) {
      chatMain.addEventListener('dragover', function(e) {
        if (hasFiles(e)) e.preventDefault();
      });
      chatMain.addEventListener('drop', function(e) {
        if (!hasFiles(e)) return;
        // If drop landed outside the composer, still attach (UX-friendly)
        if (!zone.contains(e.target)) {
          e.preventDefault();
          var files2 = e.dataTransfer && e.dataTransfer.files;
          if (files2 && files2.length) attachFiles(files2);
        }
      });
    }
  }

  // ── Model selector ───────────────────────────────────
  function loadModels() {
    if (!modelSelectorEl) return;

    // Restore persisted selection
    try { selectedModel = localStorage.getItem(MODEL_LS_KEY) || ''; } catch(e) { selectedModel = ''; }

    // Fetch saved profiles first (these take priority in the dropdown)
    var savedModels = [];
    fetch('/api/models/saved')
      .then(function(r) { return r.ok ? r.json() : []; })
      .then(function(profiles) {
        if (!Array.isArray(profiles)) profiles = [];
        if (Array.isArray(profiles)) {
          profiles.forEach(function(p) {
            if (p.model) {
              var label = p.name + ' (' + p.model + ')';
              savedModels.push({ value: p.model, label: label, isProfile: true });
            }
          });
        }
        return fetch('/api/provider/active');
      })
      .then(function(r) { return r.ok ? r.json() : {}; })
      .then(function(active) {
        // Per-session pick (localStorage) wins. Process-wide /api/provider/active
        // is only the default when this mouth has not chosen a model.
        if (!selectedModel && active && active.model) {
          selectedModel = active.model;
          try { localStorage.setItem(MODEL_LS_KEY, selectedModel); } catch(e) {}
        }

        // Fetch all providers so we can group models by provider
        fetch('/api/providers')
          .then(function(r) { return r.ok ? r.json() : []; })
          .then(function(providers) {
            // Build provider groups: [{ name, label, models: [] }]
            var providerGroups = [];
            if (Array.isArray(providers)) {
              var _localProv = { ollama: 1, 'lm-studio': 1, lmstudio: 1, local: 1 };
              providers.forEach(function(p) {
                if (!p.enabled) return;
                var pname = String(p.name || '').toLowerCase();
                // Keyless cloud providers in the dropdown pin gpt-4o-mini and
                // 401 chat even after DeepSeek Test succeeded.
                if (!p.api_key && !_localProv[pname]) return;
                var models = [];
                var visible = p.visible_models || [];
                var disc = p.discovered_models || [];
                var manual = p.models || [];
                if (Array.isArray(visible) && visible.length) {
                  visible.forEach(function(m) { if (m && models.indexOf(m) === -1) models.push(m); });
                } else {
                  if (Array.isArray(disc)) {
                    disc.forEach(function(m) { if (m && models.indexOf(m) === -1) models.push(m); });
                  }
                  if (Array.isArray(manual)) {
                    manual.forEach(function(m) { if (m && models.indexOf(m) === -1) models.push(m); });
                  }
                }
                if (models.length > 0) {
                  providerGroups.push({
                    name: p.name || 'unknown',
                    label: p.display_name || p.name || 'Unknown',
                    models: models
                  });
                }
              });
            }
            populateModelSelector(providerGroups, savedModels);
          })
          .catch(function() { populateModelSelector([], savedModels); });
      })
      .catch(function() {
        // If both fetches fail, at least show the persisted model
        var fallback = [];
        if (selectedModel) {
          fallback.push({ name: 'active', label: 'Active', models: [selectedModel] });
        }
        populateModelSelector(fallback, savedModels);
      });

    // Resume the last active session and HYDRATE messages. Previously we only
    // set chatSessionId and showed a welcome screen — sessions looked empty
    // until a manual sidebar click/refresh. Always loadSession for continuity.
    try {
      var savedSid = localStorage.getItem(SESSION_LS_KEY);
      if (savedSid) {
        loadSession(savedSid);
      } else {
        newSession();
      }
    } catch (e) {
      newSession();
    }
  }

  function populateModelSelector(providerGroups, savedProfiles) {
    if (!modelSelectorEl) return;
    savedProfiles = savedProfiles || [];
    providerGroups = providerGroups || [];
    var hasProviders = providerGroups.some(function(g) { return g.models && g.models.length > 0; });
    var allEmpty = !hasProviders && savedProfiles.length === 0;
    if (allEmpty) {
      modelSelectorEl.innerHTML = '<option value="">— default —</option>';
      return;
    }
    var html = '';
    // Saved profiles first
    if (savedProfiles.length > 0) {
      html += '<optgroup label="Saved Profiles">';
      savedProfiles.forEach(function(p) {
        var sel = (p.value === selectedModel) ? ' selected' : '';
        html += '<option value="' + escapeHtml(p.value) + '"' + sel + '>' + escapeHtml(p.label) + '</option>';
      });
      html += '</optgroup>';
    }
    // Models grouped by provider
    providerGroups.forEach(function(g) {
      if (!g.models || g.models.length === 0) return;
      html += '<optgroup label="' + escapeHtml(g.label) + '">';
      g.models.forEach(function(m) {
        var sel = (m === selectedModel) ? ' selected' : '';
        html += '<option value="' + escapeHtml(m) + '"' + sel + '>' + escapeHtml(m) + '</option>';
      });
      html += '</optgroup>';
    });
    modelSelectorEl.innerHTML = html;
    // Ensure dropdown reflects persisted value. If localStorage still has a
    // model that is not in the list (keyless OpenAI default), drop it so
    // send() does not pin a 401.
    if (selectedModel) {
      modelSelectorEl.value = selectedModel;
      if (modelSelectorEl.value !== selectedModel) {
        selectedModel = modelSelectorEl.value || '';
        try { localStorage.setItem(MODEL_LS_KEY, selectedModel); } catch (eLs) {}
      }
    }
  }

  function onModelChange() {
    if (!modelSelectorEl) return;
    var previous = selectedModel;
    selectedModel = modelSelectorEl.value || '';
    try { localStorage.setItem(MODEL_LS_KEY, selectedModel); } catch(e) {}
    // Notify other components immediately (optimistic)
    document.dispatchEvent(new CustomEvent('model-changed', { detail: selectedModel }));
    // Sync to backend — await ack; revert UI on failure / env lock
    if (selectedModel) {
      fetch('/api/settings/active_model', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active_model: selectedModel }),
      }).then(function(r) { return r.json().then(function(data) { return { ok: r.ok, data: data }; }); })
        .then(function(res) {
          var data = res.data || {};
          if (data.status === 'error' || data.ok === false) {
            selectedModel = previous;
            if (modelSelectorEl) modelSelectorEl.value = previous || '';
            try { localStorage.setItem(MODEL_LS_KEY, previous || ''); } catch(e) {}
            document.dispatchEvent(new CustomEvent('model-changed', { detail: previous || '' }));
            var msg = data.error || data.error_code || 'Model switch failed';
            if (window.KS && KS.toast) KS.toast(msg, 'error', 4000);
            else if (window.showToast) window.showToast(msg, 'error', 4000);
            return;
          }
          if (data.active_model || data.model) {
            selectedModel = data.active_model || data.model;
            if (modelSelectorEl) modelSelectorEl.value = selectedModel;
            try { localStorage.setItem(MODEL_LS_KEY, selectedModel); } catch(e) {}
          }
        }).catch(function() {
          if (window.KS && KS.toast) KS.toast('Model switch request failed', 'error', 3000);
        });
    }
  }

  // ── Send message via SSE ──────────────────────────────
  function sendMessage() {
    var text = (inputEl.value || '').trim();
    var hasTextAtt = !!pendingText;
    // Ignore in-flight uploads (no server id yet). Block send while any
    // upload is still running so we don't discard the in-flight file.
    var readyUploads = pendingUploads.filter(function(u) { return u && u.id && !u._uploading; });
    var hasUploads = readyUploads.length > 0;
    var stillUploading = pendingUploads.some(function(u) { return u && u._uploading; });
    if (stillUploading) {
      KS.toast(ti('uploading', 'Uploading\u2026'), 'info', 2000);
      return;
    }
    if (!text && !hasTextAtt && !hasUploads) {
      diag('send-skipped-empty');
      return;
    }

    // Track for the empty-turn Retry button (agent-stopped-talking layer 4).
    // Only set when there's real text — uploads-only turns can't be retried
    // by re-typing in the input.
    if (text) lastSentUserText = text;

    hideSlashMenu();

    // Handle /voice commands locally
    if (window.KazmaVoice && window.KazmaVoice.handleVoiceCommand(text)) {
      inputEl.value = '';
      inputEl.style.height = 'auto';
      return;
    }

    // Handle /help locally (list slash commands)
    if (text.toLowerCase() === '/help') {
      var helpLines = SLASH_COMMANDS.map(function(c) {
        return '`' + c.cmd + '` — ' + c.desc;
      }).join('\n');
      appendMessage('user', text);
      appendMessage('assistant', '**Slash commands**\n\n' + helpLines +
        '\n\nOn danger tools you can also **Allow tool (session)** to stop repeat prompts without full YOLO.');
      inputEl.value = '';
      inputEl.style.height = 'auto';
      return;
    }

    // Handle /new command locally
    if (text.toLowerCase() === '/new') {
      newSession();
      inputEl.value = '';
      inputEl.style.height = 'auto';
      return;
    }

    // Handle /steer <text>, /steer! <text>, /abort — out-of-band signals to
    // a RUNNING turn. Intercepted before the normal send so they never start
    // a new turn. Hard steer is fire-and-forget like /api/approve: the WS
    // bus / delivery poll surfaces the resumed turn.
    var _cmdLow = text.toLowerCase();
    var _steerHard = _cmdLow === '/steer!' || _cmdLow.startsWith('/steer! ');
    var _steerSoft = !_steerHard && (_cmdLow === '/steer' || _cmdLow.startsWith('/steer '));
    var _abortCmd = _cmdLow === '/abort';
    if (_steerHard || _steerSoft || _abortCmd) {
      if (_abortCmd) {
        inputEl.value = '';
        inputEl.style.height = 'auto';
        syncSendButtonForDraft();
        // Visible in the transcript (parity with /steer) — an invisible
        // command that only toasts reads as "not really working"
        // (command audit 2026-08-19).
        appendMessage('user', '/abort');
        if (window.showToast) window.showToast('⛔ Aborting task…', 'warning', 2500);
        _releaseHitlComposer('abort');
        if (activeStream) { try { activeStream.abort(); } catch (_e) {} activeStream = null; }
        fetch('/api/chat/abort', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: chatSessionId || '' }),
          credentials: 'same-origin',
        }).catch(function() { /* best-effort */ });
        forceEndTurn();
        return;
      }

      var _steerText = steerBody(text);
      if (!_steerText) {
        // Keep the draft queued so the user can type the note.
        if (window.showToast) window.showToast(
          'Steer queued — add your note, then Enter to apply.', 'info', 3500);
        if (inputEl && !String(inputEl.value || '').trim()) {
          inputEl.value = _steerHard ? '/steer! ' : '/steer ';
        }
        try {
          var _pos = (inputEl.value || '').length;
          inputEl.setSelectionRange(_pos, _pos);
        } catch (e) {}
        inputEl.focus();
        syncSendButtonForDraft();
        return;
      }
      if (!chatSessionId) {
        if (window.showToast) window.showToast(
          'No active task to steer — send a message first.', 'info', 3000);
        return;
      }
      // Visible in the transcript; composer clears so they can queue another.
      appendMessage('user', text);
      inputEl.value = '';
      inputEl.style.height = 'auto';
      syncSendButtonForDraft();
      if (window.showToast) window.showToast(
        _steerHard ? '⏸️ Pausing task to apply your steer…' : '🧭 Steer noted — applying on the next step.',
        'info', 3000);
      fetch('/api/chat/steer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: chatSessionId || '',
          thread_id: currentThreadId(),
          text: _steerText,
          mode: _steerHard ? 'hard' : 'soft',
        }),
        credentials: 'same-origin',
      }).then(function(r) {
        return r.ok ? r.json() : r.json().catch(function() { return { ok: false }; });
      }).then(function(body) {
        if (body && body.ok === false) {
          if (body.reason === 'no_active_task') {
            // /steer with no live turn is a NEW prompt, not a swallowed note.
            _releaseHitlComposer('steer-idle');
            var fallback = _steerText || steerBody(text) || text;
            fallback = String(fallback || '').replace(/^\/steer!?\s*/i, '').trim();
            if (fallback) {
              if (window.showToast) window.showToast(
                'No paused task — sending as a new message.', 'info', 3000);
              if (inputEl) inputEl.value = fallback;
              sendMessage();
            } else if (window.showToast) {
              window.showToast('No active task to steer.', 'info', 3000);
            }
            return;
          }
          if (body.reason && window.showToast) {
            window.showToast('Steer failed: ' + body.reason, 'error', 3500);
          }
          return;
        }
        if (body && body.demoted && window.showToast) {
          window.showToast(
            'Steer will apply on the next step (could not pause in time).',
            'info', 3500);
          return;
        }
        if (body && body.mode === 'hard') {
          _awaitingReply = true;
          if (!_streamIsLive()) {
            try { _attachJournal('steer-json'); } catch (eRe) { /* ignore */ }
          }
        }
      }).catch(function() { /* best-effort */ });
      return;
    }

    // During LIVE HITL, a normal message is a soft steer — don't start a
    // new turn. A fossil `_awaitingApproval` after restart/abort (no live
    // card) must NOT rewrite the prompt as `/steer …`.
    if (_awaitingApproval && text && text.charAt(0) !== '/') {
      if (!hasLiveGate()) {
        _awaitingApproval = false;
      } else {
        inputEl.value = '';
        inputEl.style.height = 'auto';
        syncSendButtonForDraft();
        fetch('/api/chat/steer', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: chatSessionId || '',
            thread_id: currentThreadId(),
            text: text,
            mode: 'soft',
          }),
          credentials: 'same-origin',
        }).then(function(r) {
          return r.ok ? r.json() : r.json().catch(function() { return { ok: false }; });
        }).then(function(body) {
          if (body && body.ok === false && body.reason === 'no_active_task') {
            _releaseHitlComposer('steer-idle');
            if (window.showToast) window.showToast(
              'No paused task — sending as a new message.', 'info', 3000);
            if (inputEl) inputEl.value = text;
            sendMessage();
            return;
          }
          appendMessage('user', '/steer ' + text);
          if (window.showToast) window.showToast(
            '🧭 Steering the paused task with your note.', 'info', 3000);
        }).catch(function() {
          appendMessage('user', '/steer ' + text);
        });
        return;
      }
    }

    // Unknown slash-command hint (command audit 2026-08-19): a typo like
    // /about used to silently ride to the LLM as a prompt and burn a turn.
    // Non-blocking — the message still sends (backend/graph may know
    // commands the composer list doesn't), but the user gets a pointer.
    if (text.charAt(0) === '/') {
      var _knownHeads = {};
      SLASH_COMMANDS.forEach(function(c) {
        _knownHeads[_cmdHead(c.cmd)] = true;
      });
      ['/compact', '/research', '/swarm', '/dup', '/voice'].forEach(function(h) {
        _knownHeads[h] = true;
      });
      var _head = _cmdHead(text);
      if (!_knownHeads[_head] && window.showToast) {
        window.showToast(
          'Unknown command ' + _head + ' — sending anyway. /help lists what works here.',
          'info', 3500);
      }
    }

    // Handle /reset command locally
    // NOTE: the missing `return` below is DELIBERATE — the local clear is
    // instant feedback; the fall-through then sends "/reset" to the backend
    // fast path (SSE) / intercept (WS), which deletes the thread's
    // checkpoints and persists the cleared session. That is the real reset.
    if (text.toLowerCase() === '/reset') {
      messagesEl.innerHTML =
        '<div class="chat-welcome">' +
          '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
          '<h2>Kazma</h2>' +
          '<p>How can I help you today?</p>' +
        '</div>';
      resetSessionStats();
      currentMsgEl = null;
      _turnPainted = false;
      if (activeStream) { activeStream.abort(); activeStream = null; }
      renderSessionList();
    }

    // Build message content. Text attachments are inlined; binary uploads
    // are referenced as attachments and rendered in the transcript by name.
    var content = text;
    var displayAttachName = pendingTextName || (readyUploads[0] && readyUploads[0].filename) || '';
    if (pendingText) {
      content = text
        ? text + '\n\n[Attached file: ' + pendingTextName + ']\n```\n' + pendingText.slice(0, 8000) + '\n```'
        : '[Attached file: ' + pendingTextName + ']\n```\n' + pendingText.slice(0, 8000) + '\n```';
    } else if (hasUploads && !text) {
      content = '[' + (readyUploads[0].kind || 'file') + ']';
    }
    // Build the attachments payload for the server (binary uploads only).
    // Drop in-flight placeholders so the server never sees empty ids.
    var attachmentsPayload = readyUploads.map(function(u) {
      return { id: u.id, kind: u.kind, mime: u.mime, filename: u.filename };
    });

    // Show user message — sending always re-pins the view to the bottom
    // (a new turn starts; the reader's scroll-up detach must not carry over).
    appendMessage('user', content, displayAttachName);
    scrollToBottomForce();

    // Clear the composer BEFORE beginTurn/logProgress. Those paint the CoT
    // panel and used to throw after the user bubble was already on screen,
    // leaving the sent text in the box (and Stop on the button, so the
    // only way to "clear" was to send it again).
    currentMsgEl = null;
    _turnPainted = false;
    clearPendingAttachments();
    _clearComposer();
    inputEl.placeholder = _defaultPlaceholder();

    var _instantSlash = _isInstantCapacitySlash(content);
    if (!_instantSlash) {
      try {
        disableInput(); // → beginTurn → progress panel on new assistant bubble
      } catch (eBegin) {
        console.error('[KazmaChat] beginTurn failed', eBegin);
      }
    } else {
      _resetTurnState();
    }


    // Ensure we have a stable session id
    if (!chatSessionId) {
      chatSessionId = generateSessionId();
      persistSessionId();
    }

    // Sidebar: show this season immediately (before the server list round-trip).
    // Critical for WS path which used to skip loadSessions entirely.
    noteSessionActivity(text || content);

    // Arm the delivery wait — set BEFORE any transport dispatch.
    // This flag is ONLY cleared in loadSession() (when we re-render from
    // the server). The WS/SSE/endTurn lifecycle CANNOT touch it.
    _awaitingReply = true;
    // Fresh re-attach budget for this turn (bounded recovery, not a loop).
    _reopenCount = 0;
    _sseAttempts = 0;

    // Hidden-tab UX (P4): permission may only be requested from a user
    // gesture — arm it on send.
    try {
      if (window.KazmaTurnVisibility && KazmaTurnVisibility.armPermission) {
        KazmaTurnVisibility.armPermission();
      }
    } catch (e) { /* ignore */ }

    function _dispatchSse(extraBody) {
      _startReconciler('dispatch');
      if (activeStream) {
        try { activeStream.abort(); } catch (e) { /* already dead */ }
      }
      var body = {
        message: content,
        session_id: chatSessionId,
        model: selectedModel || '',
        workspace_id: _activeWorkspaceId || '',
        attachments: attachmentsPayload,
        // Echoed on the user_message frame the other tabs paint; ours is
        // dropped by it (beginObservedTurn).
        client_msg_id: _newClientMsgId(),
      };
      if (extraBody) {
        for (var k in extraBody) {
          if (Object.prototype.hasOwnProperty.call(extraBody, k)) body[k] = extraBody[k];
        }
      }
      diag('dispatch', { attach: !!(extraBody && extraBody.last_event_id), msgLen: (content || '').length });
      activeStream = KS.sse('/api/chat/stream', body, _buildSseCallbacks(++_sseEpoch));
    }

    _buildSseCallbacks = function(epoch) {
      // Stale-stream guard: only the CURRENT dispatch may paint tokens,
      // log activity, or run terminal side effects. A superseded stream's
      // late frames (post-approval resume, cursor re-attach, aborted fetch)
      // used to create empty bubbles and a trailing "_No response received."
      // AFTER a successful reply (2026-08-26).
      function _mine() { return epoch === _sseEpoch; }
      return {
      onFrameError: function(type, err) {
        if (!_mine()) return;
        _onSseFrameError(type, err);
      },

      onToken: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        _noteSeq();
        _outboxClear();  // first streamed token = the server received the send
        // NOTE: do NOT clear the status strip per token. The strip sits
        // IN-FLOW between transcript and composer — every hide/show shifts
        // the composer ~33px, resizes the transcript viewport and makes the
        // streaming text bounce (the flicker). While tokens flow the strip
        // stays steady ("Writing reply…"); terminal paths (done/error/
        // endTurn) are the only ones allowed to hide it.
        if (!_liveAnswerText()) {
          logProgress({ kind: 'status', title: ti('writing_reply', 'Writing reply\u2026'), state: 'running' });
        }
        applyTurnEvent({
          type: 'token',
          content: data.content,
          seq: data.seq,
          turn_id: data.turn_id || _liveTurnId,
          full: !!data.full,
          source: 'sse',
        });
      },

      onToolCall: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        // Look-only: a tool step has nothing to put IN the bubble, and
        // minting one here opened every tool-first turn with a blank bubble.
        _pinLiveAssistantBubble(false);
        var inputs = data.inputs;
        if (typeof inputs === 'object') {
          try { inputs = JSON.stringify(inputs); } catch (e) { inputs = String(inputs); }
        }
        logProgress({
          kind: 'tool',
          title: data.tool_name || 'tool',
          detail: _toolDetailWithGist(_toolArgSummary(data.inputs), inputs),
          state: 'running',
          // The graph's own run id, so the LIVE row and the row the
          // server persisted are one row rather than two after a
          // refresh. activityToParts reads 'tool#<id>' back into
          // call_id, which is what partKey keys on.
          id: data.tool_call_id ? 'tool#' + data.tool_call_id : undefined,
        });
      },

      onToolResult: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        if (!currentMsgEl) return;
        var isSwarm = (data.tool_name === 'dispatch_swarm' || data.tool_name === 'swarm_dispatch' || (data.result && data.result.indexOf('Swarm task dispatched') !== -1));
        logProgress({
          kind: 'tool',
          title: data.tool_name || 'tool',
          detail: _toolDetailWithGist(_toolResultSummary(data.result), data.result),
          state: isSwarm ? 'running' : 'done',
          // The graph's own run id, so the LIVE row and the row the
          // server persisted are one row rather than two after a
          // refresh. activityToParts reads 'tool#<id>' back into
          // call_id, which is what partKey keys on.
          id: data.tool_call_id ? 'tool#' + data.tool_call_id : undefined,
        });
        if (isSwarm) {
          var content = currentMsgEl.querySelector('.message-content');
          var resultBox = document.createElement('div');
          resultBox.className = 'swarm-bg-badge';
          resultBox.innerHTML = '<span class="pulse-dot"></span><div><strong>Background Task Active:</strong> ' + escapeHtml(truncateStr(data.result, 300)) + '</div>';
          content.appendChild(resultBox);
        }
        scrollToBottom();
      },

      onMemoryExplain: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        try { applyMemoryExplain(data || {}); } catch (e) { /* ignore */ }
      },

      // SSE CoT parity with WS agentStore — routing / synthesizing / heartbeats
      onStatus: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        _noteSeq();
        var status = (data && (data.status || data.message)) || '';
        if (!status) return;
        if (status === 'resync') {
          // Journal-gap attach: the server closed the stream and told us to
          // reconcile with durable truth. Silently ignoring it left a dead
          // stream with no recovery (2026-08-26). The gap ALSO means our
          // cursor is invalid — drop it so no recovery path re-attaches
          // with the same dead cursor (that looped forever).
          _lastSeqSeen = 0;
          _resyncDelivery('sse-gap');
          return;
        }
        if (status === 'thinking' || status === 'synthesizing' || status === 'routing_node') {
          var title = status === 'synthesizing'
            ? ti('synthesizing', 'Composing response\u2026')
            : (status === 'routing_node'
              ? tiFmt('routing', 'Routing: {node}', { node: (data && data.active_node) || 'Supervisor' })
              : (data.message || ti('thinking', 'Kazma is thinking\u2026')));
          logProgress({
            kind: 'status',
            title: title,
            detail: (data && data.message && status !== 'thinking') ? data.message : '',
            state: 'running',
          });
        } else if (status === 'paused_for_approval' || status === 'idle') {
          // HITL / idle handled by other callbacks
        } else {
          logProgress({
            kind: 'status',
            title: String(data.message || status),
            state: 'running',
          });
        }
      },

      onHeartbeat: function(data) {
        // Journaled liveness: proves the turn is alive during long tool/LLM
        // phases and carries phase/tool/step. Not epoch-gated — a
        // superseded stream's graph is the live graph (same rule as HITL).
        noteTurnActivity();
        _noteSeq();
        // The heartbeat is the only frame carrying a SERVER-measured
        // elapsed. Without this the header would have to time the turn
        // itself, which is the client clock that printed "Done 0s" while
        // the graph was still working (plan §3).
        applyTurnEvent({
          type: 'turn_heartbeat',
          elapsed_s: (data && data.elapsed_s) || 0,
          seq: data && data.seq,
          turn_id: (data && data.turn_id) || _liveTurnId,
          source: 'sse',
        });
      },

      onDone: function(data) {
        if (!_mine()) return;
        activeStream = null;
        _clearStatusStrip();
        diag('done', {
          interrupted: !!(data && data.interrupted),
          truncated: !data,
          contentLen: (data && data.content || '').length,
          painted: _turnPainted,
        });
        var interrupted = !!(data && data.interrupted);
        // No terminal frame (HTTP body closed / attach ended early): the
        // turn may still be running server-side or already durable. Keep
        // the partial paint for now and reconcile with server truth —
        // this used to sit on "CoT + small text" until a manual refresh.
        var truncated = !data;
        _ingestFrameGateViews(data);
        try {
        // Terminal frame is SoT — ALWAYS replace-paint, even when plan
        // tokens already arrived (glued ```plan + answer used to be skipped
        // because the turn had already produced an answer).
        if (data && data.content) {
          applyTurnEvent({
            type: 'done',
            content: data.content,
            seq: data.seq,
            turn_id: data.turn_id || _liveTurnId,
            model: data.model || '',
            interrupted: !!(data && data.interrupted),
            source: 'done',
          });
        }
        // Terminal frame is SoT. applyEvent can no-op (dedupe) after a
        // post-restart paint miss; write the DOM directly when the bubble
        // is still empty or still showing the watchdog. Covers capacity
        // acks AND real replies (2026-09-08 calendar turn: 1155 chars
        // persisted, UI stamped "_No response received._", refresh
        // replayed the stamp).
        // A finished turn (interrupted=false) is the answer even if a HITL
        // wait flag is still stuck — sequential approvals left the bubble
        // on the "Action required" placeholder until refresh (2026-09-19).
        if (data && data.content && !interrupted) {
          _forcePaintDoneContent(data.content);
        }
        // Never leave a blank turn after "Thinking…" (empty stream / missed HITL).
        // _turnPainted: a late stale terminal must NEVER print this after a
        // successful reply already painted (the trailing "_No response
        // received." under the posted-tweets answer, 2026-08-26).
        // Do not stamp the watchdog until resync has had a chance — the
        // server often already persisted the reply.
        if (!_liveAnswerText() && !interrupted && !_awaitingApproval && !_turnPainted) {
          diag('empty-terminal');
          dumpDiagnostics();
          _resyncDelivery('empty-terminal');
          var emptyTurnEl = currentMsgEl;
          setTimeout(function() {
            if (_liveAnswerText() || _turnPainted || _awaitingApproval) return;
            try { _pinLiveAssistantBubble(); } catch (ePin2) { /* ignore */ }
            var host = emptyTurnEl || currentMsgEl;
            var emptyEl = host && host.querySelector('.message-text');
            var shown = String((emptyEl && emptyEl.textContent) || '').trim();
            if (emptyEl && (!shown || _isWatchdogNotice(shown))) {
              var retryHtml = '';
              if (lastSentUserText || (messagesEl.querySelector('.message-user'))) {
                retryHtml = ' <button class="btn btn-secondary btn-sm" '
                  + 'style="margin-left:8px;" '
                  + 'onclick="window.KazmaChat && window.KazmaChat.retry && window.KazmaChat.retry()">'
                  + '↻ Retry</button>';
              }
              emptyEl.innerHTML = (KS.markdown
                ? KS.markdown('_No response received._ Check server logs or Pending Approvals.')
                : '<em>No response received.</em>') + retryHtml;
            }
          }, 600);
        }
        if (data) {
          updateSessionStats(data.tokens, data.cost, data.session_tokens, data.session_cost);
          // Capture per-turn usage for the workbench summary bar (finalizeProgress).
          _lastTurnStats = {
            tokens: Number(data.tokens) || 0,
            cost: Number(data.cost) || 0,
            durationMs: Number(data.duration_ms) || 0,
          };
          if (currentMsgEl) {
            var meta = currentMsgEl.querySelector('.message-meta');
            if (meta) {
              var modelBit = data.model ? (' \u00B7 ' + data.model) : '';
              meta.textContent = KS.formatTokens(data.tokens) + ' ' + ti('tokens', 'tokens') + ' \u00B7 ' +
                KS.formatCost(data.cost) + ' \u00B7 ' +
                KS.formatDuration(data.duration_ms) + modelBit;
              meta.setAttribute('dir', 'auto');
            }
          }
          updateContextBadgeSoon();
        }
        // Typed-chat replies stay silent. Speak is the 🔊 on the message
        // (toggleSpeakMessage). Live voice (/ws/voice) still speaks itself.
        // Telegram/Discord/Slack auto voice-notes are Settings tts_reply
        // after a voice inbound — not this SSE path. Auto-playing here is
        // what made the tab talk after every reply, then again after a
        // refresh once Settings Voice was on.
        } finally {
        // Flush any throttled live paint so the final frame shows the FULL
        // accumulated text (the last token batch may still be coalesced).
        _flushLiveTextPaint();
        // Live HITL card: keep the approval lock. Otherwise ALWAYS release
        // Stop / Enter — a painted reply with a stuck generating flag was
        // why the next message needed a Stop click first.
        if (hasLiveGate() || _awaitingApproval) {
          if (!_awaitingApproval) pauseForApproval(null);
          if (showArchived) loadArchivedSessions(); else refreshSessionsSoon();
        } else {
          endTurn();
        }
        // Truncated stream (no terminal frame): reconcile with durable
        // truth after the lock settles — paints the persisted reply when
        // the turn already finished, re-attaches when still generating.
        if (truncated) {
          setTimeout(function() { _resyncDelivery('sse-truncated'); }, 400);
        }
        // Interrupted (HITL) turn with no rendered card anywhere = silently
        // paused. Recover the card from server truth, best-effort one shot.
        // `truncated` (stream died with no terminal frame — client refresh /
        // tab switch) is included: the interrupt event may have fired AFTER
        // this tab's stream dropped, so `interrupted` stays false and the
        // pending approval would otherwise be invisible until auto-deny.
        // Frozen or omitted chrome must not skip this.
        if ((interrupted || truncated) && !_serverGenerating) {
          setTimeout(recoverMissedApproval, 1200);
        }
        }
      },

      onApprovalRequired: function(data) {
        // HITL is not epoch-gated — see _defaultAttachCallbacks.
        // HITL: journal part + one projector paints the card.
        // Replay provenance: a frame re-delivered from the journal is
        // history — a settled approval's retained frame must not flash a
        // ghost card on refresh. The registry reconciler is the only
        // painter of pending state during load.
        if (data && data.replay) return;
        if (_hitlAlreadyClaimed(data)) return;
        if (data && data.thread_id) _lastInterruptedThreadId = String(data.thread_id);
        _clearStatusStrip();
        pauseForApproval(data);
        _ingestFrameGateViews(data);
        applyTurnEvent({
          type: 'hitl',
          state: 'pending',
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
          view: (data && data.view) || undefined,
          turn_id: (data && data.turn_id) || _liveTurnId,
          source: 'sse',
        });
        refreshSessionsSoon();
      },
      onHitl: function(data) {
        var st = String((data && data.state) || 'pending');
        // Replayed pending frames are history (ghost-card flash, 2026-09-03).
        if (st === 'pending' && data && data.replay) return;
        if (st === 'pending' && _hitlAlreadyClaimed(data)) return;
        if (st !== 'pending' && !_mine()) return;
        if (data && data.thread_id) _lastInterruptedThreadId = String(data.thread_id);
        if (st === 'pending') {
          _clearStatusStrip();
          pauseForApproval(data);
        } else {
          _awaitingApproval = false;
        }
        _ingestFrameGateViews(data);
        applyTurnEvent({
          type: 'hitl',
          state: st,
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
          view: (data && data.view) || undefined,
          turn_id: (data && data.turn_id) || _liveTurnId,
          source: 'sse',
        });
        refreshSessionsSoon();
      },

      onError: function(msg) {
        if (!_mine()) return;
        diag('sse-error', String(msg || ''));
        dumpDiagnostics();
        _sseAttempts++;
        _noteSeq();
        var lastId = (activeStream && typeof activeStream.lastEventId === 'function')
          ? activeStream.lastEventId() : null;
        activeStream = null;
        // HITL pause closes the HTTP body. That is not a failed turn — the
        // card is already on screen. Overwriting it with "network error"
        // was the live-vs-refresh mismatch (2026-09-01). Catch-up still
        // runs: do not skip resync because `_awaitingApproval`.
        if (_awaitingApproval || hasLiveGate()) {
          _resyncDelivery('sse-fail');
          if (!_serverGenerating) setTimeout(recoverMissedApproval, 400);
          return;
        }
        // One cursor resume while the turn is still awaited — only possible
        // if we actually saw a journaled id on the dead stream.
        if (_sseAttempts <= 2 && _awaitingReply
            && lastId != null && Number(lastId) > 0) {
          console.warn('[KazmaChat] SSE stream lost at seq=' + lastId + ' — resuming');
          noteTurnActivity();
          try {
            _setStatusStrip(ti('thinking', 'Kazma is thinking…'));
          } catch (_t) {}
          _lastSeqSeen = Number(lastId);
          _attachJournal('sse-lost');
          return;
        }
        // A painted reply must not be replaced by the transport error; the
        // durable store is SoT. Resync instead of clobbering the bubble.
        if (_turnPainted) {
          _resyncDelivery('sse-fail');
          return;
        }
        // Final failure: surface it, then reconcile with server truth (the
        // turn may have completed server-side and be durable already).
        _clearStatusStrip();
        _pinLiveAssistantBubble();
        var textEl = currentMsgEl.querySelector('.message-text');
        textEl.innerHTML = '<div class="error-message">\u26A0 ' + escapeHtml(msg) +
          '<br><button class="btn btn-sm btn-danger" onclick="window.KazmaChat.retry()">Retry</button></div>';
        endTurn();
        _resyncDelivery('sse-fail');
        // A dead stream can also mean the turn parked on a HITL interrupt
        // server-side that this tab never rendered — surface the approval
        // card from server truth so the user can act before auto-deny.
        if (!_serverGenerating) setTimeout(recoverMissedApproval, 800);
        if (msg && window.showToast) {
          try { window.showToast(String(msg), 'error', 4000); } catch (_t) {}
        }
      }
      };
    };

    // Park the outgoing text BEFORE dispatch: if the POST never reaches the
    // server (restart/down), the next load restores it with a Retry button
    // instead of silently losing the user's message.
    if (typeof content === 'string' && content.trim()) {
      _outboxWrite(content);
    }
    _dispatchSse(null);
  }

  function retry() {
    // Re-send last user message
    var userMsgs = messagesEl.querySelectorAll('.message-user .message-text');
    if (userMsgs.length) {
      var last = userMsgs[userMsgs.length - 1];
      var text = last.textContent;
      if (text) {
        inputEl.value = text;
        sendMessage();
      }
    }
  }

  // ── Timestamps ────────────────────────────────────────
  function formatMsgTime(isoOrDate) {
    var d;
    try {
      d = isoOrDate ? new Date(isoOrDate) : new Date();
      if (isNaN(d.getTime())) d = new Date();
    } catch (e) {
      d = new Date();
    }
    var now = new Date();
    var sameDay = d.toDateString() === now.toDateString();
    var time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: true });
    var fullStr = time;
    if (!sameDay) {
      var day = d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' });
      fullStr = day + ' ' + time;
    }
    // Return pure isolated text string using Unicode LRI (\u2066) and PDI (\u2069).
    // Plain-text Unicode isolators work in textContent, innerHTML, escapeHtml(),
    // and DOM nodes without rendering raw HTML tags as text.
    return '\u2066' + fullStr + '\u2069';
  }

  // ── Turn workbench (one solid progress surface) ───────
  // Plan (sticky checklist) + Memory explain + Activity (tools/status/thoughts).
  // Tool results stay expanded; panel does NOT auto-collapse on finish.
  var _progressEl = null;
  var _progressStepCount = 0;
  var _planItems = [];
  var _planParsedFromText = false;
  var _progressStartedAt = 0;
  var _progressTimerId = null;
  var _lastMemoryExplain = null;
  var TOOL_DETAIL_MAX = 900;
  // Detail length over which tool results render clamped with a "show more"
  // toggle instead of a 14em scroll box (B).
  var STEP_DETAIL_CLAMP_AT = 600;
  // Unique-id sequence for aria-controls on workbench panel bodies.
  var _panelSeq = 0;

  var _TOOL_FRIENDLY = {
    web_search: 'Search',
    read_url: 'Read page',
    read_url_to_file: 'Save page',
    crawl_site: 'Crawl site',
    crawl_page: 'Crawl page',
    knowledge_ingest_url: 'KB ingest',
    knowledge_ingest_site: 'KB crawl',
    knowledge_search: 'KB search',
    knowledge_create_library: 'KB create',
    knowledge_list_libraries: 'KB list',
    file_read: 'Read file',
    file_write: 'Write file',
    file_delete: 'Delete file',
    file_list: 'List files',
    file_search: 'Find files',
    shell_exec: 'Shell',
    code_exec: 'Code',
    python_exec: 'Python',
    digest_research_file: 'Digest',
    list_research_chunks: 'Chunks',
    read_research_chunk: 'Chunk',
    summarize_research_file: 'Summarize'
  };

  function _friendlyToolName(name) {
    var n = String(name || '').trim();
    if (!n) return n;
    if (_TOOL_FRIENDLY[n]) return _TOOL_FRIENDLY[n];
    var low = n.toLowerCase();
    if (_TOOL_FRIENDLY[low]) return _TOOL_FRIENDLY[low];
    return n.replace(/_/g, ' ');
  }

  function _toolFamily(rawTitle) {
    var n = String(rawTitle || '').toLowerCase();
    if (/browser_|playwright/.test(n)) return 'browse';
    if (/memory|recall|belief|episode/.test(n)) return 'memory';
    if (/web_search|read_url|crawl|search|rank_url/.test(n)) return 'search';
    if (/shell_exec|python_exec|code_exec|install|runtime/.test(n)) return 'exec';
    if (/file_write|file_delete|write_file|delete_file/.test(n)) return 'write';
    if (/file_read|file_list|file_search|read_file/.test(n)) return 'read';
    return 'tool';
  }

  function _stepGlyph(kind, family) {
    var d;
    if (kind === 'error') {
      d = '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h16.9a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/>';
    } else if (kind === 'thought') {
      d = '<path d="M12 3a7 7 0 00-4 12.8V18h8v-2.2A7 7 0 0012 3z"/><path d="M9 21h6"/>';
    } else if (kind === 'file' || family === 'read') {
      d = '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h6"/>';
    } else if (family === 'write') {
      d = '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4 12.5-12.5z"/>';
    } else if (family === 'exec') {
      d = '<path d="M4 17l6-5-6-5"/><path d="M12 19h8"/>';
    } else if (family === 'search') {
      d = '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>';
    } else if (family === 'browse') {
      d = '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 010 20M12 2a15 15 0 000 20"/>';
    } else if (family === 'memory') {
      d = '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>';
    } else if (kind === 'tool') {
      d = '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.2a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.2a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.2a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9c.3.6.9 1 1.5 1.1H21a2 2 0 110 4h-.2a1.7 1.7 0 00-1.4 1z"/>';
    } else {
      d = '<circle cx="12" cy="12" r="3"/><path d="M12 3v2M12 19v2M5 5l1.5 1.5M17.5 17.5L19 19M3 12h2M19 12h2M5 19l1.5-1.5M17.5 6.5L19 5"/>';
    }
    return '<svg class="step-glyph" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
  }

  function _setCotPhase(phase) {
    var panel = _progressEl;
    if (!panel || !phase) return;
    panel.setAttribute('data-phase', phase);
    var order = { think: 1, act: 2, write: 3 };
    var cur = order[phase] || 1;
    var nodes = panel.querySelectorAll('.cot-phase');
    for (var i = 0; i < nodes.length; i++) {
      var p = nodes[i].getAttribute('data-phase');
      var n = order[p] || 0;
      nodes[i].classList.toggle('is-on', p === phase);
      nodes[i].classList.toggle('is-done', n < cur);
    }
  }

  function _srcChipHtml(srcs) {
    var arr = Array.isArray(srcs) ? srcs : (srcs ? [srcs] : []);
    if (!arr.length) return '';
    return arr.map(function(s) {
      var key = String(s || '').toLowerCase();
      var color = '#94a3b8';
      if (key.indexOf('ppr') >= 0) color = '#93c5fd';
      else if (key.indexOf('dense') >= 0) color = '#38bdf8';
      else if (key.indexOf('fts') >= 0 || key.indexOf('belief') >= 0) color = '#34d399';
      else if (key.indexOf('session') >= 0) color = '#fbbf24';
      else if (key.indexOf('kb') >= 0) color = '#f472b6';
      return '<span class="mem-src-chip" style="color:' + color + ';">' + escapeHtml(String(s)) + '</span>';
    }).join('');
  }

  function applyMemoryExplain(data) {
    _lastMemoryExplain = data || null;
    // Attach-only (2026-09-03): memory explain updates an existing live
    // workbench; it must not mint a phantom in-bubble panel on hydration.
    var panel = messagesEl
      ? messagesEl.querySelector('.agent-progress.is-active')
      : null;
    if (!panel || !data) return;
    var wrap = panel.querySelector('.agent-memory-explain');
    var body = panel.querySelector('.agent-memory-explain-body');
    var meta = panel.querySelector('.agent-memory-explain-meta');
    if (!wrap || !body) return;
    var sum = data.summary || {};
    var nB = sum.beliefs || (data.beliefs || []).length || 0;
    var nE = sum.episodes || (data.episodes || []).length || 0;
    var nK = sum.knowledge || (data.knowledge || []).length || 0;
    if (meta) {
      meta.textContent = nB + ' beliefs · ' + nE + ' episodes · ' + nK + ' KB';
    }
    if (data.hint && data.detail === 'summary') {
      // Light inject summary when full explain is off
    }
    if (data.empty) {
      body.innerHTML = '<div class="agent-memory-explain-empty">' +
        escapeHtml(ti('memory_empty', 'No memory/KB hits this turn')) +
        (data.query ? ' <span class="muted">«' + escapeHtml(String(data.query).slice(0, 80)) + '»</span>' : '') +
        '</div>';
      wrap.hidden = false;
      return;
    }
    var lines = [];
    function row(kind, h) {
      var isAr = (document.documentElement.getAttribute('dir') || '') === 'rtl' || (window.KAZMA_LANG === 'ar');
      var label = kind === 'belief'
        ? (isAr ? 'معتقد' : 'BELIEF')
        : (kind === 'episode' ? (isAr ? 'حلقة' : 'EPISODE') : (isAr ? 'معرفة' : 'KB'));
      var cls = kind === 'belief' ? 'is-belief' : (kind === 'episode' ? 'is-episode' : 'is-kb');
      var score = (h.score != null && h.score !== '') ? Number(h.score).toFixed(3) : '';
      lines.push(
        '<div class="agent-memory-hit ' + cls + '">' +
          '<span class="agent-memory-kind">' + label + '</span> ' +
          '<span class="agent-memory-text">' + escapeHtml((h.content || '').slice(0, 180)) + '</span>' +
          '<div class="agent-memory-chips">' + _srcChipHtml(h.sources) +
          (score ? ' <span class="muted">score ' + score + '</span>' : '') +
          '</div></div>'
      );
    }
    (data.beliefs || []).forEach(function(h) { row('belief', h); });
    (data.episodes || []).forEach(function(h) { row('episode', h); });
    (data.knowledge || []).forEach(function(h) { row('knowledge', h); });
    var hintHtml = (data.hint && data.detail === 'summary')
      ? '<div class="agent-memory-explain-empty" style="margin-bottom:6px;">' + escapeHtml(String(data.hint)) + '</div>'
      : '';
    body.innerHTML = hintHtml + (lines.join('') ||
      '<div class="agent-memory-explain-empty">' + escapeHtml(ti('memory_empty', 'No memory/KB hits this turn')) + '</div>');
    wrap.hidden = false;
    var isArMem = (document.documentElement.getAttribute('dir') || '') === 'rtl' || (window.KAZMA_LANG === 'ar');
    var memUnits = isArMem
      ? (nB + ' ' + ti('beliefs', 'معتقدات') + ' / ' + nE + ' ' + ti('episodes', 'حلقات') + ' / ' + nK + ' KB')
      : (nB + 'B / ' + nE + 'E / ' + nK + 'KB');
    logProgress({
      kind: 'status',
      title: ti('memory_context', 'Memory context') + ' · ' + memUnits,
      state: 'info',
    });
  }

  function _formatElapsed(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) return s + 's';
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + 'm ' + r + 's';
  }

  function _tickProgressElapsed() {
    if (!_progressEl || !_progressStartedAt) return;
    var el = _progressEl.querySelector('.agent-progress-elapsed');
    if (!el) return;
    el.textContent = _formatElapsed(Date.now() - _progressStartedAt);
  }

  function _stopProgressTimer() {
    if (_progressTimerId) {
      clearInterval(_progressTimerId);
      _progressTimerId = null;
    }
  }

  /** Tools whose row may carry a file chip, and the verb it says. Every
   *  entry is a WRITE_FS tool in the server's side-effect registry
   *  (safety/side_effects.py; tests/test_file_chip.py checks the parity).
   *  A tool not listed gets no chip. Guessing from the name and from any
   *  slash in the result put "WROTE Asia/Kuwait" on x_list_scheduled -- a
   *  read whose output held a timezone (2026-09-24). */
  var _FILE_CHIP_OPS = {
    file_write: 'wrote',
    file_append: 'wrote',
    file_apply_patch: 'patched',
    file_apply_patch_set: 'patched',
    file_delete: 'deleted',
    generate_pdf: 'wrote',
    generate_docx: 'wrote',
    generate_xlsx: 'wrote'
  };

  function _fileChipOp(toolName) {
    var n = String(toolName || '').toLowerCase().trim();
    return Object.prototype.hasOwnProperty.call(_FILE_CHIP_OPS, n) ? _FILE_CHIP_OPS[n] : '';
  }

  function _extractPathFromTool(toolName, detail) {
    if (!_fileChipOp(toolName)) return '';
    var s = String(detail || '');
    // JSON {"path": "..."} (arguments) first, then the file the result names.
    var m = s.match(/"(?:path|file_path|output_path)"\s*:\s*"([^"]+)"/);
    if (m) return m[1];
    m = s.match(/'(?:path|file_path|output_path)'\s*:\s*'([^']+)'/);
    if (m) return m[1];
    // Bare path-ish token
    m = s.match(/(?:^|[\s"'])([A-Za-z]:\\[^\s"']+|\/[\w.\-\/]+|[\w.\-]+\/[\w.\-\/]+)/);
    if (m) return m[1];
    return '';
  }

  function _renderPlanList(panel) {
    var wrap = panel.querySelector('.agent-plan');
    var ol = panel.querySelector('.agent-plan-list');
    if (!wrap || !ol) return;
    if (!_planItems.length) {
      wrap.hidden = true;
      ol.innerHTML = '';
      return;
    }
    wrap.hidden = false;
    var doneN = _planItems.filter(function(p) { return p.done; }).length;
    var total = _planItems.length;
    var pct = total ? Math.round((doneN / total) * 100) : 0;
    var meta = panel.querySelector('.agent-plan-meta');
    if (meta) meta.textContent = doneN + '/' + total;
    var fill = panel.querySelector('.agent-plan-bar-fill');
    if (fill) fill.style.width = pct + '%';
    ol.innerHTML = _planItems.map(function(item, idx) {
      var done = item.done ? ' is-done' : '';
      var active = (!item.done && (idx === 0 || _planItems[idx - 1].done)) ? ' is-active' : '';
      return '<li class="agent-plan-item' + done + active + '" data-idx="' + idx + '">' +
        '<span class="plan-check" aria-hidden="true">' + (item.done ? '\u2713' : (idx + 1)) + '</span>' +
        '<span class="plan-text" dir="auto">' + escapeHtml(item.text) + '</span></li>';
    }).join('');
    // Arabic UI / Arabic plan text: force RTL layout (not only after bidi late-pass)
    var pageRtl = (document.documentElement.getAttribute('dir') || '') === 'rtl';
    var planSample = _planItems.map(function(p) { return p.text; }).join(' ');
    var arPlan = pageRtl || (window.KazmaBidi && KazmaBidi.isArabicDominant(planSample));
    if (arPlan) {
      wrap.setAttribute('dir', 'rtl');
      wrap.classList.add('is-rtl');
    } else {
      wrap.setAttribute('dir', 'ltr');
      wrap.classList.remove('is-rtl');
    }
    if (window.KazmaBidi) {
      try { KazmaBidi.applyAll(wrap); } catch (e) { /* ignore */ }
    }
  }

  function setPlan(items) {
    if (!items || !items.length) return;
    // Merge unique plan lines (keep order)
    items.forEach(function(raw) {
      var text = String(raw || '').replace(/^[\-\*\d\.\)\s]+/, '').trim();
      if (!text || text.length < 2) return;
      var exists = _planItems.some(function(p) {
        return p.text.toLowerCase() === text.toLowerCase();
      });
      if (!exists) _planItems.push({ text: text, done: false });
    });
    // setPlan must NEVER create an in-bubble workbench — on hydration it
    // painted a phantom "Working…" panel over finished history
    // (2026-09-03). It only updates a panel that is already open, which is
    // why the query below is a look, not an ensure.
    var panel = messagesEl
      ? messagesEl.querySelector('.agent-progress.is-active')
      : null;
    if (panel) {
      _renderPlanList(panel);
      scrollToBottom();
    }
  }

  /**
   * Split a ```plan fence (or ## Plan heading) from user-facing prose.
   * Mirrors kazma_core.agent.plan_fence.split_plan_and_prose — handles the
   * glued closer (```Saved.) that CommonMark never closes.
   */
  function splitPlanAndProse(text) {
    var s = _labelBarePlanFence(String(text || ''));
    if (!s.trim()) return { plan: '', prose: '' };
    // ALL ```plan fences are workbench scaffolding, never content. Later
    // fences WIN — the model re-plans mid-turn, and a reply carrying five
    // fences (2026-08-27 report) previously leaked fences #2..#5 into the
    // prose, rendering as raw ``` code walls. \b keeps ```plantuml /
    // ```planning out; optional [ \t]* accepts "``` plan".
    var plan = '';
    var pieces = [];
    var last = 0;
    var found = false;
    var re = /```[ \t]*plan\b[^\n]*\n?([\s\S]*?)```/gi;
    var m;
    while ((m = re.exec(s)) !== null) {
      found = true;
      pieces.push(s.slice(last, m.index));
      var body = String(m[1] || '').trim();
      if (body) plan = body; // later fence replaces the earlier plan
      last = re.lastIndex;
    }
    var residual = s.slice(last);
    // Trailing OPEN fence (closer not yet arrived — mid-stream shape).
    // Handles the glued closer (```Saved.) that CommonMark never closes.
    var open = residual.match(/```[ \t]*plan\b[^\n]*\n?([\s\S]*)$/i);
    if (open) {
      found = true;
      pieces.push(residual.slice(0, open.index));
      var split = _splitListThenProse(open[1] || '');
      if (split.plan) plan = split.plan;
      if (split.prose) pieces.push(split.prose);
    } else if (residual) {
      pieces.push(residual);
    }
    if (found) {
      var prose = pieces.map(function(p) { return String(p || '').trim(); })
        .filter(Boolean).join('\n\n').trim();
      return { plan: plan, prose: prose };
    }
    // Markdown Plan heading — only valid as the FIRST line (mirrors the
    // Python _MD_PLAN_RE anchor): a "## Plan" section deep inside a
    // rewritten document is content, not a workbench checklist.
    var md = s.replace(/^\s+/, '').match(/^(?:#{1,3}\s*plan\b|\*\*plan\*\*)[^\n]*\n([\s\S]*)/i);
    if (md) {
      var mdSplit = _splitListThenProse(md[1] || '');
      return { plan: mdSplit.plan, prose: mdSplit.prose };
    }
    return { plan: '', prose: s.trim() };
  }

  /** Relabel a leading unlabelled fence that holds only a list as ```plan.
   *  It is the plan the model forgot to label: live 2026-09-24 a bare ```
   *  plan with its closer glued to the answer (```Here's ...) never closed,
   *  and the whole reply rendered as one code block. Mirrors
   *  plan_fence._label_bare_plan_fence exactly; both read
   *  tests/fixtures/plan_fence/bare_leading_fence.json. */
  function _labelBarePlanFence(s) {
    var open = /^(\s*)```[ \t]*\n/.exec(s);
    if (!open) return s;
    var rest = s.slice(open[0].length);
    var close = rest.indexOf('```');
    if (close < 0) return s;
    var lines = rest.slice(0, close).split('\n').filter(function (l) { return l.trim(); });
    if (!lines.length) return s;
    for (var i = 0; i < lines.length; i++) {
      if (!/^\s*(?:[-*]|\d+[.)])\s+\S/.test(lines[i])) return s;
    }
    return open[1] + '```plan\n' + rest;
  }

  function _splitListThenProse(body) {
    var planLines = [];
    var rest = [];
    var inRest = false;
    String(body || '').split('\n').forEach(function(line) {
      if (inRest) { rest.push(line); return; }
      if (/^\s*(?:[-*]|\d+[.)])\s+\S/.test(line) || !line.trim()) planLines.push(line);
      else { inRest = true; rest.push(line); }
    });
    return { plan: planLines.join('\n').trim(), prose: rest.join('\n').trim() };
  }

  /** Bubble text: plan fence stripped when prose exists; never blank a plan-only turn. */
  function stripPlanFenceForDisplay(text) {
    var parts = splitPlanAndProse(text);
    if (parts.prose) return parts.prose;
    return String(text || '');
  }

  /** True when *text* is a workbench checklist with no user-facing prose.
   *  Those rows belong in the Plan widget, never the transcript — tab-return
   *  used to paint `plan- Inspect workspace…` as if it were the answer. */
  function isPlanOnlyMessage(text) {
    var s = String(text || '').trim();
    if (!s) return false;
    var parts = splitPlanAndProse(s);
    if (parts.plan && !String(parts.prose || '').trim()) return true;
    var lines = s.split('\n');
    var first = (lines[0] || '').trim();
    if (!/^plan\b/i.test(first)) return false;
    var glued = first.replace(/^plan\s*[-–—:]?\s*/i, '').trim();
    var rest = lines.slice(1).filter(function(l) { return String(l).trim(); });
    var body = (glued ? [glued] : []).concat(rest);
    if (!body.length) return true;
    var listish = 0;
    body.forEach(function(l) {
      if (/^\s*(?:[-*]|\d+[.)])\s+\S/.test(l)) listish++;
    });
    if (glued && !/^\s*(?:[-*]|\d+[.)])/.test(glued)) listish++;
    return listish >= Math.max(2, Math.ceil(body.length * 0.6));
  }

  /**
   * Post-process RENDERED plan-fence HTML (pure: html -> html).
   *
   * Why: stripPlanFenceForDisplay works at the TEXT level and drops the
   * ```plan fence whenever prose exists — but two shapes still reach the
   * paint as a raw code blob glued to surrounding prose (the 2026-08-27
   * transcript artifact: plan text + "Let me…" preamble + ":Core stats"
   * fused mid-line):
   *   1. plan-ONLY turns (no prose means the stripper returns the raw
   *      fenced text) render as a full-height <pre><code> wall;
   *   2. transient/streamed unclosed fences render as ONE <pre> holding
   *      the plan AND the trailing prose, which CommonMark never splits.
   * This transforms every rendered plan-ish code block into a COLLAPSED
   * <details class="kazma-plan"><summary>Plan</summary>...</details>,
   * drops DUPLICATED plan blocks so only one details remains, and forces
   * a block-level <p> boundary between </details> and bare trailing
   * prose.
   *
   * Contract:
   * - Pure and IDEMPOTENT under repeated innerHTML swaps driven by
   *   streaming: every paint derives the final html from scratch via
   *   transformRenderedForPlan(markdown(...)), and running the transform
   *   on already-transformed html is a no-op (regions inside an existing
   *   kazma-plan details are never touched again).
   * - Content-preserving: the original <pre>/<code> markup moves inside
   *   the details verbatim — no text node is rewritten or lost.
   * - Heuristic fallback only fires on UNSPECIFIED code fences whose body
   *   is majority checklist lines (>=2 list-marker lines and list lines
   *   form at least half of the non-empty lines). Explicitly-typed blocks
   *   (language-js, language-python, plantuml...) are never touched.
   */
  function transformRenderedForPlan(html) {
    var s = String(html || '');
    if (!s) return s;
    // Full <pre> element INCLUDING the mdRender copy-button tail — a
    // partial match would strand '<button…></pre>' outside the wrapper.
    var PLAN_OPEN = /<pre\b[^>]*>(\s*(?:<span class="code-lang">[^<]*<\/span>)?\s*<code([^>]*)>((?:(?!<\/code>)[\s\S])*)<\/code>\s*(?:<button[^>]*>[\s\S]*?<\/button>)?\s*)<\/pre>/g;

    // ── Pass 0: isolate existing kazma-plan regions (idempotency guard).
    // Everything between <details class="kazma-plan">...</details> is left
    // byte-identical; only the surrounding segments are transformed.
    var segs = [];
    var rest = s;
    var m;
    while ((m = rest.match(/<details class="kazma-plan"[^>]*>[\s\S]*?<\/details>/)) !== null) {
      if (m.index > 0) segs.push({ html: rest.slice(0, m.index), prot: false });
      segs.push({ html: m[0], prot: true });
      rest = rest.slice(m.index + m[0].length);
    }
    if (rest) segs.push({ html: rest, prot: false });
    if (!segs.some(function(seg) { return !seg.prot; })) return s;

    var primaryPlanText = null;

    function decodeEntities(txt) {
      return txt
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
        .replace(/&amp;/g, '&');
    }
    function planish(attrs, body) {
      if (/\blanguage-plan\b/.test(attrs)) return true;
      if (/class="[^"]*language-/.test(attrs)) return false; // explicitly typed
      var lines = decodeEntities(body).split('\n');
      var nonEmpty = 0, listy = 0;
      lines.forEach(function(ln) {
        if (!ln.trim()) return;
        nonEmpty++;
        if (/^\s*(?:[-*+]|\d+[.)])\s+\S/.test(ln)) listy++;
      });
      return nonEmpty >= 2 && listy >= 2 && listy * 2 >= nonEmpty;
    }
    function normKey(body) {
      return decodeEntities(body).replace(/\s+/g, ' ').trim().toLowerCase();
    }
    function buildDetails(inner) {
      // Content-preserving: the original <pre> inner markup (lang label,
      // escaped code, copy button) moves inside the details verbatim.
      return '<details class="kazma-plan"><summary>Plan</summary>'
        + '<div class="kazma-plan-body"><pre>' + inner + '</pre></div></details>';
    }

    var parts_ = [];
    segs.forEach(function(seg) {
      if (seg.prot) { parts_.push(seg.html); return; }
      var local = '';
      var cursor = 0;
      var pm;
      PLAN_OPEN.lastIndex = 0;
      while ((pm = PLAN_OPEN.exec(seg.html)) !== null) {
        if (!planish(pm[2], pm[3])) continue;
        local += seg.html.slice(cursor, pm.index);
        if (primaryPlanText === null) {
          primaryPlanText = normKey(pm[3]);
          local += buildDetails(pm[1]);
        } else if (normKey(pm[3]) !== primaryPlanText) {
          local += buildDetails(pm[1]);
        }
        // else: duplicated plan block — drop silently (collapse to ONE
        // details; the twin already renders above).
        cursor = pm.index + pm[0].length;
      }
      local += seg.html.slice(cursor);
      parts_.push(local);
    });

    var out = parts_.join('');

    // </details> directly followed by BARE TEXT (e.g. the fused
    // ":Core stats" run) needs a block boundary or the browser glues it
    // onto one line with whatever precedes it.
    out = out.replace(/(<\/details>)([^\s<])/g, '$1\n<p>$2');
    // Collapse ANY plurality of plan details (twins arriving via repeated
    // paints or duplicated server content) down to the first one.
    var seen = 0;
    out = out.replace(/<details class="kazma-plan"[^>]*>[\s\S]*?<\/details>/g, function(d) {
      seen++;
      return seen === 1 ? d : '';
    });
    return out;
  }

  /**
   * Pull a plan from model text: ```plan ... ``` or ## Plan / **Plan** lists.
   */
  function tryIngestPlanFromText(text) {
    if (!text || _planParsedFromText) return;
    var parts = splitPlanAndProse(text);
    var block = parts.plan || '';
    if (!block) return;
    var items = [];
    block.split('\n').forEach(function(line) {
      var m = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.+)/);
      if (m && m[1]) items.push(m[1].trim());
    });
    if (items.length) {
      _planParsedFromText = true;
      setPlan(items);
      logProgress({
        kind: 'status',
        title: tiFmt('plan_locked', 'Plan locked ({n} steps)', { n: items.length }),
        state: 'info',
      });
    }
  }

  /**
   * @param {object} step
   * @param {string} step.kind  status|tool|thought|plan|error|done
   * @param {string} step.title short label
   * @param {string} [step.detail] optional secondary line
   * @param {string} [step.state] running|done|failed|info
   */
  function _normalizeStatusTitle(s) {
    // Unify ellipsis / trailing dots so "thinking…" and "thinking..." coalesce.
    return String(s || '')
      .replace(/\u2026/g, '...')
      .replace(/\.+$/, '...')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
  }

  function _isThinkingStatus(s) {
    var n = _normalizeStatusTitle(s);
    // EN + AR thinking heartbeats (and localized CHAT_I18N.thinking)
    if (n.indexOf('thinking') >= 0) return true;
    if (n.indexOf('still working') >= 0) return true;
    if (n.indexOf('ما زال يعمل') >= 0) return true;
    if (n.indexOf('kazma is') === 0 && n.indexOf('think') >= 0) return true;
    if (n.indexOf('\u062a\u0641\u0643\u0631') >= 0) return true; // تفكر
    if (n.indexOf('\u0643\u0627\u0638\u0645\u0647') >= 0 && n.indexOf('\u062a\u0641\u0643') >= 0) return true;
    var canon = _normalizeStatusTitle(ti('thinking', ''));
    if (canon && (n === canon || n.indexOf(canon.replace(/\.\.\.$/, '')) === 0)) return true;
    return false;
  }

  /** Map common English CoT/HITL titles to CHAT_I18N (Activity log). */
  function _localizeCotTitle(title) {
    var s = String(title || '').trim();
    if (!s) return s;
    // Already Arabic-heavy — leave alone
    if (/[\u0600-\u06FF]/.test(s) && !/^[A-Za-z]/.test(s)) return s;
    var m;
    if (/^processing approval/i.test(s)) return ti('processing_approval', s);
    if (/^resuming graph execution/i.test(s)) return ti('resuming_graph', s);
    if (/^resuming execution/i.test(s)) return ti('resuming_execution', s);
    if (/^approval completed successfully/i.test(s)) return ti('approval_complete', s);
    if (/^continuing after deny/i.test(s)) return ti('continuing_after_deny', s);
    if (/^waiting for approval/i.test(s)) return ti('waiting_approval', s);
    m = s.match(/^preparing to execute\s+(\d+)\s+tools?/i);
    if (m) return tiFmt('preparing_n_tools', s, { n: m[1] });
    m = s.match(/^preparing to execute\s+(.+?)\s*\.?\.?\.?$/i);
    if (m) {
      var tool = m[1].replace(/\.+$/, '').trim();
      if (/^\d+\s+tools?$/i.test(tool)) {
        return tiFmt('preparing_n_tools', s, { n: (tool.match(/^(\d+)/) || [])[1] || tool });
      }
      return tiFmt('preparing_tool', s, { tool: tool });
    }
    m = s.match(/^running after\s+(\w+)\s+approval/i);
    if (m) return tiFmt('running_after_approval', s, { scope: m[1] });
    m = s.match(/^still working after approval\s*\((\d+)\s*s\)/i);
    if (m) return tiFmt('still_working_approval', s, { s: m[1] });
    m = s.match(/^still working\s*…?\s*\((\d+)\s*s\)/i);
    if (m) return tiFmt('still_working_sec', s, { s: m[1] });
    m = s.match(/^running\s+(.+?)\s*[.…]*$/i);
    if (m && !/after/i.test(s)) return tiFmt('running_tool', s, { tool: m[1] });
    return s;
  }

  /** Localized state label for tool rows (Done / Failed / Running…). */
  function _stepStateLabel(state) {
    return state === 'running'
      ? ti('running', 'Running\u2026')
      : (state === 'done'
        ? ti('step_done', ti('done', 'Done'))
        : (state === 'failed' ? ti('step_failed', 'Failed') : state));
  }

  /**
   * Detail block for a workbench row: clamped with a "show more" toggle when
   * the (truncated) result is long, expanded otherwise. Shared by the live
   * logProgress path and the restored-CoT renderer so both clamp identically.
   */
  /**
   * A step row's detail.
   *
   * `kind === 'thought'` is the model's own prose and is rendered as
   * markdown; everything else is escaped. A tool's detail is a payload —
   * JSON, a shell transcript, a diff — and the reader wants it verbatim,
   * not interpreted. A thought with a code fence in it wants the code
   * block, not three literal backticks (reported from the installed
   * build, 2026-09-20).
   */
  function _detailHtml(detail, forceExpanded, kind) {
    if (!detail) return '';
    var isThought = kind === 'thought';
    // A thought is NOT truncated.
    //
    // TOOL_DETAIL_MAX is a payload cap: 900 characters of a JSON result
    // or a shell transcript is already more than a row should carry. A
    // thought is the whole turn's thinking in one part, and cutting it
    // does not hide the rest — it removes it, so Show more reveals
    // nothing past the cut. Reported from the installed build: the
    // thoughts ended mid-sentence at "Card …", 896 characters in.
    //
    // Height is governed by `is-clamped` + Show more, which is the
    // mechanism for "long" that keeps the text.
    var t = isThought
      ? String(detail)
      : truncateStr(String(detail), TOOL_DETAIL_MAX);
    if (isThought && typeof KS !== 'undefined' && KS && KS.markdown) {
      // An unclosed fence swallows everything after it. The model can
      // leave one open even without truncation, so balance regardless.
      var fences = (t.match(/^```/gm) || []).length;
      if (fences % 2) t += '\n```';
      // Short prose expands, like the escaped path: a two-line thought
      // with a Show more button under it is noise.
      var openIt = forceExpanded || t.length <= STEP_DETAIL_CLAMP_AT;
      return '<div class="step-detail step-detail-md' +
        (openIt ? ' is-expanded' : ' is-clamped') + '">' +
        '<div class="step-detail-text">' + KS.markdown(_scrubDsml(t)) + '</div></div>' +
        (openIt ? '' :
          '<button type="button" class="step-show-more" data-open="0">' +
          escapeHtml(ti('show_more', 'Show more \u25BE')) + '</button>');
    }
    // A detail written by turn_detail.js leads with a one-line gist and keeps
    // the raw payload below it. Collapsed, show ONLY that line \u2014 the raw value
    // is for the moment you go looking, not for every row you scroll past.
    //
    // This also keeps a step row ONE line tall. Leading with the gist made
    // rows taller (gist + raw, up to the 3-line clamp), the transcript grew,
    // and the final reply landed below the fold \u2014 the operator had to scroll
    // to read an answer that used to arrive in view.
    var brk = t.indexOf('\n');
    var hasGist = brk > 0 && brk <= 120;
    // The text sits in an inner element with no padding, and THAT is what
    // clamps: overflow is clipped at the padding edge, so clamping the
    // padded box itself let the next line show through the bottom padding
    // (2026-09-24, the half-drawn "Show more" rows).
    if (forceExpanded || (!hasGist && t.length <= STEP_DETAIL_CLAMP_AT)) {
      return '<div class="step-detail is-expanded"><div class="step-detail-text">' +
        escapeHtml(t) + '</div></div>';
    }
    return '<div class="step-detail is-clamped' + (hasGist ? ' has-gist' : '') +
      '"><div class="step-detail-text">' + escapeHtml(t) + '</div></div>' +
      '<button type="button" class="step-show-more" data-open="0">' +
      escapeHtml(ti('show_more', 'Show more \u25BE')) + '</button>';
  }

  /**
   * Shared <li> inner-HTML builder for workbench step rows — THE single row
   * template used by both the live panel (logProgress) and the restored panel
   * (_activityRowsHtml), so a reloaded turn renders identically to the live
   * one: icons, state labels, per-row timestamps, file-diff chips, detail
   * clamping, and the animated running-status icon.
   */
  function _stepRowHtml(o) {
    var kind = o.kind || 'status';
    var state = o.state || 'info';
    var rawTitle = String(o.rawTitle || o.title || '').trim() || '\u2026';
    var title = o.title != null ? String(o.title) : rawTitle;
    var family = (kind === 'tool' || kind === 'file') ? _toolFamily(rawTitle) : (kind === 'thought' ? 'think' : (kind === 'error' ? 'error' : 'think'));
    var animIcon = ((kind === 'status' || kind === 'tool') && state === 'running') ? ' is-animated' : '';

    // File-diff chips for write/delete tools (one-line target path)
    var fileChip = '';
    if (kind === 'tool' || kind === 'file') {
      var pathGuess = _extractPathFromTool(rawTitle, o.detail);
      if (pathGuess) {
        fileChip =
          '<div class="file-diff-chip" title="' + escapeHtml(pathGuess) + '">' +
            '<span class="file-diff-op">' +
              (state === 'failed' ? 'failed' : _fileChipOp(rawTitle)) +
            '</span> ' +
            '<code class="file-diff-path">' + escapeHtml(pathGuess) + '</code>' +
          '</div>';
      }
    }

    // tsIso: ISO string → formatted time; null → no time (legacy rows
    // persisted before per-row timestamps); undefined → live "now".
    var timeText = o.tsIso ? formatMsgTime(o.tsIso) : (o.tsIso === null ? '' : formatMsgTime());
    return (
      '<span class="step-icon fam-' + family + animIcon + '" aria-hidden="true">' +
        _stepGlyph(kind, family) +
      '</span>' +
      '<div class="step-body">' +
        '<div class="step-line">' +
          '<span class="step-title">' + escapeHtml(title) + '</span>' +
          (kind === 'tool'
            ? ' <span class="step-state">' + escapeHtml(_stepStateLabel(state)) + '</span>'
            : '') +
          '<span class="step-time">' + escapeHtml(timeText) + '</span>' +
        '</div>' +
        fileChip +
        _detailHtml(o.detail, o.forceExpanded, kind) +
      '</div>'
    );
  }

  /**
   * Delegated "show more / less" toggles for one steps list (one listener
   * per list, works for rows appended later).
   */
  function _wireStepToggles(list) {
    if (!list || list._kazmaStepToggles) return;
    list._kazmaStepToggles = true;
    list.addEventListener('click', function(e) {
      var btn = e.target.closest('.step-show-more');
      if (!btn || !list.contains(btn)) return;
      var step = btn.closest('.agent-progress-step');
      var det = step && step.querySelector('.step-detail');
      if (!det) return;
      var open = btn.getAttribute('data-open') === '1';
      btn.setAttribute('data-open', open ? '0' : '1');
      det.classList.toggle('is-clamped', open);
      det.classList.toggle('is-expanded', !open);
      btn.textContent = open
        ? ti('show_more', 'Show more \u25BE')
        : ti('show_less', 'Show less \u25B4');
    });
  }

  /**
   * The ONE entry point for a progress step. Feeds the projector and
   * stops.
   *
   * It used to fall through to `ensureProgressPanel()` and paint a
   * panel itself whenever `window.KazmaTurnDocument` was missing. That
   * fallback was the last pre-V2 DOM writer, and it could never do the
   * job it looked like it was doing: with no projector module
   * `_docs.live` is never created, so `_answerFromDoc` returns "" and
   * the turn has no answer text. It would have painted progress over a
   * chat that cannot show replies.
   *
   * Plan §14.4 — "never run two DOM writers"; invariant U03 — only the
   * renderer mutates. Removed in Phase 5, together with
   * `ensureProgressPanel`, whose only caller it was.
   */
  function logProgress(step) {
    if (!step) return;
    var kind = step.kind || 'status';
    if (kind === 'plan') {
      var planDetail = step.detail != null ? String(step.detail) : '';
      var planTitle = String(step.title || '').trim() || '\u2026';
      if (planDetail) setPlan(planDetail.split('\n'));
      else setPlan([planTitle]);
      return;
    }
    if (kind === 'tool') _setCotPhase('act');
    else if (/synth|compos|writing reply/i.test(String(step.title || ''))) _setCotPhase('write');
    else _setCotPhase('think');
    applyTurnEvent({
      type: 'progress',
      step: step,
      source: 'progress',
      turn_id: _liveTurnId,
    });
  }

  function finalizeProgress(ok) {
    if (!_progressEl) return;
    var panel = _progressEl;
    _stopProgressTimer();
    _tickProgressElapsed();
    panel.classList.remove('is-active');
    panel.classList.add('is-done');
    // Terminal MUST NOT touch expansion either way (2026-09-03): the old
    // un-collapse here auto-expanded the panel at the exact frame it turned
    // is-done — the reader saw the CoT spring open in the grayed style,
    // then _collapseFinishedWorkbenches folded it back on the next turn.
    // Expansion is the user's chevron click only; the panel ends the turn
    // in whatever state the user left it.
    var titleEl = panel.querySelector('.agent-progress-title');
    var elapsed = _progressStartedAt ? _formatElapsed(Date.now() - _progressStartedAt) : '';
    if (titleEl) {
      if (ok === false) {
        titleEl.textContent = ti('stopped', 'Stopped');
      } else if (ok === 'empty') {
        // The turn terminal'd without painting any reply — never claim Done.
        titleEl.textContent = ti('no_response', 'No response');
        titleEl.title = 'Turn ended without a reply — see the message area or window.KazmaChat.diagnostics()';
      } else {
        // Turn summary bar: "Done · N tools · M steps · Xs · $cost · tokens"
        // One line that stays readable when the panel is collapsed.
        var parts = [ti('done', 'Done')];
        if (_progressToolCount > 0) {
          parts.push(tiCount('count_tools', _progressToolCount, '{n} tool', '{n} tools'));
        }
        if (_progressStepCount > 0) {
          parts.push(tiCount('count_steps', _progressStepCount, '{n} step', '{n} steps'));
        }
        if (elapsed) parts.push(elapsed);
        if (_lastTurnStats) {
          if (_lastTurnStats.cost) parts.push(KS.formatCost(_lastTurnStats.cost));
          if (_lastTurnStats.tokens) {
            parts.push(KS.formatTokens(_lastTurnStats.tokens) + ' ' + ti('tokens', 'tokens'));
          }
        }
        titleEl.textContent = parts.join(' \u00B7 ');
        titleEl.title = titleEl.textContent;
      }
    }
    if (_progressEl) _renderPlanList(_progressEl);
    var pulse = panel.querySelector('.agent-progress-pulse');
    if (pulse) pulse.classList.add('is-off');
    _progressEl = null;
    _planParsedFromText = false;
    _progressStartedAt = 0;
  }

  // ── Restored CoT workbench (persisted activity, shown on reload) ──
  // After a refresh / tab switch the live progress panel is gone. The server
  // persists a compact activity log with each assistant message (see
  // sse_chat / ws_chat), so returning to a session restores the "Thinking &
  // Activity" accordion instead of a blank transcript. Rows go through the
  // SAME template as the live panel (_stepRowHtml) so both render identically:
  // per-row timestamps (row.ts, persisted server-side), file-diff chips,
  // source chips, clamped details with show-more, localized titles, and
  // heartbeat coalescing for servers that persisted repeated status rows.
  function _activityRowsHtml(activity) {
    if (!Array.isArray(activity)) return '';
    var html = '';
    var lastKey = '';
    activity.forEach(function(row) {
      if (!row) return;
      var kind = row.kind === 'tool' ? 'tool'
        : (row.kind === 'thought' ? 'thought'
          : (row.kind === 'error' ? 'error' : 'status'));
      var state = row.state === 'running' ? 'running'
        : (row.state === 'failed' ? 'failed'
          : (row.state === 'done' ? 'done' : (kind === 'status' ? 'running' : 'done')));
      var rawTitle = String(row.title || '').trim() || '\u2026';
      var title = kind === 'tool' ? _friendlyToolName(rawTitle) : rawTitle;
      // Same canonicalization as the live path: thinking heartbeats localized,
      // leftover English CoT/HITL titles mapped to CHAT_I18N.
      if (kind === 'status' && _isThinkingStatus(title)) {
        title = ti('thinking', 'Kazma is thinking\u2026');
      }
      if (kind !== 'tool') title = _localizeCotTitle(title);
      var detail = row.detail != null ? String(row.detail) : '';

      // Coalesce consecutive identical status rows (persisted heartbeats).
      var key = kind + '|' + _normalizeStatusTitle(title) + '|' + (detail || '');
      if (kind === 'status' && key === lastKey) return;
      lastKey = key;

      html += '<li class="agent-progress-step step-' + kind + ' state-' + state +
        (kind === 'tool' ? ' is-expanded' : '') + '" data-kind="' + escapeHtml(kind) + '">' +
        _stepRowHtml({
          kind: kind,
          state: state,
          title: title,
          rawTitle: rawTitle,
          detail: detail,
          tsIso: row.ts || null,
        }) +
      '</li>';
    });
    return html;
  }

  /**
   * Put the panel in the state the reader asked for.
   *
   * Idempotent and cheap: called on every render pass, writes only when
   * the DOM disagrees, so a paint that changes nothing performs no
   * mutation (the renderer's idempotence is testable and this must not
   * cost it).
   */
  function _applyActivityFold(panel, turnId) {
    if (!panel || !panel.classList) return;
    var open = _activityExpanded(turnId);
    var collapsed = panel.classList.contains('is-collapsed');
    if (collapsed === !open) {
      // already correct
    } else if (open) {
      panel.classList.remove('is-collapsed');
    } else {
      panel.classList.add('is-collapsed');
    }
    var chev = panel.querySelector('.agent-progress-chevron');
    var want = open ? '\u25BE' : '\u25B8';
    if (chev && chev.textContent !== want) chev.textContent = want;
    var hdr = panel.querySelector('.agent-progress-header');
    if (hdr && hdr.getAttribute('aria-expanded') !== String(open)) {
      hdr.setAttribute('aria-expanded', String(open));
    }
  }

  /** Which turn owns this panel, right now. Reads the attribute the
   *  renderer keeps current through bind/promote rather than a value
   *  captured when the node was built. */
  function _turnIdOfPanel(panel) {
    try {
      var bubble = panel && panel.closest && panel.closest('[data-turn-id]');
      return bubble ? String(bubble.getAttribute('data-turn-id') || '') : '';
    } catch (e) {
      return '';
    }
  }

  /** The ONE place a disclosure preference is written, and only from a
   *  reader gesture (AGENTS.md section 31). Callers: _toggleActivityFold
   *  (the thoughts fold) and a decided approval card's header click.
   *  tests/test_turn_ledger_abc.py counts the setExpanded call sites.
   *  Returns false when there is no store (blocked site data). */
  function _writeFoldPreference(turnId, name, open) {
    var prefs = _turnPrefs();
    if (!prefs) return false;
    prefs.setExpanded(String(turnId || ''), name, !!open);
    return true;
  }

  function _toggleActivityFold(panel, turnId) {
    var prefs = _turnPrefs();
    var open = !panel.classList.contains('is-collapsed');
    var next = !open;
    _writeFoldPreference(turnId, 'activity', next);
    _applyActivityFold(panel, turnId);
    if (!prefs) {
      // No store (blocked site data): honour the click for this paint at
      // least, rather than snapping back and looking broken.
      panel.classList.toggle('is-collapsed', !next);
    }
    return next;
  }

  function _buildRestoredWorkbench(activity, turnId) {
    if (!Array.isArray(activity) || !activity.length) return null;
    var rows = _activityRowsHtml(activity);
    if (!rows) return null;
    var pageRtl = (document.documentElement.getAttribute('dir') || '') === 'rtl';
    var panel = document.createElement('div');
    panel.className = 'agent-progress is-done is-collapsed kazma-cot-restored';
    if (pageRtl) {
      panel.setAttribute('dir', 'rtl');
      panel.classList.add('is-rtl');
    }
    var stepCount = (rows.match(/<li /g) || []).length;
    var toolCount = (rows.match(/data-kind="tool"/g) || []).length;
    _panelSeq += 1;
    var bodyId = 'agent-progress-body-' + _panelSeq;
    // Header mirrors the live summary bar shape: "N tools · M steps" (+usage
    // when the server stamped per-turn tokens/cost on the message).
    var headBits = [];
    if (toolCount) headBits.push(tiCount('count_tools', toolCount, '{n} tool', '{n} tools'));
    headBits.push(tiCount('count_steps', stepCount, '{n} step', '{n} steps'));
    panel.innerHTML =
      '<div class="agent-progress-header" role="button" tabindex="0" title="' + escapeHtml(ti('cot_title', 'Thinking & Activity')) + '"' +
        ' aria-expanded="false" aria-controls="' + bodyId + '">' +
        '<span class="agent-progress-pulse is-off" aria-hidden="true"></span>' +
        '<div class="agent-progress-heading">' +
          '<span class="agent-progress-kicker">' + escapeHtml(ti('reasoning', 'Reasoning')) + '</span>' +
          '<span class="agent-progress-title">' + escapeHtml(ti('cot_title', 'Thinking & Activity')) + '</span>' +
        '</div>' +
        '<span class="agent-progress-count">' + escapeHtml(headBits.join(' \u00B7 ')) + '</span>' +
        '<span class="agent-progress-chevron" aria-hidden="true">\u25B8</span>' +
      '</div>' +
      '<div class="agent-progress-body" id="' + bodyId + '">' +
        '<div class="agent-activity-label">' + escapeHtml(ti('activity', 'Activity')) + '</div>' +
        '<ul class="agent-progress-steps" role="log">' + rows + '</ul>' +
      '</div>';
    var header = panel.querySelector('.agent-progress-header');
    if (header) {
      var builtUnder = String(turnId || '');
      function toggle() {
        // Routed through the preference store so the choice survives the
        // next token, the terminal frame, and a re-render from history.
        // Flipping the class here directly is what made the fold a
        // property of the last paint instead of of the reader.
        //
        // The id is resolved NOW, from the bubble the panel is in, rather
        // than captured when the panel was built: a turn opens under the
        // 'live' placeholder and is renamed on the first stamped frame,
        // so a captured id goes stale within a second of the turn
        // starting. TV.bind/promote keep data-turn-id current, which is
        // why it is the honest source.
        _toggleActivityFold(panel, _turnIdOfPanel(panel) || builtUnder);
      }
      header.addEventListener('click', toggle);
      header.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
    }
    _wireStepToggles(panel.querySelector('.agent-progress-steps'));
    return panel;
  }

  // ── Message rendering ─────────────────────────────────
  /**
   * Render user text into the bubble. Single-line input keeps the exact
   * legacy escape-only path; multi-line / pasted text (headings, bullets,
   * numbered lists) is parsed line-by-line into real HTML structure so a
   * long paste doesn't collapse into one justified block. Everything is
   * escaped BEFORE structure is built — no raw HTML ever enters from input.
   */
  function renderUserContentHtml(text) {
    var s = String(text || '');
    // Same rich renderer as assistant replies (mdRender escapes ALL HTML
    // internally — pasted markup can never inject), so a pasted formatted
    // text keeps bold/code/tables/links and the exact paragraph spacing the
    // assistant output uses. Legacy hand-rolled fallback below only if the
    // streaming module is absent.
    if (KS && KS.markdown) {
      try { return KS.markdown(s); } catch (e) { /* fall through */ }
    }
    if (s.indexOf('\n') < 0 && !/^\s*(?:#{1,4}\s|[-*\u2022]\s|\d+[.)]\s)/.test(s)) {
      return escapeHtml(s);
    }
    var html = '';
    var list = null;
    function closeList() {
      if (list) { html += '</' + list + '>'; list = null; }
    }
    s.split(/\r?\n/).forEach(function (ln) {
      var t = ln.trim();
      var m;
      if (!t) { closeList(); return; }
      if ((m = t.match(/^(#{1,4})\s+(.*)$/))) {
        closeList();
        var lvl = Math.min(m[1].length + 2, 4);   // # → h3, ## → h4
        html += '<h' + lvl + '>' + escapeHtml(m[2]) + '</h' + lvl + '>';
      } else if ((m = t.match(/^[-*\u2022]\s+(.*)$/))) {
        if (list !== 'ul') { closeList(); html += '<ul>'; list = 'ul'; }
        html += '<li>' + escapeHtml(m[1]) + '</li>';
      } else if ((m = t.match(/^(\d+)[.)]\s+(.*)$/))) {
        if (list !== 'ol') { closeList(); html += '<ol>'; list = 'ol'; }
        html += '<li>' + escapeHtml(m[2]) + '</li>';
      } else {
        closeList();
        html += '<p>' + escapeHtml(t) + '</p>';
      }
    });
    closeList();
    return html;
  }

  function appendMessage(role, content, attachmentName, ts, opts) {
    var wrapper = document.createElement('div');
    wrapper.className = 'message message-' + role;
    if (role === 'assistant' && content && String(content).trim()) {
      _turnPainted = true;  // any painted assistant text counts as a reply
    }

    var when = formatMsgTime(ts);
    var iso = '';
    try {
      iso = ts ? new Date(ts).toISOString() : new Date().toISOString();
    } catch (e) {
      iso = new Date().toISOString();
    }
    var avatarHtml = role === 'user'
      ? '<div class="message-avatar message-avatar-user">You</div>'
      : '<div class="message-avatar message-avatar-agent" title="Kazma">' +
          '<img src="/static/img/kazma-icon.png" alt="Kazma" class="message-avatar-img" ' +
          'onerror="this.style.display=\'none\';this.parentNode.textContent=\'K\';" />' +
        '</div>';

    var modelBit = (opts && opts.model) ? (' \u00B7 ' + escapeHtml(String(opts.model))) : '';
    if (role === 'assistant' && opts && opts.turn_id) {
      wrapper.setAttribute('data-turn-id', String(opts.turn_id));
    }
    wrapper.innerHTML =
      avatarHtml +
      '<div class="message-content">' +
        '<div class="message-text" dir="auto">' +
          (role === 'user' ? renderUserContentHtml(content) : KS.markdown(_scrubDsml(stripPlanFenceForDisplay(content)))) +
        '</div>' +
        '<div class="message-meta" data-ts="' + escapeHtml(iso) + '">' +
          (attachmentName ? '\uD83D\uDCCE ' + escapeHtml(attachmentName) + ' \u00B7 ' : '') +
          '<time datetime="' + escapeHtml(iso) + '">' + escapeHtml(when) + '</time>' +
          modelBit +
        '</div>' +
      '</div>';

    // Bidi for user + assistant bubbles: English UI must still render Arabic
    // RTL (dir=auto alone is not enough for mixed/dominant Arabic blocks).
    var msgTextEl = wrapper.querySelector('.message-text');
    if (msgTextEl && window.KazmaBidi) {
      try { KazmaBidi.apply(msgTextEl, content || ''); } catch (e) { /* ignore */ }
    }

    // Restore the persisted CoT workbench (activity log / TurnDocument
    // parts) for assistant messages when returning to a session.
    var restoredActivity = (opts && opts.activity) || [];
    if ((!restoredActivity || !restoredActivity.length) && opts && opts.parts
        && window.KazmaTurnDocument && KazmaTurnDocument.activityOf) {
      restoredActivity = KazmaTurnDocument.activityOf(opts.parts);
    }
    if (role === 'assistant' && restoredActivity && restoredActivity.length) {
      // Same turn id the renderer will bind this bubble under, so a fold
      // the reader opened before a refresh comes back open rather than
      // being a different turn as far as the preference store is
      // concerned.
      var cotPanel = _buildRestoredWorkbench(
        restoredActivity, String((opts && (opts.turnId || opts.turn_id)) || '')
      );
      if (cotPanel) {
        var textWrap = wrapper.querySelector('.message-text');
        if (textWrap) textWrap.parentNode.insertBefore(cotPanel, textWrap);
        else wrapper.querySelector('.message-content').appendChild(cotPanel);
      }
    }

    if (role === 'user') {
      // Add message actions
      var actions = document.createElement('div');
      actions.className = 'message-actions';
      actions.innerHTML =
        '<button class="msg-action" title="Edit" data-action="edit">\u270E</button>' +
        '<button class="msg-action" title="Copy" data-action="copy">\u2398</button>' +
        '<button class="msg-action" title="Regenerate" data-action="regenerate">\u21BB</button>';
      wrapper.querySelector('.message-content').appendChild(actions);

      // Wire up actions
      actions.addEventListener('click', function(e) {
        var btn = e.target.closest('[data-action]');
        if (!btn) return;
        var action = btn.dataset.action;
        if (action === 'edit') editMessage(wrapper);
        else if (action === 'copy') copyMessage(wrapper);
        else if (action === 'regenerate') regenerateFrom(wrapper);
      });
    } else {
      // Assistant message actions
      var aActions = document.createElement('div');
      aActions.className = 'message-actions';
      // Speak sits FIRST and stays visible while the rest of the row waits
      // for hover. It is the only action here that needs to be reachable
      // while something is already happening: when a reply is being read
      // aloud, the person wants to stop THIS one, and hunting for a hidden
      // control (or a second speaker icon in the composer, which nobody
      // could tell from the live-voice mic) is not a way to stop audio.
      aActions.innerHTML =
        '<button class="msg-action speak-action" title="Read aloud" data-action="speak" aria-label="Read this message aloud">\uD83D\uDD0A</button>' +
        '<button class="msg-action reaction-btn" title="Helpful" data-reaction="up">\uD83D\uDC4D</button>' +
        '<button class="msg-action reaction-btn" title="Not helpful" data-reaction="down">\uD83D\uDC4E</button>' +
        '<button class="msg-action" title="Copy" data-action="copy">\u2398</button>';
      wrapper.querySelector('.message-content').appendChild(aActions);

      aActions.addEventListener('click', function(e) {
        var btn = e.target.closest('[data-action]');
        var rxnBtn = e.target.closest('[data-reaction]');
        if (rxnBtn) {
          var reaction = rxnBtn.dataset.reaction;
          rxnBtn.classList.toggle('active');
          KS.toast(reaction === 'up' ? '\uD83D\uDC4D Thanks for the feedback!' : '\uD83D\uDC4E Got it. I\'ll try to improve.', 'info', 2000);
        } else if (btn && btn.dataset.action === 'copy') {
          copyAssistantMessage(wrapper);
        } else if (btn && btn.dataset.action === 'speak') {
          toggleSpeakMessage(wrapper);
        }
      });
    }

    // Remove welcome if present
    var welcome = messagesEl.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    messagesEl.appendChild(wrapper);
    // Register the bubble under its turn id at birth. A later frame for
    // this turn then resolves by map lookup instead of a querySelector that
    // can match whatever else the transcript happens to contain.
    if (role === 'assistant' && opts && opts.turn_id) {
      var TVa = _turnView();
      if (TVa) TVa.bind(String(opts.turn_id), wrapper);
    }
    updateContextBadgeSoon();
    return wrapper;
  }

  function createAssistantMessage() {
    return appendMessage('assistant', '');
  }

  /**
   * HITL approval card with scope options:
   *  - Approve once
   *  - Allow this tool for the session (stops flood for shell_exec etc.)
   *  - YOLO (all danger tools for this session)
   *  - Deny
   */
  /** True when an ACTIVE inline approval card (enabled buttons) is rendered.
      Resolved cards keep the class but their buttons are disabled/removed, so
      a stale card from an earlier approval doesn't suppress a new one. */
  // ── Live-token paint throttle ─────────────────────────────────────
  // Re-parsing the FULL accumulated markdown and replacing innerHTML on
  // every token event tore the message down and rebuilt it ~2,600 times per
  // reply (measured 2026-08-27: 5,311 DOM mutations on a 150-word story) —
  // the visible "double vision" flicker while streaming. Coalesce to one
  // render per window; the terminal frame flushes whatever is pending.
  var _LIVE_RENDER_MIN_MS = 150;
  var _liveRenderTimer = null;
  var _liveRenderLastAt = 0;
  var _liveRenderDirty = false;
  var _liveRenderEl = null;

  /**
   * The ONE way reply text becomes HTML. Every paint site funnels through
   * here so the last streamed frame and the terminal frame cannot render
   * the same text two different ways — appendLiveToken used to skip
   * _scrubDsml, so the final paint visibly changed the message.
   */
  function _renderReplyHTML(text) {
    return transformRenderedForPlan(
      KS.markdown(_scrubDsml(stripPlanFenceForDisplay(text)))
    );
  }

  /**
   * Idempotent paint: assign only when the markup actually differs.
   *
   * Assigning innerHTML tears the whole message subtree down and rebuilds
   * it, so re-painting identical HTML still costs a reflow — visible as a
   * "blink" at the end of every reply, because the terminal frame always
   * repainted the finished message even when server truth matched what was
   * already on screen. Returns true when the DOM changed.
   */
  function _paintHTML(textEl, html) {
    if (!textEl) return false;
    if (textEl.closest && textEl.closest('.message-user')) return false;
    // Compare against the SOURCE string we last wrote, not textEl.innerHTML:
    // reading innerHTML returns the browser's re-serialization of the DOM
    // (attribute order, entity and void-tag normalisation), which routinely
    // differs from the string that produced it — so an innerHTML comparison
    // would report "changed" every time and repaint anyway.
    if (textEl._kzPaintedHTML === html) return false;
    textEl.innerHTML = html;
    textEl._kzPaintedHTML = html;
    return true;
  }

  function _paintLiveTextNow(textEl, final) {
    if (!textEl) return;
    if (textEl.closest && textEl.closest('.message-user')) return;
    var liveText = _liveAnswerText();
    // Nothing to say means DO NOT PAINT, rather than paint nothing. Both
    // transports deliver a terminal frame, so the second one used to flush
    // after the first had cleared the accumulator and wrote "" over a
    // finished reply — the answer vanished at the end of the stream until
    // a refresh (2026-09-02). Reading the document instead removes the
    // window rather than guarding it, and the guard stays anyway: an empty
    // read is never an instruction to blank the answer.
    if (!String(liveText || '').trim()) return;
    if (final) {
      if (_paintHTML(textEl, _renderReplyHTML(liveText))) {
        if (window.KazmaBidi) KazmaBidi.apply(textEl, liveText);
      }
      // Re-apply dir="auto" after innerHTML (the attribute survives but the
      // bidi direction may need recalculating for the new content).
      textEl.setAttribute('dir', 'auto');
      return;
    }
    // Live paints render FULL markdown at the throttled cadence (≤ ~7
    // renders/sec via _scheduleLiveTextPaint) — the reply is ALWAYS
    // formatted, never a raw text block. This is safe now that the real
    // flicker causes are fixed (steady status strip + pin-to-bottom
    // scrolling): the throttle bounds the rebuild churn, and nothing shifts
    // the layout around it. Plan fences stay stripped live; the plan-only
    // phase holds the bubble open with a blank line.
    var liveParts = splitPlanAndProse(liveText);
    liveParts.prose = _scrubDsml(liveParts.prose);
    if (liveParts.prose) {
      if (_paintHTML(textEl, transformRenderedForPlan(KS.markdown(liveParts.prose)))) {
        if (window.KazmaBidi) KazmaBidi.apply(textEl, liveParts.prose);
      }
    } else {
      // Plan-only / CoT hop: keep a visible "Planning…" line instead of
      // blanking the bubble (nbsp). Blanking is what made the stream look
      // like thoughts that vanish, then a different final answer appears.
      var planHint = '<p class="kz-planning">' + escapeHtml(ti('planning', 'Planning\u2026')) + '</p>';
      _paintHTML(textEl, planHint);
    }
    textEl.setAttribute('dir', 'auto');
  }

  function _scheduleLiveTextPaint(textEl) {
    if (!textEl) return;
    if (textEl.closest && textEl.closest('.message-user')) return;
    _liveRenderEl = textEl;
    var since = Date.now() - _liveRenderLastAt;
    if (since >= _LIVE_RENDER_MIN_MS) {
      if (_liveRenderTimer) { clearTimeout(_liveRenderTimer); _liveRenderTimer = null; }
      _liveRenderLastAt = Date.now();
      _liveRenderDirty = false;
      _paintLiveTextNow(textEl, false);
      return;
    }
    _liveRenderDirty = true;
    if (_liveRenderTimer) return;
    _liveRenderTimer = setTimeout(function() {
      _liveRenderTimer = null;
      _liveRenderLastAt = Date.now();
      _liveRenderDirty = false;
      if (_liveRenderEl) _paintLiveTextNow(_liveRenderEl, false);
    }, _LIVE_RENDER_MIN_MS - since);
  }

  /** Terminal flush: cancel any pending throttled paint and render the final
   *  accumulated text as formatted markdown, exactly once (called from the
   *  done/finally paths). */
  function _flushLiveTextPaint() {
    if (_liveRenderTimer) { clearTimeout(_liveRenderTimer); _liveRenderTimer = null; }
    // Release the target BEFORE painting: a duplicate terminal (SSE + WS both
    // deliver done) must find no live-render handle after the first flush, so
    // a late second flush is a no-op instead of a repaint over a closed turn.
    var el = _liveRenderEl;
    _liveRenderEl = null;
    if (el) {
      _liveRenderLastAt = Date.now();
      _liveRenderDirty = false;
      _paintLiveTextNow(el, true);
    }
  }

  // ── DSML leak scrub ────────────────────────────────────────────────
  // DeepSeek models sometimes emit their NATIVE tool-call markup
  // (<｜｜DSML｜｜invoke ...>) as plain text instead of
  // structured tool calls — raw protocol garbage in the reply (2026-08-27
  // live report). Scrub it from every paint path; the markup carries no
  // human value.
  var _DSML_BLOCK = /<｜｜DSML｜｜tool_calls>[\s\S]*?<\/｜｜DSML｜｜tool_calls>/g;
  var _DSML_TAG = /<\/?｜｜[^>]*>/g;
  // A fenced block holding a single JSON object shaped like a TOOL CALL
  // (top-level "tool"/"name" key) — the model narrating an invocation it
  // could not execute (2026-08-27: '{"tool": "file_list", "path": "."}'
  // rendered as an ugly code block inside the chat).
  var _TOOLCALL_BLOCK = /```[a-zA-Z]*[ \t]*\n[ \t]*\{[\s\S]*?"(?:tool|name)"[ \t]*:[\s\S]*?\}[ \t]*\n?```/g;
  function _scrubDsml(text) {
    var t = String(text || '');
    _TOOLCALL_BLOCK.lastIndex = 0;
    var hasToolBlock = _TOOLCALL_BLOCK.test(t);
    _TOOLCALL_BLOCK.lastIndex = 0;
    if (t.indexOf('DSML') < 0 && t.indexOf('｜') < 0 && !hasToolBlock) return t;
    return t.replace(_DSML_BLOCK, '')
            .replace(_TOOLCALL_BLOCK, '')
            .replace(_DSML_TAG, '')
            .replace(/\n{3,}/g, '\n\n').trim();
  }

  function markApprovalTimedOut(msg) {
    var text = String(msg || 'Approval timed out — continuing without this tool.');
    applyTurnEvent({
      type: 'hitl',
      state: 'timeout',
      payload: { message: text },
      turn_id: _liveTurnId,
      source: 'timeout',
    });
    // Chrome comes from TurnView after the document update. Do not
    // className-stamp cards here (HITL_VIEW_MODEL C).
    _awaitingApproval = false;
    if (window.showToast) {
      try { window.showToast(text, 'warning', 6000); } catch (e) { /* ignore */ }
    }
  }

  function _releaseHitlComposer(reason) {
    // Abort / idle-steer: the composer is a NEW turn, not a steer of a
    // ghost card. Freeze any live buttons so hydrate cannot re-lock.
    _awaitingApproval = false;
    _serverPaused = false;
    if (messagesEl) {
      messagesEl.querySelectorAll('.hitl-approval-card').forEach(function(card) {
        if (_hitlCardIsClaimed(card)) return;
        _noteGateDecided({ interrupt_id: card.getAttribute('data-interrupt-id') || '' }, 'denied');
      });
    }
    _clearStoreApproval();
    diag('hitl-released', reason || '');
  }




  /**
   * Is a gate genuinely waiting on the operator?
   *
   * Answered from live views only: any view.interactive. Never the
   * document part stamp, never a DOM scan, never a frozen card.
   *
   * The DOM scan (and later the leftover pending stamp) made this
   * predicate dangerous. It returned true for a FOSSIL card whose gate
   * had already settled, and recovery paths early-returned on it, so the
   * only unconditional route back to server truth switched itself off
   * exactly when a turn had gone quiet.
   */
  function hasLiveGate() {
    var i;
    var views = _serverGateViews || [];
    for (i = 0; i < views.length; i++) {
      if (_viewIsPending(views[i])) return true;
    }
    return false;
  }

  /** Durable row for the turn this tab is projecting, not a previous reply. */
  function _messageIsThisTurn(msg) {
    if (!msg) return false;
    var tid = String(msg.turn_id || '');
    if (tid && _liveTurnId && tid === String(_liveTurnId)) return true;
    if (msg.open || msg.pending) return true;
    var parts = msg.parts;
    if (!Array.isArray(parts) || !parts.length) return false;
    var TD = window.KazmaTurnDocument;
    if (!TD || typeof TD.hitlPartsOf !== 'function') return false;
    var gates = TD.hitlPartsOf(parts);
    var i, iid, v;
    for (i = 0; i < gates.length; i++) {
      iid = _hitlInterruptIdOf(gates[i]);
      v = iid ? _gateViewById(iid) : null;
      if (v && _viewIsPending(v)) return true;
    }
    return false;
  }

  /** Is a clickable card actually on screen? A DOM question, and the only
   *  thing it may decide is whether the Alpine store fallback is needed —
   *  never whether a recovery path is allowed to run. */
  function hasInlineApprovalCard() {
    if (!messagesEl) return false;
    var cards = messagesEl.querySelectorAll('.hitl-approval-card');
    for (var i = 0; i < cards.length; i++) {
      var btns = cards[i].querySelectorAll('button');
      for (var j = 0; j < btns.length; j++) {
        if (!btns[j].disabled) return true;
      }
    }
    return false;
  }

  function _hitlInterruptIdOf(data) {
    data = data || {};
    return String(data.interrupt_id || (data.payload && data.payload.interrupt_id) || '');
  }

  function _hitlToolOf(data) {
    data = data || {};
    return String(
      data.tool || data.tool_name
      || (data.payload && (data.payload.tool || data.payload.tool_name))
      || ''
    ).trim();
  }

  function _hitlCardIsClaimed(card) {
    if (!card || !card.classList) return false;
    return card.classList.contains('hitl-approved')
      || card.classList.contains('hitl-denied')
      || card.classList.contains('hitl-error');
  }

  function _hitlAlreadyClaimed(data) {
    // Document + views only. A frozen/claimed DOM card must not drop a
    // pending frame for a later interrupt (HITL_VIEW_MODEL E).
    var iid = _hitlInterruptIdOf(data);
    if (!iid) return false;
    var view = _gateViewById(iid);
    if (!view) {
      var ov = _hitlOverlay[iid];
      if (ov && ov.view) view = ov.view;
    }
    if (view && !_viewIsPending(view)) return true;
    var part = _hitlPartById(iid);
    if (!part) return false;
    var st = _hitlDisplayState(part);
    return !!(st && st !== 'pending');
  }

  /** Existing HITL card for this interrupt. Never reuse a claimed card for a new gate. */
  /**
   * Did the inline paint LAND for this interrupt — in any state?
   *
   * The Alpine store keeps `pendingApproval` as a fallback for "the inline
   * card never rendered", and cleared it only when hasInlineApprovalCard()
   * was true — i.e. only while a card still had ENABLED buttons. On a hard
   * refresh of a finished session the inline card is painted and then
   * immediately stamped "No longer pending" by the gate reconcile, so that
   * check went false and the fallback strip stayed on screen forever: a
   * dead card with four live buttons for a gate the server had already
   * settled (2026-09-03). A card that exists is proof the paint landed,
   * whatever its buttons say.
   */
  function hitlCardExistsFor(data) {
    var iid = _hitlInterruptIdOf(data);
    if (!iid || !messagesEl) return false;
    return !!_findHitlCard(iid, null);
  }

  function _findHitlCard(interruptId, host) {
    interruptId = String(interruptId || '');
    var cards = messagesEl ? messagesEl.querySelectorAll('.hitl-approval-card') : [];
    var i;
    if (interruptId) {
      for (i = 0; i < cards.length; i++) {
        if (String(cards[i].getAttribute('data-interrupt-id') || '') === interruptId) {
          return cards[i];
        }
      }
      return null;
    }
    if (host && host.querySelectorAll) {
      var locals = host.querySelectorAll('.hitl-approval-card');
      for (i = 0; i < locals.length; i++) {
        if (!_hitlCardIsClaimed(locals[i])) return locals[i];
      }
    }
    return null;
  }

  function _notifyHitlResolved(detail) {
    try {
      window.dispatchEvent(new CustomEvent('kazma:hitl-resolved', { detail: detail || {} }));
    } catch (eEv) { /* ignore */ }
    try {
      localStorage.setItem('kazma:hitl-resolved', String(Date.now()));
    } catch (eLs) { /* ignore */ }
  }

  /**
   * Freeze the buttons of the card being decided — and ONLY that card.
   *
   * Two concurrent approval cards is a supported state (_placeHitlCard
   * deliberately stacks a second one after the first), but this froze every
   * card in the transcript. Approving the first killed the second's buttons,
   * nothing re-enables them (_reconcileHitlCardsWithGates only ever
   * disables), and with no enabled button left hasInlineApprovalCard() went
   * false — so onDone took the endTurn branch while the graph was still
   * parked on the untouched interrupt. The card vanished and the reply never
   * came (2026-09-03).
   *
   * @param {Element} [scope] The card to freeze. Omitted = every card, which
   *   is only correct at a hard turn reset.
   */
  function _freezeHitlButtons(scope) {
    var isCard = !!(scope && scope.classList
      && scope.classList.contains('hitl-approval-card'));
    var host = scope || messagesEl;
    if (!host || !host.querySelectorAll) return;
    host.querySelectorAll(isCard ? 'button' : '.hitl-approval-card button')
      .forEach(function(b) { b.disabled = true; });
  }

  /** Hide the chat.html bottom Alpine approval card (driven by the store). */
  function _clearStoreApproval() {
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent')) {
        Alpine.store('agent').pendingApproval = null;
      }
    } catch (e) { /* ignore */ }
  }

  function _payloadFromGate(g) {
    g = g || {};
    var p = (g.payload && typeof g.payload === 'object') ? g.payload : {};
    return {
      thread_id: p.thread_id || _serverThreadId || chatSessionId || '',
      kind: p.kind || g.kind || 'security',
      tool: p.tool || g.tool || 'unknown',
      args: p.args || {},
      tools: p.tools || [],
      message: p.message || g.message || '',
      yolo_allowed: p.yolo_allowed !== false,
      interrupt_id: p.interrupt_id || g.gate_id || '',
      items: p.items || null,
      approval_deadline: g.approval_deadline || p.approval_deadline || 0,
    };
  }

  function _payloadFromView(v) {
    var out = _payloadFromGate(v);
    if (!out.interrupt_id && v) out.interrupt_id = String(v.interrupt_id || '');
    return out;
  }

  /**
   * Re-paint every turn that holds a gate the registry currently lists as
   * pending.
   *
   * After /status lands, re-paint turns whose views became pending so a
   * frozen card (data-hitl-shown) can rebuild with live buttons.
   *
   * Only covering pending rows are touched. Historical awaiting cards
   * with no registry row stay frozen: that is the ghost-card defence, and
   * re-rendering them would mint live buttons for gates that have already
   * settled (the 2026-09-03 flash, on every old turn, while a current
   * pause is live).
   */
  function _rerenderHitlDocs() {
    var TD = window.KazmaTurnDocument;
    if (!TD || typeof TD.hitlPartsOf !== 'function') return;
    var pendingIds = {};
    var list = _serverGateViews || [];
    var i;
    for (i = 0; i < list.length; i++) {
      if (!_viewIsPending(list[i])) continue;
      var gid = String((list[i] || {}).gate_id || (list[i] || {}).interrupt_id || '');
      if (gid) pendingIds[gid] = true;
    }
    var id, doc, gates, g, iid, touch;
    for (id in _docs) {
      if (!Object.prototype.hasOwnProperty.call(_docs, id)) continue;
      doc = _docs[id];
      gates = TD.hitlPartsOf((doc && doc.parts) || []);
      touch = false;
      for (g = 0; g < gates.length; g++) {
        iid = _hitlInterruptIdOf(gates[g]);
        if (iid && pendingIds[iid]) { touch = true; break; }
      }
      if (touch) renderTurn(doc, { source: 'gates' });
    }
  }

  /**
   * Feed EVERY pending gate into the document.
   *
   * The two "stop as soon as a card exists" guards that used to bracket
   * this loop were there because a second card could not be represented:
   * the document held one HITL slot per turn, so painting gate B destroyed
   * gate A's card. Now a gate is a slot keyed by its interrupt id, cards
   * are built from the document, and re-feeding a gate that already has a
   * card is a no-op. Painting all of them is the correct behaviour — a
   * second pending question belongs on screen next to the first.
   */
  function _paintLiveGates() {
    var list = _serverGateViews || [];
    var i;
    for (i = 0; i < list.length; i++) {
      if (!_viewIsPending(list[i])) continue;
      var payload = _payloadFromView(list[i]);
      applyTurnEvent({
        type: 'hitl',
        state: 'pending',
        tool: payload.tool,
        interrupt_id: payload.interrupt_id,
        payload: payload,
        view: list[i],
        turn_id: _liveTurnId,
        source: 'gates',
      });
    }
  }

  /** §30 decision truth: once /status answers with the authoritative gate
   *  list and the thread is IDLE, a card still showing live buttons for an
   *  interrupt with NO pending registry row is a fossil (settled in another
   *  tab, or before a refresh). Stamp it resolved — the card stays as
   *  history, but phantom Approve buttons that only ever 409 must not
   *  linger. Never runs while a pause may be in flight: an interrupt whose
   *  approval_required frame has not registered yet has no row, and a
   *  generating thread can pause between the status fetch and this sweep. */
  function _reconcileHitlCardsWithGates() {
    if (!messagesEl || !_serverGatesAuth) return;
    var open = _openHitlPart();
    if (open && String(open.state || 'pending') === 'pending') return;
    var pendingIids = {};
    (_serverGateViews || []).forEach(function (g) {
      if (!_viewIsPending(g)) return;
      var id = String((g && (g.gate_id || g.interrupt_id)) || '');
      if (id) pendingIids[id] = true;
    });
    messagesEl.querySelectorAll('.hitl-approval-card').forEach(function (card) {
      if (_hitlCardIsClaimed(card)) return;
      var cid = String(card.getAttribute('data-interrupt-id') || '');
      // Positive identification only: stamp a card whose interrupt id is
      // KNOWN and confirmed absent from the authoritative pending list. A
      // card with no id stays untouched — disabling a live question we
      // cannot identify is exactly the failure this reconcile exists to
      // prevent (2026-09-02 semantic-card window; the approval_timeout
      // frame remains the live-stamp path for unidentified cards).
      if (!cid) return;
      if (pendingIids[cid]) return;
      var btns = card.querySelectorAll('button');
      var live = false;
      for (var i = 0; i < btns.length; i++) {
        if (!btns[i].disabled) { live = true; break; }
      }
      if (!live) return;
      _noteGateDecided({ interrupt_id: cid }, 'error');
    });
    // The store's fallback strip lives OUTSIDE messagesEl and carries no
    // interrupt id, so the sweep above can never reach it. An authoritative
    // list with nothing pending is the server saying "no gate is waiting" —
    // retire the strip too, or it keeps offering live buttons for a decision
    // that is already made (the ghost card on every hard refresh).
    var anyPending = false;
    for (var pk in pendingIids) {
      if (Object.prototype.hasOwnProperty.call(pendingIids, pk)) { anyPending = true; break; }
    }
    if (!anyPending) _clearStoreApproval();
  }

  /** Server-truth recovery: an interrupted turn whose approval card never
   *  rendered is a SILENTLY PAUSED turn — no card, no error, no progress
   *  (the 2026-08-26 "complete silence" X-post incident). One best-effort
   *  fetch of the pending list; render this session's card if present. */
  // Thread of the most recent interrupt seen by this tab (from approval
  // payloads / interrupted dones) — lets recoverMissedApproval match the
  // RIGHT pending approval instead of guessing (audit P2).
  var _lastInterruptedThreadId = '';

  /** The gate this turn is actually waiting on, else the most recent one.
   *  A turn holds one part per gate, so "the last hitl part" is the gate
   *  asked most recently — after a sequential approve that is the SETTLED
   *  one, and reading it as "the open gate" reported the turn unblocked
   *  while the graph sat waiting on an earlier question. */
  function _openHitlPart() {
    var doc = _docs[_liveTurnId] || null;
    var TD = window.KazmaTurnDocument;
    if (!doc || !doc.parts || !TD || typeof TD.hitlPartOf !== 'function') return null;
    return TD.hitlPartOf(doc.parts);
  }

  function _hitlPartById(iid) {
    iid = String(iid || '');
    if (!iid) return null;
    var TD = window.KazmaTurnDocument;
    var doc = _docs[_liveTurnId] || null;
    if (!TD || !doc || typeof TD.hitlPartsOf !== 'function') return null;
    var gates = TD.hitlPartsOf(doc.parts || []);
    var i;
    for (i = 0; i < gates.length; i++) {
      if (_hitlInterruptIdOf(gates[i]) === iid) return gates[i];
    }
    return null;
  }

  function recoverMissedApproval() {
    if (_serverGenerating && !_serverPaused) return;
    _paintLiveGates();
    var existing = _openHitlPart();
    if (existing && String(existing.state || 'pending') !== 'pending') {
      /* a settled part must not block a live gate painted above */
    } else if (existing && String(existing.state || '') === 'pending' && existing.payload) {
      // Route through the document, not straight at the DOM: the renderer
      // builds the card from the part, so recovery and live delivery paint
      // through the same path and cannot produce two different cards.
      applyTurnEvent({
        type: 'hitl',
        state: 'pending',
        tool: _hitlToolOf(existing),
        interrupt_id: _hitlInterruptIdOf(existing),
        payload: existing.payload,
        turn_id: _liveTurnId,
        source: 'recover-open',
      });
    }
    fetch('/api/pending-approvals', { credentials: 'same-origin' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(payload) {
        var pending = (payload && Array.isArray(payload.pending)) ? payload.pending : [];
        if (!pending.length) return;
        var hit = null;
        // Preference order: the thread we saw interrupt → status thread →
        // the session id → (paused-session fallback) the first pending
        // entry. The approve endpoint's ownership check still guards
        // cross-tenant abuse.
        var candidates = [_lastInterruptedThreadId, _serverThreadId || '', chatSessionId || ''];
        for (var c = 0; c < candidates.length && !hit; c++) {
          for (var i = 0; i < pending.length; i++) {
            var p = pending[i] || {};
            if (candidates[c] && String(p.thread_id || '') === candidates[c]) { hit = p; break; }
          }
        }
        // Single-operator fallback: adopt the only pending entry ONLY when
        // this chat's own status says it is paused — otherwise a fresh chat
        // adopts another chat's pause and wears its state (2026-09-01).
        if (!hit && _serverPaused && pending.length) hit = pending[0];
        if (!hit) return;
        console.warn('[KazmaChat] Recovering missed approval card for thread=' + hit.thread_id);
        applyTurnEvent({
          type: 'hitl',
          state: 'pending',
          tool: hit.tool_name || hit.tool || 'unknown',
          interrupt_id: hit.interrupt_id || '',
          payload: {
            thread_id: hit.thread_id,
            kind: hit.kind || 'security',
            tool: hit.tool_name || hit.tool || 'unknown',
            args: hit.arguments || hit.args || {},
            message: hit.message || '',
            yolo_allowed: hit.yolo_allowed !== false,
            interrupt_id: hit.interrupt_id || '',
          },
          turn_id: _liveTurnId,
          source: 'recover',
        });
      })
      .catch(function() { /* best-effort */ });
  }

  /** Chronological HITL cards: a later interrupt must sit BELOW the card
   *  already approved in this bubble. Inserting after `.agent-progress`
   *  put schedule_task above cancel_scheduled (2026-09-02). Always a
   *  direct child of the outer `.message-content` — never inside CoT. */

  /** Countdown surface (2026-09-02): an unattended approval auto-denies at
   *  the watchdog deadline (server-stamped approval_deadline, epoch s).
   *  Show it counting down instead of silently dropping the card at 300s. */
  function _stopHitlCountdown(card) {
    if (card && card.__cdTimer) {
      try { clearInterval(card.__cdTimer); } catch (eT) { /* ignore */ }
      card.__cdTimer = null;
    }
  }

  function _hitlDeadlineOf(data) {
    var d = data || {};
    var dl = Number(d.approval_deadline || (d.payload && d.payload.approval_deadline) || 0);
    return dl > 0 ? dl : 0;
  }

  function _attachHitlCountdown(card, data) {
    if (!card) return;
    var dl = _hitlDeadlineOf(data);
    if (!dl) return;
    // A deadline already in the past is NOT a countdown — it is a stale
    // stamp on a part nobody has cleared, and attaching a ticker to it makes
    // the very first tick stamp the card red "Approval timed out" and write
    // `timeout` into the document. `timeout` outranks every other HITL
    // state, so that verdict is permanent: a gate the operator actually
    // approved (YOLO included) came back from a refresh looking auto-denied
    // (2026-09-19, reported from the live install).
    //
    // The server decides whether a gate timed out. This ticker is a courtesy
    // display for a gate that is live RIGHT NOW, and it must never invent a
    // denial for the same reason a card must never invent an approval.
    if (dl - Date.now() / 1000 <= 0) return;
    // Idempotent: the slot painter re-asserts the countdown on every render
    // of a live gate, and a fresh row per frame would stack a new ticker
    // under the card several times a second.
    if (card.__cdTimer
        && String(card.getAttribute('data-approval-deadline') || '') === String(dl)
        && card.querySelector('.hitl-countdown')) {
      return;
    }
    _stopHitlCountdown(card);
    var oldRow = card.querySelector('.hitl-countdown');
    if (oldRow && oldRow.parentNode) oldRow.parentNode.removeChild(oldRow);
    // Published on the node rather than kept in this closure: the card's
    // own deadline is readable by anything reconciling it, and a reload
    // that re-adopts the card can restart the ticker from the same value.
    // (It also fed the retired Live Task Card's page-level countdown; per
    // card is the level that survived, because two gates can be waiting.)
    try { card.setAttribute('data-approval-deadline', String(dl)); }
    catch (eDl) { /* ignore */ }
    var row = document.createElement('div');
    row.className = 'hitl-countdown';
    var host = card.querySelector('.hitl-approval-body') || card;
    host.appendChild(row);
    var paint = function () {
      if (!card.isConnected) { _stopHitlCountdown(card); return; }
      var left = Math.floor(dl - Date.now() / 1000);
      if (left <= 0) {
        _stopHitlCountdown(card);
        card.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
        row.textContent = ti('waiting_server', 'Waiting for the server…');
        return;
      }
      var m = Math.floor(left / 60);
      var s = left % 60;
      row.textContent = '⏳ ' + ti('auto_deny_in', 'Auto-denies if unanswered in') + ' ' + m + ':' + (s < 10 ? '0' : '') + s;
    };
    paint();
    card.__cdTimer = setInterval(paint, 1000);
  }

  // The card-parking rule ("pending sits below the text, claimed moves
  // above it") is no longer a function that moves nodes — it is the slot
  // order TurnView declares. See modules/turn_view.js, contract 2.

  /** Title in the header. Claimed cards must not keep "Approval Required"
   *  next to an Approved chip — that was the 4-card sequential ordering
   *  the operator read as two states on one card (2026-09-20). */
  function _setHitlHeaderTitle(card, title) {
    if (!card) return;
    var header = card.querySelector('.hitl-approval-header');
    if (!header) return;
    var el = header.querySelector('.hitl-header-title');
    var text = String(title || '');
    if (el) {
      if (el.textContent !== text) el.textContent = text;
      return;
    }
    var n = header.firstChild;
    while (n) {
      if (n.nodeType === 3) {
        n.textContent = text;
        return;
      }
      n = n.nextSibling;
    }
  }

  /** Which gate a card's fold preference is stored under. */
  function _hitlFoldKey(card) {
    try {
      return String(card.getAttribute('data-interrupt-id') ||
        card.getAttribute('data-gate-key') || '');
    } catch (e) { return ''; }
  }

  /** Is this decided card open? The reader decides; collapsed by default. */
  function _hitlCardOpen(card) {
    if (card.__hitlOpen != null) return !!card.__hitlOpen;
    var key = _hitlFoldKey(card);
    var prefs = _turnPrefs();
    return !!(key && prefs && prefs.isExpanded('', 'hitl:' + key, false));
  }

  function _syncHitlFold(card, header) {
    var open = _hitlCardOpen(card);
    card.classList.toggle('hitl-collapsed', !open);
    var ch = header.querySelector('.hitl-collapse-chevron');
    if (ch) ch.textContent = open ? '▾' : '▸';
    header.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  /** Fold a decided card to a one-line CoT-style bar; the header toggles it.
   *  Keeps the decision visible in the timeline without a full card body
   *  sitting between the CoT and the streamed reply. The header chip is
   *  re-synced on every call so later state changes ("Resolving…" →
   *  "Approved") stay visible while collapsed.
   *
   *  The fold belongs to the reader (AGENTS.md §31): collapsed by default,
   *  then only a click changes it, stored in turn_preferences like the
   *  thoughts fold. This used to ADD hitl-collapsed on every call, and the
   *  painter calls it on every state change -- so a card opened while it
   *  read "Approved — running…" snapped shut when the tools finished, and
   *  an open card offered no chevron or cursor to close it again
   *  (reported 2026-09-24). */
  function _collapseClaimedHitlCard(card) {
    if (!card) return;
    var header = card.querySelector('.hitl-approval-header');
    if (!header) return;
    var status = card.querySelector('.hitl-approval-actions .hitl-status');
    var chip = header.querySelector('.hitl-collapse-chip');
    if (status) {
      var txt = status.textContent || '';
      var cls = status.className || 'hitl-status';
      if (!chip) {
        chip = document.createElement('span');
        chip.className = 'hitl-collapse-chip ' + cls;
        header.appendChild(chip);
      }
      if (chip.textContent !== txt) chip.textContent = txt;
      chip.className = 'hitl-collapse-chip ' + cls;
    }
    // Re-added on every call: the painter assigns className wholesale.
    card.classList.add('hitl-collapsible');
    if (!header.querySelector('.hitl-collapse-chevron')) {
      var chev = document.createElement('span');
      chev.className = 'hitl-collapse-chevron';
      chev.setAttribute('aria-hidden', 'true');
      header.appendChild(chev);
    }
    // Wired before anything can return early: a card that arrived already
    // carrying hitl-collapsed used to skip this and never open at all.
    if (!card.__hitlCollapseWired) {
      card.__hitlCollapseWired = true;
      header.addEventListener('click', function (e) {
        if (e.target.closest('button')) return;
        var next = !_hitlCardOpen(card);
        card.__hitlOpen = next;
        var key = _hitlFoldKey(card);
        if (key) _writeFoldPreference('', 'hitl:' + key, next);
        _syncHitlFold(card, header);
        e.stopPropagation();
      });
    }
    _syncHitlFold(card, header);
  }

  /** A pending approval card must be SEEN, not just rendered: if the
   *  reader is scrolled up (or a tall bubble grew past the fold), bounce
   *  the chat so the card lands center-frame (2026-09-03). Claimed/historical
   *  cards never bounce — only a live ask with enabled buttons does. */
  function _revealHitlCard(card) {
    if (!card || !card.isConnected) return;
    try {
      if (document.hidden) return;
      // Hydration paints HISTORICAL cards — entering an old session with a
      // stale pending-looking card must never yank the reader to it.
      if (_hydratingSession) return;
      var live = false;
      var btns = card.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        if (!btns[i].disabled) { live = true; break; }
      }
      if (!live) return;
      var r = card.getBoundingClientRect();
      var vh = window.innerHeight || 0;
      if (r.top >= 0 && r.bottom <= vh) return; // already visible
      setTimeout(function () {
        try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
        catch (eSv) { try { card.scrollIntoView(); } catch (eSv2) { /* ignore */ } }
      }, 60);
    } catch (eRv) { /* never break the render */ }
  }

  /**
   * BUILD one approval card and return it. Does NOT decide where it goes —
   * TurnView owns child order (modules/turn_view.js, contract 2).
   *
   * The thirty-odd lines of guards that used to open this function are
   * deleted, not relocated: the _hitlAlreadyClaimed paint guard, the
   * _findHitlCard rescan, the sweep that removed "other unclaimed pending
   * cards" before minting a new one, and the hasInlineApprovalCard
   * idempotency skip. Every one of them was a heuristic answer to "does a
   * card for this gate already exist?" — a question asked of the transcript
   * because nothing owned the answer. TurnView calls this only when the
   * gate's slot is EMPTY, so the question cannot arise: one gate, one slot
   * key, one node. Those guards were also load-bearing in the wrong
   * direction: the sweep deleted a second gate's pending card, and the
   * idempotency skip let the first live card on the page eat every later
   * approval (2026-09-02, three watchdog auto-denials in a row).
   */
  function renderHitlCard(data, opts) {
    if (!data) return null;
    var lockComposer = !(opts && opts.lock === false);
    if (lockComposer) pauseForApproval(data);
    var targetThreadId = data.thread_id || chatSessionId || '';
    // The renderer hands us the host it is rendering (opts.host). Only fall
    // back to guessing the open bubble when something calls this outside a
    // render pass — _pinLiveAssistantBubble picks "last assistant bubble
    // after the last user row", which is the kind of guess this refactor
    // exists to stop relying on, and it writes currentMsgEl as a side effect.
    var content = (opts && opts.host) || null;
    if (!content) {
      _pinLiveAssistantBubble();
      content = _bubbleContent(currentMsgEl);
    }
    if (!content) {
      return null;
    }
    var iid = _hitlInterruptIdOf(data);

    // Phase 3: semantic clarify/confirm → render per-option buttons instead of
    // the generic Approve/Deny. The data carries kind + items[0].options from
    // the commitment gate's resolver.
    if (data.kind && data.kind.indexOf('semantic_') === 0) {
      var _semItem = (data.items && data.items[0]) || {};
      var _semQ = _semItem.question || data.message || 'Needs clarification';
      var _semOpts = _semItem.options || [];
      var _semTcid = _semItem.tool_call_id || '';
      var _semCard = document.createElement('div');
      _semCard.className = 'hitl-approval-card';
      // Same identity stamp as the security card — id-scoped consumers
      // (_reconcileHitlCardsWithGates, markApprovalTimedOut, _findHitlCard)
      // key off data-interrupt-id; a card without one was invisible to
      // them and could be stamped resolved while live (2026-09-02).
      var _semIid = _hitlInterruptIdOf(data);
      if (_semIid) {
        try { _semCard.setAttribute('data-interrupt-id', _semIid); } catch (eAttr) { /* ignore */ }
      }
      _semCard.innerHTML =
        '<div class="hitl-approval-header">\u2754 Clarification Needed</div>' +
        '<div class="hitl-approval-body">' +
          '<p class="hitl-message">' + escapeHtml(truncateStr(_semQ, 500)) + '</p>' +
        '</div>' +
        '<div class="hitl-approval-actions" style="flex-wrap:wrap;gap:6px;">' +
          _semOpts.map(function(opt) {
            var cls = opt.id === 'cancel' ? 'btn-danger' : 'btn-primary';
            return '<button class="btn btn-sm ' + cls + ' hitl-sem-opt" data-opt="' +
                   escapeHtml(opt.id) + '">' + escapeHtml(opt.label || opt.id) + '</button>';
          }).join('') +
        '</div>';
      // Attach only — TurnView's next pass moves it to its declared slot.
      content.appendChild(_semCard);
      _revealHitlCard(_semCard);
      _attachHitlCountdown(_semCard, data);
      _clearStoreApproval();
      scrollToBottom();
      _semCard.querySelectorAll('.hitl-sem-opt').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var optId = this.getAttribute('data-opt');
          _semCard.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
          var act = _semCard.querySelector('.hitl-approval-actions');
          if (act) act.innerHTML = '<span class="hitl-status">Resolving\u2026</span>';
          // Record the decision in the document immediately; TurnView
          // re-orders the settled card above the incoming reply.
          _noteGateDecided(data, optId === 'cancel' ? 'denied' : 'approved');
          _collapseClaimedHitlCard(_semCard);
          // Resolving a semantic choice resumes THIS turn \u2014 same rule as the
          // security card: keep the workbench and its steps.
          beginTurn({ resume: true });
          var payload = { action: optId === 'cancel' ? 'deny' : 'approve', scope: 'once',
                          session_id: chatSessionId || '',
                          interrupt_id: data.interrupt_id || '',
                          choices: {} };
          payload.choices[_semTcid] = optId;
          fetch('/api/approve/' + encodeURIComponent(data.thread_id || targetThreadId), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            credentials: 'same-origin',
          }).then(function(r) {
            return r.json().then(function(body) {
              return { status: r.status, body: body || {} };
            }).catch(function() { return { status: r.status, body: {} }; });
          }).then(function(res) {
            if (res.status === 409) {
              _awaitingApproval = false;
              if (act) act.innerHTML = '<span class="hitl-status">Already resolved</span>';
              _reattachAfterApproval('approve-409');
              return;
            }
            if (res.status >= 400 || (res.body && res.body.ok === false)) {
              if (act) act.innerHTML = '<span class="hitl-status text-danger">Failed — retry</span>';
              _semCard.querySelectorAll('button').forEach(function(b) { b.disabled = false; });
              return;
            }
            _awaitingApproval = false;
            _awaitingReply = true;
            _reattachAfterApproval('approve-json');
          }).catch(function() {
            if (act) act.innerHTML = '<span class="hitl-status text-danger">Failed — retry</span>';
            _semCard.querySelectorAll('button').forEach(function(b) { b.disabled = false; });
          });
        });
      });
      return _semCard; // Don't render the security card
    }

    // NO placeholder is written into the answer slot.
    //
    // This is the line the whole bug class was named after: "_Action
    // required: the agent paused…_" went into .message-text, and the reply
    // then had to REPLACE it. Every incident was a path where that
    // replacement did not happen, and the fix was always another way to
    // force it. The card below already says an approval is required, with
    // the tool and its arguments; the status strip says it too. Writing it
    // a third time, into the one slot the answer needs, created a state
    // where "paused" and "answered" are the same element — so failing to
    // overwrite it is indistinguishable from silence.
    //
    // The answer slot now holds the answer or nothing at all, and the
    // pause is a sibling slot. There is nothing left to replace.

    var tools = Array.isArray(data.tools) ? data.tools : [];
    var toolsHtml = '';
    if (tools.length > 1) {
      toolsHtml = '<ul class="hitl-tools-list" style="margin:6px 0;padding-left:18px;font-size:0.8rem;">' +
        tools.map(function(t) {
          return '<li><code>' + escapeHtml(t.name || '') + '</code> ' +
            '<span style="color:var(--text-muted);">' +
            escapeHtml(truncateStr(JSON.stringify(t.args || {}), 120)) +
            '</span></li>';
        }).join('') + '</ul>';
    }

    // S1-3: proposal-backed posts — show the STORED drafts the approval
    // will actually publish. Resolved server-side from the durable artifact
    // store: the user approves the stored text, not the model's memory.
    var proposalHtml = '';
    try {
      var _propItems = [];
      var _propPid = '';
      tools.forEach(function(t) {
        if (t && t.proposal && Array.isArray(t.proposal.items)) {
          _propPid = t.proposal.proposal_id || _propPid;
          _propItems = _propItems.concat(t.proposal.items);
        }
      });
      if (!_propItems.length && data.proposal && Array.isArray(data.proposal.items)) {
        _propPid = data.proposal.proposal_id || '';
        _propItems = data.proposal.items;
      }
      if (_propItems.length) {
        proposalHtml =
          '<div class="hitl-proposal" style="margin:8px 0;padding:8px 10px;border-radius:8px;' +
          'background:color-mix(in srgb, var(--accent, #6c8cff) 7%, transparent);' +
          'border:1px solid color-mix(in srgb, var(--accent, #6c8cff) 20%, transparent);">' +
          '<p style="margin:0 0 6px 0;font-size:0.8rem;"><strong>Content to publish</strong>' +
          (_propPid ? ' — stored proposal <code>' + escapeHtml(_propPid) + '</code>' : '') +
          ' (verified against what you approved):</p>' +
          '<ul class="hitl-proposal-items" style="margin:0;padding-left:18px;font-size:0.78rem;">' +
          _propItems.map(function(i) {
            return '<li style="margin:3px 0;"><code>' + escapeHtml(String(i.id || '')) +
              '</code> ' + escapeHtml(truncateStr(String(i.text || ''), 240)) + '</li>';
          }).join('') +
          '</ul></div>';
      }
    } catch (e) { /* rendering must never block the card */ }

    var card = document.createElement('div');
    card.className = 'hitl-approval-card';
    var cardIid = _hitlInterruptIdOf(data);
    if (cardIid) {
      try { card.setAttribute('data-interrupt-id', cardIid); } catch (eAttr) { /* ignore */ }
    }
    var cardTool = _hitlToolOf(data);
    if (cardTool) {
      try { card.setAttribute('data-tool', cardTool); } catch (eTool) { /* ignore */ }
    }
    // Server marks always-HITL batches (X ToU fail-safes) yolo_allowed=false —
    // offering a YOLO button there reads as "approve once" when it re-prompts.
    var yoloOk = data.yolo_allowed !== false;
    card.innerHTML =
      '<div class="hitl-approval-header"><span class="hitl-header-title">\u26A0 Approval Required</span></div>' +
      '<div class="hitl-approval-body">' +
        '<p><strong>Tool:</strong> <code>' + escapeHtml(data.tool || '') + '</code></p>' +
        (tools.length <= 1
          ? '<p><strong>Args:</strong></p><div class="hitl-approval-args">' + renderApprovalArgsHtml(data.tool, data.args) + '</div>'
          : toolsHtml) +
        proposalHtml +
        (data.jail_note
          ? '<p class="hitl-jail-note">' + escapeHtml(String(data.jail_note)) + '</p>'
          : '') +
        '<p class="hitl-message">' + escapeHtml(truncateStr(data.message || '', 400)) + '</p>' +
        '<p class="hitl-scope-hint" style="font-size:0.72rem;color:var(--text-muted);margin-top:6px;">' +
          (yoloOk
            ? 'Tip: <strong>Allow tool</strong> stops repeat prompts for this tool only. ' +
              '<strong>YOLO session</strong> skips every danger tool (native + MCP) until you <code>/yolo off</code> or TTL.'
            : 'This tool <strong>always requires approval</strong> (safety fail-safe) — YOLO and session grants cannot skip it.') +
        '</p>' +
      '</div>' +
      '<div class="hitl-approval-actions" style="flex-wrap:wrap;gap:6px;">' +
        '<button class="btn btn-sm btn-success hitl-approve" data-scope="once" title="This call only">Approve once</button>' +
        // "Allow tool" is singular, but on a GROUPED card this grants every
        // tool in the batch — `_extract_pending_tools_from_snapshot` returns
        // all of their names and each gets `grant_tool()`. A button that says
        // "tool" and grants four is the same class of defect as a card that
        // hides which tools one approval covers (2026-09-21). Say the number.
        (tools.length > 1
          ? '<button class="btn btn-sm btn-primary hitl-approve-tool" data-scope="tool" title="' +
            escapeHtml('Allow these ' + tools.length + ' tools for ~30m in this session: ' +
              tools.map(function (t) { return t.name || ''; }).join(', ')) +
            '">' + escapeHtml('Allow these ' + tools.length + ' tools (session)') + '</button>'
          : '<button class="btn btn-sm btn-primary hitl-approve-tool" data-scope="tool" title="Allow this tool for ~30m in this session">Allow tool (session)</button>') +
        (yoloOk
          ? '<button class="btn btn-sm btn-warning hitl-approve-yolo" data-scope="yolo" title="Skip all danger tools for this session">YOLO session</button>'
          : '') +
        '<button class="btn btn-sm btn-danger hitl-deny" data-scope="once">Deny</button>' +
      '</div>';
    // Attach only — TurnView's next pass moves it to its declared slot.
    content.appendChild(card);
    _revealHitlCard(card);
    _attachHitlCountdown(card, data);
    _clearStoreApproval();
    scrollToBottom();

    function setCardState(state, label) {
      _stopHitlCountdown(card);
      try { card.setAttribute('data-hitl-shown', state); } catch (eS) { /* ignore */ }
      void label;
      // Record the DECISION, and only a decision.
      //
      // Two bugs lived in the one-line ternary this replaces. It mapped
      // everything that was not 'approved' to 'denied', so the in-flight
      // paint — which this function makes first, before the server has
      // accepted anything — wrote `denied` into the document for a gate the
      // operator had just approved. And had it written 'inflight' instead,
      // that would have been worse: inflight outranks approved in
      // HITL_RANK, so the confirmed 'approved' arriving afterwards would
      // have been rejected as a regression and the gate pinned mid-flight.
      //
      // "Sending decision…" is not a decision. The card already says so on
      // screen; the document waits for the server's answer.
      if (state === 'approved' || state === 'denied'
          || state === 'timeout' || state === 'error') {
        _noteGateDecided(data, state);
      }
      _collapseClaimedHitlCard(card);
    }

    function appendAssistantText(text) {
      if (!text) return;
      _pinLiveAssistantBubble();
      var textEl = currentMsgEl.querySelector('.message-text');
      if (!textEl) {
        textEl = document.createElement('div');
        textEl.className = 'message-text';
        currentMsgEl.querySelector('.message-content').appendChild(textEl);
      }
      var existing = textEl.innerHTML || '';
      tryIngestPlanFromText(text);
      var rendered = KS.markdown ? KS.markdown(stripPlanFenceForDisplay(text)) : escapeHtml(stripPlanFenceForDisplay(text));
      // bidi applied after set on textEl below
      textEl.innerHTML = existing
        ? existing + '<hr style="border:none;border-top:1px solid var(--border-subtle);margin:10px 0;">' + rendered
        : rendered;
      textEl.setAttribute('dir', 'auto');
      if (window.KazmaBidi) KazmaBidi.apply(textEl, text);
      scrollToBottom();
    }

      function submitApproval(action, scope) {
      scope = scope || 'once';
      var hitlState = action === 'deny' ? 'denied' : 'approved';
      var confirmedLabel = scope === 'yolo'
        ? ti('yolo_on', 'YOLO on ✓')
        : (scope === 'tool' ? ti('tool_allowed', 'Tool allowed ✓')
          : (action === 'deny' ? ti('denied', 'Denied ✗') : ti('approved', 'Approved ✓')));
      _clearStoreApproval();
      // Reset accum so post-approval final answer replaces (no pre-HITL + final concat).
      // RESUME, not a new turn: keep this turn's workbench and its steps.
      beginTurn({ resume: true });
      // beginTurn clears the HITL wait; keep recover/replay from re-arming
      // this card while the JSON approve is in flight.
      _awaitingApproval = true;
      // THIS card only — a sibling card is a different, still-pending gate.
      _freezeHitlButtons(card);
      // Honest in-flight paint: the server has NOT accepted the decision
      // yet. Painting "Approved ✓" optimistically left the card lying when
      // the fetch failed/network dropped (audit M-W2) — the confirmed state
      // is painted in the .then() below.
      setCardState('inflight', ti('sending_decision', 'Sending decision…'));
      // Record the decision itself so the log reads as one continuous story
      // (…tool proposed → you approved → tool ran → answer) instead of
      // restarting at "Thinking…".
      logProgress({
        kind: 'status',
        title: action === 'deny'
          ? ti('denied', 'Denied ✗')
          : (scope === 'yolo' ? ti('yolo_on', 'YOLO on ✓')
            : (scope === 'tool' ? ti('tool_allowed', 'Tool allowed ✓')
              : ti('approved', 'Approved ✓'))),
        state: 'running',
      });

      // Approve is a JSON command. The live tail is the existing chat SSE
      // (or a journal re-attach). A second graph SSE is how "Error: network
      // error" + leftover Thinking + refresh drift happened (2026-09-01).
      // Toast only after HTTP 200 — a 409 used to flash green "Allowed…"
      // while the same card came back live (cleanup 2026-09-01).
      var payload = {
        action: action,
        scope: scope,
        session_id: chatSessionId || '',
        tool: data.tool || '',
        interrupt_id: data.interrupt_id || '',
      };

      _pinLiveAssistantBubble();

      var ovIid = String(data.interrupt_id || '');
      var ovView = {
        gate_id: ovIid,
        interrupt_id: ovIid,
        tool: String(data.tool || ''),
        kind: String(data.kind || 'security'),
        state: 'inflight',
        interactive: false,
        slot: 'settled',
      };
      _setHitlOverlay(ovIid, ovView);
      applyTurnEvent({
        type: 'hitl',
        state: hitlState,
        tool: data.tool || '',
        interrupt_id: data.interrupt_id || '',
        payload: data,
        view: ovView,
        turn_id: _liveTurnId,
        source: 'approve',
      });

      var approvalUrl = '/api/approve/' + encodeURIComponent(data.thread_id || targetThreadId);
      fetch(approvalUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        credentials: 'same-origin',
      }).then(function(r) {
        return r.json().then(function(body) {
          return { status: r.status, body: body || {} };
        }).catch(function() {
          return { status: r.status, body: {} };
        });
      }).then(function(res) {
        var acceptedView = _applyApproveView(res.body, ovIid);
        if (res.status === 409) {
          var running409 = !!(res.body && (res.body.running
            || res.body.hitl_state === 'inflight'
            || res.body.hitl_state === 'approved'));
          if (running409) {
            applyTurnEvent({
              type: 'hitl', state: 'inflight', tool: data.tool || '',
              interrupt_id: (res.body && res.body.interrupt_id) || data.interrupt_id || '',
              payload: data, view: acceptedView || ovView,
              turn_id: _liveTurnId, source: 'approve-409',
            });
            _awaitingApproval = false;
            _awaitingReply = true;
            _notifyHitlResolved({
              thread_id: data.thread_id || targetThreadId,
              tool: data.tool || '',
              interrupt_id: data.interrupt_id || '',
            });
            _reattachAfterApproval('approve-409');
            return;
          }
          applyTurnEvent({
            type: 'hitl', state: 'error', tool: data.tool || '',
            payload: data, view: acceptedView,
            turn_id: _liveTurnId, source: 'approve-409',
          });
          _resyncDelivery('approve-409');
          return;
        }
        if (res.status >= 400 || (res.body && res.body.ok === false)) {
          applyTurnEvent({
            type: 'hitl', state: 'error', tool: data.tool || '',
            payload: data, turn_id: _liveTurnId, source: 'approve-error',
          });
          return;
        }
        // Decision ACCEPTED by the server — now paint the confirmed state
        // (the optimistic pre-fetch paint is gone; audit M-W2).
        setCardState(hitlState, confirmedLabel);
        applyTurnEvent({
          type: 'hitl', state: hitlState, tool: data.tool || '',
          interrupt_id: data.interrupt_id || '',
          payload: data, view: acceptedView || ovView,
          turn_id: _liveTurnId, source: 'approve-accepted',
        });
        // Decision accepted — the graph is running again. Clear the HITL
        // wait so a dead tail can re-attach (JSON approve is not an SSE).
        // Unless another card is still live: deciding gate A does not mean
        // the turn stopped waiting on gate B.
        _awaitingApproval = hasLiveGate();
        _awaitingReply = true;
        _notifyHitlResolved({
          thread_id: data.thread_id || targetThreadId,
          tool: data.tool || '',
          interrupt_id: data.interrupt_id || '',
        });
        if (scope === 'yolo' && KS.toast) {
          KS.toast('YOLO on for this session \u2014 danger tools auto-approved', 'warning', 4000);
        }
        if (scope === 'tool' && KS.toast) {
          KS.toast('Allowed ' + (data.tool || 'tool') + ' for this session (~30m)', 'success', 3000);
        }
        _reattachAfterApproval('approve-json');
        _resyncDelivery('approve-json');
      }).catch(function(err) {
        _clearHitlOverlay(ovIid);
        applyTurnEvent({
          type: 'hitl', state: 'error', tool: data.tool || '',
          payload: data, turn_id: _liveTurnId, source: 'approve-error',
        });
        void err;
      });
    }

    var onceBtn = card.querySelector('.hitl-approve');
    var toolBtn = card.querySelector('.hitl-approve-tool');
    var yoloBtn = card.querySelector('.hitl-approve-yolo');
    var denyBtn = card.querySelector('.hitl-deny');
    if (onceBtn) onceBtn.addEventListener('click', function() { submitApproval('approve', 'once'); });
    if (toolBtn) toolBtn.addEventListener('click', function() { submitApproval('approve', 'tool'); });
    if (yoloBtn) yoloBtn.addEventListener('click', function() { submitApproval('approve', 'yolo'); });
    if (denyBtn) denyBtn.addEventListener('click', function() { submitApproval('deny', 'once'); });
    return card;
  }

  function editMessage(msgEl) {
    var textEl = msgEl.querySelector('.message-text');
    var currentText = textEl.textContent;
    inputEl.value = currentText;
    inputEl.focus();
    onInputResize.call(inputEl);
    // Remove this message and all subsequent
    var siblings = Array.from(messagesEl.querySelectorAll('.message'));
    var idx = siblings.indexOf(msgEl);
    for (var i = idx; i < siblings.length; i++) siblings[i].remove();
    KS.toast('Edit your message and press Enter to resend', 'info', 2500);
  }

  function copyMessage(msgEl) {
    var text = msgEl.querySelector('.message-text').textContent;
    navigator.clipboard.writeText(text).then(function() {
      KS.toast('Copied to clipboard', 'success', 2000);
    });
  }

  function copyAssistantMessage(msgEl) {
    var text = msgEl.querySelector('.message-text').textContent;
    navigator.clipboard.writeText(text).then(function() {
      KS.toast('Copied to clipboard', 'success', 2000);
    });
  }

  // ── Read aloud ────────────────────────────────────────
  //
  // One button per assistant message: speak it, or stop it if it is the one
  // currently playing. A single global toggle could not express "stop THIS
  // reply", and a second speaker icon in the composer was indistinguishable
  // from the live-voice mic beside it.

  var _speakSeq = 0;

  function _speakId(msgEl) {
    if (!msgEl.dataset.speakId) msgEl.dataset.speakId = 'msg-' + (++_speakSeq);
    return msgEl.dataset.speakId;
  }

  function toggleSpeakMessage(msgEl) {
    if (!window.KazmaVoice) return;
    var id = _speakId(msgEl);
    // Busy, not just speaking: a second click during synthesis must CANCEL,
    // not queue a second clip. Two overlapping clips were audible and
    // unstoppable, because only the last one had a handle (2026-09-17).
    if (KazmaVoice.isBusy(id)) { KazmaVoice.stopTTS(); return; }
    var text = (msgEl.querySelector('.message-text') || {}).textContent || '';
    if (!text.trim()) { KS.toast('Nothing to read in this message', 'info', 2000); return; }
    KazmaVoice.playTTS(text, null, id);
  }

  /** Repaint every speak button so exactly one can show the stop state. */
  function refreshSpeakButtons(owner) {
    var btns = document.querySelectorAll('.speak-action');
    for (var i = 0; i < btns.length; i++) {
      var btn = btns[i];
      var wrap = btn.closest('.message');
      var mine = wrap && wrap.dataset.speakId && wrap.dataset.speakId === owner;
      btn.classList.toggle('is-speaking', !!mine);
      btn.textContent = mine ? '⏹' : '🔊';
      btn.title = mine ? 'Stop reading' : 'Read aloud';
    }
  }

  if (window.KazmaVoice && KazmaVoice.onSpeakStateChange) {
    KazmaVoice.onSpeakStateChange(refreshSpeakButtons);
  }

  function regenerateFrom(msgEl) {
    var text = msgEl.querySelector('.message-text').textContent;
    inputEl.value = text;
    sendMessage();
  }

  // ── Session management ────────────────────────────────
  /** Debounced server re-fetch so rapid turns don't spam /api/chat/sessions. */
  var _sessionsRefreshTimer = null;

  function loadSessions() {
    fetch('/api/chat/sessions')
      .then(function(r) {
        if (!r.ok) {
          throw new Error('HTTP ' + r.status);
        }
        return r.json();
      })
      .then(function(data) {
        sessions = data || [];
        // Preserve optimistic active session if the server hasn't flushed it yet
        // (race: first WS message still writing while we re-list).
        if (chatSessionId) {
          var found = sessions.some(function(s) { return s.session_id === chatSessionId; });
          if (!found) {
            var pending = _optimisticSessionStub(chatSessionId);
            if (pending) sessions = [pending].concat(sessions);
          }
        }
        renderSessionList();
      })
      .catch(function(err) {
        console.error('Failed to load sessions:', err);
        if (sessionListEl) {
          sessionListEl.innerHTML = '<div class="session-empty">Failed to load sessions</div>';
        }
      });
  }

  function refreshSessionsSoon() {
    if (_sessionsRefreshTimer) clearTimeout(_sessionsRefreshTimer);
    _sessionsRefreshTimer = setTimeout(function() {
      _sessionsRefreshTimer = null;
      loadSessions();
    }, 350);
  }

  /**
   * Immediately put/update a session row in the sidebar without waiting for
   * a full list fetch. Root cause of "new season missing until F5":
   * WS chat path never called loadSessions(), and SSE only did on onDone.
   */
  function upsertSessionLocal(partial) {
    if (!partial || !partial.session_id) return;
    var sid = partial.session_id;
    var now = new Date().toISOString();
    var idx = -1;
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i].session_id === sid) { idx = i; break; }
    }
    if (idx >= 0) {
      var prev = sessions[idx];
      sessions[idx] = Object.assign({}, prev, partial, {
        // Don't blank a good title with empty string
        title: (partial.title != null && partial.title !== '')
          ? partial.title
          : (prev.title || ''),
        updated_at: partial.updated_at || now,
        message_count: partial.message_count != null
          ? partial.message_count
          : (prev.message_count || 0),
      });
    } else {
      sessions.unshift({
        session_id: sid,
        title: partial.title || '',
        message_count: partial.message_count != null ? partial.message_count : 0,
        platform: partial.platform || 'web',
        created_at: partial.created_at || now,
        updated_at: partial.updated_at || now,
        archived: false,
        thread_id: partial.thread_id || '',
        total_cost: partial.total_cost || 0,
        total_tokens: partial.total_tokens || 0,
      });
    }
    renderSessionList();
  }

  function _optimisticSessionStub(sid) {
    if (!sid) return null;
    // Prefer whatever we already know from the open transcript
    var userCount = 0;
    var firstUser = '';
    try {
      var userEls = messagesEl
        ? messagesEl.querySelectorAll('.message-user .message-text')
        : [];
      userCount = userEls.length;
      if (userEls.length) {
        firstUser = (userEls[0].textContent || '').trim().slice(0, 60);
      }
    } catch (e) {}
    if (userCount === 0 && !lastSentUserText) return null;
    return {
      session_id: sid,
      title: firstUser || (lastSentUserText || '').trim().slice(0, 60) || 'New chat',
      message_count: Math.max(userCount * 2, userCount || 1),
      platform: 'web',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      archived: false,
    };
  }

  function noteSessionActivity(userText) {
    if (!chatSessionId) return;
    var titleHint = (userText || lastSentUserText || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    var existing = null;
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i].session_id === chatSessionId) { existing = sessions[i]; break; }
    }
    var nextCount = existing
      ? (existing.message_count || 0) + 1
      : 1;
    upsertSessionLocal({
      session_id: chatSessionId,
      title: (existing && existing.title) ? existing.title : (titleHint || 'New chat'),
      message_count: nextCount,
      platform: 'web',
      updated_at: new Date().toISOString(),
    });
    // Authoritative sync shortly after server persists the turn
    refreshSessionsSoon();
  }

  function relativeTime(isoStr) {
    if (!isoStr) return '';
    try {
      var then = new Date(isoStr);
      var now = new Date();
      var diff = Math.floor((now - then) / 1000);
      if (diff < 60) return 'just now';
      if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
      if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
      if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
      return then.toLocaleDateString();
    } catch (e) { return ''; }
  }

  // Session id whose kebab menu is open (single open menu at a time)
  var _openMenuId = null;

  function sessionGroupKey(isoStr) {
    if (!isoStr) return 'older';
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) return 'older';
    var now = new Date();
    var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var startDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var days = Math.round((startToday - startDay) / 86400000);
    if (days <= 0) return 'today';
    if (days === 1) return 'yesterday';
    if (days < 7) return 'week';
    return 'older';
  }

  function highlightTitle(title, q) {
    if (!q) return escapeHtml(title || '');
    var text = title || '';
    var idx = text.toLowerCase().indexOf(q);
    if (idx === -1) return escapeHtml(text);
    return escapeHtml(text.slice(0, idx)) + '<mark>' +
      escapeHtml(text.slice(idx, idx + q.length)) + '</mark>' +
      escapeHtml(text.slice(idx + q.length));
  }

  function sessionRowHtml(s, q) {
    var isActive = s.session_id === chatSessionId;
    var isMenuOpen = _openMenuId === s.session_id;
    var title = s.title || (s.session_id || '').slice(0, 8);
    var plat = s.platform || 'web';
    var absTime = '';
    try { absTime = new Date(s.updated_at || s.created_at).toLocaleString(); } catch (e) {}
    var lastPlat = s.last_platform || s.platform || 'web';
    var meta = lastPlat + ' \u00B7 ' + s.message_count + ' msgs \u00B7 ' + relativeTime(s.updated_at || s.created_at);
    var html = '<div class="session-item' + (isActive ? ' active' : '') + (s.pinned ? ' pinned' : '') + (isMenuOpen ? ' menu-open' : '') + '" data-session-id="' + escapeHtml(s.session_id) + '" data-platform="' + escapeHtml(plat) + '">' +
      '<span class="session-platform-dot dot-' + escapeHtml(plat) + '" title="' + escapeHtml(plat) + '"></span>' +
      '<div class="session-info">' +
        '<span class="session-title" dir="auto" title="' + escapeHtml(title) + (absTime ? ' \u00B7 ' + absTime : '') + '">' + highlightTitle(title, q) + '</span>' +
        '<span class="session-meta" dir="auto">' + escapeHtml(meta) + '</span>' +
      '</div>';
    if (showArchived) {
      html += '<div class="session-actions">' +
        '<button class="session-act-btn" data-unarchive="' + escapeHtml(s.session_id) + '" title="' + escapeHtml(ti('restore', 'Restore')) + '">\u21BA</button>' +
        '<button class="session-act-btn session-del" data-delete="' + escapeHtml(s.session_id) + '" title="' + escapeHtml(ti('delete', 'Delete')) + '">\u2715</button>' +
      '</div>';
    } else {
      html += '<div class="session-actions">' +
        '<button class="session-more' + (isMenuOpen ? ' active' : '') + '" data-more="' + escapeHtml(s.session_id) + '" title="' + escapeHtml(ti('actions', 'Actions')) + '">\u22EF</button>' +
        '<div class="session-menu' + (isMenuOpen ? ' open' : '') + '" data-menu="' + escapeHtml(s.session_id) + '">' +
          '<button class="session-menu-item" data-menu-action="' + (s.pinned ? 'unpin' : 'pin') + '" data-menu-sid="' + escapeHtml(s.session_id) + '">' +
            escapeHtml(ti(s.pinned ? 'unpin' : 'pin', s.pinned ? 'Unpin' : 'Pin')) + '</button>' +
          '<button class="session-menu-item" data-menu-action="rename" data-menu-sid="' + escapeHtml(s.session_id) + '">' +
            escapeHtml(ti('rename', 'Rename')) + '</button>' +
          '<button class="session-menu-item" data-menu-action="copyid" data-menu-sid="' + escapeHtml(s.session_id) + '">' +
            escapeHtml(ti('copy_id', 'Copy ID')) + '</button>' +
          '<button class="session-menu-item" data-menu-action="archive" data-menu-sid="' + escapeHtml(s.session_id) + '">' +
            escapeHtml(ti('archive', 'Archive')) + '</button>' +
          '<button class="session-menu-item danger" data-menu-action="delete" data-menu-sid="' + escapeHtml(s.session_id) + '">' +
            escapeHtml(ti('delete', 'Delete')) + '</button>' +
        '</div>' +
      '</div>';
    }
    html += '</div>';
    return html;
  }

  function renderSessionList() {
    if (!sessionListEl) return;
    // Backend returns sessions sorted newest-first by updated_at. Sort by
    // updated_at descending as belt-and-braces. The active session is NOT
    // pinned to the top: clicking a season must not reorder the list —
    // only real activity (a sent message) bumps updated_at and moves it up.
    // Explicitly pinned sessions (server-side `pinned`) are grouped on top.
    var q = searchQuery ? searchQuery.toLowerCase() : '';
    var filtered = sessions;
    if (q) {
      filtered = sessions.filter(function(s) {
        return ((s.title || '').toLowerCase().includes(q) ||
                (s.session_id || '').toLowerCase().includes(q));
      });
    }
    filtered = filtered.slice().sort(function(a, b) {
      return (b.updated_at || b.created_at || '').localeCompare(a.updated_at || a.created_at || '');
    });

    var countEl = document.getElementById('session-count');
    if (countEl) countEl.textContent = sessions.length ? ' (' + sessions.length + ')' : '';

    if (filtered.length === 0) {
      var emptyText = q
        ? ti('no_matching_sessions', 'No matching sessions')
        : ti('no_sessions_yet', 'No sessions yet');
      sessionListEl.innerHTML =
        '<div class="session-empty">' + escapeHtml(emptyText) +
        (q ? '' : '<button class="btn btn-sm btn-primary session-empty-cta" id="session-empty-new">' +
          escapeHtml(ti('start_new_chat', 'Start a new chat')) + '</button>') +
        '</div>';
      var cta = document.getElementById('session-empty-new');
      if (cta) cta.addEventListener('click', newSession);
      return;
    }

    // Group: pinned section first, then date buckets (Today/Yesterday/7d/Older)
    var groups = [];
    var pinned = filtered.filter(function(s) { return !!s.pinned; });
    var rest = filtered.filter(function(s) { return !s.pinned; });
    if (pinned.length) groups.push({ label: ti('pinned', 'Pinned'), items: pinned });
    var buckets = { today: [], yesterday: [], week: [], older: [] };
    rest.forEach(function(s) {
      buckets[sessionGroupKey(s.updated_at || s.created_at)].push(s);
    });
    var labels = {
      today: ti('today', 'Today'),
      yesterday: ti('yesterday', 'Yesterday'),
      week: ti('previous_7_days', 'Previous 7 days'),
      older: ti('older', 'Older'),
    };
    ['today', 'yesterday', 'week', 'older'].forEach(function(k) {
      if (buckets[k].length) groups.push({ label: labels[k], items: buckets[k] });
    });

    var html = '';
    groups.forEach(function(g) {
      html += '<div class="session-section-label">' + escapeHtml(g.label) + '</div>';
      g.items.forEach(function(s) { html += sessionRowHtml(s, q); });
    });
    sessionListEl.innerHTML = html;

    // Delete buttons (archive view)
    sessionListEl.querySelectorAll('[data-delete]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        deleteSession(this.dataset.delete);
      });
    });

    // Unarchive buttons (archive view)
    sessionListEl.querySelectorAll('[data-unarchive]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        unarchiveSession(this.dataset.unarchive);
      });
    });

    // Kebab toggle buttons
    sessionListEl.querySelectorAll('[data-more]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var sid = this.dataset.more;
        _openMenuId = (_openMenuId === sid) ? null : sid;
        renderSessionList();
      });
    });

    // Kebab menu actions
    sessionListEl.querySelectorAll('[data-menu-action]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var action = this.dataset.menuAction;
        var sid = this.dataset.menuSid;
        _openMenuId = null;
        if (action === 'pin') pinSession(sid, true);
        else if (action === 'unpin') pinSession(sid, false);
        else if (action === 'rename') renameSession(sid);
        else if (action === 'copyid') copySessionId(sid);
        else if (action === 'archive') archiveSession(sid);
        else if (action === 'delete') deleteSession(sid);
      });
    });
  }

  function pinSession(sessionId, pinned) {
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + (pinned ? '/pin' : '/unpin'), { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok') {
          KS.toast(pinned ? 'Session pinned' : 'Session unpinned', 'success', 2000);
          for (var i = 0; i < sessions.length; i++) {
            if (sessions[i].session_id === sessionId) {
              sessions[i].pinned = !!data.pinned;
              break;
            }
          }
          renderSessionList();
          refreshSessionsSoon();
        } else {
          KS.toast(data.error || 'Pin failed', 'error', 3000);
        }
      })
      .catch(function() { KS.toast('Pin failed', 'error', 3000); });
  }

  function archiveSession(sessionId) {
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/archive', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok') {
          KS.toast('Session archived', 'success', 2000);
          loadSessions();
          if (sessionId === chatSessionId) newSession();
        } else {
          KS.toast(data.error || 'Archive failed', 'error', 3000);
        }
      })
      .catch(function() { KS.toast('Archive failed', 'error', 3000); });
  }

  function unarchiveSession(sessionId) {
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/unarchive', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok') {
          KS.toast('Session restored', 'success', 2000);
          loadArchivedSessions();
        } else {
          KS.toast(data.error || 'Restore failed', 'error', 3000);
        }
      })
      .catch(function() { KS.toast('Restore failed', 'error', 3000); });
  }

  function loadArchivedSessions() {
    fetch('/api/chat/sessions/archived')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        sessions = data || [];
        renderSessionList();
      })
      .catch(function() {});
  }

  function toggleArchivedView() {
    showArchived = !showArchived;
    var headerTitle = document.querySelector('.chat-sidebar-header h3');
    var newBtn = document.getElementById('new-session-btn');
    if (showArchived) {
      if (headerTitle) headerTitle.textContent = 'Archived';
      if (newBtn) newBtn.style.display = 'none';
      loadArchivedSessions();
    } else {
      if (headerTitle) headerTitle.textContent = 'Sessions';
      if (newBtn) newBtn.style.display = '';
      loadSessions();
    }
  }

  function copySessionId(sessionId) {
    var text = String(sessionId || '');
    var link = window.location.origin + '/chat?s=' + encodeURIComponent(text);
    var payload = text + '\n' + link;
    var done = function() {
      if (window.KS && KS.toast) KS.toast('Copied ID — /session ' + text.slice(-8) + ' on Telegram/Discord', 'success', 3500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(payload).then(done).catch(function() {
        window.prompt('Session ID', text);
      });
    } else {
      window.prompt('Session ID', text);
    }
  }

  async function renameSession(sessionId) {
    var s = sessions.find(function(x) { return x.session_id === sessionId; });
    var current = s ? (s.title || sessionId.slice(0, 8)) : '';
    var title = await window.kazmaPrompt({
      title: 'Rename session',
      label: 'Session title',
      defaultValue: current,
      confirmText: 'Rename',
    });
    if (!title || !title.trim()) return;
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title.trim() }),
    })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok') {
          KS.toast('Session renamed', 'success', 2000);
          loadSessions();
        } else {
          KS.toast(data.error || 'Rename failed', 'error', 3000);
        }
      })
      .catch(function() { KS.toast('Rename failed', 'error', 3000); });
  }

  // Bounded retries for a transient session-messages fetch (restart window).
  var _loadMsgAttempts = 0;
  // In-flight guard: one loadSession per session at a time (boot used to
  // double-fetch/render — audit P1-6; retries must also not stack).
  var _loadInFlightFor = null;
  // True ONLY while a session's history is being painted. Historical
  // approval cards must never bounce the chat — entering an old session
  // with a stale pending card used to scroll-jump the reader to it
  // (2026-09-03).
  var _hydratingSession = false;

  function loadSession(sessionId) {
    if (_loadInFlightFor === sessionId) return;
    _loadInFlightFor = sessionId;
    // Loading from server — any previous wait is resolved by what renders.
    _awaitingReply = false;
    // Abort any in-flight turn from the previous session so Stop never sticks.
    if (activeStream) {
      try { activeStream.abort(); } catch (e) {}
      activeStream = null;
    }
    // Invalidate any resync/journal-attach dispatched BEFORE this load —
    // their epoch check (_mine / epochAtFetch) makes them no-ops so a stale
    // painter can never draw into (or under) the transcript this load renders.
    _sseEpoch++;
    endTurn();
    _resetSessionTurnState();

    chatSessionId = sessionId;
    persistSessionId();
    // After the assignment, not before: _resetSessionTurnState runs while
    // chatSessionId is still the OLD session, and pointing the preference
    // store at it there would have loaded the session being left.
    _syncPrefsSession();

    // Connect to Central WebSocket Telemetry Bus for THIS session
    // (connect is a no-op if already OPEN on the same sessionId).
    if (window.Alpine && Alpine.store && Alpine.store('agent')) {
      Alpine.store('agent').connect(sessionId);
    }

    // Clear messages and show loading state (only for explicit session loads)
    messagesEl.innerHTML =
      '<div class="chat-welcome">' +
        '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
        '<h2>Session ' + escapeHtml(sessionId.slice(0, 8)) + '</h2>' +
        '<p>Loading messages\u2026</p>' +
      '</div>';
    renderSessionList();
    resetSessionStats();

    // Join transcript + registry BEFORE the first paint (HITL_VIEW_MODEL A1).
    Promise.all([
      fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/messages?stats=1')
        .then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        }),
      fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/status')
        .then(function(r) { return r.ok ? r.json() : null; })
        .catch(function() { return null; }),
    ]).then(function(pair) {
        var payload = pair[0];
        // Guard against race: user switched sessions while fetch was in flight
        if (chatSessionId !== sessionId) { _loadInFlightFor = null; return; }
        _loadMsgAttempts = 0;
        _loadInFlightFor = null;
        _ingestStatus(pair[1]);

        var messages = payload;
        if (payload && !Array.isArray(payload) && Array.isArray(payload.messages)) {
          messages = payload.messages;
          updateSessionStats(null, null, payload.total_tokens, payload.total_cost);
        }

        messagesEl.innerHTML = '';

        if (!messages || messages.length === 0) {
          messagesEl.innerHTML =
            '<div class="chat-welcome">' +
              '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
              '<h2>Session ' + escapeHtml(sessionId.slice(0, 8)) + '</h2>' +
              '<p>No messages in this session yet.</p>' +
            '</div>';
          return;
        }

        var prevAssistantContent = null;
        var prevUserContent = null;
        messages = _coalesceAssistantRuns(messages);
        _hydratingSession = true;
        try {
        messages.forEach(function(msg) {
          // Only human-visible roles. System injects (self-improvement Soul,
          // knowledge fences, CONTINUITY notes) must never render as "You".
          var rawRole = (msg.role || '').toLowerCase();
          if (rawRole === 'system' || rawRole === 'tool') return;
          var content = msg.content || '';
          if (content.indexOf('<kazma:data') >= 0 && content.indexOf('untrusted') >= 0) return;
          if (content.indexOf('[SelfImprovement]') >= 0 && content.indexOf('BEGIN OBSERVATION') >= 0) return;
          var role = rawRole === 'assistant' ? 'assistant' : (rawRole === 'user' ? 'user' : null);
          if (!role) return;
          // Drop blank assistant rows (open-turn placeholders that never
          // got text). They split consecutive identical replies so the
          // collapse below could not merge them.
          if (role === 'assistant' && !(content || '').trim() && !msg.pending) return;
          // Collapse identical consecutive user rows (SSE persist + a
          // retried mobile POST of the same slash command).
          if (role === 'user') {
            var _uTrim = (content || '').trim();
            // Slash commands persist once per send; a retried mobile POST
            // stacks a second identical user row after the confirmation.
            // Do not collapse ordinary repeated chat ("hello" twice).
            if (_uTrim && prevUserContent === _uTrim && _uTrim.charAt(0) === '/') return;
            prevUserContent = _uTrim || null;
            // A question between two replies makes them two turns, however
            // alike: asking twice and getting the same answer twice is not a
            // double-persist. Without this reset the second answer vanished
            // on every reload (seen 2026-09-26 on three identical replies).
            prevAssistantContent = null;
          }
          // Collapse identical consecutive assistant rows left by older
          // double-persist bugs (same answer twice after YOLO/refresh).
          // Also collapse when source markdown differs only by whitespace /
          // equivalent rendered plain text.
          if (role === 'assistant' && content && prevAssistantContent) {
            var _cTrim = content.trim();
            if (
              prevAssistantContent === _cTrim ||
              _plainFromMarkdown(prevAssistantContent) === _plainFromMarkdown(_cTrim)
            ) {
              return;
            }
          }
          if (role === 'assistant') {
            prevAssistantContent = (content || '').trim() || null;
          }
          // If the last assistant message is marked pending (client refreshed
          // mid-turn while the LLM was still processing), show a processing
          // indicator; resync below reconciles the final state.
          if (role === 'assistant' && isPlanOnlyMessage(content)) {
            try { tryIngestPlanFromText(content); } catch (ePlanLoad) { /* ignore */ }
            return;
          }
          if (role === 'assistant' && msg.pending && !content) {
            appendMessage('assistant', '⏳ _Previous turn still processing in the background…_', null, msg.ts || msg.timestamp || msg.created_at || null);
          } else {
            if (role === 'assistant' && window.KazmaTurnDocument && KazmaTurnDocument.hydrateMessage) {
              msg = KazmaTurnDocument.hydrateMessage(msg);
              content = msg.content || content;
            }
            var painted = appendMessage(role, content, null, msg.ts || msg.timestamp || msg.created_at || null, {
              activity: (window.KazmaTurnDocument && KazmaTurnDocument.activityForMessage)
                ? KazmaTurnDocument.activityForMessage(msg)
                : msg.activity,
              parts: msg.parts,
              model: msg.model || '',
              turn_id: msg.turn_id || '',
            });
            if (role === 'assistant' && window.KazmaTurnDocument && KazmaTurnDocument.fromMessage) {
              var hydratedDoc = KazmaTurnDocument.fromMessage(msg);
              var hydratedId = String(msg.turn_id || hydratedDoc.turnId || '');
              if (hydratedId) _docs[hydratedId] = hydratedDoc;
              // Register the restored bubble, then render it like any other
              // turn. History and live delivery go through ONE painter now,
              // so a replayed transcript cannot disagree with a live one.
              var TVh = _turnView();
              if (TVh) {
                if (hydratedId) TVh.bind(hydratedId, painted);
                TVh.render(painted, hydratedDoc, _turnRenderers, { source: 'hydrate' });
              }
            }
          }
        });

        // Turn Delivery V2: one authoritative reconciliation after render.
        // Covers trailing-pending (turn still running → keep waiting) and
        // trailing-user (detached turn may exist) without any pollers —
        // live delivery arrives via the resumed WS cursor stream.
        _reopenCount = 0;
        } finally {
          _hydratingSession = false;
        }

        _resyncDelivery('load');

        scrollToBottomForce(); // session load shows the latest turn
        updateContextBadge();
        refreshCapacity();
        _restoreUndeliveredOutbox(messages);
      })
      .catch(function(err) {
        if (chatSessionId !== sessionId) { _loadInFlightFor = null; return; }
        _loadInFlightFor = null;
        diag('load-messages-failed', String((err && err.message) || err));
        // A transient load failure (server restarting / down) must NOT wipe
        // what is already on screen — replacing the transcript with an error
        // card destroyed the latest reply ("refresh loses the output",
        // 2026-08-26). Keep painted content; toast + bounded retry instead.
        var hadContent = !!(messagesEl && messagesEl.querySelector('.message'));
        if (!hadContent) {
          messagesEl.innerHTML =
            '<div class="chat-welcome">' +
              '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
              '<h2>Session ' + escapeHtml(sessionId.slice(0, 8)) + '</h2>' +
              '<p>Failed to load messages: ' + escapeHtml((err && err.message) || String(err)) + '</p>' +
            '</div>';
        }
        KS.toast(
          'Failed to load session messages' + (err && err.message ? ' (' + err.message + ')' : '') + ' — retrying…',
          'error', 4000
        );
        if (_loadMsgAttempts < 2) {
          _loadMsgAttempts++;
          setTimeout(function() {
            if (chatSessionId === sessionId) loadSession(sessionId);
          }, 1500);
        } else {
          _loadMsgAttempts = 0;
        }
      });
  }

  function bindCapacityBar() {
    // The template (chat.html ⋯ popover) is the SINGLE owner of the bar's
    // markup. This used to physically relocate #capacity-bar out of the
    // popover on every load — detaching it from the v5 popover CSS (empty ⋯
    // menu) — and kept a divergent JS-built fallback bar (audit P0-2).
    // Here we only BIND the click behavior.
    var bar = document.getElementById('capacity-bar');
    if (!bar || bar.getAttribute('data-bound')) return;
    bar.setAttribute('data-bound', '1');
    bar.addEventListener('click', function(e) {
      var btn = e.target.closest('[data-cap]');
      if (!btn || !inputEl) return;
      if (_isGenerating) {
        if (KS && KS.toast) KS.toast('Please wait for generation to finish or abort first', 'info', 2500);
        return;
      }
      var cap = btn.getAttribute('data-cap') || '';
      if (cap === '/plan on' && btn.classList.contains('is-on')) {
        cap = '/plan off';
      }
      inputEl.value = cap;
      sendMessage();
    });
  }

  function refreshCapacity() {
    if (!chatSessionId) return;
    fetch('/api/chat/capacity?session_id=' + encodeURIComponent(chatSessionId), {
      credentials: 'same-origin',
    }).then(function(r) { return r.ok ? r.json() : null; }).then(function(snap) {
      if (!snap || !snap.ok) return;
      var status = document.getElementById('capacity-status');
      if (status) {
        var bits = [];
        if (snap.plan_active) bits.push('Plan');
        if (snap.long_active) {
          bits.push(snap.mode === 'mission' ? 'Mission' : 'Long');
        }
        if (!bits.length) bits.push('Chat');
        var modeLabel = bits.join(' · ');
        var budget = String(snap.max_iterations != null ? snap.max_iterations : '');
        if (snap.iteration != null && budget) {
          status.textContent = modeLabel + ' · ' + snap.iteration + '/' + budget;
        } else {
          status.textContent = budget ? (modeLabel + ' · ' + budget) : modeLabel;
        }
        status.title = snap.yolo_active
          ? (status.textContent + ' · YOLO on')
          : (status.textContent + ' · HITL on');
      }
      var bar = document.getElementById('capacity-bar');
      if (!bar) return;
      bar.querySelectorAll('.capacity-pill[data-cap]').forEach(function(btn) {
        var cap = btn.getAttribute('data-cap') || '';
        var on = false;
        if (cap === '/long on') on = !!snap.long_active && snap.mode !== 'mission';
        if (cap === '/long mission') on = snap.mode === 'mission' && !!snap.long_active;
        if (cap === '/plan on') on = !!snap.plan_active;
        if (cap === '/yolo') on = !!snap.yolo_active;
        if (cap === '/unrestricted') on = !!snap.long_active && snap.mode === 'mission' && !!snap.yolo_active;
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }).catch(function() {});
  }

  function newSession() {
    if (activeStream) {
      try { activeStream.abort(); } catch (e) {}
      activeStream = null;
    }
    // MUST clear Stop/Enter lock — previously new chat inherited a stuck turn
    // so users had to press ESC before typing in a brand-new session.
    forceEndTurn();
    _resetSessionTurnState();

    chatSessionId = generateSessionId();
    persistSessionId();
    _syncPrefsSession();
    messagesEl.innerHTML =
      '<div class="chat-welcome">' +
        '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
        '<h2>Kazma</h2>' +
        '<p>How can I help you today?</p>' +
      '</div>';
    resetSessionStats();
    currentMsgEl = null;
    lastSentUserText = '';
    _awaitingReply = false;
    _turnPainted = false;

    // Bind WS bus to the NEW session (disconnect old so late frames can't
    // re-arm beginTurn on the fresh chat).
    if (window.Alpine && Alpine.store && Alpine.store('agent')) {
      try {
        var store = Alpine.store('agent');
        if (typeof store.disconnect === 'function') store.disconnect();
        else if (typeof store._resetTurnState === 'function') store._resetTurnState();
        store.connect(chatSessionId);
      } catch (e) {}
    }

    // Re-render so the previous season stays visible and active highlight
    // clears; the brand-new empty id is intentionally not listed until the
    // first message (noteSessionActivity) — then it appears without F5.
    renderSessionList();
    // Pull latest titles/counts for seasons that just finished on the server
    refreshSessionsSoon();
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.focus();
    }
  }

  async function deleteSession(sessionId) {
    if (!(await window.kazmaConfirm({
      title: 'Delete session',
      message: 'Delete session ' + sessionId.slice(0, 8) + '? This cannot be undone.',
      confirmText: 'Delete',
      danger: true,
    }))) return;
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId), { method: 'DELETE' })
      .then(function(resp) {
        if (!resp.ok) {
          if (window.showToast) window.showToast('Delete failed (' + resp.status + ')', 'error', 3000);
          else if (KS.toast) KS.toast('Delete failed (' + resp.status + ')', 'error', 3000);
          return;
        }
        KS.toast('Session deleted', 'success', 2000);
        loadSessions();
        if (sessionId === chatSessionId) newSession();
      })
      .catch(function() {
        KS.toast('Failed to delete session', 'error', 3000);
      });
  }

  /**
   * Update the header cost/token badges.
   *
   * Cumulative semantics: when sessionTokens/sessionCost are provided
   * (server-persisted totals in done/turn_complete payloads and the
   * messages-envelope) they are authoritative. Legacy payloads that only
   * carry per-turn values accumulate locally so multi-turn sessions still
   * show a growing total on old backends. Missing usage renders as 0 —
   * never undefined (the dead-badge regression).
   */
  function updateSessionStats(tokens, cost, sessionTokens, sessionCost) {
    if (sessionTokens != null || sessionCost != null) {
      _sessionTotals.tokens = Math.max(0, Number(sessionTokens) || 0);
      _sessionTotals.cost = Math.max(0, Number(sessionCost) || 0);
    } else if (tokens != null) {
      _sessionTotals.tokens += Math.max(0, Number(tokens) || 0);
      _sessionTotals.cost += Math.max(0, Number(cost) || 0);
    }
    if (costBadge) costBadge.textContent = KS.formatCost(_sessionTotals.cost);
    if (tokensBadge) {
      tokensBadge.textContent = formatCompactCount(_sessionTotals.tokens) + ' tok';
      tokensBadge.title = KS.formatTokens(_sessionTotals.tokens) + ' ' + ti('tokens', 'tokens');
    }
  }

  /** Zero the badges + running totals (new session / /reset / session load). */
  function resetSessionStats() {
    _sessionTotals.tokens = 0;
    _sessionTotals.cost = 0;
    if (costBadge) costBadge.textContent = KS.formatCost(0);
    if (tokensBadge) {
      tokensBadge.textContent = '0 tok';
      tokensBadge.title = '0 ' + ti('tokens', 'tokens');
    }
  }

  // ── Context conversation badge (chars → estimated tokens) ──
  /**
   * Token heuristic with no dependencies: ~4 chars/token for Latin script,
   * ~2 chars/token for Arabic script (per-string detection). Matches the
   * server-side estimate_tokens spirit (kazma_core/summarizer.py).
   */
  function estimateTokens(str) {
    var s = String(str || '');
    if (!s) return 0;
    var ar = (s.match(/[\u0600-\u06FF]/g) || []).length;
    var other = s.length - ar;
    return Math.ceil(other / 4 + ar / 2);
  }

  var _ctxBadgeTimer = null;
  function updateContextBadgeSoon() {
    if (_ctxBadgeTimer) clearTimeout(_ctxBadgeTimer);
    _ctxBadgeTimer = setTimeout(function() {
      _ctxBadgeTimer = null;
      updateContextBadge();
    }, 350);
  }

  /**
   * Recompute "N chars ≈ M tokens" from the RENDERED transcript (DOM), so it
   * is accurate after live turns, session switches, restores and edits.
   * textContent reads only — must never trigger layout work per message.
   */
  function updateContextBadge() {
    if (!contextBadge || !messagesEl) return;
    var totalChars = 0;
    var totalTokens = 0;
    try {
      var nodes = messagesEl.querySelectorAll('.message-text');
      for (var i = 0; i < nodes.length; i++) {
        var t = nodes[i].textContent || '';
        totalChars += t.length;
        totalTokens += estimateTokens(t);   // per-message: preserves script mix
      }
    } catch (e) { return; }
    var full = tiFmt('context_size', '{chars} chars \u2248 {tokens} tokens', {
      chars: totalChars.toLocaleString(),
      tokens: totalTokens.toLocaleString(),
    });
    contextBadge.textContent = totalTokens
      ? ('~' + formatCompactCount(totalTokens) + ' ctx')
      : '—';
    contextBadge.title = full;
  }

  // ── Utils ─────────────────────────────────────────────
  // rAF-coalesced: rapid row appends during streaming trigger one scroll
  // per frame instead of a forced layout per row (no jank / layout jumps).
  var _scrollRafPending = false;
  // ── Pin-to-bottom scrolling ─────────────────────────────────────────
  // scrollToBottom is called from ~20 sites (every token batch included).
  // Unconditionally snapping scrollTop to scrollHeight while OTHER parts of
  // the turn mutate heights above (status strip, activity rows, the
  // plain→markdown terminal render) makes the view bounce up and down while
  // the reply streams — measured 13 direction reversals / 18 >30px jumps in
  // one 25s stream — and it fights a reader who scrolled up. Standard chat
  // behaviour: auto-scroll ONLY while the user is pinned near the bottom;
  // scrolling up detaches for the rest of the turn, returning re-pins.
  var _userPinnedToBottom = true;

  function _isNearBottom() {
    if (!messagesEl) return true;
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight <= 80;
  }

  function _installScrollPinTracker() {
    if (!messagesEl || messagesEl.__pinTracked) return;
    messagesEl.__pinTracked = true;
    messagesEl.addEventListener('scroll', function() {
      _userPinnedToBottom = _isNearBottom();
    }, { passive: true });
  }

  function scrollToBottom() {
    if (!messagesEl) return;
    if (!_userPinnedToBottom) return; // reader scrolled up — don't fight them
    if (_scrollRafPending) return;
    _scrollRafPending = true;
    requestAnimationFrame(function() {
      _scrollRafPending = false;
      if (messagesEl && _userPinnedToBottom) {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    });
  }

  /** Jump to the bottom unconditionally (send / session load / new turn). */
  function scrollToBottomForce() {
    _userPinnedToBottom = true;
    if (!messagesEl) return;
    if (_scrollRafPending) return;
    _scrollRafPending = true;
    requestAnimationFrame(function() {
      _scrollRafPending = false;
      if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  }

  function escapeHtml(str) {
    if (!str) return '';
    var map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(str).replace(/[&<>"']/g, function(c) { return map[c]; });
  }

  // Tools where a hidden suffix is the difference between a copy and a wipe.
  var EXEC_TOOLS = ['shell_exec', 'python_exec', 'code_exec', 'computer_use',
                    'browser_eval_js'];

  // The card used to render escapeHtml(truncateStr(JSON.stringify(args), 300)),
  // so an operator was asked to authorise a command whose tail was invisible --
  // the same defect that was fixed on the Telegram side and missed here. The
  // args block is scrollable (max-height in kazma.css), so the whole thing is
  // reachable without the card swallowing the page.
  function formatPatchPreview(tool, args) {
    if (tool !== 'file_apply_patch' && tool !== 'file_apply_patch_set') return null;
    if (!args || typeof args !== 'object') return null;
    var patches = tool === 'file_apply_patch' ? [args] : args.patches;
    if (!Array.isArray(patches) || !patches.length) return null;
    var out = [patches.length + ' file(s):'];
    patches.slice(0, 20).forEach(function (item, i) {
      if (!item || typeof item !== 'object') return;
      out.push('\n' + (i + 1) + '. ' + (item.path || '?'));
      var patch = String(item.patch || '').trim();
      if (patch) {
        out = out.concat(patch.split('\n').slice(0, 80));
        return;
      }
      String(item.old_string || '').split('\n').slice(0, 40).forEach(function (ln) {
        out.push('- ' + ln);
      });
      String(item.new_string || '').split('\n').slice(0, 40).forEach(function (ln) {
        out.push('+ ' + ln);
      });
    });
    return out.join('\n');
  }

  function renderApprovalArgsHtml(tool, args) {
    var preview = formatPatchPreview(tool, args);
    if (preview == null) {
      return '<pre>' + escapeHtml(formatApprovalArgs(tool, args)) + '</pre>';
    }
    var lines = preview.split('\n').map(function (ln) {
      var cls = 'hitl-diff-ctx';
      if (/^\d+\.\s/.test(ln) || ln.indexOf(' file(s):') !== -1) cls = 'hitl-diff-file';
      else if (/^(\+\+\+|---|@@|diff )/.test(ln)) cls = 'hitl-diff-meta';
      else if (ln.charAt(0) === '-') cls = 'hitl-diff-del';
      else if (ln.charAt(0) === '+') cls = 'hitl-diff-add';
      return '<div class="' + cls + '">' + escapeHtml(ln || ' ') + '</div>';
    }).join('');
    return '<div class="hitl-diff" role="region" aria-label="Patch preview">' + lines + '</div>';
  }

  function formatApprovalArgs(tool, args) {
    var text = formatPatchPreview(tool, args);
    if (text == null) {
      try {
        text = JSON.stringify(args || {}, null, 2);
      } catch (e) {
        text = String(args);
      }
    }
    if (text.length <= 20000) return text;
    var hidden = text.length - 20000;
    var warn = '\n\n\u26A0 ' + hidden + ' MORE CHARACTERS ARE NOT SHOWN.';
    if (EXEC_TOOLS.indexOf(tool) !== -1) {
      warn += '\nDo NOT approve without reading all of it.';
    }
    return text.slice(0, 20000) + warn;
  }

  function truncateStr(str, max) {
    if (!str) return '';
    return str.length > max ? str.slice(0, max) + '\u2026' : str;
  }

  // ── Keyboard shortcuts ────────────────────────────────
  // Navigation shortcuts (Ctrl+K/N/1-8) live ONLY in modules/nav.js — the
  // old chat-local Ctrl+K/N here raced the global registry (audit P1-1).
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && document.activeElement === searchInputEl) {
      searchInputEl.value = '';
      searchQuery = '';
      renderSessionList();
      if (inputEl) inputEl.focus();
    }
  });

  // ── Boot ──────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }


  /**
   * Does this document need a transcript bubble at all?
   *
   * Text and reasoning are covered by _answerFromDoc. Beyond those, only two
   * things belong in the bubble: an approval card, and the durable one-line
   * workbench summary a FINISHED turn leaves behind (which needs a host even
   * when the turn produced no prose). A running step list is not content:
   * it is the activity fold, and a turn that has only that has nothing to
   * show yet.
   */
  function _docHasBubbleContent(doc) {
    if (!doc) return false;
    var st = String(doc.status || '');
    if (st === 'done' || st === 'error' || st === 'paused') return true;
    if (_answerFromDoc(window.KazmaTurnDocument, doc)) return true;
    if (_activityOfDoc(doc).length) return true;
    var parts = doc.parts || [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i] && parts[i].type === 'hitl') return true;
    }
    return false;
  }

  function _answerFromDoc(TD, doc) {
    var text = (TD && TD.textOf) ? TD.textOf((doc && doc.parts) || []) : '';
    if (!text && doc) text = doc.stream || '';
    return String(text || '').trim();
  }


  // ══ Render authority (Turn Delivery V2, KD-4) ═══════════════════════
  //
  // The V2 plan shipped the delivery half — journal, seq, cursor resume,
  // snapshot resync — and specified this half too:
  //
  //   "Client paints from state, not events. […] one render() applies
  //    state→DOM idempotently."
  //
  // It was never built. renderTurn kept asking the DOM where to paint, and
  // three separate writers (renderHitlCard, _syncCotPanel, appendMessage)
  // fought over one bubble's children while _rescueTurnDom ran afterwards
  // to undo the mis-nesting. modules/turn_view.js is the missing authority:
  // it owns the child list, derived from the document, keyed by the same
  // partKey the document dedupes with. Everything below is the adapter.

  /**
   * Who decides whether a fold is open.
   *
   * Invariant U08: "Event processing never changes disclosure
   * preferences." Before modules/turn_preferences.js there was no owner —
   * _paintWorkbenchSlot recomputed the fold from EXECUTION state on every
   * pass, so a reader who collapsed the thoughts panel had it reopened by
   * the next token. The document says what a turn contains; this store
   * says whether the reader wants to look at it; nothing else writes
   * either.
   */
  var _prefs = null;
  function _turnPrefs() {
    if (_prefs) return _prefs;
    var TP = window.KazmaTurnPreferences;
    if (!TP || typeof TP.create !== 'function') return null;
    _prefs = TP.create({ sessionId: chatSessionId || '' });
    return _prefs;
  }

  /** Should this turn's activity disclosure be open right now?
   *
   *  Default collapsed (plan §3). A preference, once expressed, outranks
   *  the default for as long as the tab lives — including across the
   *  terminal frame, which is where the old code flipped it back. */
  function _activityExpanded(turnId) {
    var prefs = _turnPrefs();
    if (!prefs) return false;
    return prefs.isExpanded(String(turnId || ''), 'activity', false);
  }

  var _view = null;
  function _turnView() {
    if (_view) return _view;
    var TV = window.KazmaTurnView;
    if (!TV || typeof TV.create !== 'function') return null;
    _view = TV.create({
      turnDocument: window.KazmaTurnDocument,
      onInvariant: _onRenderInvariant,
    });
    return _view;
  }

  /** One resync per (turn, failure kind) — enough to recover, never a loop. */
  var _invariantSeen = {};
  /** ...and a hard ceiling per session, so a systematically broken render
   *  reports every time but cannot turn into a fetch storm. */
  var _invariantResyncs = 0;
  var _INVARIANT_RESYNC_MAX = 3;

  /**
   * The renderer noticed it had gone silent.
   *
   * Every past incident in this class was found by the OPERATOR: the server
   * had the answer, the bubble showed a placeholder, and nothing in the
   * client knew the difference. TurnView re-derives what should be on
   * screen after every pass and calls this when it isn't. A regression is
   * now a console error plus one authoritative resync, not a person waiting.
   */
  function _onRenderInvariant(info) {
    info = info || {};
    var key = String(info.turnId || '?') + ':' + String(info.code || '?');
    try { diag('render-invariant', key + ' ' + (info.issues || []).join(',')); } catch (e) { /* ignore */ }
    console.error('[KazmaChat] render invariant ' + key, info);
    // Report ALWAYS, recover sparingly. Hydration paints the whole
    // transcript in one go, so resyncing from inside it would fire once per
    // historical turn — and loadSession already ends with one authoritative
    // resync, which is the same fetch done once.
    if (_hydratingSession) return;
    if (_invariantSeen[key]) return;
    _invariantSeen[key] = 1;
    if (_invariantResyncs >= _INVARIANT_RESYNC_MAX) return;
    _invariantResyncs++;
    try { _resyncDelivery('invariant-' + info.code); } catch (e2) { /* ignore */ }
  }

  function _activityOfDoc(doc) {
    var TD = window.KazmaTurnDocument;
    if (!TD || typeof TD.activityOf !== 'function') return [];
    // Same resolver the cards are ordered and labelled with, so the
    // workbench row for a gate cannot contradict the card next to it.
    return TD.activityOf((doc && doc.parts) || [], function (part) {
      var s = _hitlDisplayState(part);
      return s == null ? 'error' : s;
    });
  }


  // ══ Turn header ════════════════════════════════════════════════════
  //
  // docs/plans/UNIFIED_TURN_BLOCK.md §3: one header per turn, INSIDE the
  // turn block, owning phase, elapsed, counts and Stop. Those four facts
  // used to live in #live-task-card — a second status surface outside any
  // turn, with its own state machine and its own clock, which is how the
  // operator got "Done 0s" while the graph was still working.
  //
  // The model is derived by modules/turn_presentation.js, which is a pure
  // function of the document plus the server facts this page already
  // holds. Nothing here decides anything; it paints what it is given.

  var _HEADER_PHASE_LABELS = {
    queued: ['queued', 'Starting\u2026'],
    working: ['thinking', 'Working'],
    approval: ['approval_required', 'Approval required'],
    resuming: ['resuming', 'Resuming'],
    stopping: ['stopping', 'Stopping\u2026'],
    completed: ['completed', 'Completed'],
    failed: ['failed', 'Failed'],
    cancelled: ['cancelled', 'Cancelled'],
    recovering: ['recovering', 'Recovering\u2026'],
    interrupted: ['interrupted', 'Interrupted'],
  };

  var _HEADER_PHASE_ICONS = {
    queued: '\u25CB', working: '\u25CF', approval: '\u270B',
    resuming: '\u25B6', stopping: '\u25A0', completed: '\u2713',
    failed: '\u26A0', cancelled: '\u2298', recovering: '\u21BB',
    interrupted: '\u26A0',
  };

  /** The server facts the presentation model maps. Read-only snapshot. */
  function _headerFacts() {
    return {
      streamLive: _streamIsLive(),
      serverGenerating: !!_serverGenerating || !!_isGenerating,
      stopRequested: !!_stopRequested,
      gateViews: _serverGatesAuth ? _serverGateViews : null,
      // The SAME resolver the renderer orders and labels gates with, so
      // the header's count and the approval group's count are one number
      // arrived at once (see turn_presentation.gateRows).
      gateState: _hitlDisplayState,
      // How long the journal has been quiet. noteTurnActivity() already
      // stamps every live frame; the retired bar kept a SECOND clock and a
      // SECOND retry budget for the same question, next to the reconciler
      // that was already polling every 6s. Recovery stays the
      // reconciler's; this is only the report.
      lastSignalAgoMs: _lastTurnActivityTs
        ? (Date.now() - _lastTurnActivityTs) : 0,
      retrySupported: true,
    };
  }

  function _headerModel(doc) {
    var TP = window.KazmaTurnPresentation;
    if (!TP || typeof TP.header !== 'function') return null;
    try { return TP.header(doc, _headerFacts()); } catch (e) { return null; }
  }

  /** mm:ss from a server stamp, ticked forward locally while live.
   *
   *  Plan §3: "A local timer may update elapsed display. It cannot mark a
   *  gate expired or a turn complete." The tick is display only and is
   *  frozen the moment the phase is terminal, so a finished turn shows the
   *  duration the server measured rather than one this tab kept counting.
   */
  function _headerElapsedText(model) {
    if (!model) return '';
    var secs = model.elapsed.seconds;
    if (!model.terminal && model.elapsed.stampedAtMs) {
      var drift = (Date.now() - model.elapsed.stampedAtMs) / 1000;
      if (drift > 0 && drift < 3600) secs += drift;
    }
    if (secs <= 0) return '';
    return _fmtMMSS(secs);
  }

  function _headerCountsText(model) {
    if (!model) return '';
    var bits = [];
    var c = model.counts;
    if (c.tools) {
      bits.push(tiCount('count_tools', c.tools, '{n} tool', '{n} tools'));
    }
    if (c.pending) {
      bits.push(tiFmt('awaiting_decisions', '{n} awaiting your decision',
        { n: c.pending }));
    } else if (c.gates) {
      bits.push(tiCount('count_approvals', c.gates, '{n} approval', '{n} approvals'));
    }
    return bits.join(' \u00B7 ');
  }

  /**
   * Repaint the live turn's header once a second.
   *
   * Display only: the clock ticks forward from the server's stamp and the
   * silence counter grows. It changes no state, decides nothing, and stops
   * the moment the turn is terminal — plan §3, "A local timer may update
   * elapsed display. It cannot mark a gate expired or a turn complete."
   * The retired bar's tick did both, which is how a client wall clock came
   * to print "Done 0s" over a turn the graph was still running.
   */
  var _headerTicker = null;

  function _tickLiveHeader() {
    var TV = _turnView();
    var id = _liveTurnId || 'live';
    var el = TV && TV.elFor(id);
    var node = el && TV.slot(el, 'header');
    if (!node) return false;
    var doc = _docs[id];
    if (!doc) return false;
    _paintTurnHeader(node, doc);
    var model = _headerModel(doc);
    return !!(model && !model.terminal);
  }

  function _startHeaderTicker() {
    if (_headerTicker) return;
    _headerTicker = setInterval(function () {
      var keepGoing = false;
      try { keepGoing = _tickLiveHeader(); } catch (e) { keepGoing = false; }
      if (!keepGoing) _stopHeaderTicker();
    }, 1000);
  }

  function _stopHeaderTicker() {
    if (!_headerTicker) return;
    clearInterval(_headerTicker);
    _headerTicker = null;
  }

  function _buildTurnHeader(turnId) {
    var el = document.createElement('div');
    el.className = 'turn-header';
    el.setAttribute('data-turn-id', String(turnId || ''));
    el.innerHTML =
      '<span class="turn-header-phase" aria-hidden="true"></span>' +
      '<span class="turn-header-label"></span>' +
      '<span class="turn-header-meta"></span>' +
      '<span class="turn-header-conn" hidden></span>' +
      '<span class="turn-header-live sr-only" role="status" aria-live="polite"></span>' +
      '<button type="button" class="turn-header-act turn-header-stop" hidden></button>' +
      '<button type="button" class="turn-header-act turn-header-retry" hidden></button>';
    var stop = el.querySelector('.turn-header-stop');
    if (stop) {
      stop.textContent = ti('stop_generation', 'Stop');
      stop.addEventListener('click', function (e) {
        e.preventDefault();
        // The SAME command the composer Stop runs. A second surface with
        // its own abort path is what the bottom bar was.
        try { abortGeneration({ source: 'turn-header' }); } catch (e2) { /* ignore */ }
      });
    }
    // Not named `retry`: that would shadow the retry() function this
    // handler calls, and the call would silently become "invoke a DOM
    // node" inside a try that swallows it — a button that does nothing.
    var retryBtn = el.querySelector('.turn-header-retry');
    if (retryBtn) {
      retryBtn.textContent = ti('task_retry', 'Retry');
      retryBtn.addEventListener('click', function (e) {
        e.preventDefault();
        try { retry(); } catch (e2) { /* ignore */ }
      });
    }
    return el;
  }

  /**
   * Paint the header. Writes only what changed, so a render pass that
   * changes nothing performs no DOM mutation — the renderer's idempotence
   * is a tested property and a header that rewrites itself every token
   * would quietly cost it.
   */
  function _paintTurnHeader(el, doc) {
    var model = _headerModel(doc);
    if (!model) return;
    var phase = model.phase;
    var label = _HEADER_PHASE_LABELS[phase] || ['thinking', 'Working'];

    function setText(sel, text) {
      var node = el.querySelector(sel);
      if (node && node.textContent !== text) node.textContent = text;
    }
    function setHidden(sel, hidden) {
      var node = el.querySelector(sel);
      if (node && node.hidden !== hidden) node.hidden = hidden;
    }

    var cls = 'turn-header is-' + phase;
    if (model.connection === 'reconnecting') cls += ' is-reconnecting';
    if (el.className !== cls) el.className = cls;

    setText('.turn-header-phase', _HEADER_PHASE_ICONS[phase] || '\u25CF');
    setText('.turn-header-label', ti(label[0], label[1]));

    var meta = [];
    var elapsed = _headerElapsedText(model);
    if (elapsed) meta.push(elapsed);
    var counts = _headerCountsText(model);
    if (counts) meta.push(counts);
    setText('.turn-header-meta', meta.join(' \u00B7 '));

    // Connection is reported ALONGSIDE the phase, never instead of it.
    // Plan §3: "Disconnection is not completion or failure."
    var conn = el.querySelector('.turn-header-conn');
    if (conn) {
      var connText = '';
      if (model.connection === 'stalled') {
        // Silence, with its duration. The bar said "not responding" after
        // exhausting a retry budget it owned; the reconciler never stops
        // trying, so the honest thing to report is how long it has been
        // quiet rather than a verdict this surface cannot reach.
        connText = tiFmt('no_signal_for', 'No signal for {t}',
          { t: _fmtMMSS(model.silentMs / 1000) });
      } else if (model.connection === 'reconnecting') {
        connText = ti('reconnecting', 'Reconnecting\u2026');
      }
      var show = !!connText;
      if (conn.hidden !== !show) conn.hidden = !show;
      if (conn.textContent !== connText) conn.textContent = connText;
    }

    setHidden('.turn-header-stop', !model.canStop);
    setHidden('.turn-header-retry', !model.canRetry);

    // Announce the coarse phase, not every token or timer tick (plan §11).
    var live = el.querySelector('.turn-header-live');
    if (live) {
      var say = ti(label[0], label[1]);
      if (model.awaiting) {
        say = tiFmt('awaiting_decisions', '{n} awaiting your decision',
          { n: model.awaiting });
      }
      if (live.getAttribute('data-said') !== say) {
        live.setAttribute('data-said', say);
        live.textContent = say;
      }
    }
  }

  // ── Slot painters ──────────────────────────────────────────────────

  function _paintTextSlot(textEl, doc, meta) {
    var TD = window.KazmaTurnDocument;
    var text = _answerFromDoc(TD, doc);
    if (!text) return;
    tryIngestPlanFromText(text);
    var display = _scrubDsml(stripPlanFenceForDisplay(text));
    // A slot is never hidden by its own painter. (_rescueTurnDom used to
    // sweep the whole bubble for a text node left display:none by an
    // interrupted typing animation.)
    try {
      if (textEl.style && textEl.style.display === 'none') textEl.style.display = '';
      textEl.classList.remove('typing-visible');
    } catch (eS) { /* ignore */ }
    try {
      if (doc.status === 'streaming') {
        _scheduleLiveTextPaint(textEl);
      } else if (_paintHTML(textEl, _renderReplyHTML(text))) {
        // Same finish as every other paint path. _paintLiveTextNow and
        // appendMessage both re-run the bidi pass and re-assert dir="auto"
        // after writing innerHTML; this branch did not, so the terminal
        // paint and the paint you get after a refresh (which goes through
        // appendMessage) styled the same reply differently — visible
        // immediately in Arabic, and as a subtle shift in mixed text
        // (2026-09-19, live install: "the contents almost the same but how
        // the text looks").
        if (window.KazmaBidi) {
          try { KazmaBidi.apply(textEl, text); } catch (eB) { /* never break the paint */ }
        }
        try { textEl.setAttribute('dir', 'auto'); } catch (eD) { /* ignore */ }
      }
    } catch (mdErr) {
      if (textEl.textContent !== display) textEl.textContent = display;
    }
    try { textEl.setAttribute('data-md', text); } catch (eMd) { /* ignore */ }
    try { textEl.setAttribute('data-final-len', String(display.length)); } catch (eLen) { /* ignore */ }
    if (String(doc.turnId || '') === _liveTurnId) _turnPainted = true;
  }

  function _paintWorkbenchSlot(panel, doc) {
    var activity = _activityOfDoc(doc);
    if (!activity.length) return;
    var html = _activityRowsHtml(activity);
    if (!html) return;
    _progressToolCount = (html.match(/data-kind="tool"/g) || []).length;
    _progressStepCount = (html.match(/<li /g) || []).length;
    var thoughtN = (html.match(/data-kind="thought"/g) || []).length;
    var done = !!(doc && (doc.status === 'done' || doc.status === 'error'));
    panel.classList.toggle('is-done', done);
    panel.classList.toggle('is-active', !done);
    // The fold follows the READER, not the turn. This used to force the
    // panel open on every live pass and the reader could not keep it
    // shut; before that it collapsed at the terminal frame and yanked the
    // answer out of view. Both were the same mistake — execution state
    // deciding a presentation preference (invariant U08).
    _applyActivityFold(panel, (doc && doc.turnId) || '');
    panel.classList.remove('kazma-cot-restored');
    var titleEl = panel.querySelector('.agent-progress-title');
    if (titleEl) {
      titleEl.textContent = done
        ? (thoughtN
          ? ti('cot_title', 'Thinking & Activity')
          : ti('working', 'Working\u2026'))
        : ti('thinking', 'Kazma is thinking\u2026');
    }
    var list = panel.querySelector('.agent-progress-steps');
    if (!list) return;
    if (list._kzCotHTML === html) return;   // nothing changed — no churn
    list._kzCotHTML = html;
    list.innerHTML = html;
    _wireStepToggles(list);
    var countEl = panel.querySelector('.agent-progress-count');
    if (countEl) {
      var bits = [];
      if (thoughtN) bits.push(ti('thoughts', 'Thoughts'));
      if (_progressToolCount) {
        bits.push(tiCount('count_tools', _progressToolCount, '{n} tool', '{n} tools'));
      }
      bits.push(tiCount('count_steps', _progressStepCount, '{n} step', '{n} steps'));
      countEl.textContent = bits.join(' \u00B7 ');
    }
  }


  // ══ Approval group ═════════════════════════════════════════════════
  //
  // docs/plans/UNIFIED_TURN_BLOCK.md §3: "Zero groups when there are no
  // gates; exactly one when at least one exists. One row per actual gate
  // ID. […] Approval group location stays stable above the answer. Rows
  // update in request order without moving the answer between
  // containers."
  //
  // Before this, four requests in one turn were four independent
  // top-level cards, sorted by state: settled ones above the answer,
  // still-asking ones below it. Approving the first therefore MOVED the
  // answer, and a reader could not tell four requests in one turn from
  // four turns.
  //
  // The cards themselves are unchanged — renderHitlCard still builds
  // them, with the same argument preview, scope explanation, countdown
  // and controls. What changed is that they are rows in one region
  // instead of siblings of the answer.

  var GATE_KEY_ATTR = 'data-gate-key';

  function _buildApprovalGroup() {
    var el = document.createElement('div');
    el.className = 'turn-approvals';
    el.setAttribute('role', 'group');
    el.innerHTML =
      '<div class="turn-approvals-head">' +
        '<span class="turn-approvals-title"></span>' +
        '<span class="turn-approvals-count"></span>' +
      '</div>' +
      '<div class="turn-approvals-rows"></div>';
    return el;
  }

  /**
   * Reconcile the region's rows against the plan.
   *
   * Keyed by gate id — the same `partKey` the document dedupes with, so
   * "which row is this gate" cannot drift from "which part is this"
   * (turn_view contract 1). Rows are created once and repainted in
   * place; a decision changes a row's label, never its position, which is
   * what keeps the answer still.
   *
   * Rows the plan no longer mentions are KEPT (contract 4: ambiguity
   * never deletes). A truncated resync that stops mentioning a gate must
   * leave its decision on screen.
   */
  function _paintApprovalGroup(el, entry, ctx) {
    var rows = (entry && entry.rows) || [];
    var host = el.querySelector('.turn-approvals-rows');
    if (!host) return;

    // renderHitlCard appends into `opts.host`, which the slot renderer
    // used to set to .message-content. Point it at the rows container so
    // a new card is born inside the region rather than beside the answer
    // and then relocated — a card that appears in the wrong place for one
    // frame is a card the reader can click in the wrong place.
    var rowCtx = {};
    var ck;
    for (ck in (ctx || {})) {
      if (Object.prototype.hasOwnProperty.call(ctx, ck)) rowCtx[ck] = ctx[ck];
    }
    rowCtx.content = host;

    var byKey = el.__kzRows || (el.__kzRows = {});
    var ordered = [];
    var pending = 0;
    var i;

    for (i = 0; i < rows.length; i++) {
      var row = rows[i];
      var node = byKey[row.key] || null;
      if (node && node.parentNode !== host) node = null;
      if (node) {
        // Same rebuild rule the flat slots had: a card frozen in the
        // hydration 'awaiting' posture cannot have its live buttons
        // painted back, so it is torn out and rebuilt once the registry
        // says the gate is live.
        var shown = '';
        try { shown = String(node.getAttribute('data-hitl-shown') || ''); } catch (e) { shown = ''; }
        if (String(row.state || '') === 'pending' && shown && shown !== 'pending') {
          try { host.removeChild(node); } catch (eR) { /* ignore */ }
          node = null;
          delete byKey[row.key];
        }
      }
      if (!node) {
        node = _buildHitlSlotCard(row.part, rowCtx, row.state);
        if (!node) continue;
        try { node.setAttribute(GATE_KEY_ATTR, row.key); } catch (eA) { /* ignore */ }
        byKey[row.key] = node;
      }
      _paintHitlSlotCard(node, row.part, rowCtx, row.state);
      if (String(row.state || '') === 'pending') pending++;
      ordered.push(node);
    }

    // Rows the plan dropped stay, after the planned ones, in the order
    // they are already in.
    var kids = host.children;
    for (i = 0; i < kids.length; i++) {
      if (ordered.indexOf(kids[i]) < 0) ordered.push(kids[i]);
    }

    // One ordering pass, comparing first so an unchanged region performs
    // zero mutations (the renderer's idempotence is a tested property).
    var cursor = null;
    for (i = 0; i < ordered.length; i++) {
      var want = cursor ? cursor.nextElementSibling : host.firstElementChild;
      if (ordered[i] !== want) {
        try { host.insertBefore(ordered[i], want || null); } catch (eI) { /* ignore */ }
      }
      cursor = ordered[i];
    }

    var total = ordered.length;
    var titleEl = el.querySelector('.turn-approvals-title');
    var countEl = el.querySelector('.turn-approvals-count');
    var title = ti('approvals', 'Approvals');
    if (titleEl && titleEl.textContent !== title) titleEl.textContent = title;
    var bits = [];
    bits.push(tiCount('count_requests', total, '{n} request', '{n} requests'));
    if (pending) {
      bits.push(tiFmt('awaiting_decisions', '{n} awaiting your decision',
        { n: pending }));
    }
    var count = bits.join(' \u00B7 ');
    if (countEl && countEl.textContent !== count) countEl.textContent = count;
    var cls = 'turn-approvals' + (pending ? ' is-awaiting' : ' is-settled');
    if (el.className !== cls) el.className = cls;
  }

  /**
   * Renderers handed to TurnView. build() creates a slot's node, paint()
   * updates it, discard() decides removal — and it always answers false.
   *
   * Contract 4 (turn_view.js): ambiguity never deletes. A decision, an
   * answer and a workbench are transcript. A truncated resync or a partial
   * hydrate that stops mentioning one of them must leave it on screen —
   * a stale node is visible and reportable, a removed one is silence.
   */
  var _turnRenderers = {
    // The ONE answer to "what state is this gate in". TurnView orders by it
    // and hands it back on the entry for the painter to label with, so a
    // card can never be sorted as one thing and painted as another.
    gateState: _hitlDisplayState,
    has: function(kind, doc) {
      if (kind === 'text') return !!_answerFromDoc(window.KazmaTurnDocument, doc);
      if (kind === 'workbench') return _activityOfDoc(doc).length > 0;
      // The header is unconditional once a turn exists — it is the thing
      // that says "this turn is starting" before there is anything else
      // to show (plan §3).
      if (kind === 'header') return !!window.KazmaTurnPresentation;
      return true;
    },
    build: function(entry, ctx) {
      if (entry.kind === 'header') {
        return _buildTurnHeader(String((ctx.doc && ctx.doc.turnId) || ''));
      }
      if (entry.kind === 'approvals') return _buildApprovalGroup();
      if (entry.kind === 'text') {
        var t = document.createElement('div');
        t.className = 'message-text';
        try { t.setAttribute('dir', 'auto'); } catch (e) { /* ignore */ }
        return t;
      }
      if (entry.kind === 'workbench') {
        var turnId = String((ctx.doc && ctx.doc.turnId) || '');
        var panel = _buildRestoredWorkbench(_activityOfDoc(ctx.doc), turnId);
        if (!panel) return null;
        var finished = ctx.doc && (ctx.doc.status === 'done' || ctx.doc.status === 'error');
        if (!finished) {
          panel.classList.remove('is-done', 'kazma-cot-restored');
          panel.classList.add('is-active');
        }
        // Collapsed unless this reader said otherwise — for a live turn
        // exactly as for a restored one. A new turn starts collapsed
        // because it has no preference yet, not because it is new.
        _applyActivityFold(panel, turnId);
        return panel;
      }
      if (entry.kind === 'hitl') return _buildHitlSlotCard(entry.part, ctx, entry.state);
      return null;
    },
    paint: function(entry, el, ctx) {
      if (entry.kind === 'header') return _paintTurnHeader(el, ctx.doc);
      if (entry.kind === 'approvals') return _paintApprovalGroup(el, entry, ctx);
      if (entry.kind === 'text') return _paintTextSlot(el, ctx.doc, ctx.meta);
      if (entry.kind === 'workbench') return _paintWorkbenchSlot(el, ctx.doc);
      if (entry.kind === 'hitl') return _paintHitlSlotCard(el, entry.part, ctx, entry.state);
    },
    // Hydration paints a pending part as 'awaiting': the card is shown,
    // buttons replaced with "Waiting for approval…". Paint-in-place cannot
    // put those buttons back. When the registry later says the gate is
    // live, tear the frozen node out and let build() mint a real one.
    rebuild: function(entry, el) {
      // Gates are rows inside the approvals region now, so their rebuild
      // rule lives with the rows (_paintApprovalGroup). Nothing at the
      // top level needs tearing out: the region itself is stable for the
      // life of the turn, which is the point of it.
      return false;
    },
    discard: function(key, node, ctx) {
      // Contract 4 is "ambiguity never deletes", and it stands: a
      // truncated resync or a partial hydrate that stops mentioning a
      // region must leave it on screen.
      //
      // An EMPTY answer is not ambiguity. `mergeParts` never removes, so
      // the only way the answer text disappears from a document is
      // `foldNarration` deciding the text was narration after all — a
      // deliberate reclassification, not a gap in a snapshot.
      //
      // Without this the region keeps whatever was last painted into it:
      // `paint` only runs for PLANNED slots, so once the plan stops
      // asking for `text` the stale sentence sits there for the rest of
      // the turn. Measured in the live page at a pause, 2026-09-20.
      if (key !== 'text') return false;
      return !_answerFromDoc(window.KazmaTurnDocument, ctx && ctx.doc);
    },
  };

  /**
   * Which bubble does this turn own?
   *
   * The registry answers, not the DOM. The old resolver ran a querySelector
   * on the turn id, fell back to currentMsgEl, fell back to "last assistant
   * bubble after the last user row", then walked nextElementSibling to
   * decide whether that bubble was historical — four guesses, each of which
   * broke on a different markup change. A map lookup cannot be wrong about
   * which node it was handed.
   */
  function _bubbleForTurn(turnId, paintable) {
    var TV = _turnView();
    if (!TV) return null;
    var id = String(turnId || '');
    var el = null;
    if (id && id !== 'live') {
      el = TV.elFor(id);
      // Frames arrive unstamped until the server names the turn, so the
      // bubble opened under the 'live' placeholder. Promotion is a rename
      // in the map — never a DOM search, and never leaves a bubble sitting
      // in the transcript advertising data-turn-id="live" for the NEXT
      // turn's tokens to find (2026-09-03 crossed bubbles).
      if (!el) {
        el = TV.promote('live', id);
        // Every map keyed by the turn id hears about the rename, not just
        // the bubble registry. The disclosure preference did not, so a
        // fold the reader opened before the server stamped the turn was
        // written under 'live' and read back under the real id — it shut
        // again on the next token. Caught in the browser; the unit tests
        // drove one constant id and never promoted.
        var prefsP = _turnPrefs();
        if (prefsP && typeof prefsP.promote === 'function') {
          try { prefsP.promote('live', id); } catch (ePr) { /* ignore */ }
        }
      }
    } else {
      el = TV.elFor('live');
    }
    if (el) return el;
    if (!paintable) return null;
    // One user row, one assistant bubble. A HITL resume, persist heal, or
    // journal replay that stamps a second turn_id must rebind the OPEN
    // bubble — minting a new one is the duplicate-before-replay the 4-card
    // sequential run painted (2026-09-20).
    el = _assistantBubbleForOpenTurn(false);
    if (el) {
      TV.bind(id || 'live', el);
      return el;
    }
    el = createAssistantMessage();
    TV.bind(id || 'live', el);
    return el;
  }


  /**
   * What state should this gate's card SHOW?
   *
   * HITL_VIEW_MODEL A1: the server already answered. Live overlay
   * (``_serverGateViews``) wins; else the ``view`` stamped on the part at
   * /messages read. No view → omit (null). Never re-derive from part.state,
   * hydrate flags, or a DOM claimed-scan.
   */
  function _gateViewById(iid) {
    iid = String(iid || '');
    if (!iid) return null;
    var list = _serverGateViews || [];
    var i, v;
    for (i = 0; i < list.length; i++) {
      v = list[i] || {};
      if (String(v.interrupt_id || '') === iid || String(v.gate_id || '') === iid) {
        return v;
      }
    }
    return null;
  }

  function _gateViewOf(part) {
    var iid = _hitlInterruptIdOf(part);
    var ov = iid && _hitlOverlay[iid];
    if (ov && ov.view) return ov.view;
    var live = _gateViewById(iid);
    if (live) return live;
    var stamped = (part && part.view && typeof part.view === 'object')
      ? part.view : null;
    var ps = String((part && part.state) || '').toLowerCase();
    // A claimed part with no covering live row must stay SETTLED in the
    // plan. Omitting it left the DOM card unplanned (keptTail) under the
    // answer. Mirrors gate_view._view_from_part_alone for terminal stamps.
    if (ps && ps !== 'pending') {
      if (stamped && !stamped.interactive) return stamped;
      return { state: ps, interactive: false, slot: 'settled' };
    }
    if (stamped) return stamped;
    return null;
  }

  function _hitlDisplayState(part) {
    var v = _gateViewOf(part);
    if (!v) return null;
    return String(v.state || '');
  }

  /** Lock the composer only when the view says the gate is interactive. */
  function _hitlShouldLock(part) {
    var v = _gateViewOf(part);
    return !!(v && v.interactive);
  }

  /**
   * Build the card for one gate. Called by TurnView ONLY when the slot is
   * empty, so this never needs to ask whether a card already exists — the
   * slot table is the dedupe. That is what retired _hitlAlreadyClaimed's
   * paint guard, _findHitlCard's rescan, and the sweep that deleted
   * "other unclaimed pending cards" before minting a new one.
   */
  function _buildHitlSlotCard(part, ctx, resolvedState) {
    var payload = (part && part.payload) || part;
    if (!payload || typeof payload !== 'object') return null;
    // Same value TurnView ordered by. Re-resolving here was the fourth
    // independent answer to "what state is this gate in" — lock/store
    // (whether the card has live buttons) could disagree with position
    // and label even though all three are the same fact.
    var show = (resolvedState != null && resolvedState !== '')
      ? String(resolvedState) : _hitlDisplayState(part);
    if (!show) return null;
    var card = renderHitlCard(payload, {
      lock: show === 'pending' && _hitlShouldLock(part),
      store: show === 'pending',
      host: (ctx && ctx.content) || null,
    });
    return card || null;
  }

  /**
   * Bring an existing card in line with the document.
   *
   * A live gate is left alone — its buttons are wired and the operator may
   * be reading it. Everything else is frozen and stamped.
   */
  function _paintHitlSlotCard(card, part, ctx, resolvedState) {
    if (!card) return;
    // Label from the state TurnView ORDERED by. Re-resolving here is what
    // let a card sort as pending (below the answer) while painting
    // "Approved — running…" on top of it.
    var show = (resolvedState != null && resolvedState !== '')
      ? String(resolvedState) : _hitlDisplayState(part);
    if (!show) return;
    if (show === 'pending') {
      _setHitlHeaderTitle(card, '\u26A0 Approval Required');
      // Only a gate the registry confirms is live gets a ticker. Re-arming
      // it on every render of anything that merely *looks* pending is what
      // let a stale part resurrect a countdown after the fact.
      if (_hitlShouldLock(part)) {
        _attachHitlCountdown(card, (part && part.payload) || part);
      }
      return;
    }
    var already = String(card.getAttribute('data-hitl-shown') || '');
    if (already === show) return;          // idempotent: no churn per frame
    try { card.setAttribute('data-hitl-shown', show); } catch (e) { /* ignore */ }

    card.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
    // A settled card must stop counting down. The old projector disabled
    // the buttons but left the ticker running, so an approved card kept
    // advertising "auto-denies if unanswered in 3:59" under an "Approved"
    // stamp (2026-09-03).
    _stopHitlCountdown(card);
    var cdRow = card.querySelector('.hitl-countdown');
    if (cdRow && cdRow.parentNode) cdRow.parentNode.removeChild(cdRow);

    var actions = card.querySelector('.hitl-approval-actions');
    if (show === 'awaiting') {
      if (actions) {
        actions.innerHTML = '<span class="hitl-status">' +
          escapeHtml(ti('waiting_approval', 'Waiting for approval…')) + '</span>';
      }
      return;
    }
    if (show === 'timeout' || show === 'denied' || show === 'error') {
      var kind = (show === 'error') ? 'error' : 'denied';
      // Preserve hitl-collapsed: a wholesale className assignment used to
      // strip it, springing the card back open mid-reply with a stranded
      // chip in the header (2026-09-03).
      var wasCollapsed = card.classList.contains('hitl-collapsed');
      card.className = 'hitl-approval-card hitl-' + kind + (wasCollapsed ? ' hitl-collapsed' : '');
      var errLabel = show === 'timeout'
        ? 'Approval timed out — continuing without this tool.'
        : (show === 'error' ? 'No longer pending' : 'Denied');
      if (actions) {
        actions.innerHTML = '<span class="hitl-status hitl-' + kind + '">' +
          escapeHtml(errLabel) + '</span>';
      }
      _setHitlHeaderTitle(card, _hitlToolOf(part) || errLabel);
      _collapseClaimedHitlCard(card);
      return;
    }
    if (show === 'approved' || show === 'inflight' || show === 'settled') {
      var wasCol = card.classList.contains('hitl-collapsed');
      card.className = 'hitl-approval-card hitl-approved' + (wasCol ? ' hitl-collapsed' : '');
      var okLabel = show === 'inflight' ? 'Approved — running…' : 'Approved';
      if (actions) {
        actions.innerHTML = '<span class="hitl-status hitl-approved">' + okLabel + '</span>';
      }
      _setHitlHeaderTitle(card, _hitlToolOf(part) || okLabel);
      _collapseClaimedHitlCard(card);
    }
  }

  /**
   * Record a decision this tab just made, in the document, immediately.
   *
   * The click path used to move the card by hand (_parkClaimedHitlCard)
   * and leave the document saying `pending` until a server frame arrived.
   * So the client's own model disagreed with the click for as long as the
   * round trip took — and if the frame never came, forever. HITL_RANK
   * makes the transition monotonic, so a replayed `pending` cannot walk it
   * back, and TurnView re-orders the card above the reply on the next pass.
   */
  /**
   * A gate arrived on the WebSocket. Route it into the DOCUMENT.
   *
   * The Alpine store used to call the card BUILDER directly, so WS and SSE
   * deliveries of the same approval raced to paint, and renderHitlCard grew
   * a guard family to sort out the collision after the fact. Both feed the
   * projector now; the projector dedupes by interrupt id; the renderer
   * builds exactly one card per gate. That is the whole single-writer rule
   * in three lines.
   */
  function ingestHitlApproval(data) {
    if (!data) return;
    applyTurnEvent({
      type: 'hitl',
      state: 'pending',
      tool: _hitlToolOf(data),
      interrupt_id: _hitlInterruptIdOf(data),
      payload: data,
      turn_id: _liveTurnId,
      source: 'ws',
    });
  }

  function _noteGateDecided(data, state) {
    var iid = _hitlInterruptIdOf(data);
    var ev = {
      type: 'hitl',
      state: state,
      tool: _hitlToolOf(data),
      interrupt_id: iid,
      payload: data,
      turn_id: _liveTurnId,
      source: 'decision',
    };
    // Evidence, not a stamp. The gate registry is decision truth against a
    // part we merely HYDRATED — that rule exists so a stale 'approved'
    // cannot invent an approval nobody gave. It must not also outrank a
    // decision this tab watched the operator make and the server confirm:
    // /status is a snapshot, and between the approve and the next resync it
    // still lists the gate as pending, which sorted the settled card back
    // underneath the answer until a refresh (2026-09-19, live install).
    //
    // Only an operator click earns this. A client timeout/error is a
    // display guess — the ticker must not outrank a still-pending
    // registry row, for the same reason a card must not invent Approved.
    applyTurnEvent(ev);
  }

  function _isWatchdogNotice(text) {
    var s = String(text || '');
    if (/No response received/i.test(s)) return true;
    // HITL card fills an empty bubble with this italic line. Treating it
    // as real content blocked the terminal/resync paint, so the operator
    // saw silence until a refresh replayed the persisted reply (2026-09-19).
    if (/Action required:\s*The agent paused/i.test(s)) return true;
    if (/The agent paused to ask for permission/i.test(s)) return true;
    return false;
  }

  /**
   * Terminal-frame SoT: write *raw* into the open bubble when it is still
   * empty (or still showing the empty-terminal watchdog). applyEvent marks
   * the content dedupe key BEFORE renderTurn paints; after a server
   * restart (reload / session re-mint / WS reconnect) that paint can miss
   * the DOM, every later identical paint is dropped, and the watchdog
   * card fires — then a refresh *replays the watchdog* instead of the
   * persisted reply (2026-09-05 capacity acks, 2026-09-08 calendar turn).
   */
  function _forcePaintDoneContent(raw) {
    var text = String(raw || '');
    if (!text.trim() || _isWatchdogNotice(text)) return false;
    try { _pinLiveAssistantBubble(); } catch (ePin) { /* ignore */ }
    var el = currentMsgEl && currentMsgEl.querySelector('.message-text');
    var visible = String((el && el.textContent) || '').trim();
    if (el && (!visible || _isWatchdogNotice(visible))) {
      try {
        _paintHTML(el, _renderReplyHTML(text));
        try { el.setAttribute('data-md', text); } catch (eMd) { /* ignore */ }
      } catch (ePaint) {
        try { el.textContent = text; } catch (eTxt) { /* ignore */ }
      }
      try { scrollToBottom(); } catch (eScroll) { /* ignore */ }
    }
    _turnPainted = true;
    return true;
  }

  /**
   * Law 3: the only DOM writer for the live assistant bubble + restored CoT.
   * Transports mutate the in-memory TurnDocument; this paints it.
   */
  /**
   * Paint one turn. The DOM is a pure function of `doc` (V2 plan KD-4).
   *
   * What used to live here is gone, not moved: the turn-id querySelector,
   * the currentMsgEl fallback chain, _assistantBubbleForOpenTurn, the
   * nextElementSibling walk that GUESSED whether a bubble was historical,
   * two _rescueTurnDom calls, and the interior painters that saved and
   * restored currentMsgEl around themselves because they moved nodes out
   * from under each other.
   *
   * Two questions replaced all of it, and neither is asked of the DOM:
   *   "which bubble is this turn?"  → the TurnView registry.
   *   "is this the open turn?"      → turnId === _liveTurnId.
   */
  function renderTurn(doc, meta) {
    if (!doc || !messagesEl) return;
    meta = meta || {};
    var TD = window.KazmaTurnDocument;
    var TV = _turnView();
    if (!TD || !TV) return;
    var turnId = String(doc.turnId || '');

    // A doc with nothing to SHOW must not mint a bubble. beginTurn seeds a
    // "Thinking…" row, and a row is not content — counting it opened every
    // turn with an empty bubble. This is the one mint gate: see
    // tests/test_chat_as_product.py::test_live_assistant_bubble_is_pinned_not_minted.
    var paintable = !!_answerFromDoc(TD, doc) || _docHasBubbleContent(doc);
    var el = _bubbleForTurn(turnId, paintable);
    if (!el) {
      return;
    }
    if (turnId && turnId !== 'live') TV.bind(turnId, el);

    var open = !turnId || turnId === 'live' || turnId === _liveTurnId;
    if (open) currentMsgEl = el;

    // One writer, one pass. Child order, creation and removal all happen
    // inside here; nothing else may insert into this bubble.
    TV.render(el, doc, _turnRenderers, meta);

    if (doc.model) {
      var metaEl = el.querySelector('.message-meta');
      if (metaEl && String(metaEl.textContent || '').indexOf(doc.model) < 0) {
        metaEl.textContent =
          (metaEl.textContent ? metaEl.textContent + ' · ' : '') + doc.model;
      }
    }

    // A closed turn's render must not release the OPEN turn's wait state
    // (a late turn-N hydrate mid-turn-N+1 used to clear _awaitingReply,
    // disabling the cursor-resume if the live stream then died).
    if (open && (doc.status === 'done' || meta.source === 'resync'
        || meta.source === 'hydrate' || meta.source === 'capacity'
        || meta.source === 'done')) {
      _awaitingReply = false;
    }
    if ((meta.source === 'resync' || meta.source === 'hydrate') && !_streamIsLive()) {
      currentMsgEl = null;
    }
    scrollToBottom();
  }

  function applyTurnEvent(ev) {
    ev = ev || {};
    var TD = window.KazmaTurnDocument;
    if (!TD || typeof TD.applyEvent !== 'function') return false;
    var incoming = String(ev.turn_id || ev.turnId || '');
    var src = String(ev.source || ev.type || '');
    var isHitl = ev.type === 'hitl' || ev.type === 'approval_required'
      || ev.type === 'approval_needed' || ev.type === 'paused_for_approval'
      || src === 'hitl';
    if (!isHitl) {
      if (incoming && _isRetiredTurn(incoming)) return false;
      // New SSE tokens usually have no turn_id; the callback is already
      // epoch-gated. Old WS/done without an id is the duplication path.
      if (!incoming && _supersededLive && (src === 'ws' || src === 'done')) return false;
      if (incoming === 'live' && _supersededLive && _liveTurnId && _liveTurnId !== 'live') {
        return false;
      }
    }
    var turnId = incoming || _liveTurnId || '';
    if (!turnId) turnId = 'live';
    if (_isRetiredTurn(turnId)) return false;
    _liveTurnId = turnId;
    var prev = _docs[turnId] || TD.empty(turnId);
    var next = TD.applyEvent(prev, ev);
    if (next === prev) return false;
    _docs[turnId] = next;
    renderTurn(next, { source: ev.source || ev.type || '' });
    return true;
  }

  function destroyChatMouth() {
    try { if (activeStream) activeStream.abort(); } catch (e) {}
    activeStream = null;
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent') && Alpine.store('agent').disconnect) {
        Alpine.store('agent').disconnect();
      }
    } catch (e) {}
  }
  window.kazmaOnSoftNavLeave = destroyChatMouth;

  /** Live-voice user-row contract. The voice socket is the originating
   *  mouth for the USER line — exactly like the composer is for typed text
   *  — while the journal/WS projector stays the only author of the
   *  assistant. Without a fresh user row, _assistantBubbleForOpenTurn
   *  latches onto the previous reply's bubble and the new turn's tokens
   *  grow it (the 2026-09-02 crossed-bubble class). Mirrors the typed-chat
   *  pre-graph sequence; never submits a second graph turn — the server
   *  already ran the transcript. */
  /** Ids of the sends THIS tab made. The server fans each question out to
   *  every tab on the thread as a user_message frame; ours comes back to
   *  our own WebSocket too and must not be painted a second time. */
  var _ownClientMsgIds = [];
  function _newClientMsgId() {
    var id = 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    _ownClientMsgIds.push(id);
    while (_ownClientMsgIds.length > 32) _ownClientMsgIds.shift();
    return id;
  }

  /**
   * Another tab or device started a turn on the thread this tab shows.
   *
   * The user row is what separates one turn's block from the next: the reply
   * binds to the assistant bubble after the LAST user row. A tab that did not
   * send had no row for the new question, so the new turn's frames found the
   * previous turn's bubble as "the open turn", renamed it, and painted into
   * it -- three turns became one block holding three approval cards, until a
   * reload read the transcript (2026-09-26). Same contract as the voice
   * mouth below: add the user row, then let the projector mint the bubble.
   * Returns true when a row was added.
   */
  function beginObservedTurn(data) {
    data = data || {};
    if (data.replay) return false; // history: the reload already has the row
    var cmid = String(data.client_msg_id || '');
    if (cmid && _ownClientMsgIds.indexOf(cmid) !== -1) return false; // our own send
    var text = String(data.content || '');
    if (!text.trim()) return false;
    var tid = String(data.turn_id || '');
    var TVo = _turnView();
    if (tid && TVo && TVo.elFor(tid)) return false; // that turn is already on screen
    appendMessage('user', text);
    scrollToBottom();
    currentMsgEl = null;
    _liveRenderEl = null;
    _turnPainted = false;
    // Frames of this turn that arrive without an id belong to it, not to
    // the previous turn this tab last painted.
    if (tid) _liveTurnId = tid;
    return true;
  }

  function beginVoiceTurn(text) {
    var said = String(text || '').trim();
    if (!said) return;
    appendMessage('user', said);
    scrollToBottomForce(); // a new turn starts; don't leave the reader scrolled up
    currentMsgEl = null;
    _turnPainted = false;
    try {
      disableInput(); // → beginTurn() — a new utterance is a new turn (barge-in included), never resume
    } catch (eBegin) {
      console.error('[KazmaChat] beginVoiceTurn beginTurn failed', eBegin);
    }
  }

  // Expose for inline handlers + agentStore turn lifecycle bridge
  window.KazmaChat = {
    sendMessage: sendMessage,
    newSession: newSession,
    retry: retry,
    destroy: destroyChatMouth,
    toggleArchivedView: toggleArchivedView,
    /** HITL single-writer dispatch (WS store + SSE both feed it). */
    // The document is the entry point, never the card builder: every HITL
    // source (SSE frame, WS frame, gate registry, pending-approvals
    // recovery, hydration) feeds applyTurnEvent, and TurnView is the only
    // thing that builds a card.
    _hitlApproval: ingestHitlApproval,
    hasLiveGate: hasLiveGate,
    markApprovalTimedOut: markApprovalTimedOut,
    hasInlineApprovalCard: hasInlineApprovalCard,
    hitlCardExistsFor: hitlCardExistsFor,
    beginTurn: beginTurn,
    beginVoiceTurn: beginVoiceTurn,
    beginObservedTurn: beginObservedTurn,
    endTurn: endTurn,
    forceEndTurn: forceEndTurn,
    pauseForApproval: pauseForApproval,
    /**
     * Turn usage bridge for the WS path (agentStore done/turn_complete):
     * updates cumulative badges, captures per-turn stats for the workbench
     * summary bar, and refreshes the context badge. Payload keys: tokens,
     * cost, session_tokens, session_cost, duration_ms.
     */
    applyTurnStats: function(data) {
      if (!data) return;
      updateSessionStats(data.tokens, data.cost, data.session_tokens, data.session_cost);
      if (data.tokens != null || data.duration_ms != null) {
        _lastTurnStats = {
          tokens: Number(data.tokens) || 0,
          cost: Number(data.cost) || 0,
          durationMs: Number(data.duration_ms) || 0,
        };
      }
      updateContextBadgeSoon();
    },
    isGenerating: function() { return _isGenerating; },
    /** Dump the turn-lifecycle trace (dispatch/terminal sequence) — the
     *  "what actually happened" for fast-dead turns. */
    diagnostics: dumpDiagnostics,
    applyTurnEvent: applyTurnEvent,
    isRetiredTurn: _isRetiredTurn,
    hasRetiredTurns: function() {
      return _retiredTurnIds.length > 0 || _supersededLive;
    },
    renderTurn: renderTurn,
    turnStatus: function() {
      var doc = _docs[_liveTurnId];
      return doc ? String(doc.status || '') : '';
    },
    refreshSessions: loadSessions,
    refreshSessionsSoon: refreshSessionsSoon,
    getOrCreateSessionId: function() {
      if (!chatSessionId) {
        chatSessionId = generateSessionId();
        persistSessionId();
      }
      return chatSessionId;
    },

    refreshCapacity: refreshCapacity,
    /**
     * Context-integrity S3-1: a compact "earlier context compacted" chip in
     * the transcript. The user should never have to ask why the agent
     * forgot — trim/stub events now arrive as `context_compacted` SSE/WS
     * events and land here. Payload: {detail, dropped_user,
     * dropped_assistant, stubbed_segments}.
     */
    showContextCompacted: function(data) {
      if (!messagesEl) return;
      var detail = (data && data.detail) || 'earlier context was compacted';
      var chip = document.createElement('div');
      chip.className = 'context-compacted-chip';
      chip.title = detail;
      var icon = document.createElement('span');
      icon.className = 'context-compacted-chip-icon';
      icon.textContent = '🗜️';
      var label = document.createElement('span');
      label.textContent = 'Earlier context compacted';
      chip.appendChild(icon);
      chip.appendChild(label);
      var hover = document.createElement('div');
      hover.className = 'context-compacted-chip-detail';
      hover.textContent = detail;
      chip.appendChild(hover);
      messagesEl.appendChild(chip);
      try { messagesEl.scrollTop = messagesEl.scrollHeight; } catch (e) { /* ignore */ }
    },
    paintCapacityReply: function(reply, optTurnId) {
      if (!reply || !messagesEl) return;
      var incoming = String(reply).trim();
      if (!incoming) return;
      var tid = optTurnId || ((_liveTurnId && !_isRetiredTurn(_liveTurnId)) ? _liveTurnId : 'live');
      applyTurnEvent({
        type: 'capacity',
        reply: incoming,
        turn_id: tid,
        source: 'capacity',
      });
    },
    // Telemetry WS hooks — called by agentStore
    logProgress: logProgress,
    finalizeProgress: finalizeProgress,
    noteTurnActivity: noteTurnActivity,
    applyMemoryExplain: applyMemoryExplain,
    resync: function(reason) { _resyncDelivery(reason || 'api'); },
    /**
     * After HITL approve/YOLO: clear token accum so the resumed final answer
     * replaces rather than concatenating onto the pre-approval partial.
     * Keeps the open bubble (HITL card stays visible on the same turn).
     */
    preparePostApprovalTurn: function() {
      noteTurnActivity();
      // Keep currentMsgEl so renderTurn paints into the same turn bubble.
    },
    appendLiveToken: function(content, opts) {
      noteTurnActivity();
      _clearStatusStrip();
      if (!content) return;
      applyTurnEvent({
        type: 'token',
        content: content,
        full: !!(opts && opts.full),
        turn_id: (opts && opts.turn_id) || _liveTurnId,
        model: (opts && opts.model) || '',
        seq: opts && opts.seq,
        source: 'ws',
      });
    },
    setPlan: setPlan,
    appendErrorMessage: function(errMsg) {
      _clearStatusStrip();
      logProgress({ kind: 'error', title: ti('error', 'Error'), detail: String(errMsg || ''), state: 'failed' });
      _pinLiveAssistantBubble();
      var textEl = currentMsgEl.querySelector('.message-text');
      if (textEl) textEl.innerHTML = '<div class="error-message" style="display:flex;align-items:flex-start;gap:6px;">' +
        (window.KazmaIcons ? KazmaIcons.span('alert') : '') + escapeHtml(errMsg) + '</div>';
      // endTurn is invoked by agentStore after graph_error; keep bubble closed.
      finalizeProgress(false);
      currentMsgEl = null;
    },

    // No live-voice ASSISTANT paint hooks here. The voice socket authors
    // the user line via beginVoiceTurn (like Send authors typed text); the
    // journaled turn (Turn Delivery V2) is projected by the same SSE/WS
    // chat path used for typed messages. Reintroducing onUserTranscription
    // / onStreamToken / onStreamDone here would dual-paint the turn from a
    // second author.
  };
})();
