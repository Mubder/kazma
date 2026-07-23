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
    if (sendBtn) sendBtn.addEventListener('click', sendMessage);

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

    // Load sessions and connect
    loadSessions();

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
      hideSlashMenu();
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

  function disableInput() {
    if (inputEl) { inputEl.disabled = true; inputEl.placeholder = 'Kazma is thinking\u2026'; }
    if (sendBtn) sendBtn.disabled = true;
    hideSlashMenu();
  }

  function enableInput() {
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.placeholder = 'Type a message or /yolo \u2026 (Enter to send)';
    }
    if (sendBtn) sendBtn.disabled = false;
    if (inputEl) inputEl.focus();
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
    disableInput();

    // Reset attachment state
    pendingText = '';
    pendingTextName = '';
    pendingUploads = [];
    inputEl.value = '';
    inputEl.style.height = 'auto';
    inputEl.placeholder = 'Type your message\u2026 (Enter to send)';

    // Show typing indicator
    KS.showTyping(typingEl, 'Kazma is thinking');

    // Start SSE stream
    currentMsgEl = null;
    tokenAccum = '';
    if (activeStream) activeStream.abort();

    // Ensure we have a stable session id (generated client-side so it
    // survives page refreshes and is reused for the same conversation).
    if (!chatSessionId) {
      chatSessionId = generateSessionId();
      persistSessionId();
    }

    activeStream = KS.sse('/api/chat/stream', {
      message: content,
      session_id: chatSessionId,
      model: selectedModel || '',
      attachments: attachmentsPayload,
    }, {
      onToken: function(data) {
        KS.hideTyping(typingEl);
        if (!currentMsgEl) {
          currentMsgEl = createAssistantMessage();
        }
        tokenAccum += data.content;
        var textEl = currentMsgEl.querySelector('.message-text');
        textEl.innerHTML = KS.markdown(tokenAccum);
        scrollToBottom();
      },

      onToolCall: function(data) {
        if (!currentMsgEl) currentMsgEl = createAssistantMessage();
        var content = currentMsgEl.querySelector('.message-content');
        var box = document.createElement('div');
        box.className = 'tool-call-box';
        box.innerHTML = '<span class="tool-name">\u2699 ' + escapeHtml(data.tool_name) + '</span>' +
          '<code class="tool-inputs">' + escapeHtml(truncateStr(data.inputs, 200)) + '</code>' +
          '<span class="tool-status running">Running\u2026</span>';
        content.appendChild(box);
        scrollToBottom();
      },

      onToolResult: function(data) {
        if (!currentMsgEl) return;
        var content = currentMsgEl.querySelector('.message-content');
        // Update last tool-call box
        var boxes = content.querySelectorAll('.tool-call-box');
        var lastBox = boxes.length ? boxes[boxes.length - 1] : null;
        if (lastBox) {
          var statusEl = lastBox.querySelector('.tool-status');
          if (statusEl) { statusEl.textContent = 'Done'; statusEl.className = 'tool-status done'; }
        }
        // Add result box
        var resultBox = document.createElement('div');
        resultBox.className = 'tool-result-box';
        resultBox.innerHTML = '<strong>Result:</strong> ' + escapeHtml(truncateStr(data.result, 500));
        content.appendChild(resultBox);
        scrollToBottom();
      },

      onDone: function(data) {
        KS.hideTyping(typingEl);
        // Never leave a blank turn after "Thinking…" (empty stream / missed HITL)
        if (!tokenAccum && !currentMsgEl && !(data && data.interrupted)) {
          currentMsgEl = createAssistantMessage();
          var emptyEl = currentMsgEl.querySelector('.message-text');
          if (emptyEl) {
            emptyEl.innerHTML = KS.markdown
              ? KS.markdown('_No response received. Try again, or check server logs / Pending Approvals._')
              : '<em>No response received. Try again.</em>';
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
        if (tokenAccum && window.KazmaVoice) {
          window.KazmaVoice.playTTS(tokenAccum);
        }
        currentMsgEl = null;
        tokenAccum = '';
        activeStream = null;
        enableInput();
        if (showArchived) loadArchivedSessions(); else loadSessions(); // refresh session list
      },

      onApprovalRequired: function(data) {
        // HITL: graph paused — render scope-aware approval card.
        KS.hideTyping(typingEl);
        activeStream = null;
        renderHitlCard(data);
      },

      onError: function(msg) {
        KS.hideTyping(typingEl);
        if (!currentMsgEl) currentMsgEl = createAssistantMessage();
        var textEl = currentMsgEl.querySelector('.message-text');
        textEl.innerHTML = '<div class="error-message">\u26A0 ' + escapeHtml(msg) +
          '<br><button class="btn btn-sm btn-danger" onclick="window.KazmaChat.retry()">Retry</button></div>';
        currentMsgEl = null;
        tokenAccum = '';
        activeStream = null;
        enableInput();
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

  // ── Message rendering ─────────────────────────────────
  function appendMessage(role, content, attachmentName) {
    var wrapper = document.createElement('div');
    wrapper.className = 'message message-' + role;

    var avatar = role === 'user' ? 'You' : 'K';
    var avatarBg = role === 'user' ? 'var(--accent)' : 'var(--bg-surface)';

    wrapper.innerHTML =
      '<div class="message-avatar" style="background:' + avatarBg + '">' + avatar + '</div>' +
      '<div class="message-content">' +
        '<div class="message-text">' + (role === 'user' ? escapeHtml(content) : KS.markdown(content)) + '</div>' +
        '<div class="message-meta">' + (attachmentName ? '\uD83D\uDCCE ' + escapeHtml(attachmentName) + ' \u00B7 ' : '') + 'just now</div>' +
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
    if (!data || !data.thread_id) return;
    if (!currentMsgEl) currentMsgEl = createAssistantMessage();
    var content = currentMsgEl.querySelector('.message-content');
    if (!content) return;

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
      textEl.innerHTML = existing
        ? existing + '<hr style="border:none;border-top:1px solid var(--border-subtle);margin:10px 0;">' + rendered
        : rendered;
      scrollToBottom();
    }

    function submitApproval(action, scope) {
      scope = scope || 'once';
      var pendingLabel = action === 'deny'
        ? 'Denying\u2026'
        : (scope === 'yolo' ? 'YOLO on \u2014 running\u2026'
          : (scope === 'tool' ? 'Granting tool \u2014 running\u2026' : 'Running approved tool\u2026'));
      setCardState('pending', pendingLabel);

      var payload = {
        action: action,
        scope: scope,
        session_id: chatSessionId || '',
        tool: data.tool || '',
      };

      fetch('/api/approve/' + encodeURIComponent(data.thread_id), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        credentials: 'same-origin',
      }).then(function(res) {
        return res.json().then(function(body) {
          return { status: res.status, body: body || {} };
        }).catch(function() {
          return { status: res.status, body: {} };
        });
      }).then(function(out) {
        if (out.status === 202) {
          var okLabel = action === 'deny' ? 'Denied \u2717'
            : (scope === 'yolo' ? 'YOLO on \u2713'
              : (scope === 'tool' ? 'Tool allowed \u2713' : 'Approved \u2713'));
          setCardState(action === 'approve' ? 'approved' : 'denied', okLabel);

          if (out.body.content) {
            appendAssistantText(out.body.content);
          } else if (action === 'deny') {
            appendAssistantText('_Tool denied — continuing without it._');
          } else if (out.body.approval_required) {
            // Mid-chain with no new prose: quiet continue
          } else if (action === 'approve') {
            // Final turn with empty model text — server usually sends a note
            appendAssistantText('_Tool finished. Ask a follow-up if you need a summary._');
          }

          if (scope === 'tool' && KS.toast) {
            KS.toast('Allowed ' + (data.tool || 'tool') + ' for this session (~30m)', 'success', 3000);
          }
          if (scope === 'yolo' && KS.toast) {
            KS.toast('YOLO on for this session — danger tools auto-approved', 'warning', 4000);
          }

          if (out.body.approval_required) {
            var next = out.body.approval_required;
            setTimeout(function() {
              currentMsgEl = createAssistantMessage();
              renderHitlCard(next);
            }, 40);
          } else {
            currentMsgEl = null;
            tokenAccum = '';
            enableInput();
          }
        } else if (out.status === 409) {
          setCardState('error', 'No pending approval (already resumed?)');
          appendAssistantText('_' + truncateStr(String((out.body && out.body.error) || 'No pending approval for this thread.'), 200) + '_');
          enableInput();
        } else {
          var errMsg = (out.body && (out.body.error || out.body.detail)) || ('HTTP ' + out.status);
          setCardState('error', 'Error: ' + truncateStr(String(errMsg), 120));
          appendAssistantText('_Approval failed: ' + escapeHtml(String(errMsg)) + '_');
          enableInput();
        }
      }).catch(function(err) {
        setCardState('error', 'Error: ' + (err && err.message ? err.message : 'network'));
        enableInput();
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
  function loadSessions() {
    fetch('/api/chat/sessions')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        sessions = data || [];
        renderSessionList();
      })
      .catch(function() {});
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
    chatSessionId = sessionId;
    persistSessionId();
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
        // Clear the loading placeholder
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

        // Render each stored message
        messages.forEach(function(msg) {
          var role = msg.role === 'assistant' ? 'assistant' : 'user';
          appendMessage(role, msg.content || '');
        });
        scrollToBottom();
      })
      .catch(function(err) {
        messagesEl.innerHTML =
          '<div class="chat-welcome">' +
            '<div class="welcome-icon"><img src="/static/img/kazma-icon.png" alt="Kazma" class="welcome-logo"></div>' +
            '<h2>Session ' + escapeHtml(sessionId.slice(0, 8)) + '</h2>' +
            '<p>Failed to load messages: ' + escapeHtml(err.message) + '</p>' +
          '</div>';
        KS.toast('Failed to load session messages', 'error', 3000);
      });
  }

  function newSession() {
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
    if (activeStream) { activeStream.abort(); activeStream = null; }
    renderSessionList();
    if (inputEl) { inputEl.focus(); }
  }

  async function deleteSession(sessionId) {
    if (!(await window.kazmaConfirm({
      title: 'Delete session',
      message: 'Delete session ' + sessionId.slice(0, 8) + '? This cannot be undone.',
      confirmText: 'Delete',
      danger: true,
    }))) return;
    fetch('/api/chat/sessions/' + encodeURIComponent(sessionId), { method: 'DELETE' })
      .then(function() {
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

  // Expose for inline handlers
  window.KazmaChat = {
    sendMessage: sendMessage,
    newSession: newSession,
    retry: retry,
    toggleArchivedView: toggleArchivedView,
    _hitlApproval: renderHitlCard,
    getOrCreateSessionId: function() {
      if (!chatSessionId) {
        chatSessionId = generateSessionId();
        persistSessionId();
      }
      return chatSessionId;
    },

    // Voice streaming hooks — called by voice.js WebSocket client
    onUserTranscription: function(text) {
      appendMessage('user', text);
      scrollToBottom();
      KS.showTyping(typingEl, 'Kazma is thinking');
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
      currentMsgEl = null;
      tokenAccum = '';
      activeStream = null;
      enableInput();
    },
  };
})();
