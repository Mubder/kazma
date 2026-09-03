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
  var tokenAccum = '';
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
  var activeTypingEl = null;
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
  var messagesEl, inputEl, sendBtn, typingEl, sessionListEl, searchInputEl;
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
    typingEl = $('thinking-indicator');
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

    // Global Escape key — abort generation from anywhere on the page
    // (not just when the textarea has focus).
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && _isGenerating) {
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

  function _attachJournal(reason) {
    if (activeStream || _attachInFlight) return;
    if (!chatSessionId) return;
    var stream = window.KazmaStream;
    if (!stream || typeof stream.sse !== 'function') return;
    if (_reopenCount >= _REOPEN_MAX) return;
    _reopenCount++;
    _attachInFlight = true;
    var cursor = _lastSeqSeen > 0 ? _lastSeqSeen : 0;
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
  function _defaultAttachCallbacks(epoch) {
    function _mine() { return epoch === _sseEpoch; }
    return {
      onToken: function(data) {
        if (!_mine()) return;
        _noteSeq();
        _taskCardEvent({ t: 'token' });
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
        _taskCardEvent({
          t: 'tool',
          name: data.tool_name || 'tool',
          detail: _tcArgSummary(data.inputs),
        });
        var inputs = data.inputs;
        if (typeof inputs === 'object') {
          try { inputs = JSON.stringify(inputs); } catch (e) { inputs = String(inputs); }
        }
        logProgress({
          kind: 'tool',
          title: data.tool_name || 'tool',
          detail: String(inputs || ''),
          state: 'running',
        });
      },
      onToolResult: function(data) {
        if (!_mine()) return;
        _taskCardEvent({ t: 'tool_end', name: data.tool_name || 'tool' });
        logProgress({
          kind: 'tool',
          title: data.tool_name || 'tool',
          detail: String(data.result || ''),
          state: 'done',
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
        _taskCardEvent({
          t: 'hb',
          phase: (data && data.phase) || '',
          current: (data && data.current) || '',
          step: (data && data.step) || 0,
          elapsed_s: (data && data.elapsed_s) || 0,
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
        applyTurnEvent({
          type: 'hitl',
          state: 'pending',
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
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
        applyTurnEvent({
          type: 'hitl',
          state: st,
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
          turn_id: (data && data.turn_id) || _liveTurnId,
          source: 'sse',
        });
      },
      onDone: function(data) {
        if (!_mine()) return;
        activeStream = null;
        if (data && data.content) {
          applyTurnEvent({
            type: 'done',
            content: data.content,
            seq: data.seq,
            turn_id: data.turn_id || _liveTurnId,
            interrupted: !!(data && data.interrupted),
            source: 'done',
          });
        }
        if (hasInlineApprovalCard() || _awaitingApproval) {
          refreshSessionsSoon();
        } else {
          endTurn();
        }
        if (!data && !_awaitingApproval) {
          setTimeout(function() { _resyncDelivery('sse-truncated'); }, 400);
        }
      },
      onError: function() {
        if (!_mine()) return;
        activeStream = null;
        if (_awaitingApproval) return;
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
      var generating = !!status.generating;
      var paused = !!status.paused;
      _serverGenerating = generating;
      _serverPaused = paused;
      _serverHitl = (status.hitl && typeof status.hitl === 'object') ? status.hitl : null;
      _serverGates = Array.isArray(status.gates) ? status.gates : [];
      _serverGatesAuth = !!status.gates_authoritative;
      _serverThreadId = String(status.thread_id || '');
      var lastMsg = messages.length ? messages[messages.length - 1] : null;
      var pendingGate = false;
      if (_serverGates && _serverGates.length) {
        for (var gi = 0; gi < _serverGates.length; gi++) {
          if (String(_serverGates[gi].state || '') === 'pending') { pendingGate = true; break; }
        }
      }
      var hitlPending = !!(_serverHitl && String(_serverHitl.gate || '') === 'pending');
      var liveHitl = pendingGate || hitlPending || paused;

      // Registry answered authoritatively and the thread is idle → stamp
      // fossil live-button cards resolved (see _reconcileHitlCardsWithGates).
      if (!generating && !liveHitl) _reconcileHitlCardsWithGates();

      // Server idle (no live gate) after /abort or restart: do not keep the
      // composer locked on a fossil pending part. The next prompt is a turn.
      if (!generating && !liveHitl && _awaitingApproval) {
        _releaseHitlComposer('resync-idle');
      }
      if (liveHitl && !_awaitingApproval) pauseForApproval(_serverHitl);
      if (liveHitl && !hasInlineApprovalCard()) {
        _paintLiveGates();
        setTimeout(recoverMissedApproval, 0);
      }

      // Still running server-side → keep waiting honestly AND re-attach a
      // live SSE stream from the journal cursor — but only when the stream
      // is genuinely DEAD. Aborting a healthy stream on every focus/visibility
      // trigger churned connections for no gain.
      if (generating || liveHitl) {
        if (activeStream) {
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
        _awaitingReply = true;
        noteTurnActivity();
        try {
          _setStatusStrip(paused
            ? ti('waiting_approval', 'Waiting for approval…')
            : ti('thinking', 'Kazma is thinking…'));
        } catch (e2) { /* ignore */ }
        if (_reopenSseRef) {
          try { _reopenSseRef('resync-' + (reason || '?')); } catch (e3) { /* ignore */ }
        }
        return;
      }

      // Server idle with a durable assistant answer → paint server truth,
      // unconditionally (applyFinal replaces the open-turn bubble).
      // EXCEPT while an approval is pending: the graph is paused on HITL,
      // so "idle + last durable reply" is the PREVIOUS turn's answer —
      // painting it over the live bubble swapped the visible interim text
      // for a completely different (older) message on every app-switch
      // (2026-08-27). The paused bubble + approval card are already correct.
      if (hasInlineApprovalCard() || _awaitingApproval) return;

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
        });
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
   *   instead of minting a bubble. Since the Live Task Card took the live
   *   view out of the bubble, a progress-only frame that minted one left a
   *   bare avatar + timestamp + reaction buttons with nothing inside it
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
  var _awaitingApproval = false;
  var _serverGenerating = false;
  var _serverPaused = false;
  var _serverHitl = null;
  var _serverGates = [];
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
    tokenAccum = '';
  }

  function _resetSessionTurnState() {
    _docs = {};
    _liveTurnId = '';
    _retiredTurnIds = [];
    _supersededLive = false;
    _serverGenerating = false;
    _serverPaused = false;
    _serverHitl = null;
    _serverGates = [];
    _serverGatesAuth = false;
    _serverThreadId = '';
    _lastInterruptedThreadId = '';
    _awaitingApproval = false;
    _clearStoreApproval();
    // Switching sessions is not the END of a turn — it is the ABSENCE of
    // one. forceEndTurn's 'done' frame left the card on screen for its
    // 1.6s retire animation, so a brand-new empty session flashed a
    // "Done" task card for a turn that never happened (2026-09-03).
    _taskCardEvent({ t: 'reset' });
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
      if (!_isGenerating || _awaitingApproval) return;
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

  /**
   * ── Live Task Card ──────────────────────────────────────────────────
   * The ONE turn-state surface, merged from the retired status strip and
   * the live in-bubble CoT panel. Single writer: _taskCardEvent — every
   * other helper (_setStatusStrip, SSE/WS callbacks, pauseForApproval,
   * endTurn) dispatches through it, so two surfaces can never disagree
   * again (the frozen-thinking / blank-while-paused bug class).
   *
   * Header — phase icon + WHAT it is doing + elapsed + step:
   *   ⚙ Running file_search "auth middleware" · 42s · 1:12 in this tool · step 23
   *   🧠 Thinking · 12s
   *   ⏳ Awaiting your approval · auto-denies in 3:12          [Review ↑]
   *   ⚠ no signal 24s — checking…            (journal gap → resync w/ backoff)
   *   ⚠ not responding                       (backoff exhausted)  [Retry]
   *   ✓ Done · 12 steps · 3 tools · 18.4s · 4.2k tokens
   * The turn's own actions live here too: Stop while running, Review to
   * jump to the approval card, Retry once liveness recovery gives up.
   *
   * Body (remembers open/closed across turns and reloads): compact step
   * list from the TurnDocument, reasoning clamped to 2 lines, 50-row cap,
   * tail-pinned unless the reader scrolled up.
   *
   * Lifecycle: docked here while the turn runs; on done the summary
   * finalizes into the transcript bubble (existing restored-workbench
   * path) and the card unmounts.
   *
   * a11y: the toggle's accessible name is STATIC ("Task details") — its
   * live text is aria-hidden, because a per-second header rewrite made
   * screen readers re-announce the whole control every tick. Phase,
   * countdown and liveness go to the role="status" region at coarse
   * thresholds instead.
   */
  // >>> LIVE_TASK_CARD_BEGIN — self-contained state machine. tests/js/
  // test_live_task_card.js extracts this block verbatim and drives it on a
  // fake clock + fake DOM; only the stubs it declares may be referenced
  // from outside these markers.
  var _TC_OPEN_KEY = 'kazma.taskcard.open';
  var _TC_STALL_MS = 20000;        // heartbeats land every ~8-10s
  var _TC_STALL_RETRY_MS = 30000;  // backoff between resync attempts
  var _TC_STALL_MAX_TRIES = 3;     // then stop retrying and say so
  var _TC_STEP_CAP = 50;
  var _TC_TOOL_PHASE_MIN_S = 15;   // below this, "in this tool" is noise

  var _tc = {
    el: null, header: null, toggle: null, phaseEl: null, label: null,
    meta: null, stallEl: null, chevron: null, body: null, stepsEl: null,
    liveEl: null, stopBtn: null, jumpBtn: null, retryBtn: null,
    visible: false, phase: 'idle', current: '', detail: '', step: 0,
    turnStart: 0, elapsedS: 0, elapsedFloor: 0,
    srvElapsed: 0, srvElapsedAt: 0,
    phaseStart: 0, lastSignal: 0, deadline: 0,
    planTotal: 0, planDone: 0,
    stalled: false, stallTries: 0, nextResyncAt: 0, dead: false,
    open: false, tickTimer: null, doneTimer: null,
    textOverride: '', summary: '', emptyTurn: false,
    announced: '', stepsHtml: '',
    // Label hysteresis (2026-09-03): tool→think→tool flips made the header
    // churn on every event of a multi-step turn. A non-escalating label
    // change is only accepted after this long; escalations always apply.
    labelShown: '', labelShownAt: 0, phaseShown: '',
  };
  var _TC_LABEL_MIN_MS = 1200;

  function _tcMount() {
    if (_tc.el) return _tc.el;
    _tc.el = document.getElementById('live-task-card');
    if (!_tc.el) return null;
    function q(sel) { return _tc.el.querySelector(sel); }
    _tc.header = q('.live-task-header');
    _tc.toggle = q('.live-task-toggle');
    _tc.phaseEl = q('.live-task-phase');
    _tc.label = q('.live-task-label');
    _tc.meta = q('.live-task-meta');
    _tc.stallEl = q('.live-task-stall');
    _tc.chevron = q('.live-task-chevron');
    _tc.body = q('.live-task-body');
    _tc.stepsEl = q('.live-task-steps');
    _tc.liveEl = q('.live-task-live');
    _tc.stopBtn = q('.live-task-stop');
    _tc.jumpBtn = q('.live-task-jump');
    _tc.retryBtn = q('.live-task-retry');
    // Readers who want the steps open want them open on the NEXT turn too.
    try {
      _tc.open = window.localStorage.getItem(_TC_OPEN_KEY) === '1';
    } catch (eLs) { /* private mode / storage disabled */ }
    if (_tc.toggle) {
      _tc.toggle.addEventListener('click', function () {
        _tc.open = !_tc.open;
        try {
          window.localStorage.setItem(_TC_OPEN_KEY, _tc.open ? '1' : '0');
        } catch (eSet) { /* ignore */ }
        if (_tc.open) _tcStepsFromDoc();
        _tcRender();
      });
    }
    if (_tc.stopBtn) {
      _tc.stopBtn.addEventListener('click', function () {
        try { abortGeneration(); } catch (eAb) { /* ignore */ }
      });
    }
    if (_tc.jumpBtn) _tc.jumpBtn.addEventListener('click', _tcJumpToApproval);
    if (_tc.retryBtn) {
      _tc.retryBtn.addEventListener('click', function () {
        _tc.stallTries = 0;
        _tc.nextResyncAt = 0;
        _tc.dead = false;
        try { _resyncDelivery('stall-retry'); } catch (eR) { /* ignore */ }
        _tcRender();
      });
    }
    return _tc.el;
  }

  /** The countdown says "auto-denies in 3:12" — the buttons are hundreds of
   *  pixels up the transcript. Put them one click away. */
  function _tcJumpToApproval() {
    if (!messagesEl) return;
    var cards = messagesEl.querySelectorAll('.hitl-approval-card');
    for (var i = cards.length - 1; i >= 0; i--) {
      if (!cards[i].querySelector('button:not([disabled])')) continue;
      var card = cards[i];
      try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      catch (eSc) { try { card.scrollIntoView(); } catch (eS2) { /* ignore */ } }
      card.classList.add('is-flash');
      setTimeout(function () { card.classList.remove('is-flash'); }, 1400);
      return;
    }
  }

  function _tcPhaseIcon(phase) {
    if (phase === 'tool') return '⚙';
    if (phase === 'awaiting') return '⏳';
    if (phase === 'resuming') return '↻';
    // A turn that delivered nothing must not wear a checkmark.
    if (phase === 'done') return _tc.emptyTurn ? '⚠' : '✓';
    if (phase === 'error') return '✕';
    return '🧠'; // llm / supervisor / thinking
  }

  /**
   * Structural phases outrank the free-text override. The override used to
   * win unconditionally, so beginTurn's "Kazma is thinking…" painted itself
   * over "Resuming after approval" one line after the resume set it — the
   * card read "↻ Kazma is thinking…" through every approve.
   */
  function _tcPhaseLabel() {
    switch (_tc.phase) {
      case 'tool': {
        var t = ti('task_running_tool', 'Running') + ' ' + (_tc.current || 'tool');
        return _tc.detail ? t + ' ' + _tc.detail : t;
      }
      case 'awaiting': return ti('task_awaiting', 'Awaiting your approval');
      case 'resuming': return ti('task_resuming', 'Resuming after approval');
      case 'error': return _tc.textOverride || ti('task_error', 'Turn failed');
      case 'done':
        if (_tc.textOverride) return _tc.textOverride;
        return _tc.emptyTurn
          ? ti('task_no_reply', 'No reply received')
          : ti('task_done', 'Done');
      default:
        if (_tc.textOverride) return _tc.textOverride;
        return _tc.phase === 'llm'
          ? ti('task_thinking', 'Thinking')
          : ti('thinking', 'Kazma is thinking…');
    }
  }

  function _tcFmtMMSS(s) {
    s = Math.max(0, Math.floor(s));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ':' + (r < 10 ? '0' : '') + r;
  }

  /**
   * Server heartbeats are authoritative; the local clock fills the gaps.
   * The first version had this backwards — it only recomputed while signals
   * were FRESH, so the timer froze at exactly the moment you are staring at
   * it wondering whether the turn hung. A resumed run restarts the server's
   * own clock at zero, so take the max: the displayed turn time never goes
   * backwards.
   */
  function _tcElapsed(now) {
    var local = _tc.turnStart ? (now - _tc.turnStart) / 1000 : 0;
    var srv = _tc.srvElapsedAt
      ? _tc.srvElapsed + (now - _tc.srvElapsedAt) / 1000
      : 0;
    // Monotonic within a turn: whichever clock is further ahead wins, and
    // the reading never moves backwards. Both can regress on their own — the
    // server's restarts at zero on a resumed run, the local one starts late
    // when this tab attached to a turn already in flight.
    _tc.elapsedFloor = Math.max(local, srv, _tc.elapsedFloor || 0);
    return _tc.elapsedFloor;
  }

  function _tcRender() {
    if (!_tcMount() || !_tc.visible) return;
    var now = Date.now();
    var terminal = _tc.phase === 'done' || _tc.phase === 'error';
    if (!terminal) _tc.elapsedS = _tcElapsed(now);
    // Label hysteresis: show label + icon as ONE accepted snapshot so a
    // fast tool batch cannot strobe the header (Thinking ↔ Running X ↔
    // Thinking ↔ Running Y …). Escalations cut through immediately.
    var escalated = _tc.phase === 'awaiting' || _tc.stalled || _tc.dead || terminal;
    var lbl = _tcPhaseLabel();
    if (lbl !== _tc.labelShown) {
      if (escalated || !_tc.labelShownAt || now - _tc.labelShownAt >= _TC_LABEL_MIN_MS) {
        _tc.labelShown = lbl;
        _tc.phaseShown = _tc.phase;
        _tc.labelShownAt = now;
      }
    } else {
      _tc.phaseShown = escalated ? _tc.phase : (_tc.phaseShown || _tc.phase);
    }
    if (_tc.phaseEl) _tc.phaseEl.textContent = _tcPhaseIcon(_tc.phaseShown || _tc.phase);
    if (_tc.label) _tc.label.textContent = _tc.labelShown || lbl;

    var bits = [];
    if (terminal) {
      if (_tc.summary) bits.push(_tc.summary);
    } else if (_tc.phase === 'awaiting' && _tc.deadline) {
      var left = Math.floor(_tc.deadline - now / 1000);
      bits.push('⏳ ' + (left > 0
        ? ti('task_auto_deny_in', 'auto-denies in') + ' ' + _tcFmtMMSS(left)
        : ti('approval_expired_short', 'expired')));
    } else {
      if (_tc.elapsedS > 2) bits.push(_tcFmtMMSS(_tc.elapsedS));
      // A tool that has been running three minutes is the thing worth
      // seeing; total turn time hides it behind everything that came before.
      var inPhase = _tc.phaseStart ? (now - _tc.phaseStart) / 1000 : 0;
      if (_tc.phase === 'tool' && inPhase > _TC_TOOL_PHASE_MIN_S) {
        bits.push(tiFmt('task_in_tool', '{d} in this tool', { d: _tcFmtMMSS(inPhase) }));
      }
      if (_tc.step > 0) bits.push(ti('task_step', 'step') + ' ' + _tc.step);
      if (_tc.planTotal > 0) {
        bits.push(ti('task_plan', 'plan') + ' ' + _tc.planDone + '/' + _tc.planTotal);
      }
    }
    if (_tc.meta) _tc.meta.textContent = bits.join(' · ');

    if (_tc.stallEl) {
      _tc.stallEl.hidden = !_tc.stalled;
      if (_tc.stalled) {
        _tc.stallEl.textContent = '⚠ ' + (_tc.dead
          ? ti('task_not_responding', 'not responding')
          : ti('task_no_signal', 'no signal') + ' ' +
            Math.floor((now - _tc.lastSignal) / 1000) + 's — ' +
            ti('task_checking', 'checking…'));
      }
    }
    if (_tc.stopBtn) _tc.stopBtn.hidden = terminal || _tc.phase === 'awaiting';
    if (_tc.jumpBtn) _tc.jumpBtn.hidden = _tc.phase !== 'awaiting';
    if (_tc.retryBtn) _tc.retryBtn.hidden = !_tc.dead;

    if (_tc.chevron) _tc.chevron.textContent = _tc.open ? '▾' : '▸';
    if (_tc.toggle) _tc.toggle.setAttribute('aria-expanded', _tc.open ? 'true' : 'false');
    if (_tc.body) _tc.body.hidden = !_tc.open;
    _tc.el.className = 'live-task-card' +
      (_tc.phase === 'awaiting' ? ' is-awaiting' : '') +
      (_tc.stalled ? ' is-stalled' : '') +
      (_tc.dead ? ' is-dead' : '') +
      (_tc.phase === 'done' ? ' is-done' : '') +
      (_tc.phase === 'error' ? ' is-error' : '') +
      (terminal && _tc.emptyTurn ? ' is-empty' : '') +
      (_tc.open ? ' is-open' : '');
    _tc.el.hidden = false;
    _tcAnnounce(now, terminal);
  }

  /**
   * Screen-reader channel. The header's visible text is aria-hidden and the
   * toggle's name is fixed, so nothing here fires on the 1s tick — only on
   * a phase change, a coarse countdown threshold, a liveness change, or the
   * terminal summary.
   */
  function _tcAnnounce(now, terminal) {
    if (!_tc.liveEl) return;
    var say = _tcPhaseLabel();
    if (_tc.phase === 'awaiting' && _tc.deadline) {
      var left = Math.floor(_tc.deadline - now / 1000);
      var bucket = left <= 0 ? 0
        : (left <= 10 ? 10 : (left <= 30 ? 30 : (left <= 60 ? 60 : -1)));
      if (bucket === 0) say += ' — ' + ti('approval_expired_short', 'expired');
      else if (bucket > 0) {
        say += ' — ' + tiFmt('auto_deny_seconds', 'auto-denies in {n} seconds',
          { n: bucket });
      }
    }
    if (_tc.stalled) {
      say += ' — ' + (_tc.dead
        ? ti('task_not_responding', 'not responding')
        : ti('task_no_signal', 'no signal'));
    }
    if (terminal && _tc.summary) say += ' — ' + _tc.summary;
    if (say === _tc.announced) return;
    _tc.announced = say;
    _tc.liveEl.textContent = say;
  }

  function _tcTick() {
    if (!_tc.visible) return;
    var now = Date.now();
    // Stalled honesty: heartbeats arrive every ~8-10s during silence, so a
    // _TC_STALL_MS gap means the JOURNAL went quiet — surface it and try to
    // reconcile. Recovery is a BACKOFF, not a one-shot: the first version
    // latched after a single resync, so a genuinely dead stream sat amber
    // forever with nothing else attempted and no way to say so.
    var watched = _tc.phase !== 'awaiting' && !_tcIsTerminal();
    if (watched && _tc.lastSignal && now - _tc.lastSignal > _TC_STALL_MS) {
      if (!_tc.stalled) {
        _tc.stalled = true;
        _tc.stallTries = 0;
        _tc.nextResyncAt = 0;
      }
      if (!_tc.dead && now >= _tc.nextResyncAt) {
        _tc.stallTries += 1;
        _tc.nextResyncAt = now + _TC_STALL_RETRY_MS;
        if (_tc.stallTries > _TC_STALL_MAX_TRIES) _tc.dead = true;
        else { try { _resyncDelivery('heartbeat-gap'); } catch (eR) { /* ignore */ } }
      }
    } else if (_tc.stalled) {
      _tc.stalled = false;
      _tc.dead = false;
      _tc.stallTries = 0;
      _tc.nextResyncAt = 0;
    }
    _tcRender();
  }

  function _tcIsTerminal() {
    return _tc.phase === 'done' || _tc.phase === 'error';
  }

  function _tcStepsFromDoc() {
    if (!_tc.stepsEl) return;
    var doc = _docs[_liveTurnId] || null;
    var rows = (window.KazmaTurnDocument && doc && KazmaTurnDocument.activityOf)
      ? KazmaTurnDocument.activityOf(doc.parts || [])
      : [];
    // An empty READ is not an empty turn. _liveTurnId is retired and _docs is
    // dropped around the end of a turn, so blanking the body here wiped the
    // steps out from under anyone reading them the moment the turn finished.
    // Clearing belongs to the events that know a turn STARTED or a session
    // CHANGED — 'begin' and 'reset' both empty the list explicitly.
    if (!rows.length) return;
    // Newest last (chronological); cap _TC_STEP_CAP live rows.
    rows = rows.slice(-_TC_STEP_CAP);
    var html = '';
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i] || {};
      var cls = 'live-task-step kind-' + (r.kind || 'status') +
        ' state-' + (r.state || 'done');
      html += '<li class="' + cls + '" title="' + escapeHtml(truncateStr(String(r.detail || ''), 300)) + '">' +
        '<span class="live-task-step-title">' + escapeHtml(truncateStr(String(r.title || ''), 120)) + '</span>' +
        '<span class="live-task-step-detail">' + escapeHtml(truncateStr(String(r.detail || ''), 400)) + '</span>' +
        '</li>';
    }
    // Re-assigning identical markup still tears the subtree down and costs a
    // reflow (same lesson as the live-token paint throttle) AND throws away
    // the reader's scroll position.
    if (html === _tc.stepsHtml) return;
    _tc.stepsHtml = html;
    var el = _tc.stepsEl;
    var pinned = (el.scrollHeight - el.scrollTop - el.clientHeight) < 24;
    el.innerHTML = html;
    // Tail-pinned by default; a reader who scrolled up keeps their place.
    if (pinned) el.scrollTop = el.scrollHeight;
  }

  /**
   * The single writer. Events:
   *  begin | token | tool{name,detail} | tool_end{name}
   *  hb{phase,current,detail,step,elapsed_s} | status{status,message}
   *  text{msg} | plan{total,done} | approval{deadline} | resuming
   *  done{ok,summary,msg} | error{msg} | doc
   */
  function _taskCardEvent(ev) {
    ev = ev || {};
    if (!_tcMount()) return;
    var now = Date.now();

    // A session change is the ABSENCE of a turn, not the end of one: unmount
    // now, with no terminal frame and no retire animation.
    if (ev.t === 'reset') {
      if (_tc.doneTimer) { clearTimeout(_tc.doneTimer); _tc.doneTimer = null; }
      if (_tc.tickTimer) { clearInterval(_tc.tickTimer); _tc.tickTimer = null; }
      _tc.visible = false;
      _tc.phase = 'idle';
      _tc.current = '';
      _tc.detail = '';
      _tc.step = 0;
      _tc.elapsedS = 0;
      _tc.elapsedFloor = 0;
      _tc.turnStart = 0;
      _tc.phaseStart = 0;
      _tc.srvElapsed = 0;
      _tc.srvElapsedAt = 0;
      _tc.lastSignal = 0;
      _tc.deadline = 0;
      _tc.planTotal = 0;
      _tc.planDone = 0;
      _tc.stalled = false;
      _tc.dead = false;
      _tc.stallTries = 0;
      _tc.nextResyncAt = 0;
      _tc.textOverride = '';
      _tc.summary = '';
      _tc.emptyTurn = false;
      _tc.announced = '';
      _tc.stepsHtml = '';
      if (_tc.stepsEl) _tc.stepsEl.innerHTML = '';
      if (_tc.liveEl) _tc.liveEl.textContent = '';
      if (_tc.el) _tc.el.hidden = true;
      return;
    }

    if (ev.t === 'begin') {
      _tc.phase = 'idle';
      _tc.current = '';
      _tc.detail = '';
      _tc.step = 0;
      _tc.elapsedS = 0;
      _tc.elapsedFloor = 0;
      _tc.turnStart = now;
      _tc.phaseStart = now;
      _tc.srvElapsed = 0;
      _tc.srvElapsedAt = 0;
      _tc.deadline = 0;
      _tc.planTotal = 0;
      _tc.planDone = 0;
      _tc.stalled = false;
      _tc.dead = false;
      _tc.stallTries = 0;
      _tc.nextResyncAt = 0;
      _tc.textOverride = '';
      _tc.summary = '';
      _tc.emptyTurn = false;
      _tc.announced = '';
      _tc.stepsHtml = '';
      _tc.labelShown = '';
      _tc.phaseShown = '';
      _tc.labelShownAt = 0;
      if (_tc.stepsEl) _tc.stepsEl.innerHTML = '';
      _tcWake(now);
      _tcSetPhase('llm', '', '', now);
    } else if (ev.t === 'token' || ev.t === 'tool' || ev.t === 'tool_end' ||
               ev.t === 'status' || ev.t === 'hb' || ev.t === 'approval' ||
               ev.t === 'resuming') {
      // Every liveness event restores the card. `approval` and `resuming`
      // used to skip this: a pending hide from the previous terminal frame
      // stayed armed and blanked the card mid-approve, and a resume never
      // restarted the tick timer (frozen elapsed, dead stall detection).
      _tcWake(now);
    }

    switch (ev.t) {
      case 'tool':
        _tcSetPhase('tool', ev.name || 'tool', ev.detail, now);
        _tc.step += 1;
        _tc.textOverride = '';
        break;
      case 'tool_end':
        if (_tc.phase === 'tool') _tcSetPhase('supervisor', '', '', now);
        break;
      case 'token':
        if (_tc.phase !== 'awaiting') _tcSetPhase('llm', '', '', now);
        break;
      case 'hb':
        if (ev.phase) _tcSetPhase(String(ev.phase), ev.current, ev.detail, now);
        if (ev.step) _tc.step = Math.max(_tc.step, parseInt(ev.step, 10) || 0);
        if (ev.elapsed_s) {
          _tc.srvElapsed = Number(ev.elapsed_s) || 0;
          _tc.srvElapsedAt = now;
        }
        if (_tc.phase !== 'awaiting') _tc.textOverride = '';
        break;
      case 'status': {
        var st = String(ev.status || '');
        if (st === 'synthesizing') {
          _tcSetPhase('llm', '', '', now);
          _tc.textOverride = ti('task_writing', 'Writing the reply…');
        } else if (st === 'routing_node') {
          _tc.textOverride = String(ev.message || '');
        } else if (st === 'paused_for_approval') {
          /* the approval event owns this */
        } else if (ev.message) {
          _tc.textOverride = String(ev.message);
        }
        break;
      }
      case 'text':
        // An empty msg CLEARS. `if (ev.msg)` let _clearStatusStrip leave a
        // stale override ("Writing the reply…") alive under a later phase.
        _tc.textOverride = String(ev.msg || '');
        break;
      case 'plan':
        _tc.planTotal = parseInt(ev.total, 10) || 0;
        _tc.planDone = parseInt(ev.done, 10) || 0;
        break;
      case 'approval':
        _tcSetPhase('awaiting', '', '', now);
        _tc.deadline = Number(ev.deadline || 0);
        _tc.textOverride = '';
        break;
      case 'resuming':
        _tcSetPhase('resuming', '', '', now);
        _tc.deadline = 0;
        _tc.textOverride = '';
        break;
      case 'doc':
        // Cheap when collapsed: the body is not on screen, so skip the
        // rebuild entirely — the toggle builds it on open.
        if (_tc.visible && _tc.open) _tcStepsFromDoc();
        return;
      case 'done':
      case 'error':
        _tc.phase = ev.t === 'error' ? 'error' : 'done';
        _tc.phaseStart = now;
        _tc.deadline = 0;
        _tc.stalled = false;
        _tc.dead = false;
        _tc.textOverride = ev.msg ? String(ev.msg) : '';
        _tc.summary = String(ev.summary || '');
        // `ok: false` is an explicit "the turn delivered nothing" from
        // endTurn — undefined (abort / forceEndTurn) is not a failure.
        _tc.emptyTurn = ev.ok === false;
        if (_tc.tickTimer) { clearInterval(_tc.tickTimer); _tc.tickTimer = null; }
        if (!_tc.visible) return;
        _tcRender();
        // Bubble carries the durable summary now — card retires shortly.
        if (_tc.doneTimer) clearTimeout(_tc.doneTimer);
        _tc.doneTimer = setTimeout(function () {
          _tc.visible = false;
          if (_tc.el) _tc.el.hidden = true;
          _tc.doneTimer = null;
        }, ev.t === 'error' ? 4000 : (_tc.summary ? 3200 : 1600));
        return;
      default:
        break;
    }
    _tcRender();
    if (_tc.open) _tcStepsFromDoc();
  }

  /** Liveness restore, shared by every event that proves the turn is alive. */
  function _tcWake(now) {
    _tc.visible = true;
    _tc.lastSignal = now;
    if (!_tc.turnStart) _tc.turnStart = now;
    if (!_tc.phaseStart) _tc.phaseStart = now;
    // A frame IS the signal — clear the warning here, not a tick later, or
    // the same render that shows the new phase also shows "no signal 0s".
    if (_tc.stalled) {
      _tc.stalled = false;
      _tc.dead = false;
      _tc.stallTries = 0;
      _tc.nextResyncAt = 0;
    }
    // A hide armed by a previous terminal frame must never fire onto a live
    // card — this is what blanked the card the instant you hit Approve.
    if (_tc.doneTimer) { clearTimeout(_tc.doneTimer); _tc.doneTimer = null; }
    if (!_tc.tickTimer) _tc.tickTimer = setInterval(_tcTick, 1000);
  }

  /** Phase changes restart the phase-scoped clock ("1:12 in this tool"). */
  function _tcSetPhase(phase, current, detail, now) {
    if (phase && phase !== _tc.phase) {
      _tc.phase = phase;
      _tc.phaseStart = now;
    }
    if (current !== undefined) _tc.current = String(current || '');
    if (detail !== undefined) _tc.detail = String(detail || '');
  }
  // <<< LIVE_TASK_CARD_END

  /**
   * "12 steps · 3 tools · 18.4s · 4.2k tokens" for the card's terminal
   * frame. The shape of what just happened used to be thrown away — the
   * card flashed a bare "Done" and the counts died with the live panel.
   */
  function _tcTurnSummary() {
    var bits = [];
    if (_progressStepCount > 0) {
      bits.push(_progressStepCount + ' ' +
        (_progressStepCount === 1 ? ti('step', 'step') : ti('steps', 'steps')));
    }
    if (_progressToolCount > 0) {
      bits.push(_progressToolCount + ' ' +
        (_progressToolCount === 1 ? ti('task_tool', 'tool') : ti('task_tools', 'tools')));
    }
    var s = _lastTurnStats || null;
    if (s && s.durationMs > 0 && KS.formatDuration) bits.push(KS.formatDuration(s.durationMs));
    else if (_tc.elapsedS > 2) bits.push(_tcFmtMMSS(_tc.elapsedS));
    if (s && s.tokens > 0 && KS.formatTokens) {
      bits.push(KS.formatTokens(s.tokens) + ' ' + ti('tokens', 'tokens'));
    }
    if (s && s.cost > 0 && KS.formatCost) bits.push(KS.formatCost(s.cost));
    return bits.join(' · ');
  }

  /**
   * A compact "what is it doing this to" for the card header: the first
   * meaningful scalar out of a tool's arguments. "Running file_search" tells
   * you far less than 'Running file_search "auth middleware"'.
   */
  var _TC_ARG_SKIP = { session_id: 1, thread_id: 1, workspace_id: 1, turn_id: 1, id: 1 };
  var _TC_ARG_PREFER = ['query', 'q', 'path', 'file', 'file_path', 'url', 'name',
    'command', 'cmd', 'pattern', 'text', 'prompt', 'title', 'to'];
  function _tcArgSummary(inputs) {
    var obj = inputs;
    if (typeof obj === 'string') {
      var s = obj.trim();
      if (!s) return '';
      if (s.charAt(0) === '{' || s.charAt(0) === '[') {
        try { obj = JSON.parse(s); } catch (eP) { return _tcQuote(s); }
      } else {
        return _tcQuote(s);
      }
    }
    if (!obj || typeof obj !== 'object') return '';
    if (Array.isArray(obj)) return obj.length ? _tcArgSummary(obj[0]) : '';
    var k, i;
    for (i = 0; i < _TC_ARG_PREFER.length; i++) {
      k = _TC_ARG_PREFER[i];
      if (typeof obj[k] === 'string' && obj[k].trim()) return _tcQuote(obj[k]);
      if (typeof obj[k] === 'number') return _tcQuote(String(obj[k]));
    }
    var keys = Object.keys(obj);
    for (i = 0; i < keys.length; i++) {
      k = keys[i];
      if (_TC_ARG_SKIP[k]) continue;
      var v = obj[k];
      if (typeof v === 'string' && v.trim()) return _tcQuote(v);
      if (typeof v === 'number' || typeof v === 'boolean') return _tcQuote(String(v));
    }
    return '';
  }
  function _tcQuote(s) {
    s = String(s).replace(/\s+/g, ' ').trim();
    if (!s) return '';
    return '“' + truncateStr(s, 48) + '”';
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
    _taskCardEvent({ t: 'text', msg: msg });
    // Store flag kept for WS liveness logic; it no longer owns any DOM.
    _setStoreThinking(true, msg);
  }
  function _clearStatusStrip() {
    _taskCardEvent({ t: 'text', msg: '' });
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

  function _isUserBubble(el) {
    return !!(el && el.classList && el.classList.contains('message-user'));
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
    var bubbles = messagesEl.querySelectorAll('.message-assistant');
    for (var b = 0; b < bubbles.length; b++) _rescueTurnDom(bubbles[b]);
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

  /**
   * @param {{resume?: boolean}} [opts] `resume: true` continues the turn that
   *   is already on screen (HITL approve / deny) instead of starting a new
   *   one. A resume MUST NOT reset the workbench: the panel below belongs to
   *   this same turn and holds every step that led up to the approval card.
   *   Wiping it made the whole CoT vanish the instant you clicked Approve,
   *   leaving a lone "Thinking…" row above the answer.
   */
  function beginTurn(opts) {
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
    _taskCardEvent(resume ? { t: 'resuming' } : { t: 'begin' });
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
      tokenAccum = '';
      _liveTurnId = '';
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
    return copy;
  }

  function endTurn() {
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
    _taskCardEvent({ t: 'done', ok: !!_turnPainted, summary: _tcTurnSummary() });
    if (activeTypingEl && KS.hideTyping) {
      KS.hideTyping(activeTypingEl);
    }
    activeTypingEl = null;
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
    tokenAccum = '';
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
    _taskCardEvent({ t: 'done' });
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
    _isGenerating = false;
    _awaitingApproval = true;
    // This tab saw the interrupt. Do NOT treat this flag as "already approved"
    // — _paintHitlFromDoc used to stamp "Approved — running…" on first paint
    // because pauseForApproval runs before the pending card is created.
    _serverPaused = true;
    if (activeTypingEl && KS.hideTyping) KS.hideTyping(activeTypingEl);
    activeTypingEl = null;
    _clearStatusStrip();
    // The card is the ONE surface while paused: it shows the awaiting
    // phase + the watchdog countdown (pause used to blank the strip and
    // leave dead air when the inline card was late — 2026-09-03).
    _taskCardEvent({ t: 'approval', deadline: _hitlDeadlineOf(data) });
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
        if (hasInlineApprovalCard()) return;
        if (!window.Alpine || !Alpine.store || !Alpine.store('agent')) return;
        var store = Alpine.store('agent');
        if (store.pendingApproval) return;
        // Reply already painted and the SSE fetch is gone — Stop was stuck
        // because WS still had isThinking from a leftover status frame.
        var sseDead = !activeStream;
        var replyPainted = !!(tokenAccum && String(tokenAccum).trim());
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
              providers.forEach(function(p) {
                if (!p.enabled) return;
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
    // Ensure dropdown reflects persisted value
    if (selectedModel) {
      modelSelectorEl.value = selectedModel;
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
          if (!activeStream) {
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
      if (!hasInlineApprovalCard()) {
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
      tokenAccum = '';
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
    tokenAccum = '';
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
    }

    // Status strip is store-owned now; beginTurn arms it below.
    activeTypingEl = typingEl;

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
      if (activeStream) {
        try { activeStream.abort(); } catch (e) { /* already dead */ }
      }
      var body = {
        message: content,
        session_id: chatSessionId,
        model: selectedModel || '',
        workspace_id: _activeWorkspaceId || '',
        attachments: attachmentsPayload,
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
      onToken: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        _noteSeq();
        _taskCardEvent({ t: 'token' });
        _outboxClear();  // first streamed token = the server received the send
        // NOTE: do NOT clear the status strip per token. The strip sits
        // IN-FLOW between transcript and composer — every hide/show shifts
        // the composer ~33px, resizes the transcript viewport and makes the
        // streaming text bounce (the flicker). While tokens flow the strip
        // stays steady ("Writing reply…"); terminal paths (done/error/
        // endTurn) are the only ones allowed to hide it.
        activeTypingEl = null;
        if (!tokenAccum) {
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
        _taskCardEvent({
          t: 'tool',
          name: data.tool_name || 'tool',
          detail: _tcArgSummary(data.inputs),
        });
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
          detail: String(inputs || ''),
          state: 'running',
        });
      },

      onToolResult: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        _taskCardEvent({ t: 'tool_end', name: data.tool_name || 'tool' });
        if (!currentMsgEl) return;
        var isSwarm = (data.tool_name === 'dispatch_swarm' || data.tool_name === 'swarm_dispatch' || (data.result && data.result.indexOf('Swarm task dispatched') !== -1));
        logProgress({
          kind: 'tool',
          title: data.tool_name || 'tool',
          detail: String(data.result || ''),
          state: isSwarm ? 'running' : 'done',
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
          _taskCardEvent({
            t: 'status',
            status: status,
            message: (data && data.message) || title,
          });
          logProgress({
            kind: 'status',
            title: title,
            detail: (data && data.message && status !== 'thinking') ? data.message : '',
            state: 'running',
          });
        } else if (status === 'paused_for_approval' || status === 'idle') {
          // HITL / idle handled by other callbacks
        } else {
          _taskCardEvent({ t: 'status', status: status, message: String(data.message || status) });
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
        _taskCardEvent({
          t: 'hb',
          phase: (data && data.phase) || '',
          current: (data && data.current) || '',
          step: (data && data.step) || 0,
          elapsed_s: (data && data.elapsed_s) || 0,
        });
      },

      onDone: function(data) {
        if (!_mine()) return;
        activeStream = null;
        _clearStatusStrip();
        activeTypingEl = null;
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
        try {
        // Terminal frame is SoT — ALWAYS replace-paint, even when plan
        // tokens already arrived (glued ```plan + answer used to be skipped
        // because tokenAccum was nonempty).
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
        // Never leave a blank turn after "Thinking…" (empty stream / missed HITL).
        // _turnPainted: a late stale terminal must NEVER print this after a
        // successful reply already painted (the trailing "_No response
        // received." under the posted-tweets answer, 2026-08-26).
        // `!currentMsgEl` used to gate this. Any open bubble — including the
        // blank one a progress frame minted — suppressed the diagnosis, so a
        // turn that died on an unanswered gate showed nothing at all. What
        // matters is that the bubble is EMPTY, not that it is absent.
        if (!tokenAccum && !interrupted && !_awaitingApproval && !_turnPainted) {
          diag('empty-terminal');
          dumpDiagnostics();
          _pinLiveAssistantBubble();
          var emptyEl = currentMsgEl && currentMsgEl.querySelector('.message-text');
          if (emptyEl && !String(emptyEl.textContent || '').trim()) {
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
        // Play TTS for the assistant's response
        if (tokenAccum && window.KazmaVoice && !interrupted) {
          window.KazmaVoice.playTTS(tokenAccum);
        }
        } finally {
        // Flush any throttled live paint so the final frame shows the FULL
        // accumulated text (the last token batch may still be coalesced).
        _flushLiveTextPaint();
        // Live HITL card: keep the approval lock. Otherwise ALWAYS release
        // Stop / Enter — a painted reply with a stuck generating flag was
        // why the next message needed a Stop click first.
        if (hasInlineApprovalCard() || _awaitingApproval) {
          if (!_awaitingApproval) pauseForApproval(null);
          if (showArchived) loadArchivedSessions(); else refreshSessionsSoon();
        } else {
          endTurn();
        }
        // Truncated stream (no terminal frame): reconcile with durable
        // truth after the lock settles — paints the persisted reply when
        // the turn already finished, re-attaches when still generating.
        if (truncated && (!hasInlineApprovalCard())) {
          setTimeout(function() { _resyncDelivery('sse-truncated'); }, 400);
        }
        // Interrupted (HITL) turn with no rendered card anywhere = silently
        // paused. Recover the card from server truth, best-effort one shot.
        // `truncated` (stream died with no terminal frame — client refresh /
        // tab switch) is included: the interrupt event may have fired AFTER
        // this tab's stream dropped, so `interrupted` stays false and the
        // pending approval would otherwise be invisible until auto-deny.
        if ((interrupted || truncated) && !hasInlineApprovalCard() && !_serverGenerating) {
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
        activeTypingEl = null;
        pauseForApproval(data);
        applyTurnEvent({
          type: 'hitl',
          state: 'pending',
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
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
          activeTypingEl = null;
          pauseForApproval(data);
        } else {
          _awaitingApproval = false;
        }
        applyTurnEvent({
          type: 'hitl',
          state: st,
          tool: (data && data.tool) || '',
          interrupt_id: (data && data.interrupt_id) || '',
          payload: data || {},
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
        // was the live-vs-refresh mismatch (2026-09-01).
        if (_awaitingApproval) {
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
        _taskCardEvent({ t: 'error', msg: String(msg || '') });
        activeTypingEl = null;
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

  function _cotPhasesHtml(active) {
    function chip(id, label) {
      var on = active === id ? ' is-on' : '';
      return '<span class="cot-phase' + on + '" data-phase="' + id + '">' +
        escapeHtml(label) + '</span>';
    }
    return '<div class="cot-phases" aria-hidden="true">' +
      chip('think', ti('phase_think', 'Think')) +
      chip('act', ti('phase_act', 'Act')) +
      chip('write', ti('phase_write', 'Write')) +
      '</div>';
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

  function _startProgressTimer() {
    _progressStartedAt = Date.now();
    if (_progressTimerId) clearInterval(_progressTimerId);
    _progressTimerId = setInterval(_tickProgressElapsed, 1000);
    _tickProgressElapsed();
  }

  function _stopProgressTimer() {
    if (_progressTimerId) {
      clearInterval(_progressTimerId);
      _progressTimerId = null;
    }
  }

  function _extractPathFromTool(toolName, detail) {
    var n = String(toolName || '').toLowerCase();
    if (n.indexOf('file_write') < 0 && n.indexOf('file_delete') < 0 &&
        n.indexOf('write') < 0 && n.indexOf('delete') < 0) {
      // still try path-like detail for shell redirects etc.
      if (!detail || detail.indexOf('/') < 0 && detail.indexOf('\\') < 0) return '';
    }
    var s = String(detail || '');
    // JSON {"path": "..."}
    var m = s.match(/"path"\s*:\s*"([^"]+)"/);
    if (m) return m[1];
    m = s.match(/'path'\s*:\s*'([^']+)'/);
    if (m) return m[1];
    // Bare path-ish token
    m = s.match(/(?:^|[\s"'])([A-Za-z]:\\[^\s"']+|\/[\w.\-\/]+|[\w.\-]+\/[\w.\-\/]+)/);
    if (m) return m[1];
    return '';
  }

  function ensureProgressPanel() {
    if (_isUserBubble(currentMsgEl)) currentMsgEl = null;
    _pinLiveAssistantBubble();
    _rescueTurnDom(currentMsgEl);
    var content = _bubbleContent(currentMsgEl);
    if (!content) return null;
    var panel = _directChildByClass(content, 'agent-progress');
    if (panel) {
      _progressEl = panel;
      return panel;
    }
    panel = document.createElement('div');
    panel.className = 'agent-progress is-active';
    var pageRtl = (document.documentElement.getAttribute('dir') || '') === 'rtl';
    if (pageRtl) {
      panel.setAttribute('dir', 'rtl');
      panel.classList.add('is-rtl');
    }
    _panelSeq += 1;
    var bodyId = 'agent-progress-body-' + _panelSeq;
    panel.innerHTML =
      '<div class="agent-progress-header" role="button" tabindex="0" title="Collapse/expand workbench"' +
        ' aria-expanded="true" aria-controls="' + bodyId + '">' +
        '<span class="agent-progress-pulse" aria-hidden="true"></span>' +
        '<div class="agent-progress-heading">' +
          '<span class="agent-progress-kicker">' + escapeHtml(ti('reasoning', 'Reasoning')) + '</span>' +
          '<span class="agent-progress-title">' + escapeHtml(ti('working', 'Working\u2026')) + '</span>' +
        '</div>' +
        _cotPhasesHtml('think') +
        '<span class="agent-progress-elapsed" title="Elapsed">0s</span>' +
        '<span class="agent-progress-count">0 ' + escapeHtml(ti('steps', 'steps')) + '</span>' +
        '<span class="agent-progress-chevron" aria-hidden="true">\u25BE</span>' +
      '</div>' +
      '<div class="agent-progress-body" id="' + bodyId + '">' +
        '<div class="agent-plan' + (pageRtl ? ' is-rtl' : '') + '"' +
          (pageRtl ? ' dir="rtl"' : '') + ' hidden>' +
          '<div class="agent-plan-head">' +
            '<div class="agent-plan-label">' + escapeHtml(ti('plan', 'Plan')) + '</div>' +
            '<div class="agent-plan-meta"></div>' +
          '</div>' +
          '<div class="agent-plan-bar" aria-hidden="true"><div class="agent-plan-bar-fill"></div></div>' +
          '<ol class="agent-plan-list"></ol>' +
        '</div>' +
        '<div class="agent-memory-explain is-collapsed" hidden>' +
          '<div class="agent-memory-explain-head" role="button" tabindex="0" title="Collapse/expand memory">' +
            '<div class="agent-memory-explain-label">' + escapeHtml(ti('memory_context', 'Memory context')) + '</div>' +
            '<div class="agent-memory-explain-meta"></div>' +
            '<span class="agent-memory-chevron" aria-hidden="true">\u25B8</span>' +
          '</div>' +
          '<div class="agent-memory-explain-body"></div>' +
        '</div>' +
        '<div class="agent-activity-label">' + escapeHtml(ti('activity', 'Activity')) + '</div>' +
        '<ul class="agent-progress-steps" role="log" aria-live="polite"></ul>' +
      '</div>';
    var textEl = _directChildByClass(content, 'message-text');
    if (textEl) content.insertBefore(panel, textEl);
    else content.appendChild(panel);
    var header = panel.querySelector('.agent-progress-header');
    function toggle() {
      panel.classList.toggle('is-collapsed');
      var collapsed = panel.classList.contains('is-collapsed');
      var chev = panel.querySelector('.agent-progress-chevron');
      if (chev) chev.textContent = collapsed ? '\u25B8' : '\u25BE';
      if (header) header.setAttribute('aria-expanded', String(!collapsed));
    }
    if (header) {
      header.addEventListener('click', toggle);
      header.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
    }
    // Memory sub-panel: independently collapsible (starts collapsed to save space).
    var memHead = panel.querySelector('.agent-memory-explain-head');
    if (memHead) {
      function toggleMem() {
        var mem = panel.querySelector('.agent-memory-explain');
        if (!mem) return;
        mem.classList.toggle('is-collapsed');
        var chev = mem.querySelector('.agent-memory-chevron');
        if (chev) chev.textContent = mem.classList.contains('is-collapsed') ? '\u25B8' : '\u25BE';
      }
      memHead.addEventListener('click', toggleMem);
      memHead.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleMem(); }
      });
    }
    _progressEl = panel;
    _progressStepCount = 0;
    _planItems = [];
    _planParsedFromText = false;
    _startProgressTimer();
    return panel;
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
    // Live Task Card: plan progress rides the card header meta. setPlan
    // must NEVER create an in-bubble workbench — on hydration it painted a
    // phantom "Working…" panel over finished history (2026-09-03).
    _taskCardEvent({
      t: 'plan',
      total: _planItems.length,
      done: _planItems.filter(function(p) { return p.done; }).length,
    });
    var panel = messagesEl
      ? messagesEl.querySelector('.agent-progress.is-active')
      : null;
    if (panel) {
      _renderPlanList(panel);
      scrollToBottom();
    }
  }

  function markPlanProgress(toolName) {
    // Soft match: mark first incomplete plan item that mentions the tool or shares a word
    if (!_planItems.length || !toolName) return;
    var t = String(toolName).toLowerCase().replace(/_/g, ' ');
    var marked = false;
    for (var i = 0; i < _planItems.length; i++) {
      if (_planItems[i].done) continue;
      var pt = _planItems[i].text.toLowerCase();
      if (pt.indexOf(t) >= 0 || t.split(' ').some(function(w) { return w.length > 3 && pt.indexOf(w) >= 0; })) {
        _planItems[i].done = true;
        marked = true;
        break;
      }
    }
    // If no lexical match, advance the next open plan step on tool completion
    if (!marked) {
      for (var j = 0; j < _planItems.length; j++) {
        if (!_planItems[j].done) { _planItems[j].done = true; break; }
      }
    }
    if (_progressEl) _renderPlanList(_progressEl);
    _taskCardEvent({
      t: 'plan',
      total: _planItems.length,
      done: _planItems.filter(function(p) { return p.done; }).length,
    });
  }

  /**
   * Split a ```plan fence (or ## Plan heading) from user-facing prose.
   * Mirrors kazma_core.agent.plan_fence.split_plan_and_prose — handles the
   * glued closer (```Saved.) that CommonMark never closes.
   */
  function splitPlanAndProse(text) {
    var s = String(text || '');
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
  function _detailHtml(detail, forceExpanded) {
    if (!detail) return '';
    var t = truncateStr(String(detail), TOOL_DETAIL_MAX);
    if (forceExpanded || t.length <= STEP_DETAIL_CLAMP_AT) {
      return '<div class="step-detail is-expanded">' + escapeHtml(t) + '</div>';
    }
    return '<div class="step-detail is-clamped">' + escapeHtml(t) + '</div>' +
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
              (state === 'failed' ? 'failed'
                : (rawTitle.toLowerCase().indexOf('delete') >= 0 ? 'deleted' : 'wrote')) +
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
        _detailHtml(o.detail, o.forceExpanded) +
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
    if (window.KazmaTurnDocument && typeof window.KazmaTurnDocument.applyEvent === 'function') {
      applyTurnEvent({
        type: 'progress',
        step: step,
        source: 'progress',
        turn_id: _liveTurnId,
      });
      return;
    }

    var panel = ensureProgressPanel();
    if (!panel) return;

    var state = step.state || (kind === 'error' ? 'failed' : (kind === 'done' ? 'done' : 'info'));

    // Reactivate panel if a new active step arrives (prevents premature "Done" title during background execution)
    if (panel.classList.contains('is-done') && (state === 'running' || kind === 'status' || kind === 'tool')) {
      panel.classList.remove('is-done');
      panel.classList.add('is-active');
      var headerTitle = panel.querySelector('.agent-progress-title');
      if (headerTitle) {
        headerTitle.textContent = ti('thinking', 'Kazma is thinking\u2026');
      }
      var pulse = panel.querySelector('.agent-progress-pulse');
      if (pulse) pulse.classList.remove('is-off');
      _startProgressTimer();
    }
    var rawTitle = String(step.title || '').trim() || '\u2026';
    var title = kind === 'tool' ? _friendlyToolName(rawTitle) : rawTitle;
    // Canonical display for thinking heartbeats (localized)
    if (kind === 'status' && _isThinkingStatus(title)) {
      title = ti('thinking', 'Kazma is thinking\u2026');
    }
    // Localize leftover English HITL / CoT lines if server sent raw EN
    if (kind !== 'tool') {
      title = _localizeCotTitle(title);
    }
    var detail = step.detail != null ? String(step.detail) : '';

    var list = panel.querySelector('.agent-progress-steps');
    if (!list) return;

    // Coalesce rapid identical status lines (heartbeats update last row).
    // Also merge thinking variants from beginTurn + WS status frames.
    var last = list.lastElementChild;
    var sameStatus =
      last &&
      kind === 'status' &&
      last.dataset.kind === 'status' &&
      (
        last.dataset.title === title ||
        _normalizeStatusTitle(last.dataset.title) === _normalizeStatusTitle(title) ||
        (_isThinkingStatus(last.dataset.title) && _isThinkingStatus(title))
      ) &&
      (!detail || detail === (last.dataset.detail || ''));
    if (sameStatus) {
      var tEl = last.querySelector('.step-time');
      if (tEl) tEl.textContent = formatMsgTime();
      last.className = 'agent-progress-step step-' + kind + ' state-' + state;
      last.dataset.title = title;
      last.dataset.detail = detail || '';
      var titleNode = last.querySelector('.step-title');
      if (titleNode) titleNode.textContent = title;
      if (detail) {
        var det0 = last.querySelector('.step-detail');
        if (det0) det0.textContent = truncateStr(detail, TOOL_DETAIL_MAX);
      }
      list.scrollTop = list.scrollHeight;
      scrollToBottom();
      return;
    }
    // Update in-place when the same tool is completing — keep result expanded
    // Match either friendly label or raw tool name on the row.
    if (last && kind === 'tool' && state !== 'running' && last.dataset.kind === 'tool' &&
        (last.dataset.title === title || last.dataset.rawTitle === rawTitle || last.dataset.title === rawTitle)) {
      last.className = 'agent-progress-step step-tool state-' + state + ' is-expanded';
      last.dataset.state = state;
      last.dataset.title = title;
      var st = last.querySelector('.step-state');
      if (st) st.textContent = state === 'done'
        ? ti('step_done', 'Done')
        : (state === 'failed' ? ti('step_failed', 'Failed') : state);
      var titleNode = last.querySelector('.step-title');
      if (titleNode) titleNode.textContent = title;
      if (detail) {
        // Rebuild the detail block through the shared template so long
        // results clamp with a "show more" toggle like freshly-appended rows.
        var oldDet = last.querySelector('.step-detail');
        if (oldDet) oldDet.remove();
        var oldBtn = last.querySelector('.step-show-more');
        if (oldBtn) oldBtn.remove();
        var bodyEl = last.querySelector('.step-body');
        if (bodyEl) bodyEl.insertAdjacentHTML('beforeend', _detailHtml(detail));
        // Surface search backend / recovery source when present
        _maybeAddSourceChip(last, detail);
      }
      var t2 = last.querySelector('.step-time');
      if (t2) t2.textContent = formatMsgTime();
      if (state === 'done') markPlanProgress(rawTitle);
      list.scrollTop = list.scrollHeight;
      scrollToBottom();
      return;
    }

    _progressStepCount += 1;
    if (kind === 'tool') _progressToolCount += 1;
    var li = document.createElement('li');
    li.className = 'agent-progress-step step-' + kind + ' state-' + state +
      (kind === 'tool' ? ' is-expanded' : '');
    li.dataset.kind = kind;
    li.dataset.title = title;
    li.dataset.rawTitle = rawTitle;
    li.dataset.state = state;

    li.innerHTML = _stepRowHtml({
      kind: kind,
      state: state,
      title: title,
      rawTitle: rawTitle,
      detail: detail,
    });
    _wireStepToggles(list);

    list.appendChild(li);
    if (detail) _maybeAddSourceChip(li, detail);
    while (list.children.length > 100) list.removeChild(list.firstChild);

    var countEl = panel.querySelector('.agent-progress-count');
    if (countEl) {
      var planN = _planItems.length;
      var donePlan = _planItems.filter(function(p) { return p.done; }).length;
      var stepWord = _progressStepCount === 1 ? ti('step', 'step') : ti('steps', 'steps');
      var planBit = planN
        ? ' \u00B7 ' + tiFmt('plan_progress', 'plan {done}/{total}', { done: donePlan, total: planN })
        : '';
      countEl.textContent = _progressStepCount + ' ' + stepWord + planBit;
    }
    var titleEl = panel.querySelector('.agent-progress-title');
    if (titleEl && state === 'running') {
      titleEl.textContent = kind === 'tool' ? title : ti('working', 'Working\u2026');
    }
    if (titleEl && kind === 'status' && step.title) titleEl.textContent = truncateStr(step.title, 48);

    panel.classList.add('is-active');
    // Never auto-expand (2026-09-03): un-done yes, un-collapse no — the
    // user's chevron click is the only thing that opens a CoT panel.
    panel.classList.remove('is-done');
    list.scrollTop = list.scrollHeight;
    scrollToBottom();
  }

  function _maybeAddSourceChip(li, detail) {
    if (!li || !detail) return;
    var body = li.querySelector('.step-body');
    if (!body) return;
    if (body.querySelector('.source-chip')) return;
    var m = String(detail).match(/Source:\s*([a-z0-9_.@\/:\-]+)/i) ||
      String(detail).match(/searxng:ok@([^\s,]+)/i) ||
      String(detail).match(/\b(jina|firecrawl|playwright|duckduckgo|bing|wikipedia)\b/i);
    if (!m) return;
    var label = m[1] || m[0];
    var chip = document.createElement('div');
    chip.className = 'source-chip';
    chip.title = 'Backend / source';
    chip.innerHTML = '<span class="source-chip-label">via</span> ' +
      '<code>' + escapeHtml(String(label).slice(0, 48)) + '</code>';
    var detailEl = body.querySelector('.step-detail');
    if (detailEl) body.insertBefore(chip, detailEl);
    else body.appendChild(chip);
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
          parts.push(_progressToolCount + ' ' + tiFmt('summary_tools', '{n} tools', { n: _progressToolCount }));
        }
        if (_progressStepCount > 0) {
          parts.push(_progressStepCount + ' ' + (_progressStepCount === 1
            ? ti('step', 'step') : ti('steps', 'steps')));
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

  function _buildRestoredWorkbench(activity) {
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
    var stepWord = stepCount === 1 ? ti('step', 'step') : ti('steps', 'steps');
    _panelSeq += 1;
    var bodyId = 'agent-progress-body-' + _panelSeq;
    // Header mirrors the live summary bar shape: "N tools · M steps" (+usage
    // when the server stamped per-turn tokens/cost on the message).
    var headBits = [];
    if (toolCount) headBits.push(toolCount + ' ' + tiFmt('summary_tools', '{n} tools', { n: toolCount }));
    headBits.push(stepCount + ' ' + stepWord);
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
      function toggle() {
        panel.classList.toggle('is-collapsed');
        var collapsed = panel.classList.contains('is-collapsed');
        var chev = panel.querySelector('.agent-progress-chevron');
        if (chev) chev.textContent = collapsed ? '\u25B8' : '\u25BE';
        header.setAttribute('aria-expanded', String(!collapsed));
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
      var cotPanel = _buildRestoredWorkbench(restoredActivity);
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
      aActions.innerHTML =
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
        }
      });
    }

    // Remove welcome if present
    var welcome = messagesEl.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    messagesEl.appendChild(wrapper);
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
    // An EMPTY accumulator at paint time is always a stale duplicate
    // terminal: the first done's endTurn zeroed tokenAccum, then the SECOND
    // transport's terminal frame (SSE + WS both deliver done) flushed after
    // it and painted "" over the finished reply — the answer vanished at the
    // end of the stream until a refresh re-painted it (2026-09-02). A final
    // flush may only ever render real accumulated text.
    if (!String(tokenAccum || '').trim()) return;
    if (final) {
      if (_paintHTML(textEl, _renderReplyHTML(tokenAccum))) {
        if (window.KazmaBidi) KazmaBidi.apply(textEl, tokenAccum);
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
    var liveParts = splitPlanAndProse(tokenAccum);
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
    if (messagesEl) {
      messagesEl.querySelectorAll('.hitl-approval-card').forEach(function(card) {
        var btns = card.querySelectorAll('button');
        var live = false;
        for (var i = 0; i < btns.length; i++) {
          if (!btns[i].disabled) live = true;
        }
        if (!live) return;
        _stopHitlCountdown(card);
        btns.forEach(function (b) { b.disabled = true; });
        card.className = 'hitl-approval-card hitl-denied';
        var actions = card.querySelector('.hitl-approval-actions');
        if (actions) {
          actions.innerHTML = '<span class="hitl-status hitl-denied">' + escapeHtml(text) + '</span>';
        }
        // Timeout is a decision too: park above the continuing reply and
        // collapse to the one-line bar.
        _parkClaimedHitlCard(card);
        _collapseClaimedHitlCard(card);
      });
    }
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
        card.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
        card.className = 'hitl-approval-card hitl-denied';
        var actions = card.querySelector('.hitl-approval-actions');
        if (actions) {
          actions.innerHTML = '<span class="hitl-status hitl-denied">Aborted — send a new message</span>';
        }
        _parkClaimedHitlCard(card);
        _collapseClaimedHitlCard(card);
      });
    }
    _clearStoreApproval();
    diag('hitl-released', reason || '');
  }

  function _hitlCardIsTrapped(card) {
    if (!card || !card.closest) return false;
    return !!card.closest('.agent-progress');
  }

  function _outerAssistantBubble(el) {
    if (!el) return null;
    var n = el;
    var found = null;
    while (n && n !== messagesEl) {
      if (n.classList && n.classList.contains('message-assistant')) {
        var parent = n.parentElement || n.parentNode;
        var insideCot = parent && parent.closest ? parent.closest('.agent-progress') : null;
        if (!insideCot) found = n;
      }
      n = n.parentElement || n.parentNode;
    }
    return found;
  }

  function _hitlHostContent(el) {
    var bubble = _outerAssistantBubble(el) || el;
    var content = _bubbleContent(bubble);
    if (content && !_hitlCardIsTrapped(content)) return content;
    if (!bubble || !bubble.querySelectorAll) return content;
    var all = bubble.querySelectorAll('.message-content');
    var i;
    for (i = 0; i < all.length; i++) {
      if (!_hitlCardIsTrapped(all[i])) return all[i];
    }
    return content;
  }

  function hasInlineApprovalCard() {
    if (!messagesEl) return false;
    var cards = messagesEl.querySelectorAll('.hitl-approval-card');
    for (var i = 0; i < cards.length; i++) {
      // A card inside CoT is not an inline approval — overflow:hidden and
      // the collapsed body hide it, but enabled buttons still match, which
      // blocked render + recoverMissedApproval (dashboard-only, 2026-09-02).
      if (_hitlCardIsTrapped(cards[i])) continue;
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
    var iid = _hitlInterruptIdOf(data);
    var tool = _hitlToolOf(data);
    var part = _openHitlPart();
    if (part) {
      var st = String(part.state || 'pending');
      var pid = _hitlInterruptIdOf(part);
      var ptool = _hitlToolOf(part);
      if (st !== 'pending') {
        if (iid && pid && iid === pid) return true;
        if (!iid && !pid && tool && ptool && tool === ptool) return true;
      }
    }
    if (messagesEl) {
      var cards = messagesEl.querySelectorAll('.hitl-approval-card');
      for (var i = 0; i < cards.length; i++) {
        if (!_hitlCardIsClaimed(cards[i])) continue;
        var cid = String(cards[i].getAttribute('data-interrupt-id') || '');
        if (iid && cid && iid === cid) return true;
        var ctool = String(cards[i].getAttribute('data-tool') || '');
        if (!iid && !cid && tool && ctool && tool === ctool) return true;
      }
    }
    return false;
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

  /** Fallback when the inline bubble card did not land — keep a card on
   *  the chat page (the Alpine strip under the transcript), not only on
   *  Dashboard. Cleared as soon as hasInlineApprovalCard() is true. */
  function _showStoreApproval(data) {
    if (!data) return;
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent')) {
        Alpine.store('agent').pendingApproval = data;
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

  function _paintLiveGates() {
    if (hasInlineApprovalCard()) return;
    var list = _serverGates || [];
    var i;
    for (i = 0; i < list.length; i++) {
      if (String((list[i] || {}).state || '') !== 'pending') continue;
      var payload = _payloadFromGate(list[i]);
      applyTurnEvent({
        type: 'hitl',
        state: 'pending',
        tool: payload.tool,
        interrupt_id: payload.interrupt_id,
        payload: payload,
        turn_id: _liveTurnId,
        source: 'gates',
      });
      if (hasInlineApprovalCard()) return;
    }
    if (_serverHitl && String(_serverHitl.gate || '') === 'pending' && _serverHitl.tool) {
      applyTurnEvent({
        type: 'hitl',
        state: 'pending',
        tool: _serverHitl.tool || '',
        interrupt_id: _serverHitl.interrupt_id || '',
        payload: {
          thread_id: _serverThreadId || chatSessionId || '',
          tool: _serverHitl.tool || '',
          interrupt_id: _serverHitl.interrupt_id || '',
          message: '',
        },
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
    (_serverGates || []).forEach(function (g) {
      if (String((g || {}).state || '') === 'pending') pendingIids[String(g.gate_id || '')] = true;
    });
    messagesEl.querySelectorAll('.hitl-approval-card').forEach(function (card) {
      if (_hitlCardIsClaimed(card) || _hitlCardIsTrapped(card)) return;
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
      _stopHitlCountdown(card);
      btns.forEach(function (b) { b.disabled = true; });
      card.className = 'hitl-approval-card hitl-denied';
      var actions = card.querySelector('.hitl-approval-actions');
      if (actions) {
        actions.innerHTML = '<span class="hitl-status hitl-denied">No longer pending</span>';
      }
      _parkClaimedHitlCard(card);
      _collapseClaimedHitlCard(card);
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

  function _openHitlPart() {
    var doc = _docs[_liveTurnId] || null;
    if (!doc || !doc.parts) return null;
    for (var i = doc.parts.length - 1; i >= 0; i--) {
      if (doc.parts[i] && doc.parts[i].type === 'hitl') return doc.parts[i];
    }
    return null;
  }

  function recoverMissedApproval() {
    if (hasInlineApprovalCard()) return;
    if (_serverGenerating && !_serverPaused) return;
    _paintLiveGates();
    if (hasInlineApprovalCard()) return;
    var existing = _openHitlPart();
    if (existing && String(existing.state || 'pending') !== 'pending') {
      /* a settled part must not block a live gate painted above */
    } else if (existing && String(existing.state || '') === 'pending' && existing.payload) {
      renderHitlCard(existing.payload, { lock: true });
      if (hasInlineApprovalCard()) return;
    }
    fetch('/api/pending-approvals', { credentials: 'same-origin' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(payload) {
        if (hasInlineApprovalCard()) return;
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
  function _placeHitlCard(content, card) {
    if (!card) return;
    var host = _hitlHostContent(content) || content;
    if (!host) return;
    var kids = host.children;
    var lastCard = null;
    var progress = null;
    var i;
    for (i = 0; i < kids.length; i++) {
      if (!kids[i].classList || kids[i] === card) continue;
      if (kids[i].classList.contains('hitl-approval-card')) lastCard = kids[i];
      else if (!progress && kids[i].classList.contains('agent-progress')) progress = kids[i];
    }
    // NEVER auto-expand a CoT panel (2026-09-03): expansion is the user's
    // click only. The old sweep opened collapsed panels that trapped a
    // card — with the card parked as a sibling, an auto-expanded panel
    // just pushed the approval card below the fold while the reader was
    // scrolled elsewhere. A trapped card is LIFTED out by the caller's
    // cleanup, not revealed by expanding its cage.
    var after = lastCard || progress;
    try {
      if (after && after.parentNode === host && after !== card) {
        if (after.nextSibling) host.insertBefore(card, after.nextSibling);
        else host.appendChild(card);
        return;
      }
    } catch (ePlace) { /* fall through to append */ }
    try { host.appendChild(card); } catch (eAppend) { /* ignore */ }
  }

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

  /** Deadline of the newest card that still has live buttons, 0 if none.
   *  Lets the Live Task Card keep counting down for a SIBLING gate after the
   *  first one is decided. */
  function _liveHitlDeadline() {
    if (!messagesEl) return 0;
    var cards = messagesEl.querySelectorAll('.hitl-approval-card');
    for (var i = cards.length - 1; i >= 0; i--) {
      if (!cards[i].querySelector('button:not([disabled])')) continue;
      return Number(cards[i].getAttribute('data-approval-deadline') || 0) || 0;
    }
    return 0;
  }

  function _attachHitlCountdown(card, data) {
    if (!card) return;
    var dl = _hitlDeadlineOf(data);
    if (!dl) return;
    // Published on the node so _liveHitlDeadline can find it — the value
    // used to live only in this closure.
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
        card.className = 'hitl-approval-card hitl-denied';
        var act = card.querySelector('.hitl-approval-actions');
        if (act) act.innerHTML = '<span class="hitl-status hitl-denied">' +
          escapeHtml(ti('approval_expired', 'Approval timed out — continuing without this tool.')) + '</span>';
        row.textContent = '';
        _parkClaimedHitlCard(card);
        _collapseClaimedHitlCard(card);
        return;
      }
      var m = Math.floor(left / 60);
      var s = left % 60;
      row.textContent = '⏳ ' + ti('auto_deny_in', 'Auto-denies if unanswered in') + ' ' + m + ':' + (s < 10 ? '0' : '') + s;
    };
    paint();
    card.__cdTimer = setInterval(paint, 1000);
  }

  /** Park a CLAIMED card between the CoT block and the reply text, so the
   *  post-approval response streams BELOW it. While pending the card sits
   *  at the bottom (chronology: narration → ask); on decision it moves up
   *  (narration → ✓ card → reply). The streamed reply used to paint ABOVE
   *  a bottom-docked card — "response on top of the card" (2026-09-03). */
  function _parkClaimedHitlCard(card) {
    if (!card || !card.parentNode) return;
    var host = card.parentNode;
    if (!host.classList || !host.classList.contains('message-content')) return;
    var textEl = _directChildByClass(host, 'message-text');
    if (textEl && textEl !== card.nextSibling) {
      host.insertBefore(card, textEl);
      return;
    }
    if (!textEl) {
      var cot = _directChildByClass(host, 'agent-progress');
      if (cot && cot.nextSibling) host.insertBefore(card, cot.nextSibling);
    }
  }

  /** Collapse a claimed card to a one-line CoT-style bar (click to expand).
   *  Keeps the decision visible in the timeline without a full card body
   *  sitting between the CoT and the streamed reply. The header chip is
   *  re-synced on every call so later state changes ("Resolving…" →
   *  "Approved") stay visible while collapsed. */
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
    if (card.classList.contains('hitl-collapsed')) return;
    card.classList.add('hitl-collapsed');
    if (!header.querySelector('.hitl-collapse-chevron')) {
      var chev = document.createElement('span');
      chev.className = 'hitl-collapse-chevron';
      chev.setAttribute('aria-hidden', 'true');
      chev.textContent = '▸';
      header.appendChild(chev);
    }
    if (!card.__hitlCollapseWired) {
      card.__hitlCollapseWired = true;
      header.addEventListener('click', function (e) {
        if (e.target.closest('button')) return;
        var nowCollapsed = card.classList.toggle('hitl-collapsed');
        var ch = header.querySelector('.hitl-collapse-chevron');
        if (ch) ch.textContent = nowCollapsed ? '▸' : '▾';
        e.stopPropagation();
      });
    }
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

  function renderHitlCard(data, opts) {
    if (!data) return;
    var lockComposer = !(opts && opts.lock === false);
    // Idempotent: WS and SSE both deliver the approval (journal fan-out +
    // SSE frame). The FIRST render wins; a second live card for the same
    // interrupt duplicates buttons and double-fires resumes. Suppression
    // used to live in the WS store (skip when SSE is live) — but when the
    // SSE frame was late/lost NO card appeared at all and the paused turn
    // went completely silent (2026-08-26 X-post incident).
    if (_hitlAlreadyClaimed(data)) return;
    if (lockComposer) pauseForApproval(data);
    var targetThreadId = data.thread_id || chatSessionId || '';
    _pinLiveAssistantBubble();
    var content = _hitlHostContent(currentMsgEl);
    if (!content) {
      // Inline bubble never materialized — keep the chat-page Alpine card
      // so approval is not dashboard-only.
      _showStoreApproval(data);
      return;
    }
    var iid = _hitlInterruptIdOf(data);
    var existing = iid ? _findHitlCard(iid, currentMsgEl) : null;
    if (existing && !_hitlCardIsTrapped(existing)) {
      if (hasInlineApprovalCard()) _clearStoreApproval();
      else _showStoreApproval(data);
      return;
    }
    if (existing && _hitlCardIsTrapped(existing)) _placeHitlCard(content, existing);
    var scope = _outerAssistantBubble(currentMsgEl) || content;
    // Never strip a claimed card (Approved/Denied) — that is the
    // disappear-then-live-again loop (cleanup 2026-09-01). Lift trapped
    // cards for this interrupt; drop other unclaimed pending cards.
    (scope.querySelectorAll ? scope.querySelectorAll('.hitl-approval-card') : []).forEach(function(old) {
      if (_hitlCardIsClaimed(old)) {
        if (_hitlCardIsTrapped(old)) _placeHitlCard(content, old);
        return;
      }
      var oid = String(old.getAttribute('data-interrupt-id') || '');
      if (iid && oid && oid === iid) {
        _placeHitlCard(content, old);
        return;
      }
      old.remove();
    });
    if (hasInlineApprovalCard()) {
      // Idempotent skip — but ONLY for the SAME interrupt (WS + SSE both
      // deliver the approval; first render wins). A DIFFERENT gate's pending
      // card elsewhere in the transcript — or a stale pending card from an
      // older turn — must not suppress this gate's card. The global check
      // let the first live-button card on the page eat every later
      // approval: cards landed dashboard-only while the gate auto-denied
      // (2026-09-02 multi-gate sessions, three watchdog denials in a row).
      var liveSameCard = iid ? _findHitlCard(iid, content) : null;
      if (!iid || (liveSameCard && !_hitlCardIsClaimed(liveSameCard))) {
        _clearStoreApproval();
        return;
      }
    }

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
      _placeHitlCard(content, _semCard);
      _revealHitlCard(_semCard);
      _attachHitlCountdown(_semCard, data);
      if (hasInlineApprovalCard()) _clearStoreApproval();
      else _showStoreApproval(data);
      scrollToBottom();
      _semCard.querySelectorAll('.hitl-sem-opt').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var optId = this.getAttribute('data-opt');
          _semCard.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
          var act = _semCard.querySelector('.hitl-approval-actions');
          if (act) act.innerHTML = '<span class="hitl-status">Resolving\u2026</span>';
          // Same claim treatment as the security card: park above the
          // incoming reply and collapse to the one-line bar.
          _parkClaimedHitlCard(_semCard);
          _collapseClaimedHitlCard(_semCard);
          tokenAccum = '';
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
              if (!activeStream && typeof _reopenSseRef === 'function') {
                try { _reopenSseRef('approve-409'); } catch (eRe) { /* ignore */ }
              }
              return;
            }
            if (res.status >= 400 || (res.body && res.body.ok === false)) {
              if (act) act.innerHTML = '<span class="hitl-status text-danger">Failed — retry</span>';
              _semCard.querySelectorAll('button').forEach(function(b) { b.disabled = false; });
              return;
            }
            _awaitingApproval = false;
            _awaitingReply = true;
            if (!activeStream && typeof _reopenSseRef === 'function') {
              try { _reopenSseRef('approve-json'); } catch (eRe) { /* ignore */ }
            }
          }).catch(function() {
            if (act) act.innerHTML = '<span class="hitl-status text-danger">Failed — retry</span>';
            _semCard.querySelectorAll('button').forEach(function(b) { b.disabled = false; });
          });
        });
      });
      return; // Don't render the security card
    }

    var textEl = content.querySelector('.message-text');
    if (textEl && !textEl.innerHTML.trim()) {
      textEl.innerHTML = KS.markdown ? KS.markdown('_Action required: The agent paused to ask for permission to run a tool._') : '<em>Action required: The agent paused to ask for permission to run a tool.</em>';
    }

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
      '<div class="hitl-approval-header">\u26A0 Approval Required</div>' +
      '<div class="hitl-approval-body">' +
        '<p><strong>Tool:</strong> <code>' + escapeHtml(data.tool || '') + '</code></p>' +
        (tools.length <= 1
          ? '<p><strong>Args:</strong></p><div class="hitl-approval-args"><pre>' + escapeHtml(formatApprovalArgs(data.tool, data.args)) + '</pre></div>'
          : toolsHtml) +
        proposalHtml +
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
        '<button class="btn btn-sm btn-primary hitl-approve-tool" data-scope="tool" title="Allow this tool for ~30m in this session">Allow tool (session)</button>' +
        (yoloOk
          ? '<button class="btn btn-sm btn-warning hitl-approve-yolo" data-scope="yolo" title="Skip all danger tools for this session">YOLO session</button>'
          : '') +
        '<button class="btn btn-sm btn-danger hitl-deny" data-scope="once">Deny</button>' +
      '</div>';
    _placeHitlCard(content, card);
    _revealHitlCard(card);
    _attachHitlCountdown(card, data);
    if (hasInlineApprovalCard()) _clearStoreApproval();
    else _showStoreApproval(data);
    scrollToBottom();

    function setCardState(state, label) {
      _stopHitlCountdown(card);
      card.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
      card.className = 'hitl-approval-card hitl-' + state;
      var actions = card.querySelector('.hitl-approval-actions');
      if (actions) actions.innerHTML = '<span class="hitl-status hitl-' + state + '">' + label + '</span>';
      // Decision made: park between CoT and the reply text (the streamed
      // answer must land BELOW the card) and collapse to the one-line bar.
      _parkClaimedHitlCard(card);
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
      _clearStoreApproval();
      // Reset accum so post-approval final answer replaces (no pre-HITL + final concat).
      tokenAccum = '';
      // RESUME, not a new turn: keep this turn's workbench and its steps.
      beginTurn({ resume: true });
      // beginTurn clears the HITL wait; keep recover/replay from re-arming
      // this card while the JSON approve is in flight.
      _awaitingApproval = true;
      // THIS card only — a sibling card is a different, still-pending gate.
      _freezeHitlButtons(card);
      setCardState('approved', scope === 'yolo'
        ? ti('yolo_on', 'YOLO on ✓')
        : (scope === 'tool' ? ti('tool_allowed', 'Tool allowed ✓')
          : ti('approved', 'Approved ✓')));
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

      applyTurnEvent({
        type: 'hitl',
        state: hitlState,
        tool: data.tool || '',
        interrupt_id: data.interrupt_id || '',
        payload: data,
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
        if (res.status === 409) {
          var running409 = !!(res.body && (res.body.running
            || res.body.hitl_state === 'inflight'
            || res.body.hitl_state === 'approved'));
          if (running409) {
            applyTurnEvent({
              type: 'hitl', state: 'inflight', tool: data.tool || '',
              interrupt_id: (res.body && res.body.interrupt_id) || data.interrupt_id || '',
              payload: data, turn_id: _liveTurnId, source: 'approve-409',
            });
            _awaitingApproval = false;
            _awaitingReply = true;
            _notifyHitlResolved({
              thread_id: data.thread_id || targetThreadId,
              tool: data.tool || '',
              interrupt_id: data.interrupt_id || '',
            });
            if (!activeStream && typeof _reopenSseRef === 'function') {
              try { _reopenSseRef('approve-409'); } catch (eRe) { /* ignore */ }
            }
            return;
          }
          applyTurnEvent({
            type: 'hitl', state: 'error', tool: data.tool || '',
            payload: data, turn_id: _liveTurnId, source: 'approve-409',
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
        // Decision accepted — the graph is running again. Clear the HITL
        // wait so a dead tail can re-attach (JSON approve is not an SSE).
        // Unless another card is still live: deciding gate A does not mean
        // the turn stopped waiting on gate B.
        _awaitingApproval = hasInlineApprovalCard();
        _awaitingReply = true;
        if (_awaitingApproval) {
          _taskCardEvent({ t: 'approval', deadline: _liveHitlDeadline() });
        }
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
        if (!activeStream) {
          if (typeof _reopenSseRef === 'function') {
            try { _reopenSseRef('approve-json'); } catch (eRe) { /* ignore */ }
          }
          _resyncDelivery('approve-json');
        }
      }).catch(function(err) {
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

    // Fetch the session messages from the API and render them.
    // ?stats=1 opts into the envelope {messages, total_tokens, total_cost}
    // so cumulative badges are correct after refresh; the legacy bare-list
    // shape is still handled for old servers.
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/messages?stats=1')
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(payload) {
        // Guard against race: user switched sessions while fetch was in flight
        if (chatSessionId !== sessionId) { _loadInFlightFor = null; return; }
        _loadMsgAttempts = 0;
        _loadInFlightFor = null;

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
              if (msg.turn_id) _docs[String(msg.turn_id)] = hydratedDoc;
              _paintHitlFromDoc(painted, hydratedDoc);
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
    messagesEl.innerHTML =
      '<div class="chat-welcome">' +
        '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
        '<h2>Kazma</h2>' +
        '<p>How can I help you today?</p>' +
      '</div>';
    resetSessionStats();
    currentMsgEl = null;
    tokenAccum = '';
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
  function formatApprovalArgs(tool, args) {
    var text;
    try {
      text = JSON.stringify(args || {}, null, 2);
    } catch (e) {
      text = String(args);
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

  function _rescueTurnDom(el) {
    // Collapsed CoT must never own the answer. If .message-text (or the HITL
    // card) landed inside .agent-progress-body, expanding CoT was the only
    // way to see the reply (2026-09-01). Never lift nodes out of a nested
    // .message — that emptied the You bubble after send.
    if (!el || _isUserBubble(el)) return;
    var content = _bubbleContent(el);
    if (!content) return;
    var panel = _directChildByClass(content, 'agent-progress');
    var i;
    if (panel) {
      var body = panel.querySelector('.agent-progress-body') || panel;
      var trapped = body.querySelectorAll('.message-text, .hitl-approval-card, .message-meta, .message-actions');
      var cursor = panel;
      for (i = 0; i < trapped.length; i++) {
        var node = trapped[i];
        var owner = node.closest ? node.closest('.message') : null;
        if (owner && owner !== el) {
          // Nested You-bubble text must stay put (lifting emptied it).
          // HITL cards are the exception: they are the live approval UI.
          if (!node.classList || !node.classList.contains('hitl-approval-card')) continue;
        }
        // Same-anchor insertBefore reverses the node list (later cards
        // would land above earlier ones). Walk the cursor forward.
        content.insertBefore(node, cursor.nextSibling);
        cursor = node;
      }
    }
    for (i = 0; i < content.children.length; i++) {
      var n = content.children[i];
      if (n.classList && n.classList.contains('message-text')) {
        if (n.style.display === 'none') n.style.display = '';
        n.classList.remove('typing-visible');
      }
    }
  }

  /**
   * Does this document need a transcript bubble at all?
   *
   * Text and reasoning are covered by _answerFromDoc. Beyond those, only two
   * things belong in the bubble: an approval card, and the durable one-line
   * workbench summary a FINISHED turn leaves behind (which needs a host even
   * when the turn produced no prose). Everything else — the running step
   * list — is the Live Task Card's job now.
   */
  function _docHasBubbleContent(doc) {
    if (!doc) return false;
    var st = String(doc.status || '');
    if (st === 'done' || st === 'error' || st === 'paused') return true;
    var parts = doc.parts || [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i] && parts[i].type === 'hitl') return true;
    }
    return false;
  }

  function _answerFromDoc(TD, doc) {
    var text = (TD && TD.textOf) ? TD.textOf(doc.parts) : '';
    if (!text) text = doc.stream || '';
    if (String(text || '').trim()) return String(text).trim();
    var parts = doc.parts || [];
    for (var i = parts.length - 1; i >= 0; i--) {
      var p = parts[i];
      if (p && p.type === 'reasoning' && String(p.text || '').trim()) {
        return String(p.text).trim();
      }
    }
    return '';
  }

  function _cssEscapeAttr(s) {
    if (window.CSS && typeof CSS.escape === 'function') return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function _syncCotPanel(el, activity, status, meta) {
    if (!el || !activity || !activity.length) return;
    if (_isUserBubble(el)) return;
    meta = meta || {};
    var hitlLive = false;
    try {
      hitlLive = !!(el.querySelector('.message-content > .hitl-approval-card button:not([disabled])'));
    } catch (eHitl) { hitlLive = false; }
    var holdOpen = hitlLive || _awaitingApproval;
    var terminal = !holdOpen && (status === 'done' || status === 'paused'
      || meta.source === 'hydrate' || meta.source === 'resync');
    var html = _activityRowsHtml(activity);
    if (!html) return;
    var tools = (html.match(/data-kind="tool"/g) || []).length;
    var steps = (html.match(/<li /g) || []).length;
    _progressToolCount = tools;
    _progressStepCount = steps;
    _rescueTurnDom(el);
    if (terminal) {
      var contentHost = _bubbleContent(el) || el;
      var existingCot = _directChildByClass(contentHost, 'agent-progress');
      var liveActive = existingCot && existingCot.classList.contains('is-active')
        && !existingCot.classList.contains('kazma-cot-restored');
      if (liveActive) return;
      var cot = _buildRestoredWorkbench(activity);
      if (!cot) return;
      // The turn you are reading stays OPEN: inherit the live panel's
      // expansion. finalizeProgress deliberately never collapses at the
      // terminal frame (layout-shift flash) — the restore-swap must not
      // smuggle the collapse back in. History restores (no live panel) keep
      // the collapsed one-line summary.
      if (existingCot && !existingCot.classList.contains('is-collapsed')) {
        cot.classList.remove('is-collapsed');
        var cotChev = cot.querySelector('.agent-progress-chevron');
        if (cotChev) cotChev.textContent = '▾';
        var cotHead = cot.querySelector('.agent-progress-header');
        if (cotHead) cotHead.setAttribute('aria-expanded', 'true');
      }
      if (existingCot) {
        var nestedMsgs = existingCot.querySelectorAll('.message');
        for (var ni = 0; ni < nestedMsgs.length; ni++) {
          contentHost.insertBefore(nestedMsgs[ni], existingCot);
        }
        var cotHitl = existingCot.querySelectorAll('.hitl-approval-card');
        var hitlAnchor = existingCot.nextSibling;
        for (var chi = 0; chi < cotHitl.length; chi++) {
          if (hitlAnchor) contentHost.insertBefore(cotHitl[chi], hitlAnchor);
          else contentHost.appendChild(cotHitl[chi]);
        }
        existingCot.replaceWith(cot);
      } else {
        var tw = _directChildByClass(contentHost, 'message-text');
        if (tw) contentHost.insertBefore(cot, tw);
        else contentHost.appendChild(cot);
      }
      _rescueTurnDom(el);
      return;
    }
    var prev = currentMsgEl;
    currentMsgEl = el;
    // LIVE turn: the docked Live Task Card owns the live view (header +
    // expandable compact steps). No in-bubble live panel is created — the
    // terminal branch above swaps in the durable one-line summary when the
    // turn ends. A live panel that already exists (hydrate-hold, legacy
    // paint) is left untouched; the terminal swap replaces it.
    _progressToolCount = tools;
    _progressStepCount = steps;
    _taskCardEvent({ t: 'doc' });
    var existingLive = _directChildByClass(_bubbleContent(el) || el, 'agent-progress');
    if (!(existingLive && existingLive.classList.contains('is-active')
          && !existingLive.classList.contains('kazma-cot-restored'))) {
      currentMsgEl = prev || el;
      return;
    }
    var panel = ensureProgressPanel();
    currentMsgEl = prev || el;
    if (!panel) return;
    if (holdOpen) {
      // Re-activate but never auto-expand: an approval pause must not
      // spring a collapsed panel open under the reader (2026-09-03).
      panel.classList.remove('is-done');
      panel.classList.add('is-active');
    }
    var list = panel.querySelector('.agent-progress-steps');
    if (!list) return;
    if (list._kzCotHTML === html) return;
    list._kzCotHTML = html;
    list.innerHTML = html;
    _wireStepToggles(list);
    var countEl = panel.querySelector('.agent-progress-count');
    if (countEl) {
      countEl.textContent = steps + ' ' + (steps === 1 ? ti('step', 'step') : ti('steps', 'steps'));
    }
  }

  function _paintHitlFromDoc(el, doc) {
    var parts = (doc && doc.parts) || [];
    var hitl = null;
    for (var i = parts.length - 1; i >= 0; i--) {
      if (parts[i] && parts[i].type === 'hitl') { hitl = parts[i]; break; }
    }
    if (!hitl) return;
    var state = String(hitl.state || 'pending');
    var iid = _hitlInterruptIdOf(hitl);
    // ── Gate registry (P2): a live gate row is DECISION TRUTH and overrides
    // any stale part stamp. `pending` means nobody has clicked — the card
    // renders live buttons no matter what an old part claims (kills the
    // pre-approved stamp). `claimed`/`resuming` means the decision is made.
    var gateRow = null;
    if (iid && _serverGates && _serverGates.length) {
      for (var gi = 0; gi < _serverGates.length; gi++) {
        if (String(_serverGates[gi].gate_id || '') === String(iid)) {
          gateRow = _serverGates[gi];
          break;
        }
      }
    }
    var prev = currentMsgEl;
    if (el) currentMsgEl = el;
    try {
      if (gateRow && gateRow.state === 'pending' && hitl.payload) {
        renderHitlCard(hitl.payload, { lock: true });
        return;
      }
      if (gateRow && (gateRow.state === 'claimed' || gateRow.state === 'resuming')) {
        if (state === 'pending') state = 'inflight';
      }
      // A finished turn must not revive a live Approve card on refresh.
      // Inflight ONLY when this interrupt was actually claimed (this tab
      // clicked, or the server gate/persist says so).
      // The HITL-wait flag is set when the pending card first appears —
      // using it here stamped "Approved — running…" with no click
      // (dashboard still had live buttons, 2026-09-01).
      // A live turn is generating before the interrupt is marked paused;
      // that pair is also not a claim.
      if (state === 'pending' && hitl.payload) {
        // Registry-authoritative fail posture: the server answered with the
        // live-gates list and NO row covers this interrupt. Without registry
        // evidence of a claim, chat must never invent "Approved" from
        // leftover status or old parts — render live buttons. A stale click
        // is recoverable (the server re-verifies and answers "no longer
        // pending"); a fabricated Approved stamp is the incident.
        if (_serverGatesAuth && !gateRow) {
          // Live buttons (never invent Approved) but do NOT lock the
          // composer — an empty authoritative list means no live gate,
          // so the next prompt is a new turn, not /steer.
          renderHitlCard(hitl.payload, { lock: false });
          return;
        }
        // Registry did not answer: thin fallback. Never invent Approved
        // from leftover status. Paint idempotency (_hitlAlreadyClaimed)
        // still blocks cloning the SAME interrupt's live card. Hydrate
        // without a pending gate row must not steal the next send as /steer.
        if (_hitlAlreadyClaimed(hitl)) {
          state = 'inflight';
        } else {
          renderHitlCard(hitl.payload, { lock: false });
          return;
        }
      }
      var host = el || currentMsgEl;
      var card = _findHitlCard(iid, host);
      if (!card && hitl.payload && (state === 'timeout' || state === 'denied' || state === 'approved' || state === 'inflight' || state === 'settled')) {
        renderHitlCard(hitl.payload, { lock: false });
        host = el || currentMsgEl;
        card = _findHitlCard(iid, host);
      }
      if (!card) return;
      card.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
      // A settled card must stop counting down. This projector disabled the
      // buttons but left the ticker running, so an approved card kept
      // advertising "auto-denies if unanswered in 3:59" under an "Approved"
      // stamp (only the click path and the timeout path stopped it).
      _stopHitlCountdown(card);
      var cdRow = card.querySelector('.hitl-countdown');
      if (cdRow && cdRow.parentNode) cdRow.parentNode.removeChild(cdRow);
      if (state === 'timeout' || state === 'denied' || state === 'error') {
        card.className = 'hitl-approval-card hitl-' + (state === 'error' ? 'error' : 'denied');
        var deniedActions = card.querySelector('.hitl-approval-actions');
        if (deniedActions) {
          var errLabel = state === 'timeout'
            ? 'Approval timed out — continuing without this tool.'
            : (state === 'error' ? 'No longer pending' : 'Denied');
          deniedActions.innerHTML = '<span class="hitl-status hitl-' +
            (state === 'error' ? 'error' : 'denied') + '">' +
            escapeHtml(errLabel) + '</span>';
        }
        // This projector runs on the journal's claim frame — right AFTER
        // the click path already parked+collapsed the card. The wholesale
        // className assignment above used to strip `hitl-collapsed`, so
        // the card sprang back open mid-reply with a stranded chip in the
        // header (2026-09-03). Re-assert the claim treatment.
        _parkClaimedHitlCard(card);
        _collapseClaimedHitlCard(card);
      } else if (state === 'approved' || state === 'inflight' || state === 'settled') {
        card.className = 'hitl-approval-card hitl-approved';
        var okActions = card.querySelector('.hitl-approval-actions');
        if (okActions) {
          var okLabel = state === 'inflight' ? 'Approved — running\u2026' : 'Approved';
          okActions.innerHTML = '<span class="hitl-status hitl-approved">' + okLabel + '</span>';
        }
        _parkClaimedHitlCard(card);
        _collapseClaimedHitlCard(card);
      }
    } finally {
      currentMsgEl = prev || el;
    }
  }

  /**
   * Law 3: the only DOM writer for the live assistant bubble + restored CoT.
   * Transports mutate the in-memory TurnDocument; this paints it.
   */
  function renderTurn(doc, meta) {
    if (!doc || !messagesEl) return;
    meta = meta || {};
    var TD = window.KazmaTurnDocument;
    if (!TD) return;
    var turnId = String(doc.turnId || '');
    var el = null;
    // 'live' is a PLACEHOLDER, not an identity — applyTurnEvent falls back to
    // it for every frame the server has not yet stamped with a real turn id,
    // which is most of them at the start of a turn. Matching on it made any
    // bubble left carrying data-turn-id="live" a permanent magnet: the NEXT
    // turn's first tokens painted into that old bubble, above the new user
    // message, and when a frame finally arrived with the real id the stale
    // bubble was 'historical' (a user row now follows it) so a second bubble
    // was minted at the end — the same reply above AND below (2026-09-03).
    // The open turn is anchored by currentMsgEl, which is what the fallback
    // below already uses.
    if (turnId && turnId !== 'live') {
      try {
        el = messagesEl.querySelector('.message-assistant[data-turn-id="' + _cssEscapeAttr(turnId) + '"]');
      } catch (eSel) { el = null; }
    }
    // A doc with nothing to SHOW in a bubble (progress rows only — the Live
    // Task Card's territory) must never mint one. beginTurn seeds a
    // "Thinking…" progress row, which used to land here with currentMsgEl
    // freshly nulled and open every turn with an empty bubble.
    var _paintable = !!_answerFromDoc(TD, doc) || _docHasBubbleContent(doc);
    if (!el) el = currentMsgEl || _assistantBubbleForOpenTurn(_paintable);
    if (_isUserBubble(el) || _isUserBubble(currentMsgEl)) {
      currentMsgEl = null;
      el = _assistantBubbleForOpenTurn(_paintable);
    }
    if (!el) el = _assistantBubbleForOpenTurn(_paintable);
    if (!el) {
      // Progress-only frame with no bubble yet: the card is the surface.
      _taskCardEvent({ t: 'doc' });
      return;
    }
    // A bubble FOLLOWED by a user message belongs to a closed historical
    // turn: paint it, but never let it capture the open-turn pointer. A late
    // hydrate/resync for the previous turn used to re-pin its bubble as
    // currentMsgEl; the next turn's first token then painted INTO that old
    // bubble and re-stamped its turn id — the two replies crossed bubbles,
    // seen as "my new message's answer appeared above the previous reply"
    // (2026-09-02).
    var _prevOpenEl = currentMsgEl;
    var _historical = false;
    for (var _sib = el.nextElementSibling; _sib; _sib = _sib.nextElementSibling) {
      if (_sib.classList && _sib.classList.contains('message-user')) { _historical = true; break; }
    }
    if (!_historical) currentMsgEl = el;
    // Never stamp the placeholder (see the lookup above): a real server turn
    // id identifies a bubble, 'live' identifies nothing.
    if (turnId && turnId !== 'live') {
      try { el.setAttribute('data-turn-id', turnId); } catch (eAttr) { /* ignore */ }
    }
    _rescueTurnDom(el);
    var text = _answerFromDoc(TD, doc);
    if (text) tokenAccum = text;
    var host = _bubbleContent(el) || el;
    var textEl = _directChildByClass(host, 'message-text');
    if (!textEl) {
      var fallback = el.querySelector('.message-text');
      if (fallback && !(fallback.closest && fallback.closest('.message-user'))) textEl = fallback;
    }
    if (textEl && text) {
      tryIngestPlanFromText(text);
      var display = _scrubDsml(stripPlanFenceForDisplay(text));
      try {
        if (doc.status === 'streaming') {
          _scheduleLiveTextPaint(textEl);
        } else {
          _paintHTML(textEl, _renderReplyHTML(text));
        }
      } catch (mdErr) {
        if (textEl.textContent !== display) textEl.textContent = display;
      }
      try { textEl.setAttribute('data-md', text); } catch (eMd) { /* ignore */ }
      try { textEl.setAttribute('data-final-len', String(display.length)); } catch (eLen) { /* ignore */ }
      // A historical paint must not mark the CURRENT turn as painted — the
      // "No response received." fallback keys off this for the live turn.
      if (!_historical) _turnPainted = true;
    }
    if (doc.model) {
      var metaEl = el.querySelector('.message-meta');
      if (metaEl && String(metaEl.textContent || '').indexOf(doc.model) < 0) {
        metaEl.textContent = (metaEl.textContent ? metaEl.textContent + ' · ' : '') + doc.model;
      }
    }
    var activity = TD.activityOf(doc.parts);
    _syncCotPanel(el, activity, doc.status, meta);
    _paintHitlFromDoc(el, doc);
    _rescueTurnDom(el);
    // A closed turn's render must not release the OPEN turn's wait state
    // (a late turn-N hydrate mid-turn-N+1 used to clear _awaitingReply,
    // disabling the cursor-resume if the live stream then died).
    if (!_historical && (doc.status === 'done' || meta.source === 'resync' || meta.source === 'hydrate' || meta.source === 'capacity' || meta.source === 'done')) {
      _awaitingReply = false;
    }
    if ((meta.source === 'resync' || meta.source === 'hydrate') && !activeStream) {
      currentMsgEl = null;
    }
    // Interior painters (_syncCotPanel/_paintHitlFromDoc) save/restore
    // currentMsgEl as `prev || el` — for a historical render that restore
    // re-pins the old bubble when prev was null. Force the pointer back to
    // whatever the open turn owned on entry.
    if (_historical) currentMsgEl = _prevOpenEl;
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

  // Expose for inline handlers + agentStore turn lifecycle bridge
  window.KazmaChat = {
    sendMessage: sendMessage,
    newSession: newSession,
    retry: retry,
    destroy: destroyChatMouth,
    toggleArchivedView: toggleArchivedView,
    /** Live Task Card single-writer dispatch (WS store + SSE both feed it). */
    taskCard: _taskCardEvent,
    _hitlApproval: renderHitlCard,
    markApprovalTimedOut: markApprovalTimedOut,
    hasInlineApprovalCard: hasInlineApprovalCard,
    hitlCardExistsFor: hitlCardExistsFor,
    beginTurn: beginTurn,
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
    paintCapacityReply: function(reply) {
      if (!reply || !messagesEl) return;
      var incoming = String(reply).trim();
      if (!incoming) return;
      applyTurnEvent({
        type: 'capacity',
        reply: incoming,
        turn_id: _liveTurnId,
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
      tokenAccum = '';
      noteTurnActivity();
      // Keep currentMsgEl so renderTurn paints into the same turn bubble.
    },
    appendLiveToken: function(content, opts) {
      noteTurnActivity();
      _clearStatusStrip();
      activeTypingEl = null;
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
      activeTypingEl = null;
      logProgress({ kind: 'error', title: ti('error', 'Error'), detail: String(errMsg || ''), state: 'failed' });
      _pinLiveAssistantBubble();
      var textEl = currentMsgEl.querySelector('.message-text');
      if (textEl) textEl.innerHTML = '<div class="error-message" style="display:flex;align-items:flex-start;gap:6px;">' +
        (window.KazmaIcons ? KazmaIcons.span('alert') : '') + escapeHtml(errMsg) + '</div>';
      // endTurn is invoked by agentStore after graph_error; keep bubble closed.
      finalizeProgress(false);
      currentMsgEl = null;
      tokenAccum = '';
    },

    // Voice streaming hooks — called by voice.js WebSocket client
    onUserTranscription: function(text) {
      appendMessage('user', text);
      scrollToBottom();
      beginTurn();
    },
    onStreamToken: function(content) {
      _clearStatusStrip();
      _pinLiveAssistantBubble();
      tokenAccum += content;
      tryIngestPlanFromText(tokenAccum);
      var textEl = currentMsgEl.querySelector('.message-text');
      // Funnels through the shared render + idempotent paint: these two
      // sites skipped _scrubDsml, so scaffolding showed while streaming
      // and vanished on the terminal paint - a guaranteed end-of-reply
      // flash.
      if (textEl) _paintHTML(textEl, _renderReplyHTML(tokenAccum));
      scrollToBottom();
    },
    onStreamDone: function() {
      endTurn();
    },
  };
})();
