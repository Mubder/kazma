/**
 * Central Alpine.js Reactive Store & WebSocket Client Manager for Kazma Agent Telemetry.
 *
 * Exposes `Alpine.store('agent')` to drive chat UI templates, "Kazma is Thinking..." status,
 * active tool execution badges, and HITL approval dialogs reactively.
 *
 * CRITICAL CONTRACT with chat.js:
 *   - beginTurn()  → Stop button / input lock for the current turn
 *   - endTurn()    → ALWAYS released on idle / error / stream_end
 *   - pauseForApproval() → HITL card visible; input locked for approval only
 * Without these hooks the WS path left `_isGenerating=true` forever (Stop pulses,
 * Enter blocked) because SSE callbacks never fire when the WS bus is preferred.
 */

document.addEventListener('alpine:init', () => {
  function _ti(key, fallback) {
    const m = window.CHAT_I18N || {};
    const v = m[key];
    return (v != null && String(v) !== '') ? String(v) : (fallback || key);
  }
  function _budgetSuffix(data, frame) {
    const src = Object.assign({}, frame || {}, data || {});
    const iter = src.iteration;
    const maxI = src.max_iterations;
    if (iter == null || maxI == null || maxI === '') return '';
    return ' · ' + iter + '/' + maxI;
  }
  function _tiFmt(key, fallback, vars) {
    let s = _ti(key, fallback);
    if (vars) {
      Object.keys(vars).forEach((k) => {
        s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), String(vars[k]));
      });
    }
    return s;
  }

  /** Localize server/EN CoT status lines for Arabic (and structured step codes). */
  function _localizeStatus(raw, data) {
    const msg = String(raw || '').trim();
    const d = data || {};
    const step = String(d.step || '').toLowerCase();
    const tool = String(d.tool || d.tool_name || '').trim();
    const scope = String(d.scope || '').trim() || 'tool';
    const nTools = Array.isArray(d.tools) ? d.tools.length : 0;

    if (step === 'preparing' || /^preparing to execute\b/i.test(msg)) {
      const mN = msg.match(/preparing to execute\s+(\d+)\s+tools?/i);
      if (mN) return _tiFmt('preparing_n_tools', 'Preparing to execute {n} tools…', { n: mN[1] });
      if (/^\d+\s+tools?$/i.test(tool) || (nTools > 1 && !tool)) {
        const n = nTools > 1 ? nTools : (tool.match(/^(\d+)/) || [])[1] || tool;
        return _tiFmt('preparing_n_tools', 'Preparing to execute {n} tools…', { n: n });
      }
      const tname = tool || (msg.replace(/^preparing to execute\s+/i, '').replace(/\.{2,}$/, '').trim()) || 'tool';
      if (/^\d+\s+tools?$/i.test(tname)) {
        return _tiFmt('preparing_n_tools', 'Preparing to execute {n} tools…', {
          n: (tname.match(/^(\d+)/) || [])[1] || tname,
        });
      }
      return _tiFmt('preparing_tool', 'Preparing to execute {tool}…', { tool: tname });
    }
    if (step === 'resuming' || /resuming graph execution/i.test(msg) || /resuming execution/i.test(msg)) {
      return /graph/i.test(msg)
        ? _ti('resuming_graph', 'Resuming graph execution…')
        : _ti('resuming_execution', 'Resuming execution…');
    }
    if (/processing approval/i.test(msg)) {
      return _ti('processing_approval', 'Processing approval…');
    }
    if (/approval completed successfully/i.test(msg)) {
      return _ti('approval_complete', 'Approval completed successfully!');
    }
    if (/continuing after deny/i.test(msg)) {
      return _ti('continuing_after_deny', 'Continuing after deny…');
    }
    const afterAppr = msg.match(/running after\s+(\w+)\s+approval/i);
    if (afterAppr) {
      return _tiFmt('running_after_approval', 'Running after {scope} approval…', {
        scope: afterAppr[1] || scope,
      });
    }
    const still = msg.match(/still working after approval\s*\((\d+)\s*s\)/i);
    if (still) {
      return _tiFmt('still_working_approval', 'Still working after approval ({s}s)…', {
        s: still[1],
      });
    }
    const stillWork = msg.match(/still working\s*…?\s*\((\d+)\s*s\)/i);
    if (stillWork) {
      return _tiFmt('still_working_sec', 'Still working… ({s}s)', {
        s: stillWork[1],
      });
    }
    const runTool = msg.match(/^running\s+(.+?)\s*[.…]*$/i);
    if (runTool && !/after/i.test(msg)) {
      return _tiFmt('running_tool', 'Running {tool}…', { tool: runTool[1] });
    }
    return msg;
  }

  Alpine.store('agent', {
    // ── Reactive State Properties ───────────────────────────
    isThinking: false,
    statusMessage: _ti('thinking', 'Kazma is thinking…'),
    activeNode: '',
    activeTool: null, // { name: string, status: string, inputs: object|null, result: string|null, error: string|null }
    pendingApproval: null, // { thread_id: string, tool: string, args: object, tools: array, message: string }
    messages: [],
    sessionId: null,
    connectionStatus: 'disconnected',

    // ── Internal Socket Handles ──────────────────────────────
    _socket: null,
    _reconnectTimer: null,
    _reconnectDelay: 1000,
    _maxReconnectDelay: 16000,
    /** True while a send_prompt / approve_tool turn is in flight on this bus. */
    _turnActive: false,
    /** True when we close the socket on purpose (session switch / reconnect). */
    _intentionalClose: false,
    /**
     * Outbound frames waiting for an OPEN socket (or re-send after drop
     * before prompt_ack). Each entry: { payload, expectAck, clientMsgId, attempts }.
     */
    _outboundQueue: [],
    /** client_msg_id → { timer, attempts } for unacked send_prompt frames. */
    _pendingAcks: {},
    /** Max time to wait for server prompt_ack before surfacing an error. */
    _ackTimeoutMs: 20000,
    /** Max resend attempts for an unacked send_prompt across reconnects. */
    _maxSendAttempts: 5,

    // ── UI bridge helpers ────────────────────────────────────
    _chat() {
      return window.KazmaChat || null;
    },
    _progress(step) {
      const chat = this._chat();
      if (chat && typeof chat.logProgress === 'function') chat.logProgress(step);
    },
    _beginTurn() {
      this._turnActive = true;
      this.isThinking = true;
      const chat = this._chat();
      if (chat && typeof chat.beginTurn === 'function') chat.beginTurn();
    },
    _endTurn() {
      this.isThinking = false;
      this.activeNode = '';
      this.activeTool = null;
      // Server always emits idle after a graph pause so the thinking spinner
      // clears — but if HITL is still waiting, keep the approval lock. Check
      // BOTH the store card (pendingApproval) and an active inline card in the
      // message stream: renderHitlCard clears pendingApproval to hide the
      // bottom card, so pendingApproval alone no longer signals a pending
      // approval (incident 2026-08-16 dedup).
      const chat = this._chat();
      const inlineApproval = !!(
        chat && typeof chat.hasInlineApprovalCard === 'function' &&
        chat.hasInlineApprovalCard()
      );
      if (this.pendingApproval || inlineApproval) {
        this._turnActive = false;
        if (chat && typeof chat.pauseForApproval === 'function') {
          chat.pauseForApproval(this.pendingApproval);
        }
        return;
      }
      this._turnActive = false;
      if (chat && typeof chat.endTurn === 'function') chat.endTurn();
    },
    _pauseForApproval(approval) {
      this.isThinking = false;
      this.activeTool = null;
      this._turnActive = false;
      const chat = this._chat();
      // Inline card is the only HITL UI. Never set pendingApproval when we
      // can render inline — that was the duplicated YOLO card (bottom Alpine
      // + message stream). Keep pendingApproval as fallback only.
      if (chat && typeof chat._hitlApproval === 'function') {
        this.pendingApproval = null;
        chat._hitlApproval(approval);
        return;
      }
      this.pendingApproval = approval;
      if (chat && typeof chat.pauseForApproval === 'function') {
        chat.pauseForApproval(approval);
      }
    },
    _resetTurnState() {
      this._turnActive = false;
      this.isThinking = false;
      this.activeNode = '';
      this.activeTool = null;
      this.pendingApproval = null;
      this.statusMessage = _ti('thinking', 'Kazma is thinking…');
    },

    /** Keep the bottom thinking banner in sync with live status text. */
    _syncThinkingBanner() {
      try {
        const el = document.getElementById('thinking-indicator');
        if (!el) return;
        let text = el.querySelector('.thinking-text');
        if (!text) {
          // Vanilla path may have replaced children via showTyping
          const spans = el.querySelectorAll('span');
          // last text node area
        }
        text = el.querySelector('.thinking-text');
        if (text && this.statusMessage) {
          text.textContent = this.statusMessage;
        }
      } catch (e) { /* ignore */ }
    },

    // ── Connection Lifecycle ─────────────────────────────────
    connect(sessionId) {
      if (!sessionId) return;
      if (this.sessionId === sessionId && this._socket && this._socket.readyState === WebSocket.OPEN) {
        return;
      }
      // Already connecting to the same session — avoid close/reopen thrash.
      if (
        this.sessionId === sessionId &&
        this._socket &&
        this._socket.readyState === WebSocket.CONNECTING
      ) {
        return;
      }

      // Switching sessions must never inherit a stuck turn from the previous one.
      if (this.sessionId && this.sessionId !== sessionId) {
        this._failAllPendingAcks(null);
        this._outboundQueue = [];
        this._resetTurnState();
        const chat = this._chat();
        if (chat && typeof chat.endTurn === 'function') chat.endTurn();
      }

      this.sessionId = sessionId;
      // Intentional close for reconnect/switch — do not treat as a drop that
      // starts the SessionStore poller (that caused chat blink every 2s).
      this._intentionalClose = true;
      this._closeSocket();

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/chat/${encodeURIComponent(sessionId)}`;

      // Auth via the same-origin kazma-session cookie, which the browser sends
      // automatically on the WS handshake (+ loopback trust for localhost).
      // The old meta-tag / ?token= path was removed: it leaked the bearer
      // token into page source, proxy logs, history, and referrer headers
      // (audit MED #6).

      this.connectionStatus = 'connecting';
      try {
        this._socket = new WebSocket(wsUrl);
      } catch (err) {
        console.error('[AgentStore] Failed creating WebSocket:', err);
        this._intentionalClose = false;
        this._scheduleReconnect();
        return;
      }

      this._socket.onopen = () => {
        this._intentionalClose = false;
        this.connectionStatus = 'connected';
        this._reconnectDelay = 1000;
        if (this._reconnectTimer) {
          clearTimeout(this._reconnectTimer);
          this._reconnectTimer = null;
        }
        console.log(`[AgentStore] Connected to telemetry bus: ${sessionId}`);
        // Flush any prompts that were typed while reconnecting / socket down.
        this._flushOutboundQueue();
      };

      this._socket.onmessage = (evt) => {
        try {
          const frame = JSON.parse(evt.data);
          this.handleSocketMessage(frame);
        } catch (err) {
          console.warn('[AgentStore] Malformed WS frame received:', evt.data, err);
        }
      };

      this._socket.onerror = (err) => {
        console.warn('[AgentStore] WebSocket transport error:', err);
      };

      this._socket.onclose = (evt) => {
        this.connectionStatus = 'disconnected';
        const code = evt ? evt.code : 0;
        const reason = evt ? evt.reason : '';
        const intentional = !!this._intentionalClose;
        this._intentionalClose = false;
        console.warn(`[AgentStore] Telemetry socket closed for session ${sessionId} (code=${code}, reason=${reason || 'none'})`);

        const hasUnacked =
          (this._outboundQueue && this._outboundQueue.length > 0) ||
          (this._pendingAcks && Object.keys(this._pendingAcks).length > 0);

        // Unexpected drop mid-turn: if we still have an unacked send_prompt,
        // keep the UI in "thinking" and re-send after reconnect. Otherwise
        // unlock and poll SessionStore for a detached turn result.
        if (!intentional && this._turnActive) {
          if (hasUnacked) {
            // Re-queue any in-flight unacked prompts so open flushes them.
            this._requeuePendingAcks();
            console.warn('[AgentStore] Socket dropped with unacked prompt — will resend on reconnect');
          } else {
            this._endTurn();
            try {
              if (window.KazmaChat && typeof window.KazmaChat.pollBackgroundTurn === 'function' && this.sessionId) {
                window.KazmaChat.pollBackgroundTurn(this.sessionId, 0);
              }
            } catch (e) { /* ignore */ }
          }
        }

        if (code === 4003) {
          console.warn('[AgentStore] WebSocket connection rejected (4003 Unauthorized). Pausing auto-reconnect.');
          this._failAllPendingAcks('WebSocket unauthorized (login expired). Refresh and try again.');
          return;
        }

        if (!intentional) {
          this._scheduleReconnect();
        }
      };
    },

    disconnect() {
      this._intentionalClose = true;
      this._failAllPendingAcks(null); // silent clear on session switch
      this._outboundQueue = [];
      this._closeSocket();
      this._resetTurnState();
      this.sessionId = null;
      this.connectionStatus = 'disconnected';
    },

    _closeSocket() {
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
      }
      if (this._socket) {
        this._socket.onopen = null;
        this._socket.onmessage = null;
        this._socket.onerror = null;
        this._socket.onclose = null;
        if (this._socket.readyState === WebSocket.OPEN || this._socket.readyState === WebSocket.CONNECTING) {
          this._socket.close();
        }
        this._socket = null;
      }
    },

    _scheduleReconnect() {
      if (this._reconnectTimer) return;
      this._reconnectTimer = setTimeout(() => {
        this._reconnectTimer = null;
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxReconnectDelay);
        if (this.sessionId) {
          console.log(`[AgentStore] Reconnecting to WS session ${this.sessionId}...`);
          this.connect(this.sessionId);
        }
      }, this._reconnectDelay);
    },

    // ── WebSocket Actions ────────────────────────────────────
    sendPrompt(text, model, attachments, opts) {
      if (!text || !text.trim()) return;
      this.pendingApproval = null;
      const options = opts || {};
      // Slash capacity/yolo must not open a thinking turn — reconnect
      // catch-up then painted the confirmation into the next real prompt.
      if (!options.noTurn) {
        this._beginTurn();
        this.statusMessage = _ti('thinking', 'Kazma is thinking…');
        this.activeNode = 'Supervisor';
      }

      let clientMsgId = '';
      try {
        if (window.crypto && crypto.randomUUID) clientMsgId = crypto.randomUUID();
      } catch (e) { /* ignore */ }
      if (!clientMsgId) {
        clientMsgId = 'm-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
      }

      const payload = {
        action: 'send_prompt',
        text: text.trim(),
        model: model || '',
        client_msg_id: clientMsgId,
      };

      // Attachments (binary uploads) carried over the WS bus the same way the
      // SSE fallback sends them — the server builds the multimodal content
      // via build_user_content (T7).
      if (attachments && Array.isArray(attachments) && attachments.length) {
        payload.attachments = attachments;
      }

      this._enqueueSend(payload, { expectAck: true, clientMsgId: clientMsgId });
    },

    submitApproval(approved = true, scope = 'once', threadId = null, tool = null) {
      const pending = this.pendingApproval;
      const targetThreadId =
        threadId ||
        (pending && pending.thread_id) ||
        this.sessionId;

      const payload = {
        action: 'approve_tool',
        thread_id: targetThreadId,
        approved: !!approved,
        scope: scope || 'once',
        // Required for tool-scope grants when interrupt payload is unavailable.
        // Explicit tool wins: the inline card clears pendingApproval to hide the
        // bottom card, so its own data.tool must be passed through.
        tool: tool || (pending && pending.tool) || '',
      };

      this.pendingApproval = null;
      // Reset token accumulator so post-HITL full-answer delivery replaces
      // instead of concatenating onto the pre-approval partial (duplicate text).
      try {
        const chat = this._chat();
        if (chat && typeof chat.preparePostApprovalTurn === 'function') {
          chat.preparePostApprovalTurn();
        }
      } catch (e) { /* ignore */ }
      this._beginTurn();
      this.statusMessage = approved
        ? (scope === 'yolo'
          ? _ti('yolo_running', 'YOLO on — running…')
          : _ti('executing_approved', 'Executing approved action…'))
        : _ti('denying_tool', 'Denying tool…');

      this._enqueueSend(payload, { expectAck: false });
    },

    /**
     * Queue a frame until the socket is OPEN, then send. For send_prompt,
     * track prompt_ack so a reconnect can safely resend once.
     */
    _enqueueSend(payload, opts) {
      const options = opts || {};
      const entry = {
        payload: payload,
        expectAck: !!options.expectAck,
        clientMsgId: options.clientMsgId || (payload && payload.client_msg_id) || '',
        attempts: 0,
      };
      if (!this._outboundQueue) this._outboundQueue = [];
      this._outboundQueue.push(entry);
      this._flushOutboundQueue();
      if (!this._socket || this._socket.readyState !== WebSocket.OPEN) {
        console.warn('[AgentStore] WS not connected — queued frame, reconnecting');
        if (this.sessionId) this.connect(this.sessionId);
        else this._failAllPendingAcks('No active session. Refresh and try again.');
      }
    },

    _flushOutboundQueue() {
      if (!this._outboundQueue || !this._outboundQueue.length) return;
      if (!this._socket || this._socket.readyState !== WebSocket.OPEN) return;

      const remaining = [];
      for (let i = 0; i < this._outboundQueue.length; i++) {
        const entry = this._outboundQueue[i];
        if (!entry || !entry.payload) continue;
        entry.attempts = (entry.attempts || 0) + 1;
        if (entry.attempts > (this._maxSendAttempts || 5)) {
          if (entry.expectAck && entry.clientMsgId) {
            this._failAck(
              entry.clientMsgId,
              'Could not deliver your message after several retries. Please resend.'
            );
          }
          continue;
        }
        try {
          this._socket.send(JSON.stringify(entry.payload));
          if (entry.expectAck && entry.clientMsgId) {
            this._armAckTimeout(entry);
          }
          // Non-ack frames leave the queue once sent; ack frames leave on prompt_ack.
          if (!entry.expectAck) {
            continue;
          }
          // Keep a shadow for resend-on-drop until ack arrives (not in outbound queue).
        } catch (err) {
          console.warn('[AgentStore] send failed, will retry:', err);
          remaining.push(entry);
        }
      }
      this._outboundQueue = remaining;
    },

    _armAckTimeout(entry) {
      if (!entry || !entry.clientMsgId) return;
      if (!this._pendingAcks) this._pendingAcks = {};
      const existing = this._pendingAcks[entry.clientMsgId];
      if (existing && existing.timer) {
        try { clearTimeout(existing.timer); } catch (e) { /* ignore */ }
      }
      const clientMsgId = entry.clientMsgId;
      const timer = setTimeout(() => {
        const pending = this._pendingAcks && this._pendingAcks[clientMsgId];
        if (!pending) return;
        // Soft retry once more via reconnect if attempts remain.
        if ((pending.attempts || 1) < (this._maxSendAttempts || 5)) {
          console.warn('[AgentStore] prompt_ack timeout — requeueing', clientMsgId);
          this._requeueAckEntry(pending);
          if (this.sessionId) this.connect(this.sessionId);
          return;
        }
        this._failAck(
          clientMsgId,
          'Server did not acknowledge your message. Please resend.'
        );
      }, this._ackTimeoutMs || 20000);
      this._pendingAcks[clientMsgId] = {
        payload: entry.payload,
        clientMsgId: clientMsgId,
        attempts: entry.attempts || 1,
        timer: timer,
      };
    },

    _clearAck(clientMsgId) {
      if (!clientMsgId || !this._pendingAcks) return;
      const pending = this._pendingAcks[clientMsgId];
      if (pending && pending.timer) {
        try { clearTimeout(pending.timer); } catch (e) { /* ignore */ }
      }
      delete this._pendingAcks[clientMsgId];
    },

    _requeueAckEntry(pending) {
      if (!pending || !pending.payload) return;
      this._clearAck(pending.clientMsgId);
      if (!this._outboundQueue) this._outboundQueue = [];
      // Avoid duplicate queue entries for the same client_msg_id.
      const id = pending.clientMsgId;
      const already = this._outboundQueue.some(
        (e) => e && e.clientMsgId === id
      );
      if (!already) {
        this._outboundQueue.push({
          payload: pending.payload,
          expectAck: true,
          clientMsgId: id,
          attempts: pending.attempts || 1,
        });
      }
    },

    _requeuePendingAcks() {
      if (!this._pendingAcks) return;
      const ids = Object.keys(this._pendingAcks);
      for (let i = 0; i < ids.length; i++) {
        this._requeueAckEntry(this._pendingAcks[ids[i]]);
      }
    },

    _failAck(clientMsgId, message) {
      this._clearAck(clientMsgId);
      if (message) {
        try {
          const chat = this._chat();
          if (chat && typeof chat.appendErrorMessage === 'function') {
            chat.appendErrorMessage(message);
          }
        } catch (e) { /* ignore */ }
      }
      this._endTurn();
    },

    _failAllPendingAcks(message) {
      const ids = this._pendingAcks ? Object.keys(this._pendingAcks) : [];
      for (let i = 0; i < ids.length; i++) {
        this._clearAck(ids[i]);
      }
      this._outboundQueue = [];
      if (message) {
        try {
          const chat = this._chat();
          if (chat && typeof chat.appendErrorMessage === 'function') {
            chat.appendErrorMessage(message);
          }
        } catch (e) { /* ignore */ }
        this._endTurn();
      }
    },

    _handlePromptAck(data) {
      const d = data || {};
      const clientMsgId = d.client_msg_id || '';
      if (clientMsgId) this._clearAck(clientMsgId);
      // Drop matching outbound queue entry if still present.
      if (this._outboundQueue && clientMsgId) {
        this._outboundQueue = this._outboundQueue.filter(
          (e) => !e || e.clientMsgId !== clientMsgId
        );
      }
      if (d.accepted) {
        // Durable: refresh sidebar so the session appears with the real title.
        try {
          if (window.KazmaChat && typeof window.KazmaChat.refreshSessionsSoon === 'function') {
            window.KazmaChat.refreshSessionsSoon();
          } else if (window.KazmaChat && typeof window.KazmaChat.refreshSessions === 'function') {
            window.KazmaChat.refreshSessions();
          }
        } catch (e) { /* ignore */ }
        return;
      }
      // Rejected (turn busy / persist failed) — surface and unlock.
      const msg =
        d.message ||
        (d.reason === 'turn_busy'
          ? 'Previous message is still processing.'
          : 'Message was not accepted. Please try again.');
      // turn_busy already gets a graph_error from the server; only paint if needed.
      if (d.reason === 'persist_failed' || d.reason === 'turn_busy') {
        // graph_error handler also ends the turn; keep thinking until that arrives,
        // but if no graph_error follows, unlock after a short grace.
        const self = this;
        setTimeout(function () {
          if (self._turnActive && !self.pendingApproval) {
            // leave unlock to graph_error/idle; no-op if already idle
          }
        }, 50);
      }
      if (d.reason === 'persist_failed') {
        try {
          const chat = this._chat();
          if (chat && typeof chat.appendErrorMessage === 'function') {
            chat.appendErrorMessage(msg);
          }
        } catch (e) { /* ignore */ }
        this._endTurn();
      }
    },

    // ── Deterministic Dual-Schema Event Dispatcher ────────────
    handleSocketMessage(frame) {
      if (!frame || !frame.type) return;

      const type = frame.type;
      const data = frame.data || {};

      // Keep chat.js idle-watchdog armed on any live frame
      try {
        if (window.KazmaChat && typeof window.KazmaChat.noteTurnActivity === 'function') {
          if (type !== 'pong' && type !== 'ping') window.KazmaChat.noteTurnActivity();
        }
      } catch (e) { /* ignore */ }

      switch (type) {
        case 'memory_explain': {
          try {
            if (window.KazmaChat && typeof window.KazmaChat.applyMemoryExplain === 'function') {
              window.KazmaChat.applyMemoryExplain(data || frame || {});
            }
          } catch (e) { /* ignore */ }
          break;
        }

        case 'prompt_ack': {
          this._handlePromptAck(data);
          break;
        }

        case 'status':
        case 'status_update': {
          const statusVal = frame.status || data.status;
          if (statusVal === 'thinking') {
            this.isThinking = true;
            this._turnActive = true;
            // Prefer server heartbeat message ("Still working… (45s)") when present
            this.statusMessage = (data && data.message)
              ? String(data.message)
              : _ti('thinking', 'Kazma is thinking…');
            this.statusMessage += _budgetSuffix(data, frame);
            if (frame.active_node || data.active_node) this.activeNode = frame.active_node || data.active_node;
            this._progress({
              kind: 'status',
              title: this.statusMessage,
              state: 'running',
            });
            this._syncThinkingBanner();
            // Reconnect catch-up ("previous turn still running") — arm SessionStore
            // poll so a turn that finishes on a rebound/dead socket still paints.
            if (/reconnected|still running/i.test(this.statusMessage || '')) {
              try {
                if (window.KazmaChat && typeof window.KazmaChat.pollBackgroundTurn === 'function' && this.sessionId) {
                  window.KazmaChat.pollBackgroundTurn(this.sessionId, 0);
                }
              } catch (e) { /* ignore */ }
            }
          } else if (statusVal === 'routing_node') {
            this.isThinking = true;
            this._turnActive = true;
            this.activeNode = frame.active_node || data.active_node || 'Supervisor';
            this.statusMessage = _tiFmt('routing', 'Routing: {node}', { node: this.activeNode })
              + _budgetSuffix(data, frame);
            this._progress({
              kind: 'plan',
              title: _tiFmt('routing_arrow', 'Routing → {node}', { node: this.activeNode })
                + _budgetSuffix(data, frame),
              state: 'running',
            });
            this._syncThinkingBanner();
          } else if (statusVal === 'synthesizing') {
            // The graph finished tool execution and is now composing the
            // final answer. Keep the thinking indicator alive so the user
            // doesn't see "Done 0s" during the gap between tool completion
            // and the backfilled response text arriving.
            this.isThinking = true;
            this._turnActive = true;
            this.statusMessage = _ti('synthesizing', 'Composing response…');
            this._progress({
              kind: 'status',
              title: _ti('synthesizing', 'Composing response…'),
              state: 'running',
            });
            this._syncThinkingBanner();
          } else if (statusVal === 'paused_for_approval') {
            this._progress({
              kind: 'status',
              title: _ti('waiting_approval', 'Waiting for approval'),
              detail: frame.tool || data.tool || data.message || '',
              state: 'info',
            });
            this._pauseForApproval({
              thread_id: frame.thread_id || data.thread_id || this.sessionId,
              tool: frame.tool || data.tool || '',
              args: frame.args || data.args || {},
              tools: frame.tools || data.tools || [],
              message: frame.message || data.message || '',
              // kind/items drive the semantic clarify/confirm option cards
              // in chat.js — dropping them degraded every WS approval to a
              // generic Approve/Deny (SSE passed them through correctly).
              kind: frame.kind || data.kind || '',
              items: frame.items || data.items || null,
            });
          } else if (statusVal === 'idle') {
            // End of turn — MUST release chat.js Stop / Enter lock.
            this._endTurn();
          }
          break;
        }

        case 'tool_start':
        case 'tool_lifecycle': {
          this.isThinking = true;
          this._turnActive = true;
          const toolStatus = frame.status || data.status || 'tool_running';
          const tName = frame.tool_name || data.tool_name || 'tool';
          if (type === 'tool_start' || toolStatus === 'tool_running') {
            this.activeTool = {
              name: tName,
              status: 'running',
              inputs: frame.input || data.inputs || null,
              result: null,
              error: null,
            };
            let detail = '';
            try {
              const raw = this.activeTool.inputs;
              detail = typeof raw === 'string' ? raw : JSON.stringify(raw || {});
            } catch (e) {
              detail = String(this.activeTool.inputs || '');
            }
            this._progress({
              kind: 'tool',
              title: tName,
              detail: detail || '',
              state: 'running',
            });
            this.statusMessage = _tiFmt('running_tool', 'Running {tool}…', { tool: tName });
            this._syncThinkingBanner();
          } else if (toolStatus === 'tool_completed') {
            if (this.activeTool) {
              this.activeTool.status = 'completed';
              this.activeTool.result = frame.output || data.result || 'Done';
            }
            this._progress({
              kind: 'tool',
              title: tName,
              detail: String(frame.output || data.result || ''),
              state: 'done',
            });
          } else if (toolStatus === 'tool_failed') {
            if (this.activeTool) {
              this.activeTool.status = 'failed';
              this.activeTool.error = frame.output || data.error || 'Execution failed';
            }
            this._progress({
              kind: 'tool',
              title: tName,
              detail: String(frame.output || data.error || 'failed'),
              state: 'failed',
            });
          }
          break;
        }

        case 'tool_completed':
          this.isThinking = true;
          if (this.activeTool) {
            this.activeTool.status = 'completed';
            this.activeTool.result = frame.output || 'Done';
            this._progress({
              kind: 'tool',
              title: this.activeTool.name || 'tool',
              detail: String(frame.output || 'Done').slice(0, 200),
              state: 'done',
            });
          }
          break;

        case 'approval_required':
          this._progress({
            kind: 'status',
            title: _ti('waiting_approval', 'Waiting for approval'),
            detail: frame.tool || data.tool || frame.message || '',
            state: 'info',
          });
          this._pauseForApproval({
            thread_id: frame.thread_id || data.thread_id || this.sessionId,
            tool: frame.tool || data.tool || '',
            args: frame.args || data.args || {},
            tools: frame.tools || data.tools || [],
            message: frame.message || data.message || '',
            // kind/items drive the semantic clarify/confirm option cards
            // (see the paused_for_approval case above).
            kind: frame.kind || data.kind || '',
            items: frame.items || data.items || null,
          });
          break;

        case 'approval_started':
        case 'approval_progress':
        case 'approval_resuming':
          this.isThinking = true;
          this._turnActive = true;
          {
            let raw =
              (data && data.message) ||
              (type === 'approval_resuming'
                ? 'Resuming graph execution...'
                : type === 'approval_progress'
                  ? (data && data.tool
                      ? 'Preparing to execute ' + data.tool + '...'
                      : 'Processing approval...')
                  : 'Processing approval...');
            // Prefer structured step when present
            if (type === 'approval_resuming' && !(data && data.message)) {
              raw = 'Resuming graph execution...';
            }
            if (type === 'approval_started' && !(data && data.message)) {
              raw = 'Processing approval...';
            }
            this.statusMessage = _localizeStatus(raw, Object.assign({}, data, {
              step:
                (data && data.step) ||
                (type === 'approval_resuming'
                  ? 'resuming'
                  : type === 'approval_progress'
                    ? 'preparing'
                    : ''),
              tool: (data && (data.tool || data.tool_name)) || frame.tool || '',
            }));
          }
          this._progress({
            kind: 'thought',
            title: this.statusMessage,
            state: 'running',
          });
          this._syncThinkingBanner();
          break;

        case 'approval_complete':
          // Prefer idle (always sent after this). If idle is dropped, still
          // release Stop so the user is never trapped after a finished turn.
          this.isThinking = false;
          if (!this.pendingApproval) {
            this._endTurn();
          }
          break;

        case 'done':
        case 'turn_complete': {
          // Authoritative terminal: REPLACE paint (never append — that doubled
          // post-HITL answers when backfill re-sent the full text).
          const finalText = data.content || frame.content || '';
          // reconnect catch-up sets replay:true — chat.js must not open a
          // second bubble when loadSession already painted the same answer.
          const isReplay = !!(data && data.replay) || !!(frame && frame.replay);
          if (finalText && window.KazmaChat) {
            if (typeof window.KazmaChat.applyFinalAssistantText === 'function') {
              window.KazmaChat.applyFinalAssistantText(finalText, data.model || '', {
                replay: isReplay,
              });
            } else if (typeof window.KazmaChat.appendLiveToken === 'function') {
              window.KazmaChat.appendLiveToken(finalText, { full: true });
            }
          }
          // Usage stats (tokens/cost + cumulative session totals) → badges +
          // workbench summary bar. Runs BEFORE _endTurn so finalizeProgress
          // sees the turn's usage when it renders the summary line.
          if (window.KazmaChat && typeof window.KazmaChat.applyTurnStats === 'function') {
            try { window.KazmaChat.applyTurnStats(data || {}); } catch (e) { /* stats must never break the turn */ }
          }
          if (!this.pendingApproval) {
            this._endTurn();
          }
          break;
        }

        case 'approval_error':
          console.error('[AgentStore] Approval error:', frame);
          this._endTurn();
          {
            const errMsg = (data && (data.error || data.message)) || 'Approval failed';
            const chat = this._chat();
            if (chat && typeof chat.appendErrorMessage === 'function') {
              chat.appendErrorMessage(errMsg);
            }
          }
          break;

        case 'token':
        case 'llm_delta': {
          this.isThinking = true;
          this._turnActive = true;
          const text = frame.content || data.content;
          if (!text || !window.KazmaChat) break;
          // full=true backfill / recovery: replace, don't concatenate
          const isFull = !!(data && data.full) || !!(frame && frame.full);
          if (isFull && typeof window.KazmaChat.applyFinalAssistantText === 'function') {
            window.KazmaChat.applyFinalAssistantText(text, data.model || '');
          } else if (typeof window.KazmaChat.appendLiveToken === 'function') {
            window.KazmaChat.appendLiveToken(text, { full: isFull });
          }
          break;
        }

        case 'stream_end':
          // Same as idle — _endTurn keeps approval lock if pendingApproval set.
          this._endTurn();
          break;

        case 'error':
        case 'graph_error':
          console.error('[AgentStore] Graph error:', frame);
          {
            const errMsg = frame.message || data.message || 'Graph execution error';
            if (window.KazmaChat && typeof window.KazmaChat.appendErrorMessage === 'function') {
              window.KazmaChat.appendErrorMessage(errMsg);
            }
          }
          this._endTurn();
          break;

        case 'capacity': {
          try {
            const reply = (data && data.reply) || frame.reply || '';
            if (reply && window.KazmaChat && typeof window.KazmaChat.paintCapacityReply === 'function') {
              window.KazmaChat.paintCapacityReply(reply);
            }
            if (window.KazmaChat && typeof window.KazmaChat.refreshCapacity === 'function') {
              window.KazmaChat.refreshCapacity();
            }
          } catch (e) { /* ignore */ }
          break;
        }

        case 'steer': {
          // WS steer ack — previously unhandled, so the "demoted: true"
          // notification (hard steer timed out, fell back to soft) never
          // reached the user.
          const sMsg =
            (data && data.message) ||
            ((data && data.demoted)
              ? 'Hard steer timed out — applied as a soft steer hint instead.'
              : 'Steer applied.');
          this._progress({
            kind: 'status',
            title: sMsg,
            detail: '',
            state: 'info',
          });
          break;
        }

        default:
          console.debug('[AgentStore] Unhandled telemetry event type:', type, frame);
      }
    },
  });
});
