/* ═══════════════════════════════════════════════════════
   Kazma Streaming — SSE + WebSocket utilities
   Shared transport layer for chat, dashboard, and swarm
   ═══════════════════════════════════════════════════════ */

var KazmaStream = (function() {
  'use strict';

  // ── SSE (Server-Sent Events) ──────────────────────────
  function ssePost(url, body, callbacks) {
    var controller = new AbortController();
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(function(response) {
      if (!response.ok) {
        if (callbacks.onError) callbacks.onError('HTTP ' + response.status);
        return;
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      function pump() {
        reader.read().then(function(result) {
          if (result.done) {
            if (callbacks.onDone) callbacks.onDone();
            return;
          }
          buffer += decoder.decode(result.value, { stream: true });
          var lines = buffer.split('\n');
          buffer = lines.pop() || '';

          var eventType = null;
          var dataLines = [];
          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              dataLines.push(line.slice(6));
            } else if (line === '' && eventType) {
              var payload = null;
              try { payload = JSON.parse(dataLines.join('\n')); } catch(e) {}
              dispatch(eventType, payload);
              eventType = null;
              dataLines = [];
            }
          }
          pump();
        }).catch(function(err) {
          if (err.name !== 'AbortError' && callbacks.onError) {
            callbacks.onError(err.message);
          }
        });
      }

      function dispatch(type, data) {
        switch (type) {
          case 'token':
            if (callbacks.onToken) callbacks.onToken(data);
            break;
          case 'tool_call':
            if (callbacks.onToolCall) callbacks.onToolCall(data);
            break;
          case 'tool_result':
            if (callbacks.onToolResult) callbacks.onToolResult(data);
            break;
          case 'done':
            if (callbacks.onDone) callbacks.onDone(data);
            break;
          case 'error':
            if (callbacks.onError) callbacks.onError(data ? data.content : 'Unknown error');
            break;
          default:
            if (callbacks.onEvent) callbacks.onEvent(type, data);
        }
      }

      pump();
    }).catch(function(err) {
      if (err.name !== 'AbortError' && callbacks.onError) {
        callbacks.onError(err.message);
      }
    });

    return { abort: function() { controller.abort(); } };
  }

  // ── WebSocket with auto-reconnect ─────────────────────
  function wsConnect(path, callbacks) {
    var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = protocol + '//' + location.host + path;
    var ws = null;
    var reconnectTimer = null;
    var reconnectDelay = 1000;
    var maxReconnect = 30000;

    function connect() {
      if (callbacks.onStatus) callbacks.onStatus('connecting');
      try { ws = new WebSocket(url); } catch(e) {
        scheduleReconnect();
        return;
      }

      ws.onopen = function() {
        reconnectDelay = 1000;
        if (callbacks.onStatus) callbacks.onStatus('connected');
        if (callbacks.onOpen) callbacks.onOpen();
      };

      ws.onmessage = function(event) {
        var data;
        try { data = JSON.parse(event.data); } catch(e) { return; }
        if (callbacks.onMessage) callbacks.onMessage(data);
      };

      ws.onclose = function() {
        if (callbacks.onStatus) callbacks.onStatus('disconnected');
        scheduleReconnect();
        if (callbacks.onClose) callbacks.onClose();
      };

      ws.onerror = function() {
        ws.close();
      };
    }

    function scheduleReconnect() {
      if (reconnectTimer) return;
      if (callbacks.onStatus) callbacks.onStatus('reconnecting');
      reconnectTimer = setTimeout(function() {
        reconnectTimer = null;
        connect();
      }, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.5, maxReconnect);
    }

    connect();

    return {
      send: function(data) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(typeof data === 'string' ? data : JSON.stringify(data));
        }
      },
      close: function() {
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        if (ws) { ws.onclose = null; ws.close(); }
      },
      getState: function() { return ws ? ws.readyState : WebSocket.CLOSED; }
    };
  }

  // ── Markdown Renderer ─────────────────────────────────
  var mdRender = (function() {
    var entityMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    function esc(str) {
      return String(str).replace(/[&<>"']/g, function(c) { return entityMap[c]; });
    }

    function codeBlock(lang, code) {
      var escaped = esc(code);
      var langLabel = lang ? '<span class="code-lang">' + esc(lang) + '</span>' : '';
      return '<pre class="code-block">' + langLabel +
        '<code>' + escaped + '</code>' +
        '<button class="copy-btn" onclick="KazmaStream.copyCode(this)" title="Copy">\u2398</button></pre>';
    }

    function render(text) {
      if (!text) return '';
      var html = esc(text);

      // Fenced code blocks (```lang\ncode\n```)
      html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
        return '\n%%CODEBLOCK%%' + lang + '%%\n' + code + '\n%%ENDCODE%%\n';
      });

      // Bold, italic, strikethrough
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
      html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');

      // Inline code
      html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

      // Links
      html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

      // Line breaks
      html = html.replace(/\n\n/g, '</p><p>');
      html = html.replace(/\n/g, '<br>');
      html = '<p>' + html + '</p>';

      // Restore code blocks
      html = html.replace(/%%CODEBLOCK%%(\w*)%%\n([\s\S]*?)%%ENDCODE%%/g, function(_, lang, code) {
        return codeBlock(lang || null, code);
      });

      return html;
    }

    return render;
  })();

  function copyCode(btn) {
    var pre = btn.closest('pre');
    if (!pre) return;
    var code = pre.querySelector('code');
    if (!code) return;
    var text = code.textContent;
    navigator.clipboard.writeText(text).then(function() {
      btn.textContent = '\u2713';
      btn.classList.add('copied');
      setTimeout(function() { btn.textContent = '\u2398'; btn.classList.remove('copied'); }, 2000);
    }).catch(function() {
      btn.textContent = '\u2717';
    });
  }

  // ── Typing indicator ─────────────────────────────────
  var _typingTimer = null;
  function showTyping(el, text) {
    if (!el) return;
    el.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span> ' +
      (text || 'Thinking') + '...';
    el.style.display = 'flex';
    el.classList.add('typing-visible');
  }
  function hideTyping(el) {
    if (!el) return;
    el.style.display = 'none';
    el.classList.remove('typing-visible');
    if (_typingTimer) { clearTimeout(_typingTimer); _typingTimer = null; }
  }

  // ── Toast notifications ───────────────────────────────
  function toast(msg, type, duration) {
    type = type || 'info';
    duration = duration || 3500;
    var container = document.getElementById('toast-container') ||
      document.querySelector('.toast-container');
    if (!container) return;
    var el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.innerHTML = msg;
    el.style.animation = 'slideIn 0.3s ease';
    container.appendChild(el);
    setTimeout(function() {
      el.style.opacity = '0';
      el.style.transition = 'opacity 0.3s';
      setTimeout(function() { el.remove(); }, 300);
    }, duration);
  }

  // ── Formatting utilities ──────────────────────────────
  function formatCost(cost) {
    if (cost === undefined || cost === null) return '$0.0000';
    return '$' + Number(cost).toFixed(4);
  }

  function formatTokens(tokens) {
    if (!tokens) return '0';
    return Number(tokens).toLocaleString();
  }

  function formatDuration(ms) {
    if (!ms) return '0ms';
    if (ms < 1000) return Math.round(ms) + 'ms';
    return (ms / 1000).toFixed(1) + 's';
  }

  function timeAgo(dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr);
    var now = new Date();
    var diff = now - d;
    var mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    var hours = Math.floor(mins / 60);
    if (hours < 24) return hours + 'h ago';
    var days = Math.floor(hours / 24);
    return days + 'd ago';
  }

  // ── Public API ────────────────────────────────────────
  return {
    sse: ssePost,
    ws: wsConnect,
    markdown: mdRender,
    copyCode: copyCode,
    showTyping: showTyping,
    hideTyping: hideTyping,
    toast: toast,
    formatCost: formatCost,
    formatTokens: formatTokens,
    formatDuration: formatDuration,
    timeAgo: timeAgo,
  };
})();
