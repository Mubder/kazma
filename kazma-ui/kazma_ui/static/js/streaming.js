/* ═══════════════════════════════════════════════════════
   Kazma Streaming — SSE + WebSocket utilities
   Shared transport layer for chat, dashboard, and swarm
   ═══════════════════════════════════════════════════════ */

var KazmaStream = (function() {
  'use strict';

  // ── SSE (Server-Sent Events) ──────────────────────────

  /**
   * Incremental ``text/event-stream`` parser: text in, whole frames out.
   *
   * A frame is dispatched at its blank line and not before, however the
   * bytes were cut on the way. The reader used to keep the half-built frame
   * (event type, data lines, id) in variables local to ONE network read, so
   * a frame split across two reads lost its event type -- and its orphaned
   * data line was then joined onto the NEXT frame, whose JSON no longer
   * parsed and reached the handlers as ``null``. A direct connection almost
   * never splits a frame, so it never showed locally. Cloudflare Tunnel
   * re-chunks the body, so on the live install it dropped tool rows and
   * approval cards and threw ``Cannot read properties of null (reading
   * 'tool_name')`` in the middle of turns (2026-09-26).
   *
   * Field rules follow the WHATWG event-stream format: lines end in LF, CR
   * or CRLF (a CR at the end of a read is held, it may be half of a CRLF),
   * ``:`` starts a comment (the server's keepalive), one space after the
   * colon is dropped, repeated ``data:`` lines join with ``\n``, and a
   * frame with no ``data:`` line is not dispatched. ``id`` is per frame
   * here, because the caller only records the ids of journaled frames.
   *
   * @param {function(string, string, (string|null))} onFrame
   *   Called with (event type, raw data, id or null) once per frame.
   */
  function createSseParser(onFrame) {
    var buffer = '';
    var eventType = '';
    var dataLines = [];
    var frameId = null;

    function reset() {
      eventType = '';
      dataLines = [];
      frameId = null;
    }

    function takeLine(line) {
      if (line === '') {
        if (dataLines.length) onFrame(eventType || 'message', dataLines.join('\n'), frameId);
        reset();
        return;
      }
      if (line.charCodeAt(0) === 58) return; // ':' comment -- keepalive
      var colon = line.indexOf(':');
      var field = colon === -1 ? line : line.slice(0, colon);
      var value = colon === -1 ? '' : line.slice(colon + 1);
      if (value.charCodeAt(0) === 32) value = value.slice(1);
      if (field === 'event') eventType = value;
      else if (field === 'data') dataLines.push(value);
      else if (field === 'id') frameId = value.indexOf('\u0000') === -1 ? value : frameId;
      // 'retry' and unknown fields are ignored, as the format requires.
    }

    return {
      /** Feed decoded text; complete frames are dispatched synchronously. */
      push: function(text) {
        if (!text) return;
        buffer += text;
        var holdCR = buffer.charCodeAt(buffer.length - 1) === 13;
        var lines = (holdCR ? buffer.slice(0, -1) : buffer).split(/\r\n|\r|\n/);
        buffer = lines.pop() + (holdCR ? '\r' : '');
        for (var i = 0; i < lines.length; i++) takeLine(lines[i]);
      },
      /** End of body: a held CR finishes its line; an unfinished frame is
       *  discarded (the format never dispatches a frame without its blank
       *  line -- the stream's own end handling covers a cut-off turn). */
      end: function() {
        if (buffer.charCodeAt(buffer.length - 1) === 13) takeLine(buffer.slice(0, -1));
        buffer = '';
        reset();
      },
    };
  }

  function ssePost(url, body, callbacks) {
    var controller = new AbortController();
    // Turn Delivery V2: journaled SSE frames carry an ``id: <seq>`` line.
    // Track the last seen id so a resume retry can present it as
    // last_event_id and the server replays exactly what was missed. MUST
    // live at ssePost's own scope — the returned ``lastEventId`` getter
    // closes over this, not over the fetch .then callback below (which is
    // a different scope; a var declared there made the getter throw
    // "lastEventId is not defined" on any stream error).
    var lastEventId = null;
    // Is this stream still able to deliver? Same scope rule as lastEventId
    // above, and for the same reason -- the getter below closes over THIS,
    // not over the fetch callback.
    //
    // chat.js used to answer "is the stream alive?" by testing whether its
    // `activeStream` variable was non-null. That is a belief, not a fact: a
    // handle stays non-null after the server closes the body unless some
    // callback nulls it, and those callbacks are epoch-gated, so a
    // superseded stream's terminal frame nulls nothing. On 2026-09-14 that
    // belief cost an operator a finished 3,725-character reply -- the
    // post-approval re-attach was skipped because a dead handle looked
    // alive. Liveness is reported here, where it is known.
    var closed = false;

    // Guard: the SSE ``event: done`` frame already completes the turn.
    // When the HTTP body closes, the reader also ends — without this flag
    // chat.js would call onDone a *second* time with no payload and paint
    // the false ``No response received…`` bubble after a good reply.
    //
    // THESE THREE LIVE AT ssePost SCOPE, not inside the fetch .then.
    // They were declared in the .then callback while the .catch below --
    // which is chained on the OUTER promise -- called both functions. They
    // are not lexically visible there, so that handler threw
    // `ReferenceError: isBenignStreamClose is not defined` EVERY time it
    // ran, and `onError` was never reached. chat.js nulls `activeStream`
    // and resyncs from onError, so a transport failure left a dead handle
    // that looked alive and no recovery at all. Same scope trap the
    // lastEventId comment above already warns about, in the same function.
    // Found 2026-09-14 by a test that drove a failing fetch through it.
    var streamFinished = false;

    function finishStream(data) {
      if (streamFinished) return;
      streamFinished = true;
      closed = true;
      if (callbacks.onDone) callbacks.onDone(data);
    }

    function isBenignStreamClose(err) {
      if (!err) return true;
      if (err.name === 'AbortError') return true;
      var msg = String((err && err.message) || err || '').toLowerCase();
      return msg === 'network error'
        || msg.indexOf('networkerror') >= 0
        || msg.indexOf('failed to fetch') >= 0
        || msg.indexOf('load failed') >= 0
        || msg.indexOf('body stream') >= 0
        || msg.indexOf('err_incomplete') >= 0;
    }
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(function(response) {
      if (!response.ok) {
        closed = true;
        if (callbacks.onError) callbacks.onError('HTTP ' + response.status);
        return;
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder();

      // One frame's failure is that frame's. A handler that threw used to
      // escape into pump()'s .catch, which stopped reading the stream for
      // good and reported the whole turn as a lost connection; data that is
      // not JSON used to reach the handlers as null and make one of them
      // throw. Both are now reported through onFrameError and the stream
      // carries on with the next frame -- what a re-attach from this
      // frame's id would have delivered anyway.
      function reportFrameError(type, err) {
        console.error('[KazmaStream] ' + type + ' frame failed; the stream continues', err);
        if (callbacks.onFrameError) {
          try { callbacks.onFrameError(type, err); } catch (eReport) { /* never stop the stream */ }
        }
      }
      var parser = createSseParser(function(type, raw, id) {
        if (id != null && id !== '') lastEventId = id;
        var payload;
        try {
          payload = JSON.parse(raw);
        } catch (eParse) {
          reportFrameError(type, eParse);
          return;
        }
        try {
          dispatch(type, payload);
        } catch (eHandler) {
          reportFrameError(type, eHandler);
        }
      });

      function pump() {
        reader.read().then(function(result) {
          if (result.done) {
            parser.push(decoder.decode());
            parser.end();
            // Only fire if the server never sent ``event: done`` (truncated stream)
            finishStream(undefined);
            return;
          }
          parser.push(decoder.decode(result.value, { stream: true }));
          pump();
        }).catch(function(err) {
          if (err.name === 'AbortError') return;
          if (streamFinished) return;
          // Firefox/Edge report a normal SSE close as "network error".
          // That is a truncated close, not a failed turn (HITL pause and
          // long tool calls both close the HTTP body this way — 2026-09-01).
          if (isBenignStreamClose(err)) {
            finishStream(undefined);
            return;
          }
          if (callbacks.onError) callbacks.onError(err.message);
        });
      }

      function dispatch(type, data) {
        switch (type) {
          case 'token':
            // Record that tokens were streamed so the done/turn_complete
            // fallback below doesn't re-feed the full final text to onToken
            // (the guard was read here but never set — audit finding).
            callbacks._sawToken = true;
            if (callbacks.onToken) callbacks.onToken(data);
            break;
          case 'tool_call':
            if (callbacks.onToolCall) callbacks.onToolCall(data);
            break;
          case 'tool_result':
            if (callbacks.onToolResult) callbacks.onToolResult(data);
            break;
          case 'done':
          case 'turn_complete':
            // Prefer turn_complete / enriched done content when tokens were missed.
            if (data && data.content && !callbacks._sawToken) {
              if (data.capacity) {
                // Capacity ack (slash fast-path): REPLACE, never append —
                // feeding the ack through onToken doubled the reply when the
                // capacity frame had already painted (the §29F duplicated-
                // prefix class), and paintCapacityReply is content-key
                // idempotent, so a repeat after a successful paint dedupes.
                try {
                  if (window.KazmaChat && typeof window.KazmaChat.paintCapacityReply === 'function') {
                    window.KazmaChat.paintCapacityReply(data.content, data.turn_id);
                  }
                } catch (e) { /* ignore */ }
              } else if (callbacks.onToken) {
                try { callbacks.onToken({ content: data.content }); } catch (e) { /* ignore */ }
              }
            }
            if (type === 'done' || type === 'turn_complete') {
              finishStream(data);
            }
            break;
          case 'snapshot':
            // Time Travel: live snapshot captured — notify the replay panel.
            if (window.KazmaReplay && window.KazmaReplay.onLiveSnapshot) {
              window.KazmaReplay.onLiveSnapshot(data);
            }
            break;
          case 'memory_explain':
            if (callbacks.onMemoryExplain) callbacks.onMemoryExplain(data || {});
            else if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'context_compacted':
            // Context-integrity S3-1: earlier turns were trimmed/stubbed —
            // surface a chip so the user knows why the agent forgot.
            try {
              if (window.KazmaChat && typeof window.KazmaChat.showContextCompacted === 'function') {
                window.KazmaChat.showContextCompacted(data || {});
              } else if (window.showToast) {
                window.showToast('🗜️ ' + ((data && data.detail) || 'Earlier context was compacted'), 'info', 6000);
              }
            } catch (e) { /* never break the stream */ }
            if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'resumed':
            // Journal-attach handshake. Tokens follow on this same stream.
            if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'status_update':
          case 'status':
            if (callbacks.onStatus) callbacks.onStatus(data || {});
            else if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'turn_heartbeat':
            // Live Task Card liveness: journaled every ~8-10s of stream
            // silence with the current phase/tool/step — the "is it hung?"
            // signal. Data shape: {phase, current, step, elapsed_s, seq}.
            if (callbacks.onHeartbeat) callbacks.onHeartbeat(data || {});
            else if (callbacks.onStatus) callbacks.onStatus({ status: 'thinking', heartbeat: data || {} });
            else if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'approval_required':
            if (callbacks.onApprovalRequired) callbacks.onApprovalRequired(data);
            break;
          case 'hitl':
            if (callbacks.onHitl) callbacks.onHitl(data);
            else if (data && String(data.state || 'pending') === 'pending'
                && callbacks.onApprovalRequired) {
              callbacks.onApprovalRequired(data);
            } else if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'approval_timeout':
            try {
              if (window.KazmaChat && typeof window.KazmaChat.markApprovalTimedOut === 'function') {
                window.KazmaChat.markApprovalTimedOut((data && data.message) || '', data || {});
              }
            } catch (e) { /* ignore */ }
            if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'capacity':
            try {
              if (data && data.reply && window.KazmaChat && typeof window.KazmaChat.paintCapacityReply === 'function') {
                window.KazmaChat.paintCapacityReply(data.reply, data.turn_id);
              }
              if (window.KazmaChat && typeof window.KazmaChat.refreshCapacity === 'function') {
                window.KazmaChat.refreshCapacity();
              }
            } catch (e) { /* ignore */ }
            if (callbacks.onEvent) callbacks.onEvent(type, data);
            break;
          case 'error':
            if (callbacks.onError) callbacks.onError(data ? data.content : 'Unknown error');
            // Mark the stream finished HERE. Otherwise the HTTP body close
            // fires onDone(undefined), which the approve-resume path treated
            // as a successful Approved with no answer (2026-09-01).
            finishStream({ error: true, content: data && data.content });
            break;
          default:
            if (callbacks.onEvent) callbacks.onEvent(type, data);
        }
      }

      pump();
    }).catch(function(err) {
      closed = true;
      if (err.name === 'AbortError') return;
      if (isBenignStreamClose(err)) {
        finishStream(undefined);
        return;
      }
      if (callbacks.onError) callbacks.onError(err.message);
    });

    return {
      abort: function() { closed = true; controller.abort(); },
      /** Last journaled seq seen on this stream (Turn Delivery V2). */
      lastEventId: function() { return lastEventId; },
      /**
       * True once this stream can no longer deliver anything: the body
       * ended, it errored, or it was aborted. Ask this instead of
       * guessing from whether a variable still holds the handle.
       */
      isClosed: function() { return closed; },
    };
  }

  // ── WebSocket with auto-reconnect ─────────────────────
  function wsConnect(path, callbacks) {
    var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = protocol + '//' + location.host + path;
    var ws = null;
    var reconnectTimer = null;
    var reconnectDelay = 1000;
    var maxReconnect = 30000;
    var reconnectCount = 0;
    var maxReconnectAttempts = 50;

    function connect() {
      if (callbacks.onStatus) callbacks.onStatus('connecting');
      try { ws = new WebSocket(url); } catch(e) {
        scheduleReconnect();
        return;
      }

      ws.onopen = function() {
        reconnectDelay = 1000;
        reconnectCount = 0;
        if (callbacks.onStatus) callbacks.onStatus('connected');
        if (callbacks.onOpen) callbacks.onOpen();
      };

      ws.onmessage = function(event) {
        var data;
        try { data = JSON.parse(event.data); } catch(e) { return; }
        if (callbacks.onMessage) callbacks.onMessage(data);
      };

      ws.onclose = function(event) {
        if (callbacks.onStatus) callbacks.onStatus('disconnected');
        if (event && event.code === 4003) {
          // Unauthorized WS close = session expired. Route to login (same
          // routine as the global fetch wrapper) instead of a blind reload,
          // which previously landed the user back on a still-broken page.
          console.warn('[KazmaStream] WebSocket closed with 4003 (Unauthorized). Redirecting to login...');
          if (!window.__kazmaAuthRedirecting) {
            window.__kazmaAuthRedirecting = true;
            try {
              if (typeof window.showToast === 'function') {
                window.showToast(
                  (window.t && window.t('auth.session_expired')) || 'Your session has expired. Redirecting to login…',
                  'warning', 3500
                );
              }
            } catch (e) { /* best-effort */ }
            var next = encodeURIComponent(location.pathname + location.search);
            location.href = '/login?next=' + next + '&reason=session_expired';
          }
          return;
        }
        scheduleReconnect();
        if (callbacks.onClose) callbacks.onClose();
      };

      ws.onerror = function() {
        ws.close();
      };
    }

    function scheduleReconnect() {
      if (reconnectTimer) return;
      if (reconnectCount >= maxReconnectAttempts) {
        if (callbacks.onStatus) callbacks.onStatus('failed');
        if (callbacks.onError) callbacks.onError('Max reconnection attempts reached');
        return;
      }
      reconnectCount++;
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
  // Line-oriented, streaming-safe, bidi-safe (dir=auto on blocks).
  // Supports: code fences, headers, hr, paragraphs, bold/italic/strike,
  // inline code, links, auto-URLs, GFM tables, lists (ul/ol/task), blockquotes.
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

    function isTableAlignRow(line) {
      // | --- | :---: | ---: |
      return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
    }

    function isTableRow(line) {
      if (!line || line.indexOf('|') < 0) return false;
      // Must look like a pipe-row (not a lone | or prose with one |)
      var t = line.trim();
      if (t.charAt(0) === '|' || t.charAt(t.length - 1) === '|') {
        return (t.match(/\|/g) || []).length >= 2;
      }
      // a | b | c (no leading pipe)
      return (t.match(/\|/g) || []).length >= 2;
    }

    function splitTableCells(line) {
      var t = line.trim();
      if (t.charAt(0) === '|') t = t.slice(1);
      if (t.charAt(t.length - 1) === '|') t = t.slice(0, -1);
      return t.split('|').map(function(c) { return c.trim(); });
    }

    function parseAlignments(alignLine, colCount) {
      var cells = splitTableCells(alignLine);
      var aligns = [];
      for (var i = 0; i < colCount; i++) {
        var c = (cells[i] || '').replace(/\s/g, '');
        if (/^:-+:$/.test(c)) aligns.push('center');
        else if (/^-+:$/.test(c)) aligns.push('end');
        else if (/^:-+$/.test(c)) aligns.push('start');
        else aligns.push('');
      }
      return aligns;
    }

    function listItemMatch(line) {
      // task: - [ ] / - [x]
      var task = line.match(/^(\s*)([-*+])\s+\[([ xX])\]\s+(.*)$/);
      if (task) {
        return {
          indent: task[1].length,
          ordered: false,
          task: true,
          checked: task[3].toLowerCase() === 'x',
          text: task[4],
        };
      }
      var ul = line.match(/^(\s*)([-*+])\s+(.*)$/);
      if (ul) {
        return { indent: ul[1].length, ordered: false, task: false, text: ul[3] };
      }
      var ol = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
      if (ol) {
        return {
          indent: ol[1].length,
          ordered: true,
          task: false,
          start: parseInt(ol[2], 10) || 1,
          text: ol[3],
        };
      }
      return null;
    }

    function isBlockquote(line) {
      return /^\s{0,3}>\s?/.test(line);
    }

    function stripBlockquote(line) {
      return line.replace(/^\s{0,3}>\s?/, '');
    }

    function render(text) {
      if (!text) return '';
      // Process line-oriented markdown first (headers, rules, tables, lists)
      // so structural markers never sit inside an LTR paragraph next to Arabic.
      var lines = String(text).replace(/\r\n/g, '\n').split('\n');
      var blocks = [];
      var para = [];
      var inCode = false;
      var codeLang = '';
      var codeBuf = [];

      function flushPara() {
        if (!para.length) return;
        var body = para.join('\n');
        blocks.push({ type: 'p', text: body });
        para = [];
      }

      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var fence = line.match(/^```([\w+-]*)\s*$/);
        if (fence) {
          if (inCode) {
            blocks.push({ type: 'code', lang: codeLang, text: codeBuf.join('\n') });
            codeBuf = [];
            inCode = false;
            codeLang = '';
          } else {
            flushPara();
            inCode = true;
            codeLang = fence[1] || '';
          }
          continue;
        }
        if (inCode) {
          codeBuf.push(line);
          continue;
        }

        // ── GFM table ────────────────────────────────────────────
        // Header row + optional align row + body rows. Streaming-safe:
        // partial tables (header only) still render as a table.
        if (isTableRow(line) && !isTableAlignRow(line)) {
          var next = lines[i + 1] || '';
          var looksTable = isTableAlignRow(next) || isTableRow(next);
          if (looksTable || (i + 1 < lines.length && isTableAlignRow(next))) {
            flushPara();
            var headerCells = splitTableCells(line);
            var aligns = [];
            i++;
            if (i < lines.length && isTableAlignRow(lines[i])) {
              aligns = parseAlignments(lines[i], headerCells.length);
              i++;
            }
            var rows = [];
            while (i < lines.length && isTableRow(lines[i]) && !isTableAlignRow(lines[i])) {
              rows.push(splitTableCells(lines[i]));
              i++;
            }
            i--; // outer for-loop will ++
            blocks.push({
              type: 'table',
              header: headerCells,
              aligns: aligns,
              rows: rows,
            });
            continue;
          }
        }

        // ── Blockquote (consecutive > lines) ─────────────────────
        if (isBlockquote(line)) {
          flushPara();
          var bq = [stripBlockquote(line)];
          while (i + 1 < lines.length && isBlockquote(lines[i + 1])) {
            i++;
            bq.push(stripBlockquote(lines[i]));
          }
          // Nested markdown in quotes: recurse on inner text (no tables to
          // avoid deep nesting issues; simple paragraphs/lists via re-render).
          blocks.push({ type: 'blockquote', text: bq.join('\n') });
          continue;
        }

        // ── Lists (ul / ol / task) ────────────────────────────────
        var lm = listItemMatch(line);
        if (lm) {
          flushPara();
          var items = [lm];
          var baseIndent = lm.indent;
          while (i + 1 < lines.length) {
            var peek = lines[i + 1];
            if (peek.trim() === '') {
              // blank line ends list only if next non-blank isn't a list item
              var j = i + 2;
              while (j < lines.length && lines[j].trim() === '') j++;
              if (j < lines.length && listItemMatch(lines[j])) {
                i++;
                continue;
              }
              break;
            }
            var cont = listItemMatch(peek);
            if (cont && cont.indent >= baseIndent) {
              i++;
              items.push(cont);
              continue;
            }
            // Continuation line of previous item (indented prose)
            if (/^\s{2,}\S/.test(peek) && !listItemMatch(peek) && !isTableRow(peek)) {
              i++;
              items[items.length - 1].text += '\n' + peek.trim();
              continue;
            }
            break;
          }
          blocks.push({ type: 'list', items: items, ordered: lm.ordered });
          continue;
        }

        var hm = line.match(/^(#{1,6})\s+(.+)$/);
        if (hm) {
          flushPara();
          blocks.push({ type: 'h', level: hm[1].length, text: hm[2] });
          continue;
        }
        // HR: don't confuse with table align rows (already handled)
        if (/^(\s*[-*_]){3,}\s*$/.test(line) && !isTableAlignRow(line) && line.indexOf('|') < 0) {
          flushPara();
          blocks.push({ type: 'hr' });
          continue;
        }
        if (line.trim() === '') {
          flushPara();
          continue;
        }
        para.push(line);
      }
      if (inCode) {
        blocks.push({ type: 'code', lang: codeLang, text: codeBuf.join('\n') });
      }
      flushPara();

      function inline(s) {
        var html = esc(s);
        // Images first (so ![a](u) isn't partially eaten by links)
        html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function(_, alt, url) {
          var decodedUrl = url.replace(/&amp;/g, '&');
          if (/^(https?:\/\/|\/)/i.test(decodedUrl)) {
            return '<img src="' + esc(decodedUrl) + '" alt="' + esc(alt) + '" loading="lazy" class="md-img">';
          }
          return esc(alt || '');
        });
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');
        // Single * italic only (underscore italic breaks snake_case / model ids)
        html = html.replace(/(^|[^\*])\*([^\*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
        html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');
        html = html.replace(/`([^`]+)`/g, '<code class="inline-code" dir="ltr">$1</code>');
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(_, text, url) {
          var decodedUrl = url.replace(/&amp;/g, '&');
          if (/^(https?:|mailto:)/i.test(decodedUrl)) {
            return '<a href="' + esc(decodedUrl) + '" target="_blank" rel="noopener noreferrer" dir="ltr">' + text + '</a>';
          }
          if (/^\/[A-Za-z0-9_./?#&=%-]+$/.test(decodedUrl)) {
            return '<a href="' + esc(decodedUrl) + '">' + text + '</a>';
          }
          return '<span class="dead-link" title="Blocked URL">' + text + '</span>';
        });
        html = html.replace(
          /(^|[\s>(])((?:https?:\/\/|www\.)[^\s<]+[^\s<.,;:!?'")\]])/g,
          function(_, pre, url) {
            var href = url;
            if (/^www\./i.test(href)) href = 'https://' + href;
            var rawHref = href.replace(/&amp;/g, '&');
            if (!/^(https?:)/i.test(rawHref)) return pre + url;
            return pre + '<a href="' + esc(rawHref) + '" target="_blank" rel="noopener noreferrer" dir="ltr">' + url + '</a>';
          }
        );
        return html;
      }

      function renderList(block) {
        // Group consecutive items by indent into nested lists (one level deep).
        var base = block.items.length ? block.items[0].indent : 0;
        var tag = block.ordered ? 'ol' : 'ul';
        var startAttr = '';
        if (block.ordered && block.items[0] && block.items[0].start > 1) {
          startAttr = ' start="' + block.items[0].start + '"';
        }
        var html = '<' + tag + startAttr + ' class="md-list" dir="auto">';
        var k = 0;
        while (k < block.items.length) {
          var it = block.items[k];
          var nested = [];
          k++;
          while (k < block.items.length && block.items[k].indent > base) {
            nested.push(block.items[k]);
            k++;
          }
          var liClass = it.task ? ' class="md-task"' : '';
          var check = '';
          if (it.task) {
            check = '<input type="checkbox" disabled' +
              (it.checked ? ' checked' : '') +
              ' class="md-task-check" aria-hidden="true"> ';
          }
          html += '<li' + liClass + ' dir="auto">' + check +
            inline(it.text).replace(/\n/g, '<br>');
          if (nested.length) {
            html += renderList({
              type: 'list',
              ordered: nested[0].ordered,
              items: nested.map(function(n) {
                return { indent: base, ordered: n.ordered, task: n.task, checked: n.checked, text: n.text, start: n.start };
              }),
            });
          }
          html += '</li>';
        }
        html += '</' + tag + '>';
        return html;
      }

      function renderTable(block) {
        var cols = block.header.length;
        // Detect if table content is Arabic-dominant → render RTL
        var allText = (block.header || []).join(' ') + ' ' +
          (block.rows || []).map(function(r) { return r.join(' '); }).join(' ');
        var isAr = !!(window.KazmaBidi && KazmaBidi.isArabicDominant(allText));
        var tableDir = isAr ? 'rtl' : 'ltr';
        var html = '<div class="md-table-wrap" dir="' + tableDir + '"><table class="md-table" dir="' + tableDir + '">';
        html += '<thead><tr>';
        for (var c = 0; c < cols; c++) {
          var al = (block.aligns && block.aligns[c]) ? ' style="text-align:' + block.aligns[c] + '"' : '';
          html += '<th' + al + ' dir="auto">' + inline(block.header[c] || '') + '</th>';
        }
        html += '</tr></thead><tbody>';
        for (var r = 0; r < block.rows.length; r++) {
          html += '<tr>';
          for (var c2 = 0; c2 < cols; c2++) {
            var al2 = (block.aligns && block.aligns[c2]) ? ' style="text-align:' + block.aligns[c2] + '"' : '';
            html += '<td' + al2 + ' dir="auto">' + inline(block.rows[r][c2] || '') + '</td>';
          }
          html += '</tr>';
        }
        html += '</tbody></table></div>';
        return html;
      }

      var out = [];
      for (var j = 0; j < blocks.length; j++) {
        var b = blocks[j];
        if (b.type === 'code') {
          out.push(codeBlock(b.lang || null, b.text));
        } else if (b.type === 'h') {
          var htag = 'h' + Math.min(6, Math.max(1, b.level));
          out.push('<' + htag + ' dir="auto">' + inline(b.text) + '</' + htag + '>');
        } else if (b.type === 'hr') {
          out.push('<hr>');
        } else if (b.type === 'table') {
          out.push(renderTable(b));
        } else if (b.type === 'list') {
          out.push(renderList(b));
        } else if (b.type === 'blockquote') {
          // Re-render inner content so lists/paragraphs inside quotes work
          var inner = render(b.text);
          out.push('<blockquote class="md-quote" dir="auto">' + inner + '</blockquote>');
        } else {
          out.push('<p dir="auto">' + inline(b.text).replace(/\n/g, '<br>') + '</p>');
        }
      }
      return out.join('\n');
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
    if (!el) return el;
    // Never wipe .message-content / .message-text — that destroyed the CoT
    // panel and then hideTyping set display:none on the whole bubble, so the
    // answer only reappeared when the operator expanded CoT (2026-09-01).
    if (el.closest && el.closest('.message-user')) return el;
    if (el.classList && (el.classList.contains('message-content')
        || el.classList.contains('message-text')
        || el.classList.contains('agent-progress'))) {
      var host = el.classList.contains('message-content') ? el : (el.parentNode || el);
      var existing = host.querySelector && host.querySelector('.kz-typing-row');
      if (existing) return existing;
      var row = document.createElement('div');
      row.className = 'kz-typing-row typing-visible';
      row.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span> '
        + (text || 'Thinking') + '...';
      host.appendChild(row);
      return row;
    }
    var span = document.createElement('span');
    span.className = 'typing-dots';
    span.innerHTML = '<span></span><span></span><span></span>';
    el.textContent = '';
    el.appendChild(span);
    el.appendChild(document.createTextNode(' ' + (text || 'Thinking') + '...'));
    el.style.display = 'flex';
    el.classList.add('typing-visible');
    return el;
  }
  function hideTyping(el) {
    if (!el) return;
    if (_typingTimer) { clearTimeout(_typingTimer); _typingTimer = null; }
    if (el.classList && el.classList.contains('kz-typing-row')) {
      if (el.parentNode) el.parentNode.removeChild(el);
      return;
    }
    if (el.closest && el.closest('.message-user')) {
      var urow = el.querySelector && el.querySelector('.kz-typing-row');
      if (urow && urow.parentNode) urow.parentNode.removeChild(urow);
      return;
    }
    if (el.classList && (el.classList.contains('message-content')
        || el.classList.contains('message-text'))) {
      var row = el.querySelector && el.querySelector('.kz-typing-row');
      if (row && row.parentNode) row.parentNode.removeChild(row);
      el.classList.remove('typing-visible');
      if (el.style.display === 'none' || el.style.display === 'flex') el.style.display = '';
      return;
    }
    el.style.display = 'none';
    el.classList.remove('typing-visible');
  }

  // ── Toast notifications ───────────────────────────────
  function toast(msg, type, duration) {
    type = type || 'info';
    duration = duration || 3500;
    // Delegate to the unified Alpine $store.toast when available so all
    // Kazma notifications share one system + container. Falls back to the
    // vanilla-DOM path when Alpine hasn't booted (early load / offline).
    if (window.Alpine && window.Alpine.store && Alpine.store('toast')) {
      Alpine.store('toast').add(msg, type, duration);
      return;
    }
    var container = document.getElementById('toast-container') ||
      document.querySelector('.toast-container');
    if (!container) return;
    var el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.textContent = msg;
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

  // Same rules as KazmaUtils.formatDuration (modules/util.js); the two are
  // held equal by tests/js/test_format_duration.js. A four-minute turn read
  // "240.0s" here (2026-09-26). Each unit is rounded before it is compared,
  // so 59.96 s reads "1m 0s", never "60.0s".
  function formatDuration(ms) {
    ms = Number(ms) || 0;
    if (ms <= 0) return '0s';
    if (ms < 999.5) return Math.round(ms) + 'ms';
    var tenths = Math.round(ms / 100) / 10;
    if (tenths < 60) return tenths.toFixed(1) + 's';
    var secs = Math.round(ms / 1000);
    if (secs < 3600) return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
    var mins = Math.round(ms / 60000);
    return Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm';
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
  // `sse` is the canonical name; `ssePost` is kept as a stable alias —
  // chat.js HITL cards and hitl_approval.js call ssePost and must not break.
  return {
    sse: ssePost,
    ssePost: ssePost,
    createSseParser: createSseParser,
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
