/* ═══════════════════════════════════════════════════════
   Kazma Chat — Full-featured chat interface
   Uses SSE streaming for real-time responses
   ═══════════════════════════════════════════════════════ */

(function() {
  'use strict';
  var KS = window.KazmaStream;
  var chatSessionId = null;
  var currentMsgEl = null;
  var tokenAccum = '';
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
      // loadSession is in-flight-guarded and loadModels() below triggers
      // the load for the same saved id — the old +100ms duplicate schedule
      // caused a double fetch/render flicker on every boot (audit P1-6).
      // Turn Delivery V2: reconcile with server truth (a turn may have
      // finished or still be running while the page was closed).
      _resyncDelivery('init');
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
      var lastMsg = messages.length ? messages[messages.length - 1] : null;

      // Still running server-side → keep waiting honestly AND re-attach a
      // live SSE stream from the journal cursor — but only when the stream
      // is genuinely DEAD. Aborting a healthy stream on every focus/visibility
      // trigger churned connections for no gain.
      if (generating) {
        if (activeStream) {
          // A live stream owns this turn — NEVER abort it here. Aborting a
          // healthy stream forced a journal-cursor reopen whose replay
          // painted terminal segments, fragmenting one reply into multiple
          // bubbles each with its own "Writing reply…" row (2026-08-27
          // post-restart). The live stream IS the delivery path; a genuinely
          // dead stream is handled below (no activeStream → reopen).
          try {
            _setStatusStrip(ti('thinking', 'Kazma is thinking…'));
          } catch (e2) { /* ignore */ }
          return;
        }
        _awaitingReply = true;
        noteTurnActivity();
        try {
          _setStatusStrip(ti('thinking', 'Kazma is thinking…'));
        } catch (e2) { /* ignore */ }
        if (_reopenSseRef) {
          try { _reopenSseRef('resync-' + (reason || '?')); } catch (e3) { /* ignore */ }
        }
        return;
      }

      // Server idle with a durable assistant answer → paint server truth,
      // unconditionally (applyFinal replaces the open-turn bubble).
      if (lastMsg && lastMsg.role === 'assistant' && (lastMsg.content || '').trim() && !lastMsg.pending) {
        if (window.KazmaChat && typeof window.KazmaChat.applyFinalAssistantText === 'function') {
          window.KazmaChat.applyFinalAssistantText(lastMsg.content, lastMsg.model || '', { source: 'resync' });
        }
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
   * Assistant bubble for the open turn: the one after the last user message.
   * NEVER create a second assistant without a new user row (duplicate root cause).
   */
  function _assistantBubbleForOpenTurn() {
    if (!messagesEl) return createAssistantMessage();
    var msgs = messagesEl.querySelectorAll('.message-user, .message-assistant');
    var lastAsstAfterUser = null;
    for (var i = 0; i < msgs.length; i++) {
      if (msgs[i].classList.contains('message-user')) {
        lastAsstAfterUser = null;
      } else if (msgs[i].classList.contains('message-assistant')) {
        lastAsstAfterUser = msgs[i];
      }
    }
    if (lastAsstAfterUser) return lastAsstAfterUser;
    return createAssistantMessage();
  }

  // ── Slash commands (discoverable in Web UI) ───────────
  var SLASH_COMMANDS = [
    { cmd: '/yolo', desc: 'Skip danger-tool approvals for this session (TTL)' },
    { cmd: '/yolo off', desc: 'Restore HITL approvals + clear tool grants' },
    { cmd: '/yolo status', desc: 'Show YOLO / grant status for this session' },
    { cmd: '/long', desc: 'Show iteration budget + HITL status' },
    { cmd: '/long on', desc: 'Research budget (40 rounds) — HITL still on' },
    { cmd: '/long mission', desc: 'Run until done (hard wall ~500 rounds)' },
    { cmd: '/long yolo', desc: 'Research budget AND skip danger-tool approvals' },
    { cmd: '/unrestricted', desc: 'Mission + YOLO — finish this job, don’t ask' },
    { cmd: '/unrestricted off', desc: 'Restore Settings budget + HITL' },
    { cmd: '/long off', desc: 'Budget only off (HITL unchanged)' },
    { cmd: '/plan', desc: 'Show plan-mode status (inspect then propose)' },
    { cmd: '/plan on', desc: 'Plan mode — write/exec tools blocked until /plan go' },
    { cmd: '/plan go', desc: 'Approve the plan and execute (HITL still on)' },
    { cmd: '/plan off', desc: 'Leave plan mode' },
    { cmd: '/new', desc: 'Start a new chat session' },
    { cmd: '/reset', desc: 'Clear this conversation history' },
    { cmd: '/steer', insert: '/steer ', desc: 'Queue a note for the running task — edit, then Enter' },
    { cmd: '/steer!', insert: '/steer! ', desc: 'Pause the running task and inject a requirement' },
    { cmd: '/abort', desc: 'Stop and abandon the running task' },
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

  // ── Input handling ────────────────────────────────────
  function onInputKeydown(e) {
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
   * Single owner of the top status strip (#thinking-indicator): the Alpine
   * store (isThinking + statusMessage). The old imperative
   * KS.showTyping/hideTyping inline styles fought Alpine's x-show over the
   * same element — combined with beginTurn never setting the store flag,
   * the strip appeared only when WS frames happened to arrive and vanished
   * mid-turn on idle/approval frames (the intermittent "no status bar",
   * 2026-08-26).
   */
  function _setStatusStrip(msg) {
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent')) {
        var st = Alpine.store('agent');
        st.isThinking = true;
        if (msg) st.statusMessage = msg;
      }
    } catch (e) { /* store not ready */ }
  }
  function _clearStatusStrip() {
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent')) {
        Alpine.store('agent').isThinking = false;
      }
    } catch (e) { /* store not ready */ }
  }

  function beginTurn() {
    _isGenerating = true;
    _awaitingApproval = false;
    _lastTurnActivityTs = Date.now();
    _serverActivitySeen = false;
    // Status strip shows the instant ANY turn starts (SSE, WS, or
    // approve-resume) — no longer dependent on WS frames arriving.
    _setStatusStrip(ti('thinking', 'Kazma is thinking\u2026'));
    // Keep visibility recovery armed even if no token frames arrive before
    // the user switches tabs (WS can be silent for seconds at turn start).
    _armTurnWatchdog();
    // Fresh progress log for this turn (don't reuse previous bubble's panel)
    if (currentMsgEl) {
      var oldProg = currentMsgEl.querySelector('.agent-progress');
      if (oldProg) oldProg.remove();
    }
    _progressEl = null;
    _progressStepCount = 0;
    _progressToolCount = 0;
    _planItems = [];
    _planParsedFromText = false;
    _lastTurnStats = null;
    logProgress({ kind: 'status', title: ti('thinking', 'Kazma is thinking\u2026'), state: 'running' });
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
    if (activeTypingEl && KS.hideTyping) {
      KS.hideTyping(activeTypingEl);
    }
    activeTypingEl = null;
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
    _isGenerating = false;
    _awaitingApproval = true;
    if (activeTypingEl && KS.hideTyping) KS.hideTyping(activeTypingEl);
    activeTypingEl = null;
    _clearStatusStrip();
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
        var ct = (r.headers.get('content-type') || '').toLowerCase();
        if (ct.indexOf('text/event-stream') >= 0) {
          return { ok: r.ok, mode: 'hard', streamed: true };
        }
        return r.ok ? r.json() : r.json().catch(function() { return { ok: false }; });
      }).then(function(body) {
        if (body && body.ok === false && window.showToast) {
          if (body.reason === 'no_active_task') {
            window.showToast('No active task to steer.', 'info', 3000);
          } else if (body.reason) {
            window.showToast('Steer failed: ' + body.reason, 'error', 3500);
          }
        } else if (body && body.demoted && window.showToast) {
          window.showToast(
            'Steer will apply on the next step (could not pause in time).',
            'info', 3500);
        }
      }).catch(function() { /* best-effort */ });
      return;
    }

    // During HITL, a normal message is a soft steer — don't start a new turn.
    if (_awaitingApproval && text && text.charAt(0) !== '/') {
      appendMessage('user', '/steer ' + text);
      inputEl.value = '';
      inputEl.style.height = 'auto';
      if (window.showToast) window.showToast(
        '🧭 Steering the paused task with your note.', 'info', 3000);
      fetch('/api/chat/steer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: chatSessionId || '',
          thread_id: currentThreadId(),
          text: text,
          mode: 'soft',
        }),
        credentials: 'same-origin',
      }).catch(function() { /* best-effort */ });
      return;
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

    // Start a clean assistant turn (must clear currentMsgEl *before* beginTurn
    // so progress attaches to a new bubble, not the previous reply).
    currentMsgEl = null;
    tokenAccum = '';
    _turnPainted = false;
    disableInput(); // → beginTurn → progress panel on new assistant bubble

    // Reset attachment state (chips above the box, not placeholder text)
    clearPendingAttachments();
    inputEl.value = '';
    inputEl.style.height = 'auto';
    inputEl.placeholder = _defaultPlaceholder();
    syncInputBidi();
    updateComposerCharCount();

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

    // Hidden-tab UX (P4): permission may only be requested from a user
    // gesture — arm it on send.
    try {
      if (window.KazmaTurnVisibility && KazmaTurnVisibility.armPermission) {
        KazmaTurnVisibility.armPermission();
      }
    } catch (e) { /* ignore */ }

    // Graph turns always go over HTTP SSE. The WebSocket is telemetry /
    // cursor resume only (industry stack part 5). Do not re-route send
    // through agentStore.sendPrompt — that is a second graph client.
    // Turn Delivery V2 cursor resume: a stream lost mid-turn (sleep / proxy
    // cull / hidden-tab freeze) retries ONCE from its last journaled seq
    // (`last_event_id`); the server replays exactly what was missed. No
    // pollers — one retry, then reconcile from the durable store.
    var _sseAttempts = 0;
    // Last journaled seq seen this session (survives stream-object death so
    // a resync-triggered re-attach can resume from the right cursor).
    var _lastSeqSeen = 0;
    function _noteSeq() {
      if (activeStream && typeof activeStream.lastEventId === 'function') {
        var sid = Number(activeStream.lastEventId());
        if (sid > 0) _lastSeqSeen = sid;
      }
    }
    /**
     * Re-attach the SSE stream from the journal cursor WITHOUT sending a new
     * prompt (the server treats last_event_id as attach-only). Delivery rule
     * (2026-08-26): resync used to leave a generating turn with NO live
     * transport — an undisturbed visible tab then painted the reply only on
     * manual refresh.
     */
    function _reopenSse(reason) {
      if (activeStream) return;               // already live
      if (!_awaitingReply || _awaitingApproval) return;
      if (_lastSeqSeen <= 0) return;          // no cursor → replaying from 0 risks duplication
      if (_reopenCount >= _REOPEN_MAX) return; // bounded: gap-attach loops must die out
      _reopenCount++;
      console.warn('[KazmaChat] Re-attaching SSE stream (' + reason + ') from seq=' + _lastSeqSeen);
      noteTurnActivity();
      _dispatchSse({ last_event_id: _lastSeqSeen });
    }
    _reopenSseRef = _reopenSse;

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
      activeStream = KS.sse('/api/chat/stream', body, buildSseCallbacks(++_sseEpoch));
    }

    function buildSseCallbacks(epoch) {
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
        _outboxClear();  // first streamed token = the server received the send
        // NOTE: do NOT clear the status strip per token. The strip sits
        // IN-FLOW between transcript and composer — every hide/show shifts
        // the composer ~33px, resizes the transcript viewport and makes the
        // streaming text bounce (the flicker). While tokens flow the strip
        // stays steady ("Writing reply…"); terminal paths (done/error/
        // endTurn) are the only ones allowed to hide it.
        activeTypingEl = null;
        if (!currentMsgEl) {
          currentMsgEl = createAssistantMessage();
        }
        if (!tokenAccum) {
          logProgress({ kind: 'status', title: ti('writing_reply', 'Writing reply\u2026'), state: 'running' });
        }
        tokenAccum += data.content;
        tryIngestPlanFromText(tokenAccum);
        var textEl = currentMsgEl.querySelector('.message-text');
        _scheduleLiveTextPaint(textEl);
        scrollToBottom();
      },

      onToolCall: function(data) {
        if (!_mine()) return;
        noteTurnActivity();
        if (!currentMsgEl) currentMsgEl = createAssistantMessage();
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
          logProgress({
            kind: 'status',
            title: title,
            detail: (data && data.message && status !== 'thinking') ? data.message : '',
            state: 'running',
          });
          if (typingEl && KS.showTyping) {
            try { _setStatusStrip(title); } catch (e) { /* ignore */ }
          }
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
          window.KazmaChat.applyFinalAssistantText(data.content, data.model || '', { source: 'done' });
        }
        // Never leave a blank turn after "Thinking…" (empty stream / missed HITL).
        // _turnPainted: a late stale terminal must NEVER print this after a
        // successful reply already painted (the trailing "_No response
        // received." under the posted-tweets answer, 2026-08-26).
        if (!tokenAccum && !currentMsgEl && !interrupted && !_awaitingApproval
            && !_turnPainted) {
          diag('empty-terminal');
          dumpDiagnostics();
          currentMsgEl = createAssistantMessage();
          var emptyEl = currentMsgEl.querySelector('.message-text');
          if (emptyEl) {
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
        if (truncated && !_awaitingApproval) {
          setTimeout(function() { _resyncDelivery('sse-truncated'); }, 400);
        }
        // Interrupted (HITL) turn with no rendered card anywhere = silently
        // paused. Recover the card from server truth, best-effort one shot.
        // `truncated` (stream died with no terminal frame — client refresh /
        // tab switch) is included: the interrupt event may have fired AFTER
        // this tab's stream dropped, so `interrupted` stays false and the
        // pending approval would otherwise be invisible until auto-deny.
        if ((interrupted || truncated) && !hasInlineApprovalCard() && !_awaitingApproval) {
          setTimeout(recoverMissedApproval, 1200);
        }
        }
      },

      onApprovalRequired: function(data) {
        if (!_mine()) return;
        // HITL: graph paused — render scope-aware approval card and lock input.
        if (data && data.thread_id) _lastInterruptedThreadId = String(data.thread_id);
        _clearStatusStrip();
        activeTypingEl = null;
        logProgress({
          kind: 'status',
          title: 'Waiting for approval',
          detail: (data && (data.tool || data.message)) || '',
          state: 'info',
        });
        pauseForApproval(data);
        renderHitlCard(data);
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
        // One cursor resume while the turn is still awaited — only possible
        // if we actually saw a journaled id on the dead stream.
        if (_sseAttempts <= 2 && _awaitingReply && !_awaitingApproval
            && lastId != null && Number(lastId) > 0) {
          console.warn('[KazmaChat] SSE stream lost at seq=' + lastId + ' — resuming');
          noteTurnActivity();
          try {
            _setStatusStrip(ti('thinking', 'Kazma is thinking…'));
          } catch (_t) {}
          _dispatchSse({ last_event_id: Number(lastId) });
          return;
        }
        // Final failure: surface it, then reconcile with server truth (the
        // turn may have completed server-side and be durable already).
        _clearStatusStrip();
        activeTypingEl = null;
        if (!currentMsgEl) currentMsgEl = createAssistantMessage();
        var textEl = currentMsgEl.querySelector('.message-text');
        textEl.innerHTML = '<div class="error-message">\u26A0 ' + escapeHtml(msg) +
          '<br><button class="btn btn-sm btn-danger" onclick="window.KazmaChat.retry()">Retry</button></div>';
        endTurn();
        _resyncDelivery('sse-fail');
        // A dead stream can also mean the turn parked on a HITL interrupt
        // server-side that this tab never rendered — surface the approval
        // card from server truth so the user can act before auto-deny.
        setTimeout(recoverMissedApproval, 800);
        if (msg && window.showToast) {
          try { window.showToast(String(msg), 'error', 4000); } catch (_t) {}
        }
      }
      };
    }

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
    var panel = ensureProgressPanel();
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
    if (!currentMsgEl) currentMsgEl = createAssistantMessage();
    var content = currentMsgEl.querySelector('.message-content');
    if (!content) return null;
    var panel = content.querySelector('.agent-progress');
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
    var textEl = content.querySelector('.message-text');
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
    var panel = ensureProgressPanel();
    if (!panel) return;
    // Merge unique plan lines (keep order)
    items.forEach(function(raw) {
      var text = String(raw || '').replace(/^[\-\*\d\.\)\s]+/, '').trim();
      if (!text || text.length < 2) return;
      var exists = _planItems.some(function(p) {
        return p.text.toLowerCase() === text.toLowerCase();
      });
      if (!exists) _planItems.push({ text: text, done: false });
    });
    _renderPlanList(panel);
    scrollToBottom();
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
    var panel = ensureProgressPanel();
    if (!panel) return;

    var kind = step.kind || 'status';
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

    // Plan lines go to the sticky plan list (not the activity log)
    if (kind === 'plan') {
      if (detail) {
        setPlan(detail.split('\n'));
      } else {
        setPlan([title]);
      }
      return;
    }

    if (kind === 'tool') _setCotPhase('act');
    else if (/synth|compos|writing reply/i.test(rawTitle + ' ' + title)) _setCotPhase('write');
    else _setCotPhase('think');

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
    panel.classList.remove('is-collapsed', 'is-done');
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
    // Stay expanded — user can collapse; tool results remain visible
    panel.classList.remove('is-collapsed');
    var chev = panel.querySelector('.agent-progress-chevron');
    if (chev) chev.textContent = '\u25BE';
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

    // Restore the persisted CoT workbench (activity log) for assistant
    // messages when returning to a session after refresh / tab switch.
    if (role === 'assistant' && opts && opts.activity && opts.activity.length) {
      var cotPanel = _buildRestoredWorkbench(opts.activity);
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

  function _paintLiveTextNow(textEl, final) {
    if (!textEl) return;
    if (final) {
      textEl.innerHTML = transformRenderedForPlan(KS.markdown(_scrubDsml(stripPlanFenceForDisplay(tokenAccum))));
      // Re-apply dir="auto" after innerHTML (the attribute survives but the
      // bidi direction may need recalculating for the new content).
      textEl.setAttribute('dir', 'auto');
      if (window.KazmaBidi) KazmaBidi.apply(textEl, tokenAccum);
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
      textEl.innerHTML = transformRenderedForPlan(KS.markdown(liveParts.prose));
      if (window.KazmaBidi) KazmaBidi.apply(textEl, liveParts.prose);
    } else {
      textEl.textContent = '\u00a0';
    }
    textEl.setAttribute('dir', 'auto');
  }

  function _scheduleLiveTextPaint(textEl) {
    if (!textEl) return;
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
    if (_liveRenderEl) {
      _liveRenderLastAt = Date.now();
      _liveRenderDirty = false;
      _paintLiveTextNow(_liveRenderEl, true);
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

  /** Hide the chat.html bottom Alpine approval card (driven by the store). */
  function _clearStoreApproval() {
    try {
      if (window.Alpine && Alpine.store && Alpine.store('agent')) {
        Alpine.store('agent').pendingApproval = null;
      }
    } catch (e) { /* ignore */ }
  }

  /** Server-truth recovery: an interrupted turn whose approval card never
   *  rendered is a SILENTLY PAUSED turn — no card, no error, no progress
   *  (the 2026-08-26 "complete silence" X-post incident). One best-effort
   *  fetch of the pending list; render this session's card if present. */
  // Thread of the most recent interrupt seen by this tab (from approval
  // payloads / interrupted dones) — lets recoverMissedApproval match the
  // RIGHT pending approval instead of guessing (audit P2).
  var _lastInterruptedThreadId = '';

  function recoverMissedApproval() {
    if (hasInlineApprovalCard() || _awaitingApproval) return;
    fetch('/api/pending-approvals', { credentials: 'same-origin' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(payload) {
        if (hasInlineApprovalCard() || _awaitingApproval) return;
        var pending = (payload && Array.isArray(payload.pending)) ? payload.pending : [];
        if (!pending.length) return;
        var hit = null;
        // Preference order: the thread we saw interrupt → the session id →
        // (single-operator fallback) the only pending entry. The approve
        // endpoint's ownership check still guards cross-tenant abuse.
        var candidates = [_lastInterruptedThreadId, chatSessionId || ''];
        for (var c = 0; c < candidates.length && !hit; c++) {
          for (var i = 0; i < pending.length; i++) {
            var p = pending[i] || {};
            if (candidates[c] && String(p.thread_id || '') === candidates[c]) { hit = p; break; }
          }
        }
        if (!hit && pending.length === 1) hit = pending[0];
        if (!hit) return;
        console.warn('[KazmaChat] Recovering missed approval card for thread=' + hit.thread_id);
        renderHitlCard({
          thread_id: hit.thread_id,
          kind: hit.kind || 'security',
          tool: hit.tool_name || hit.tool || 'unknown',
          args: hit.arguments || hit.args || {},
          message: hit.message || '',
          yolo_allowed: hit.yolo_allowed !== false,
        });
      })
      .catch(function() { /* best-effort */ });
  }

  function renderHitlCard(data) {
    if (!data) return;
    // Idempotent: WS and SSE both deliver the approval (journal fan-out +
    // SSE frame). The FIRST render wins; a second live card for the same
    // interrupt duplicates buttons and double-fires resumes. Suppression
    // used to live in the WS store (skip when SSE is live) — but when the
    // SSE frame was late/lost NO card appeared at all and the paused turn
    // went completely silent (2026-08-26 X-post incident).
    if (hasInlineApprovalCard()) return;
    pauseForApproval(data);
    // The inline card is the primary approval UI. Hide the bottom Alpine card
    // (driven by $store.agent.pendingApproval) so the same approval is never
    // rendered twice — incident 2026-08-16: duplicated YOLO card (one inline,
    // one lower). The store fields submitApproval needs are set by the inline
    // card's own handlers.
    _clearStoreApproval();
    // Replace stale/disabled cards instead of stacking another YOLO view
    // after reconnect (hasInlineApprovalCard ignores disabled buttons).
    if (messagesEl) {
      messagesEl.querySelectorAll('.hitl-approval-card').forEach(function(old) {
        old.remove();
      });
    }
    var targetThreadId = data.thread_id || chatSessionId || '';
    if (!currentMsgEl) currentMsgEl = createAssistantMessage();
    var content = currentMsgEl.querySelector('.message-content');
    if (!content) return;

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
      content.appendChild(_semCard);
      scrollToBottom();
      _semCard.querySelectorAll('.hitl-sem-opt').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var optId = this.getAttribute('data-opt');
          _semCard.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
          var act = _semCard.querySelector('.hitl-approval-actions');
          if (act) act.innerHTML = '<span class="hitl-status">Resolving\u2026</span>';
          tokenAccum = '';
          beginTurn();
          var payload = { action: optId === 'cancel' ? 'deny' : 'approve', scope: 'once',
                          session_id: chatSessionId || '', choices: {} };
          payload.choices[_semTcid] = optId;
          fetch('/api/approve/' + encodeURIComponent(data.thread_id || targetThreadId), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          }).catch(function() {
            if (act) act.innerHTML = '<span class="hitl-status text-danger">Failed — retry</span>';
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

    var card = document.createElement('div');
    card.className = 'hitl-approval-card';
    // Server marks always-HITL batches (X ToU fail-safes) yolo_allowed=false —
    // offering a YOLO button there reads as "approve once" when it re-prompts.
    var yoloOk = data.yolo_allowed !== false;
    card.innerHTML =
      '<div class="hitl-approval-header">\u26A0 Approval Required</div>' +
      '<div class="hitl-approval-body">' +
        '<p><strong>Tool:</strong> <code>' + escapeHtml(data.tool || '') + '</code></p>' +
        (tools.length <= 1
          ? '<p><strong>Args:</strong> <code>' + escapeHtml(truncateStr(JSON.stringify(data.args || {}), 300)) + '</code></p>'
          : toolsHtml) +
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
    content.appendChild(card);
    scrollToBottom();

    function setCardState(state, label) {
      card.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
      card.className = 'hitl-approval-card hitl-' + state;
      var actions = card.querySelector('.hitl-approval-actions');
      if (actions) actions.innerHTML = '<span class="hitl-status hitl-' + state + '">' + label + '</span>';
    }

    function appendAssistantText(text) {
      if (!text) return;
      if (!currentMsgEl) currentMsgEl = createAssistantMessage();
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
      var pendingLabel = action === 'deny'
        ? 'Denying\u2026'
        : (scope === 'yolo' ? 'YOLO on \u2014 running\u2026'
          : (scope === 'tool' ? 'Granting tool \u2014 running\u2026' : 'Running approved tool\u2026'));
      setCardState('pending', pendingLabel);
      // Reset accum so post-approval final answer replaces (no pre-HITL + final concat).
      tokenAccum = '';
      beginTurn();

      // HITL resume is SSE (`POST /api/approve/{thread_id}`). WS approve_tool
      // is off unless KAZMA_WS_GRAPH=1; the browser never uses that path.
      if (scope === 'yolo' && KS.toast) {
        KS.toast('YOLO on for this session \u2014 danger tools auto-approved', 'warning', 4000);
      }
      if (scope === 'tool' && KS.toast) {
        KS.toast('Allowed ' + (data.tool || 'tool') + ' for this session (~30m)', 'success', 3000);
      }
      var payload = {
        action: action,
        scope: scope,
        session_id: chatSessionId || '',
        tool: data.tool || '',
      };

      if (!currentMsgEl) {
        currentMsgEl = createAssistantMessage();
      }

      var approvalTypingEl = KS.showTyping(currentMsgEl.querySelector('.message-content'));
      var approvalUrl = '/api/approve/' + encodeURIComponent(data.thread_id || targetThreadId);
      var sseFn = (KS && (KS.ssePost || KS.sse)) || (window.KazmaStream && (KazmaStream.ssePost || KazmaStream.sse));

      if (!sseFn) {
        setCardState('error', 'Streaming unavailable');
        endTurn();
        return;
      }

      if (activeStream) {
        try { activeStream.abort(); } catch (e) {}
        activeStream = null;
      }
      // The approve-resume stream now owns the turn: invalidate the original
      // main-stream callbacks (epoch) so its lingering terminal can never
      // paint the empty-notice or finalize over the resumed turn.
      _sseEpoch++;

      activeStream = sseFn(approvalUrl, payload, {
        onEvent: function() {},
        onToken: function(tokenData) {
          KS.hideTyping(approvalTypingEl);
          if (activeTypingEl) { KS.hideTyping(activeTypingEl); activeTypingEl = null; }
          if (!currentMsgEl) currentMsgEl = createAssistantMessage();
          tokenAccum += tokenData.content;
          _turnPainted = true;
          tryIngestPlanFromText(tokenAccum);
          var textEl = currentMsgEl.querySelector('.message-text');
          _scheduleLiveTextPaint(textEl);
          scrollToBottom();
        },
        onToolCall: function(toolData) {
          KS.hideTyping(approvalTypingEl);
          if (activeTypingEl) { KS.hideTyping(activeTypingEl); activeTypingEl = null; }
          if (!currentMsgEl) currentMsgEl = createAssistantMessage();
          var contentEl = currentMsgEl.querySelector('.message-content');
          var box = document.createElement('div');
          box.className = 'tool-call-box';
          box.innerHTML = '<span class="tool-name">\u2699 ' + escapeHtml(toolData.tool_name || toolData.name || 'tool') + '</span>' +
            '<code class="tool-inputs">' + escapeHtml(truncateStr(toolData.inputs || '{}', 200)) + '</code>' +
            '<span class="tool-status running">Running\u2026</span>';
          contentEl.appendChild(box);
          scrollToBottom();
        },
        onToolResult: function(resultData) {
          if (!currentMsgEl) return;
          var contentEl = currentMsgEl.querySelector('.message-content');
          var boxes = contentEl.querySelectorAll('.tool-call-box');
          var lastBox = boxes.length ? boxes[boxes.length - 1] : null;
          if (lastBox) {
            var statusEl = lastBox.querySelector('.tool-status');
            if (statusEl) { statusEl.textContent = 'Done'; statusEl.className = 'tool-status done'; }
          }
          var isSwarm = (resultData.tool_name === 'dispatch_swarm' || resultData.tool_name === 'swarm_dispatch' || (resultData.result && resultData.result.indexOf('Swarm task dispatched') !== -1));
          var resultBox = document.createElement('div');
          if (isSwarm) {
            resultBox.className = 'swarm-bg-badge';
            resultBox.innerHTML = '<span class="pulse-dot"></span><div><strong>Background Task Active:</strong> ' + escapeHtml(truncateStr(resultData.result, 300)) + '</div>';
          } else {
            resultBox.className = 'tool-result-box';
            resultBox.innerHTML = '<strong>Result:</strong> ' + escapeHtml(truncateStr(resultData.result, 500));
          }
          contentEl.appendChild(resultBox);
          scrollToBottom();
        },
        onApprovalRequired: function(nextApproval) {
          KS.hideTyping(approvalTypingEl);
          if (activeTypingEl) { KS.hideTyping(activeTypingEl); activeTypingEl = null; }
          var okLabel = action === 'deny' ? 'Denied \u2717'
            : (scope === 'yolo' ? 'YOLO on \u2713'
              : (scope === 'tool' ? 'Tool allowed \u2713' : 'Approved \u2713'));
          setCardState(action === 'approve' ? 'approved' : 'denied', okLabel);
          // Another danger tool after grant — should be rare for YOLO; surface card.
          // Do NOT eagerly create an assistant bubble here — tokens create it
          // lazily; an eager empty one stayed blank when the turn ended
          // (the stray "Thinking…"/empty containers, 2026-08-26).
          setTimeout(function() {
            tokenAccum = '';
            renderHitlCard(nextApproval);
          }, 40);
        },
        onDone: function(doneData) {
          KS.hideTyping(approvalTypingEl);
          if (activeTypingEl) { KS.hideTyping(activeTypingEl); activeTypingEl = null; }
          var okLabel = action === 'deny' ? 'Denied \u2717'
            : (scope === 'yolo' ? 'YOLO on \u2713'
              : (scope === 'tool' ? 'Tool allowed \u2713' : 'Approved \u2713'));
          setCardState(action === 'approve' ? 'approved' : 'denied', okLabel);

          if (scope === 'tool' && KS.toast) {
            KS.toast('Allowed ' + (data.tool || 'tool') + ' for this session (~30m)', 'success', 3000);
          }
          if (scope === 'yolo' && KS.toast) {
            KS.toast('YOLO on for this session \u2014 danger tools auto-approved', 'warning', 4000);
          }

          var interrupted = !!(doneData && doneData.interrupted);
          // Same rule as the main onDone: never after a painted reply
          // (sequential approvals clear tokenAccum/currentMsgEl — a later
          // resume terminal with cleared state used to print this UNDER
          // the successful answer).
          if (!tokenAccum && !currentMsgEl && !interrupted && !_awaitingApproval
              && !_turnPainted) {
            appendAssistantText('_No response received._');
          }

          if (doneData && (doneData.cost || doneData.tokens)) {
            if (currentMsgEl) {
              var meta = currentMsgEl.querySelector('.message-meta');
              if (meta) {
                var _toksLabel = ti('tokens', 'tokens');
                meta.innerHTML = '<span dir="auto">' + (doneData.tokens ? doneData.tokens.toLocaleString() + ' ' + _toksLabel : '') +
                  (doneData.cost ? ' \u2022 $' + doneData.cost.toFixed(4) : '') +
                  (doneData.duration_ms ? ' \u2022 ' + (doneData.duration_ms / 1000).toFixed(1) + 's' : '') +
                  '</span>';
              }
            }
          }

          if (interrupted || _awaitingApproval) {
            activeStream = null;
            if (!_awaitingApproval) pauseForApproval(null);
          } else {
            endTurn();
          }
          if (showArchived) loadArchivedSessions(); else loadSessions();
        },
        onError: function(errMsg) {
          setCardState('error', 'Error: ' + truncateStr(String(errMsg || 'Approval failed'), 120));
          appendAssistantText('_Approval failed: ' + escapeHtml(String(errMsg || 'Error')) + '_');
          endTurn();
        }
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
    endTurn();

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
          } else {
            prevAssistantContent = null;
          }
          // If the last assistant message is marked pending (client refreshed
          // mid-turn while the LLM was still processing), show a processing
          // indicator; resync below reconciles the final state.
          if (role === 'assistant' && msg.pending && !content) {
            appendMessage('assistant', '⏳ _Previous turn still processing in the background…_', null, msg.ts || msg.timestamp || msg.created_at || null);
          } else {
            appendMessage(role, content, null, msg.ts || msg.timestamp || msg.created_at || null, {
              activity: msg.activity,
              model: msg.model || '',
            });
          }
        });

        // Turn Delivery V2: one authoritative reconciliation after render.
        // Covers trailing-pending (turn still running → keep waiting) and
        // trailing-user (detached turn may exist) without any pollers —
        // live delivery arrives via the resumed WS cursor stream.
        _resyncDelivery('load');

        scrollToBottomForce(); // session load shows the latest turn
        checkPendingApprovals();
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

  function checkPendingApprovals() {
    if (!chatSessionId) return;
    // Resolve LangGraph thread_id from the sidebar session list (web sessions
    // use session_id ≠ thread_id — matching only session_id missed approvals).
    var threadId = '';
    try {
      var listed = sessions.find(function(s) { return s.session_id === chatSessionId; });
      if (listed && listed.thread_id) threadId = listed.thread_id;
    } catch (e) {}

    fetch('/api/pending-approvals', { credentials: 'same-origin' })
      .then(function(res) { return res.ok ? res.json() : null; })
      .then(function(data) {
        if (!data || !Array.isArray(data.pending)) return;
        for (var i = 0; i < data.pending.length; i++) {
          var item = data.pending[i];
          var match =
            item.thread_id === chatSessionId ||
            item.session_id === chatSessionId ||
            (threadId && item.thread_id === threadId);
          if (match) {
            if (!hasInlineApprovalCard()) {
              item.tool = item.tool || item.tool_name;
              item.args = item.args || item.arguments;
              // renderHitlCard clears $store.agent.pendingApproval so the
              // bottom Alpine card can't duplicate the inline one.
              renderHitlCard(item);
            }
            break;
          }
        }
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
    _hitlApproval: renderHitlCard,
    hasInlineApprovalCard: hasInlineApprovalCard,
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
    /**
     * True while an SSE stream owns the live turn. The telemetry WS checks
     * this to avoid double-painting the same reply over both transports
     * (the duplicated-bubble incident class, 2026-08-26).
     *
     * CONTRACT: `activeStream` is assigned ONLY by the two turn-owning
     * dispatches — the /api/chat/stream send/attach (_dispatchSse) and the
     * /api/approve resume (submitApproval). Never point it at any other
     * fetch/stream, or the WS dedupe would suppress painting for unrelated
     * traffic (audit P2).
     */
    hasLiveSSE: function() { return !!activeStream; },
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
    paintCapacityReply: function(reply) {
      if (!reply || !messagesEl) return;
      var incoming = String(reply).trim();
      if (!incoming) return;
      var msgs = messagesEl.querySelectorAll('.message-user, .message-assistant');
      var lastAsst = null;
      for (var i = 0; i < msgs.length; i++) {
        if (msgs[i].classList.contains('message-user')) lastAsst = null;
        else lastAsst = msgs[i];
      }
      if (lastAsst) {
        var te = lastAsst.querySelector('.message-text');
        // Exact-match only — no fuzzy "did it render" heuristics (V2).
        if (te && (te.textContent || '').replace(/\s+/g, ' ').trim() === incoming.replace(/\s+/g, ' ').trim()) return;
      }
      var prev = currentMsgEl;
      currentMsgEl = createAssistantMessage();
      if (window.KazmaChat && typeof window.KazmaChat.applyFinalAssistantText === 'function') {
        window.KazmaChat.applyFinalAssistantText(incoming, '', {});
      }
      currentMsgEl = prev;
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
      // Keep currentMsgEl so applyFinal paints into the same turn bubble.
    },
    applyFinalAssistantText: function(content, model, opts) {
      _clearStatusStrip();
      activeTypingEl = null;
      if (!content) return;
      var incoming = String(content).trim();
      if (!incoming) return;
      opts = opts || {};

      // Always bind to the open-turn assistant (after last user). Never open a
      // second bubble for the same turn — that was the refresh/tab duplicate.
      if (!currentMsgEl) {
        currentMsgEl = _assistantBubbleForOpenTurn();
      }
      var textEl = currentMsgEl ? currentMsgEl.querySelector('.message-text') : null;
      if (!textEl && currentMsgEl) {
        // Defensive: bubble without .message-text — open a clean one.
        currentMsgEl = createAssistantMessage();
        textEl = currentMsgEl.querySelector('.message-text');
      }

      // Server truth → DOM. ALWAYS. No "already shows it?" text matching:
      // dedupe belongs to the seq-journaled transports, painting twice is
      // idempotent, and a skipped paint was the "no response until refresh"
      // root cause.
      tokenAccum = incoming;
      _turnPainted = true;
      tryIngestPlanFromText(tokenAccum);
      var display = _scrubDsml(stripPlanFenceForDisplay(tokenAccum));
      if (textEl) {
        try {
          textEl.innerHTML = transformRenderedForPlan(KS.markdown(display));
        } catch (mdErr) {
          textEl.textContent = display;
        }
        textEl.setAttribute('data-final-len', String(display.length));
      }
      if (model && currentMsgEl) {
        var meta = currentMsgEl.querySelector('.message-meta');
        if (meta && meta.textContent.indexOf(model) < 0) {
          meta.textContent = (meta.textContent ? meta.textContent + ' · ' : '') + model;
        }
      }
      // Release wait only after paint — server content is on screen (or tried).
      _awaitingReply = false;
      if ((opts.replay || opts.source === 'resync') && !activeStream) {
        // Replay/resync paints are terminal for this turn — close the bubble
        // so the next message opens a fresh one. But NOT while a live stream
        // owns the turn: closing mid-stream makes the next token open a NEW
        // bubble + its own "Writing reply…" row, fragmenting one reply into
        // pieces (2026-08-27 post-restart).
        currentMsgEl = null;
        tokenAccum = '';
      }
      scrollToBottom();
    },
    appendLiveToken: function(content, opts) {
      noteTurnActivity();
      _clearStatusStrip();
      activeTypingEl = null;
      if (!content) return;
      // Full-message backfill: replace, never append (post-HITL duplicate fix).
      if (opts && opts.full && window.KazmaChat && typeof window.KazmaChat.applyFinalAssistantText === 'function') {
        window.KazmaChat.applyFinalAssistantText(content, (opts && opts.model) || '');
        return;
      }
      // If server re-sends a full answer that already starts with what we have,
      // treat as replace (guards older servers without full=true).
      if (tokenAccum && content && content.length > tokenAccum.length + 20
          && content.indexOf(tokenAccum.slice(0, Math.min(60, tokenAccum.length))) === 0) {
        if (window.KazmaChat && typeof window.KazmaChat.applyFinalAssistantText === 'function') {
          window.KazmaChat.applyFinalAssistantText(content, '');
          return;
        }
      }
      if (!currentMsgEl) currentMsgEl = createAssistantMessage();
      if (!tokenAccum) {
        logProgress({ kind: 'status', title: ti('writing_reply', 'Writing reply\u2026'), state: 'running' });
      }
      tokenAccum += content;
      tryIngestPlanFromText(tokenAccum);
      var textEl = currentMsgEl.querySelector('.message-text');
      if (textEl) textEl.innerHTML = transformRenderedForPlan(KS.markdown(stripPlanFenceForDisplay(tokenAccum)));
      scrollToBottom();
    },
    setPlan: setPlan,
    appendErrorMessage: function(errMsg) {
      _clearStatusStrip();
      activeTypingEl = null;
      logProgress({ kind: 'error', title: ti('error', 'Error'), detail: String(errMsg || ''), state: 'failed' });
      if (!currentMsgEl) currentMsgEl = createAssistantMessage();
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
      if (!currentMsgEl) currentMsgEl = createAssistantMessage();
      tokenAccum += content;
      tryIngestPlanFromText(tokenAccum);
      var textEl = currentMsgEl.querySelector('.message-text');
      if (textEl) textEl.innerHTML = transformRenderedForPlan(KS.markdown(stripPlanFenceForDisplay(tokenAccum)));
      scrollToBottom();
    },
    onStreamDone: function() {
      endTurn();
    },
  };
})();
