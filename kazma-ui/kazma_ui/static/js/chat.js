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

  // DOM refs
  var messagesEl, inputEl, sendBtn, typingEl, sessionListEl, searchInputEl;
  var costBadge, tokensBadge;
  var modelSelectorEl;

  // Currently selected model (persisted in localStorage)
  var selectedModel = '';
  var MODEL_LS_KEY = 'kazma.selectedModel';

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
    inputEl = $('chat-input');
    sendBtn = $('send-btn');
    typingEl = $('thinking-indicator');
    sessionListEl = $('session-list');
    searchInputEl = $('session-search');
    costBadge = $('session-cost');
    tokensBadge = $('session-tokens');
    modelSelectorEl = $('model-selector');

    if (!messagesEl) return; // not on chat page

    // Input handlers
    if (inputEl) {
      inputEl.addEventListener('keydown', onInputKeydown);
      inputEl.addEventListener('input', onInputResize);
    }
    if (sendBtn) sendBtn.addEventListener('click', function() {
      if (_isGenerating) { abortGeneration(); } else { sendMessage(); }
    });

    // Make the entire input box focus the text field (no dead zones).
    var inputWrapper = document.querySelector('.input-wrapper');
    if (inputWrapper && inputEl) {
      inputWrapper.addEventListener('click', function (e) {
        if (e.target.closest('button')) return; // let buttons do their job
        inputEl.focus();
      });
    }

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

    // File upload
    var fileInput = $('file-input');
    var attachBtn = $('attach-btn');
    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', function() { fileInput.click(); });
      fileInput.addEventListener('change', onFileSelected);
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

    // Global Escape key — abort generation from anywhere on the page
    // (not just when the textarea has focus).
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && _isGenerating) {
        e.preventDefault();
        abortGeneration();
      }
    });

    // Load sessions and connect
    loadSessions();

    // Load the current session's messages if we have a session ID
    // (e.g., after page refresh)
    var initialSessionId = localStorage.getItem(SESSION_LS_KEY);
    if (initialSessionId) {
      chatSessionId = initialSessionId;
      // Use setTimeout to ensure Alpine store is initialized before connecting
      setTimeout(function() { loadSession(initialSessionId); }, 100);
    }

    // Load available models for the model selector
    loadModels();

    // Refresh the sidebar session list when the tab regains focus.
    // Lightweight — only reloads the list, never disrupts the active
    // conversation or re-fetches messages.
    document.addEventListener('visibilitychange', function() {
      if (!document.hidden) {
        if (showArchived) loadArchivedSessions(); else loadSessions();
      }
    });
  }

  // ── Slash commands (discoverable in Web UI) ───────────
  var SLASH_COMMANDS = [
    { cmd: '/yolo', desc: 'Skip danger-tool approvals for this session (TTL)' },
    { cmd: '/yolo off', desc: 'Restore HITL approvals + clear tool grants' },
    { cmd: '/yolo status', desc: 'Show YOLO / grant status for this session' },
    { cmd: '/new', desc: 'Start a new chat session' },
    { cmd: '/reset', desc: 'Clear this conversation history' },
    { cmd: '/help', desc: 'List available slash commands' },
  ];

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
      return '<button type="button" class="chat-slash-item" data-cmd="' + escapeHtml(c.cmd) + '" ' +
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
        inputEl.value = btn.getAttribute('data-cmd') || '';
        hideSlashMenu();
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
    // While generating, Enter just inserts a newline (user is typing a
    // queued message). They can press the Stop button to abort.
    if (_isGenerating && e.key === 'Enter') {
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
  /** Absolute failsafe so a dropped idle frame can never trap the UI forever. */
  var _turnWatchdogTimer = null;
  /** Desync healer: if agent store is idle but Stop is still on, release. */
  var _turnSyncTimer = null;
  var TURN_WATCHDOG_MS = 3 * 60 * 1000;

  function _clearTurnTimers() {
    if (_turnWatchdogTimer) {
      clearTimeout(_turnWatchdogTimer);
      _turnWatchdogTimer = null;
    }
  }

  function _armTurnWatchdog() {
    _clearTurnTimers();
    _turnWatchdogTimer = setTimeout(function() {
      _turnWatchdogTimer = null;
      if (_isGenerating && !_awaitingApproval) {
        console.warn('[KazmaChat] Turn watchdog released a stuck Stop/Enter lock');
        forceEndTurn();
      }
    }, TURN_WATCHDOG_MS);
  }

  function beginTurn() {
    _isGenerating = true;
    _awaitingApproval = false;
    _armTurnWatchdog();
    // Fresh progress log for this turn (don't reuse previous bubble's panel)
    if (currentMsgEl) {
      var oldProg = currentMsgEl.querySelector('.agent-progress');
      if (oldProg) oldProg.remove();
    }
    _progressEl = null;
    _progressStepCount = 0;
    _planItems = [];
    _planParsedFromText = false;
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
  }

  function endTurn() {
    _clearTurnTimers();
    _isGenerating = false;
    _awaitingApproval = false;
    finalizeProgress(true);
    if (activeTypingEl && KS.hideTyping) {
      KS.hideTyping(activeTypingEl);
    }
    activeTypingEl = null;
    if (typingEl && KS.hideTyping) KS.hideTyping(typingEl);
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
    // HITL: turn is paused, not generating. Stop must not pulse; input locked
    // until the user picks Approve / YOLO / Deny.
    _clearTurnTimers();
    _isGenerating = false;
    _awaitingApproval = true;
    if (activeTypingEl && KS.hideTyping) KS.hideTyping(activeTypingEl);
    activeTypingEl = null;
    if (typingEl && KS.hideTyping) KS.hideTyping(typingEl);
    if (inputEl) {
      inputEl.disabled = true;
      inputEl.placeholder = 'Please approve or deny the pending action to continue.';
    }
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.classList.remove('stop-mode');
      sendBtn.title = 'Awaiting approval';
      sendBtn.innerHTML = _SEND_SVG;
    }
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

  function abortGeneration() {
    if (activeStream) {
      activeStream.abort();
      activeStream = null;
      KS.toast('Generation stopped', 'info', 2000);
    }
    forceEndTurn();
  }

  // Heal desync: WS sets isThinking=false but missed chat.endTurn (or vice versa).
  // Runs cheaply; only acts when Stop is stuck while the bus reports idle.
  if (!_turnSyncTimer) {
    _turnSyncTimer = setInterval(function() {
      if (!_isGenerating || _awaitingApproval) return;
      try {
        if (!window.Alpine || !Alpine.store || !Alpine.store('agent')) return;
        var store = Alpine.store('agent');
        if (store.pendingApproval) return;
        if (store._turnActive || store.isThinking) return;
        // Bus is idle; chat still thinks a turn is running → release.
        console.warn('[KazmaChat] Desync recovery: releasing stuck generation lock');
        endTurn();
      } catch (e) {}
    }, 1500);
  }

  // ── File handling ─────────────────────────────────────
  // Pending attachments accumulated for the next send. Text files stay
  // client-side (inlined); binary files (images, PDFs, etc.) are uploaded
  // to /api/chat/upload and referenced by the returned descriptor.
  var pendingText = '';
  var pendingTextName = '';
  var pendingUploads = []; // [{id, kind, mime, filename, path}]

  function _isTextFile(file) {
    var allowedTypes = ['text/plain', 'text/markdown', 'text/html', 'application/json', 'text/csv', 'text/x-python', 'text/javascript'];
    var allowedExts = ['.txt', '.md', '.markdown', '.json', '.csv', '.py', '.js', '.ts', '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.sql'];
    var ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
    return allowedTypes.indexOf(file.type) !== -1 || allowedExts.indexOf(ext) !== -1;
  }

  function onFileSelected(e) {
    var file = e.target.files[0];
    if (!file) return;
    // Text files ≤ 1MB are still inlined client-side (cheap, no upload).
    if (_isTextFile(file) && file.size <= 1048576) {
      var reader = new FileReader();
      reader.onload = function(evt) {
        pendingText = evt.target.result;
        pendingTextName = file.name;
        KS.toast('Attached: ' + file.name + ' (' + KS.formatTokens(file.size) + ' bytes)', 'info', 2500);
        inputEl.placeholder = '\uD83D\uDCCE ' + file.name + ' attached. Type a message\u2026';
      };
      reader.readAsText(file);
      e.target.value = '';
      return;
    }
    // Everything else (images, PDFs, docs, large text) is uploaded.
    if (file.size > 20 * 1024 * 1024) {
      KS.toast('File too large (max 20MB)', 'error', 3000);
      e.target.value = '';
      return;
    }
    KS.toast('Uploading ' + file.name + '\u2026', 'info', 2000);
    var fd = new FormData();
    fd.append('file', file);
    fetch('/api/chat/upload', { method: 'POST', body: fd })
      .then(function(r) { return r.ok ? r.json() : Promise.reject(new Error('Upload failed (' + r.status + ')')); })
      .then(function(desc) {
        pendingUploads.push(desc);
        KS.toast('Attached: ' + (desc.filename || file.name), 'info', 2500);
        inputEl.placeholder = '\uD83D\uDCCE ' + (desc.filename || file.name) + ' attached. Type a message\u2026';
      })
      .catch(function(err) {
        KS.toast('Upload failed: ' + err.message, 'error', 3500);
      });
    e.target.value = '';
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
        // Backend is authoritative — always prefer it over cached localStorage
        if (active && active.model) {
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
    loadSessions();
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
    selectedModel = modelSelectorEl.value || '';
    try { localStorage.setItem(MODEL_LS_KEY, selectedModel); } catch(e) {}
    // Notify other components immediately
    document.dispatchEvent(new CustomEvent('model-changed', { detail: selectedModel }));
    // Sync to backend so the sidebar dropdown stays in sync
    if (selectedModel) {
      fetch('/api/settings/active_model', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active_model: selectedModel }),
      }).then(function() {}).catch(function() {});
    }
  }

  // ── Send message via SSE ──────────────────────────────
  function sendMessage() {
    var text = (inputEl.value || '').trim();
    var hasTextAtt = !!pendingText;
    var hasUploads = pendingUploads.length > 0;
    if (!text && !hasTextAtt && !hasUploads) return;

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

    // Handle /reset command locally
    if (text.toLowerCase() === '/reset') {
      messagesEl.innerHTML =
        '<div class="chat-welcome">' +
          '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
          '<h2>Kazma</h2>' +
          '<p>How can I help you today?</p>' +
        '</div>';
      updateSessionStats(0, 0);
      currentMsgEl = null;
      tokenAccum = '';
      if (activeStream) { activeStream.abort(); activeStream = null; }
      renderSessionList();
    }

    // Build message content. Text attachments are inlined; binary uploads
    // are referenced as attachments and rendered in the transcript by name.
    var content = text;
    var displayAttachName = pendingTextName || (pendingUploads[0] && pendingUploads[0].filename) || '';
    if (pendingText) {
      content = text
        ? text + '\n\n[Attached file: ' + pendingTextName + ']\n```\n' + pendingText.slice(0, 8000) + '\n```'
        : '[Attached file: ' + pendingTextName + ']\n```\n' + pendingText.slice(0, 8000) + '\n```';
    } else if (hasUploads && !text) {
      content = '[' + (pendingUploads[0].kind || 'file') + ']';
    }
    // Build the attachments payload for the server (binary uploads only).
    var attachmentsPayload = pendingUploads.map(function(u) {
      return { id: u.id, kind: u.kind, mime: u.mime, filename: u.filename, path: u.path };
    });

    // Show user message
    appendMessage('user', content, displayAttachName);
    scrollToBottom();

    // Start a clean assistant turn (must clear currentMsgEl *before* beginTurn
    // so progress attaches to a new bubble, not the previous reply).
    currentMsgEl = null;
    tokenAccum = '';
    disableInput(); // → beginTurn → progress panel on new assistant bubble

    // Reset attachment state
    pendingText = '';
    pendingTextName = '';
    pendingUploads = [];
    inputEl.value = '';
    inputEl.style.height = 'auto';
    inputEl.placeholder = ti('type_message', 'Type your message\u2026 (Enter to send)');

    // Show typing indicator (tracked so abortGeneration can clear it)
    activeTypingEl = typingEl;
    KS.showTyping(typingEl, ti('thinking', 'Kazma is thinking\u2026'));

    // Ensure we have a stable session id
    if (!chatSessionId) {
      chatSessionId = generateSessionId();
      persistSessionId();
    }

    // Sidebar: show this season immediately (before the server list round-trip).
    // Critical for WS path which used to skip loadSessions entirely.
    noteSessionActivity(text || content);

    // Route over Central WebSocket Telemetry Bus if connected
    const agentStore = (window.Alpine && Alpine.store) ? Alpine.store('agent') : null;
    if (agentStore && agentStore.connectionStatus === 'connected') {
      agentStore.sendPrompt(content, selectedModel || '');
      return;
    }

    // Fallback to HTTP SSE stream if WS is disconnected
    if (activeStream) {
      activeStream.abort();
      activeStream = null;
    }

    activeStream = KS.sse('/api/chat/stream', {
      message: content,
      session_id: chatSessionId,
      model: selectedModel || '',
      attachments: attachmentsPayload,
    }, {
      onToken: function(data) {
        KS.hideTyping(typingEl);
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
        textEl.innerHTML = KS.markdown(tokenAccum);
        if (window.KazmaBidi) KazmaBidi.apply(textEl, tokenAccum);
        scrollToBottom();
      },

      onToolCall: function(data) {
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

      onDone: function(data) {
        KS.hideTyping(typingEl);
        activeTypingEl = null;
        // Never leave a blank turn after "Thinking…" (empty stream / missed HITL).
        // Layer 4 of the agent-stopped-talking defense: instead of a bare
        // "_No response received._" with no recourse, show a Retry button
        // that re-sends the last user message in one click. Recoverable in
        // one click instead of looking broken.
        var interrupted = !!(data && data.interrupted);
        if (!tokenAccum && !currentMsgEl && !interrupted && !_awaitingApproval) {
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
          updateSessionStats(data.tokens, data.cost);
          if (currentMsgEl) {
            var meta = currentMsgEl.querySelector('.message-meta');
            if (meta) {
              meta.textContent = KS.formatTokens(data.tokens) + ' tokens \u00B7 ' +
                KS.formatCost(data.cost) + ' \u00B7 ' +
                KS.formatDuration(data.duration_ms);
            }
          }
        }
        // Play TTS for the assistant's response
        if (tokenAccum && window.KazmaVoice && !interrupted) {
          window.KazmaVoice.playTTS(tokenAccum);
        }
        // If HITL paused this turn, onApprovalRequired already called
        // pauseForApproval — do NOT endTurn (would re-enable input under the card).
        if (interrupted || _awaitingApproval) {
          activeStream = null;
          if (!_awaitingApproval) pauseForApproval(null);
          // Still refresh list so a new season appears mid-HITL
          if (showArchived) loadArchivedSessions(); else refreshSessionsSoon();
        } else {
          endTurn(); // also refreshSessionsSoon
        }
      },

      onApprovalRequired: function(data) {
        // HITL: graph paused — render scope-aware approval card and lock input.
        KS.hideTyping(typingEl);
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
        KS.hideTyping(typingEl);
        activeTypingEl = null;
        if (!currentMsgEl) currentMsgEl = createAssistantMessage();
        var textEl = currentMsgEl.querySelector('.message-text');
        textEl.innerHTML = '<div class="error-message">\u26A0 ' + escapeHtml(msg) +
          '<br><button class="btn btn-sm btn-danger" onclick="window.KazmaChat.retry()">Retry</button></div>';
        endTurn();
        if (msg && window.showToast) {
          try { window.showToast(String(msg), 'error', 4000); } catch (_t) {}
        }
      }
    });
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
    var time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (sameDay) return time;
    var day = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    return day + ', ' + time;
  }

  // ── Turn workbench (one solid progress surface) ───────
  // Plan (sticky checklist) + Activity (tools/status/thoughts).
  // Tool results stay expanded; panel does NOT auto-collapse on finish.
  var _progressEl = null;
  var _progressStepCount = 0;
  var _planItems = [];
  var _planParsedFromText = false;
  var _progressStartedAt = 0;
  var _progressTimerId = null;
  var TOOL_DETAIL_MAX = 900;

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
    panel.innerHTML =
      '<div class="agent-progress-header" role="button" tabindex="0" title="Collapse/expand workbench">' +
        '<span class="agent-progress-pulse" aria-hidden="true"></span>' +
        '<span class="agent-progress-title">' + escapeHtml(ti('working', 'Working\u2026')) + '</span>' +
        '<span class="agent-progress-elapsed" title="Elapsed">0s</span>' +
        '<span class="agent-progress-count">0 ' + escapeHtml(ti('steps', 'steps')) + '</span>' +
        '<span class="agent-progress-chevron" aria-hidden="true">\u25BE</span>' +
      '</div>' +
      '<div class="agent-progress-body">' +
        '<div class="agent-plan' + (pageRtl ? ' is-rtl' : '') + '"' +
          (pageRtl ? ' dir="rtl"' : '') + ' hidden>' +
          '<div class="agent-plan-head">' +
            '<div class="agent-plan-label">' + escapeHtml(ti('plan', 'Plan')) + '</div>' +
            '<div class="agent-plan-meta"></div>' +
          '</div>' +
          '<div class="agent-plan-bar" aria-hidden="true"><div class="agent-plan-bar-fill"></div></div>' +
          '<ol class="agent-plan-list"></ol>' +
        '</div>' +
        '<div class="agent-activity-label">' + escapeHtml(ti('activity', 'Activity')) + '</div>' +
        '<ul class="agent-progress-steps"></ul>' +
      '</div>';
    var textEl = content.querySelector('.message-text');
    if (textEl) content.insertBefore(panel, textEl);
    else content.appendChild(panel);
    var header = panel.querySelector('.agent-progress-header');
    function toggle() {
      panel.classList.toggle('is-collapsed');
      var chev = panel.querySelector('.agent-progress-chevron');
      if (chev) chev.textContent = panel.classList.contains('is-collapsed') ? '\u25B8' : '\u25BE';
    }
    if (header) {
      header.addEventListener('click', toggle);
      header.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
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
        '<span class="plan-text">' + escapeHtml(item.text) + '</span></li>';
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
   * Pull a plan from model text: ```plan ... ``` or ## Plan / **Plan** lists.
   */
  function tryIngestPlanFromText(text) {
    if (!text || _planParsedFromText) return;
    var fence = text.match(/```plan\s*([\s\S]*?)```/i);
    var block = fence ? fence[1] : '';
    if (!block) {
      var md = text.match(/(?:^|\n)#{1,3}\s*plan\b[^\n]*\n([\s\S]{0,800}?)(?:\n#{1,3}\s|\n```|$)/i);
      if (!md) md = text.match(/(?:^|\n)\*\*plan\*\*[^\n]*\n([\s\S]{0,800}?)(?:\n\*\*|\n#{1,3}\s|$)/i);
      if (md) block = md[1];
    }
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
    m = s.match(/^running\s+(.+?)\s*[.…]*$/i);
    if (m && !/after/i.test(s)) return tiFmt('running_tool', s, { tool: m[1] });
    return s;
  }

  function logProgress(step) {
    if (!step) return;
    var panel = ensureProgressPanel();
    if (!panel) return;

    var kind = step.kind || 'status';
    var state = step.state || (kind === 'error' ? 'failed' : (kind === 'done' ? 'done' : 'info'));
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
        var det = last.querySelector('.step-detail');
        if (!det) {
          det = document.createElement('div');
          det.className = 'step-detail is-expanded';
          last.querySelector('.step-body').appendChild(det);
        }
        det.textContent = truncateStr(detail, TOOL_DETAIL_MAX);
        det.classList.add('is-expanded');
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
    var li = document.createElement('li');
    li.className = 'agent-progress-step step-' + kind + ' state-' + state +
      (kind === 'tool' ? ' is-expanded' : '');
    li.dataset.kind = kind;
    li.dataset.title = title;
    li.dataset.rawTitle = rawTitle;
    li.dataset.state = state;

    var icon = kind === 'tool' ? '\u2699'
      : (kind === 'thought' ? '\u25C8'
        : (kind === 'error' ? '\u26A0'
          : (kind === 'file' ? '\u2398' : '\u2022')));

    // File-diff chips for write/delete tools
    var fileChip = '';
    if (kind === 'tool' || kind === 'file') {
      var pathGuess = _extractPathFromTool(rawTitle, detail);
      if (pathGuess) {
        fileChip =
          '<div class="file-diff-chip" title="' + escapeHtml(pathGuess) + '">' +
            '<span class="file-diff-op">' +
              (state === 'failed' ? 'failed' : (rawTitle.toLowerCase().indexOf('delete') >= 0 ? 'deleted' : 'wrote')) +
            '</span> ' +
            '<code class="file-diff-path">' + escapeHtml(pathGuess) + '</code>' +
          '</div>';
      }
    }

    li.innerHTML =
      '<span class="step-icon" aria-hidden="true">' + icon + '</span>' +
      '<div class="step-body">' +
        '<div class="step-line">' +
          '<span class="step-title">' + escapeHtml(title) + '</span>' +
          (kind === 'tool'
            ? ' <span class="step-state">' + escapeHtml(
                state === 'running'
                  ? ti('running', 'Running\u2026')
                  : (state === 'done'
                      ? ti('step_done', ti('done', 'Done'))
                      : (state === 'failed' ? ti('step_failed', 'Failed') : state))
              ) + '</span>'
            : '') +
          '<span class="step-time">' + escapeHtml(formatMsgTime()) + '</span>' +
        '</div>' +
        fileChip +
        (detail
          ? '<div class="step-detail is-expanded">' + escapeHtml(truncateStr(detail, TOOL_DETAIL_MAX)) + '</div>'
          : '') +
      '</div>';

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
      var donePlan = _planItems.filter(function(p) { return p.done; }).length;
      var base = ok === false
        ? ti('stopped', 'Stopped')
        : (_planItems.length
          ? ti('done', 'Done') + ' \u00B7 ' + tiFmt('plan_progress', 'plan {done}/{total}', {
              done: donePlan,
              total: _planItems.length,
            })
          : ti('done', 'Done'));
      titleEl.textContent = elapsed ? base + ' \u00B7 ' + elapsed : base;
    }
    if (_progressEl) _renderPlanList(_progressEl);
    var pulse = panel.querySelector('.agent-progress-pulse');
    if (pulse) pulse.classList.add('is-off');
    _progressEl = null;
    _planParsedFromText = false;
    _progressStartedAt = 0;
  }

  // ── Message rendering ─────────────────────────────────
  function appendMessage(role, content, attachmentName, ts) {
    var wrapper = document.createElement('div');
    wrapper.className = 'message message-' + role;

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

    wrapper.innerHTML =
      avatarHtml +
      '<div class="message-content">' +
        '<div class="message-text" dir="auto">' + (role === 'user' ? escapeHtml(content) : KS.markdown(content)) + '</div>' +
        '<div class="message-meta" data-ts="' + escapeHtml(iso) + '">' +
          (attachmentName ? '\uD83D\uDCCE ' + escapeHtml(attachmentName) + ' \u00B7 ' : '') +
          '<time datetime="' + escapeHtml(iso) + '">' + escapeHtml(when) + '</time>' +
        '</div>' +
      '</div>';

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
  function renderHitlCard(data) {
    if (!data) return;
    pauseForApproval(data);
    var targetThreadId = data.thread_id || chatSessionId || '';
    if (!currentMsgEl) currentMsgEl = createAssistantMessage();
    var content = currentMsgEl.querySelector('.message-content');
    if (!content) return;

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
    card.innerHTML =
      '<div class="hitl-approval-header">\u26A0 Approval Required</div>' +
      '<div class="hitl-approval-body">' +
        '<p><strong>Tool:</strong> <code>' + escapeHtml(data.tool || '') + '</code></p>' +
        (tools.length <= 1
          ? '<p><strong>Args:</strong> <code>' + escapeHtml(truncateStr(JSON.stringify(data.args || {}), 300)) + '</code></p>'
          : toolsHtml) +
        '<p class="hitl-message">' + escapeHtml(data.message || '') + '</p>' +
        '<p class="hitl-scope-hint" style="font-size:0.72rem;color:var(--text-muted);margin-top:6px;">' +
          'Tip: <strong>Allow tool</strong> stops repeat prompts for this tool only. ' +
          '<strong>YOLO</strong> skips all danger tools (TTL). Or type <code>/yolo</code> anytime.' +
        '</p>' +
      '</div>' +
      '<div class="hitl-approval-actions" style="flex-wrap:wrap;gap:6px;">' +
        '<button class="btn btn-sm btn-success hitl-approve" data-scope="once" title="This call only">Approve once</button>' +
        '<button class="btn btn-sm btn-primary hitl-approve-tool" data-scope="tool" title="Allow this tool for ~30m in this session">Allow tool (session)</button>' +
        '<button class="btn btn-sm btn-warning hitl-approve-yolo" data-scope="yolo" title="Skip all danger tools for this session">YOLO session</button>' +
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
      var rendered = KS.markdown ? KS.markdown(text) : escapeHtml(text);
      // bidi applied after set on textEl below
      textEl.innerHTML = existing
        ? existing + '<hr style="border:none;border-top:1px solid var(--border-subtle);margin:10px 0;">' + rendered
        : rendered;
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
      beginTurn();

      // Prefer the live WebSocket bus when connected (same graph + grants path).
      var agentStore = (window.Alpine && Alpine.store) ? Alpine.store('agent') : null;
      if (agentStore && agentStore.connectionStatus === 'connected') {
        agentStore.submitApproval(action === 'approve', scope, data.thread_id || targetThreadId);
        if (scope === 'yolo' && KS.toast) {
          KS.toast('YOLO on for this session \u2014 danger tools auto-approved', 'warning', 4000);
        }
        if (scope === 'tool' && KS.toast) {
          KS.toast('Allowed ' + (data.tool || 'tool') + ' for this session (~30m)', 'success', 3000);
        }
        return;
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

      activeStream = sseFn(approvalUrl, payload, {
        onEvent: function() {},
        onToken: function(tokenData) {
          KS.hideTyping(approvalTypingEl);
          if (activeTypingEl) { KS.hideTyping(activeTypingEl); activeTypingEl = null; }
          if (!currentMsgEl) currentMsgEl = createAssistantMessage();
          tokenAccum += tokenData.content;
          var textEl = currentMsgEl.querySelector('.message-text');
          if (textEl) textEl.innerHTML = KS.markdown(tokenAccum);
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
          setTimeout(function() {
            currentMsgEl = createAssistantMessage();
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
          if (!tokenAccum && !currentMsgEl && !interrupted && !_awaitingApproval) {
            appendAssistantText('_No response received._');
          }

          if (doneData && (doneData.cost || doneData.tokens)) {
            if (currentMsgEl) {
              var meta = currentMsgEl.querySelector('.message-meta');
              if (meta) {
                meta.innerHTML = '<span>' + (doneData.tokens ? doneData.tokens.toLocaleString() + ' tokens' : '') +
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
      .then(function(r) { return r.json(); })
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
      .catch(function() {});
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

  function renderSessionList() {
    if (!sessionListEl) return;
    // Backend returns sessions sorted newest-first by updated_at.
    // Additionally, pin the active session to the top so it's always
    // visible (matches ChatGPT behavior).
    var filtered = sessions;
    if (searchQuery) {
      var q = searchQuery.toLowerCase();
      filtered = sessions.filter(function(s) {
        return ((s.title || '').toLowerCase().includes(q) ||
                (s.session_id || '').toLowerCase().includes(q));
      });
    }
    // Sort: active session first, then by updated_at descending.
    filtered = filtered.slice().sort(function(a, b) {
      var aActive = a.session_id === chatSessionId ? 1 : 0;
      var bActive = b.session_id === chatSessionId ? 1 : 0;
      if (aActive !== bActive) return bActive - aActive;
      return (b.updated_at || b.created_at || '').localeCompare(a.updated_at || a.created_at || '');
    });

    if (filtered.length === 0) {
      sessionListEl.innerHTML =
        '<div class="session-empty">' + (searchQuery ? 'No matching sessions' : 'No sessions yet') + '</div>';
      return;
    }

    sessionListEl.innerHTML = filtered.map(function(s) {
      var isActive = s.session_id === chatSessionId;
      var title = s.title || (s.session_id || '').slice(0, 8);
      var plat = s.platform || 'web';
      var platIcon = ({
        telegram: 'TG', discord: 'DC', slack: 'SL', gateway: 'GW', web: 'Web'
      })[plat] || plat;
      var meta = platIcon + ' \u00B7 ' + s.message_count + ' msgs \u00B7 ' + relativeTime(s.updated_at || s.created_at);
      var html = '<div class="session-item' + (isActive ? ' active' : '') + '" data-session-id="' + escapeHtml(s.session_id) + '" data-platform="' + escapeHtml(plat) + '">' +
        '<div class="session-info">' +
          '<span class="session-title" title="Double-click to rename — same season continues on ' + escapeHtml(plat) + '">' + escapeHtml(title) + '</span>' +
          '<span class="session-meta">' + meta + '</span>' +
        '</div>';
      if (showArchived) {
        // In archive view: show unarchive + delete buttons
        html += '<button class="session-unarchive" data-unarchive="' + escapeHtml(s.session_id) + '" title="Restore">\u21BA</button>';
        html += '<button class="session-delete" data-delete="' + escapeHtml(s.session_id) + '" title="Delete forever">\u2715</button>';
      } else {
        // Normal view: show rename + archive + delete buttons
        html += '<button class="session-rename" data-rename="' + escapeHtml(s.session_id) + '" title="Rename">\u270F</button>';
        html += '<button class="session-archive" data-archive="' + escapeHtml(s.session_id) + '" title="Archive">\u25A0</button>';
        html += '<button class="session-delete" data-delete="' + escapeHtml(s.session_id) + '" title="Delete session">\u2715</button>';
      }
      html += '</div>';
      return html;
    }).join('');

    // Delete button handlers
    sessionListEl.querySelectorAll('[data-delete]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        deleteSession(this.dataset.delete);
      });
    });

    // Rename button handlers
    sessionListEl.querySelectorAll('[data-rename]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        renameSession(this.dataset.rename);
      });
    });

    // Archive button handlers
    sessionListEl.querySelectorAll('[data-archive]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        archiveSession(this.dataset.archive);
      });
    });

    // Unarchive button handlers
    sessionListEl.querySelectorAll('[data-unarchive]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        unarchiveSession(this.dataset.unarchive);
      });
    });
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

  function loadSession(sessionId) {
    // Abort any in-flight turn from the previous session so Stop never sticks.
    if (activeStream) {
      try { activeStream.abort(); } catch (e) {}
      activeStream = null;
    }
    endTurn();

    chatSessionId = sessionId;
    persistSessionId();

    // Connect to Central WebSocket Telemetry Bus for THIS session
    if (window.Alpine && Alpine.store && Alpine.store('agent')) {
      Alpine.store('agent').connect(sessionId);
    }

    // Clear messages and show loading state
    messagesEl.innerHTML =
      '<div class="chat-welcome">' +
        '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
        '<h2>Session ' + escapeHtml(sessionId.slice(0, 8)) + '</h2>' +
        '<p>Loading messages\u2026</p>' +
      '</div>';
    renderSessionList();
    updateSessionStats(0, 0);

    // Fetch the session messages from the API and render them
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/messages')
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(messages) {
        // Guard against race: user switched sessions while fetch was in flight
        if (chatSessionId !== sessionId) return;

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

        messages.forEach(function(msg) {
          var role = msg.role === 'assistant' ? 'assistant' : 'user';
          appendMessage(role, msg.content || '', null, msg.ts || msg.timestamp || msg.created_at || null);
        });
        scrollToBottom();
        checkPendingApprovals();
      })
      .catch(function(err) {
        if (chatSessionId !== sessionId) return;
        messagesEl.innerHTML =
          '<div class="chat-welcome">' +
            '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
            '<h2>Session ' + escapeHtml(sessionId.slice(0, 8)) + '</h2>' +
            '<p>Failed to load messages: ' + escapeHtml(err.message) + '</p>' +
          '</div>';
        KS.toast('Failed to load session messages', 'error', 3000);
      });
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
            if (!messagesEl.querySelector('.hitl-approval-card')) {
              item.tool = item.tool || item.tool_name;
              item.args = item.args || item.arguments;
              renderHitlCard(item);
            }
            try {
              if (window.Alpine && Alpine.store && Alpine.store('agent')) {
                Alpine.store('agent').pendingApproval = {
                  thread_id: item.thread_id || threadId || chatSessionId,
                  tool: item.tool || '',
                  args: item.args || {},
                  tools: item.tools || [],
                  message: item.message || '',
                };
              }
            } catch (e2) {}
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
    updateSessionStats(0, 0);
    currentMsgEl = null;
    tokenAccum = '';
    lastSentUserText = '';

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

  function updateSessionStats(tokens, cost) {
    if (costBadge) costBadge.textContent = KS.formatCost(cost);
    if (tokensBadge) tokensBadge.textContent = KS.formatTokens(tokens) + ' tokens';
  }

  // ── Utils ─────────────────────────────────────────────
  function scrollToBottom() {
    if (messagesEl) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
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
  document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && e.key === 'k') {
      e.preventDefault();
      if (searchInputEl) searchInputEl.focus();
    }
    if (e.ctrlKey && e.key === 'n') {
      e.preventDefault();
      newSession();
    }
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

  // Expose for inline handlers + agentStore turn lifecycle bridge
  window.KazmaChat = {
    sendMessage: sendMessage,
    newSession: newSession,
    retry: retry,
    toggleArchivedView: toggleArchivedView,
    _hitlApproval: renderHitlCard,
    beginTurn: beginTurn,
    endTurn: endTurn,
    forceEndTurn: forceEndTurn,
    pauseForApproval: pauseForApproval,
    isGenerating: function() { return _isGenerating; },
    refreshSessions: loadSessions,
    refreshSessionsSoon: refreshSessionsSoon,
    getOrCreateSessionId: function() {
      if (!chatSessionId) {
        chatSessionId = generateSessionId();
        persistSessionId();
      }
      return chatSessionId;
    },

    // Telemetry WS hooks — called by agentStore
    logProgress: logProgress,
    finalizeProgress: finalizeProgress,
    appendLiveToken: function(content) {
      KS.hideTyping(typingEl);
      activeTypingEl = null;
      if (!currentMsgEl) currentMsgEl = createAssistantMessage();
      if (!tokenAccum) {
        logProgress({ kind: 'status', title: ti('writing_reply', 'Writing reply\u2026'), state: 'running' });
      }
      tokenAccum += content;
      tryIngestPlanFromText(tokenAccum);
      var textEl = currentMsgEl.querySelector('.message-text');
      if (textEl) textEl.innerHTML = KS.markdown(tokenAccum);
      scrollToBottom();
    },
    setPlan: setPlan,
    appendErrorMessage: function(errMsg) {
      KS.hideTyping(typingEl);
      activeTypingEl = null;
      logProgress({ kind: 'error', title: ti('error', 'Error'), detail: String(errMsg || ''), state: 'failed' });
      if (!currentMsgEl) currentMsgEl = createAssistantMessage();
      var textEl = currentMsgEl.querySelector('.message-text');
      if (textEl) textEl.innerHTML = '<div class="error-message">⚠️ ' + escapeHtml(errMsg) + '</div>';
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
      KS.showTyping(typingEl, ti('thinking', 'Kazma is thinking\u2026'));
    },
    onStreamToken: function(content) {
      KS.hideTyping(typingEl);
      if (!currentMsgEl) currentMsgEl = createAssistantMessage();
      tokenAccum += content;
      var textEl = currentMsgEl.querySelector('.message-text');
      if (textEl) textEl.innerHTML = KS.markdown(tokenAccum);
      scrollToBottom();
    },
    onStreamDone: function() {
      endTurn();
    },
  };
})();
