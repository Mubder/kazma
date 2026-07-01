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

  // DOM refs
  var messagesEl, inputEl, sendBtn, typingEl, sessionListEl, searchInputEl;
  var costBadge, tokensBadge;
  var modelSelectorEl;

  // Currently selected model (persisted in localStorage)
  var selectedModel = '';
  var MODEL_LS_KEY = 'kazma.selectedModel';

  function $(id) { return document.getElementById(id); }

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

    // Model selector
    if (modelSelectorEl) {
      modelSelectorEl.addEventListener('change', onModelChange);
    }

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
  }

  // ── Input handling ────────────────────────────────────
  function onInputKeydown(e) {
    // Enter (without Shift and without Ctrl) sends the message.
    // Ctrl+Enter also sends the message (so users who press Ctrl+Enter
    // from muscle-memory get the expected behaviour).
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      sendMessage();
      return;
    }
    // Ctrl+Enter or Cmd+Enter sends the message
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendMessage();
      return;
    }
    // Shift+Enter inserts a newline (default textarea behaviour — no preventDefault)
  }

  function onInputResize() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 200) + 'px';
  }

  function disableInput() {
    if (inputEl) { inputEl.disabled = true; inputEl.placeholder = 'Kazma is thinking\u2026'; }
    if (sendBtn) sendBtn.disabled = true;
  }

  function enableInput() {
    if (inputEl) { inputEl.disabled = false; inputEl.placeholder = 'Type your message\u2026 (Enter to send)'; }
    if (sendBtn) sendBtn.disabled = false;
    if (inputEl) inputEl.focus();
  }

  // ── File handling ─────────────────────────────────────
  function onFileSelected(e) {
    var file = e.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(evt) {
      var content = evt.target.result;
      var name = file.name;
      // Show file attachment preview
      KS.toast('Attached: ' + name + ' (' + KS.formatTokens(file.size) + ' bytes)', 'info', 2500);
      // Store for sending
      inputEl.dataset.attachment = content;
      inputEl.dataset.attachmentName = name;
      inputEl.placeholder = '\uD83D\uDCCE ' + name + ' attached. Type a message\u2026';
    };
    reader.readAsText(file);
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
        if (active && active.model && !selectedModel) {
          selectedModel = active.model;
        }

        // Fetch all providers so we can group models by provider
        fetch('/api/providers')
          .then(function(r) { return r.ok ? r.json() : []; })
          .then(function(providers) {
            // Build provider groups: [{ name, label, models: [] }]
            var providerGroups = [];
            if (Array.isArray(providers)) {
              providers.forEach(function(p) {
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
    // Sync to backend so the sidebar dropdown stays in sync
    if (selectedModel) {
      fetch('/api/settings/active_model', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active_model: selectedModel }),
      }).then(function(r) { return r.ok ? r.json() : {}; })
        .then(function(data) {
          // Update sidebar component if it exists on this page
          var sidebarEl = document.querySelector('[x-data*="sidebarComponent"]');
          if (sidebarEl && window.Alpine) {
            var sd = Alpine.$data(sidebarEl);
            if (sd) sd.activeModel = selectedModel;
          }
          // Dispatch global event so other components can react
          document.dispatchEvent(new CustomEvent('model-changed', { detail: selectedModel }));
        }).catch(function() {});
    }
  }

  // ── Send message via SSE ──────────────────────────────
  function sendMessage() {
    var text = (inputEl.value || '').trim();
    var attachment = inputEl.dataset.attachment || '';
    var attachmentName = inputEl.dataset.attachmentName || '';
    if (!text && !attachment) return;

    // Build message content
    var content = text;
    if (attachment) {
      content = text
        ? text + '\n\n[Attached file: ' + attachmentName + ']\n```\n' + attachment.slice(0, 8000) + '\n```'
        : '[Attached file: ' + attachmentName + ']\n```\n' + attachment.slice(0, 8000) + '\n```';
    }

    // Show user message
    appendMessage('user', content, attachmentName);
    scrollToBottom();
    disableInput();

    // Reset attachment state
    inputEl.dataset.attachment = '';
    inputEl.dataset.attachmentName = '';
    inputEl.value = '';
    inputEl.style.height = 'auto';
    inputEl.placeholder = 'Type your message\u2026 (Enter to send)';

    // Show typing indicator
    KS.showTyping(typingEl, 'Kazma is thinking');

    // Start SSE stream
    currentMsgEl = null;
    tokenAccum = '';
    if (activeStream) activeStream.abort();

    activeStream = KS.sse('/api/chat/stream', {
      message: content,
      session_id: chatSessionId,
      model: selectedModel || '',
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
        if (data) {
          updateSessionStats(data.tokens, data.cost);
          if (currentMsgEl) {
            var meta = currentMsgEl.querySelector('.message-meta');
            meta.textContent = KS.formatTokens(data.tokens) + ' tokens \u00B7 ' +
              KS.formatCost(data.cost) + ' \u00B7 ' +
              KS.formatDuration(data.duration_ms);
          }
        }
        currentMsgEl = null;
        tokenAccum = '';
        activeStream = null;
        enableInput();
        loadSessions(); // refresh session list
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

  function renderSessionList() {
    if (!sessionListEl) return;
    var filtered = sessions;
    if (searchQuery) {
      filtered = sessions.filter(function(s) {
        return (s.session_id || '').toLowerCase().includes(searchQuery);
      });
    }

    if (filtered.length === 0) {
      sessionListEl.innerHTML =
        '<div class="session-empty">' + (searchQuery ? 'No matching sessions' : 'No sessions yet') + '</div>';
      return;
    }

    sessionListEl.innerHTML = filtered.map(function(s) {
      var isActive = s.session_id === chatSessionId;
      return '<div class="session-item' + (isActive ? ' active' : '') + '" data-session-id="' + escapeHtml(s.session_id) + '">' +
        '<span class="session-title">' + escapeHtml(s.session_id.slice(0, 8)) + '\u2026</span>' +
        '<span class="session-meta">' + s.message_count + ' msgs \u00B7 ' + KS.formatTokens(s.total_tokens) + ' tokens</span>' +
        '<button class="session-delete" data-delete="' + escapeHtml(s.session_id) + '" title="Delete session">\u2715</button>' +
      '</div>';
    }).join('');

    // Delete button handlers
    sessionListEl.querySelectorAll('[data-delete]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        deleteSession(this.dataset.delete);
      });
    });
  }

  function loadSession(sessionId) {
    chatSessionId = sessionId;
    // Clear messages and show loading state
    messagesEl.innerHTML =
      '<div class="chat-welcome">' +
        '<div class="welcome-icon">\u{1F30A}</div>' +
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
              '<div class="welcome-icon">\u{1F30A}</div>' +
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
            '<div class="welcome-icon">\u{1F30A}</div>' +
            '<h2>Session ' + escapeHtml(sessionId.slice(0, 8)) + '</h2>' +
            '<p>Failed to load messages: ' + escapeHtml(err.message) + '</p>' +
          '</div>';
        KS.toast('Failed to load session messages', 'error', 3000);
      });
  }

  function newSession() {
    chatSessionId = null;
    messagesEl.innerHTML =
      '<div class="chat-welcome">' +
        '<div class="welcome-icon">\u{1F30A}</div>' +
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

  function deleteSession(sessionId) {
    if (!confirm('Delete session ' + sessionId.slice(0, 8) + '?')) return;
    fetch('/api/chat/sessions/' + sessionId, { method: 'DELETE' })
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
  };
})();
