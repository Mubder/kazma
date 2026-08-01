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
      // clears — but if HITL is still waiting, keep the approval lock.
      if (this.pendingApproval) {
        this._turnActive = false;
        const chat = this._chat();
        if (chat && typeof chat.pauseForApproval === 'function') {
          chat.pauseForApproval(this.pendingApproval);
        }
        return;
      }
      this._turnActive = false;
      const chat = this._chat();
      if (chat && typeof chat.endTurn === 'function') chat.endTurn();
    },
    _pauseForApproval(approval) {
      this.isThinking = false;
      this.activeTool = null;
      this._turnActive = false;
      this.pendingApproval = approval;
      // Stop must not pulse; input locked until Approve / YOLO / Deny.
      const chat = this._chat();
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

      // Switching sessions must never inherit a stuck turn from the previous one.
      if (this.sessionId && this.sessionId !== sessionId) {
        this._resetTurnState();
        const chat = this._chat();
        if (chat && typeof chat.endTurn === 'function') chat.endTurn();
      }
      
      // If reconnecting to the same session, check for active tasks
      if (this.sessionId === sessionId && this.connectionStatus === 'disconnected') {
        // Notify chat.js to check for background generation after a short delay
        setTimeout(() => {
          const chat = this._chat();
          if (chat && typeof chat._checkBackgroundGeneration === 'function') {
            chat._checkBackgroundGeneration();
          }
        }, 100);
      }

      this.sessionId = sessionId;
      this._closeSocket();

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      let wsUrl = `${protocol}//${window.location.host}/ws/chat/${encodeURIComponent(sessionId)}`;

      // Append token from localStorage or meta tag as query param fallback
      // (browser WebSocket can't send custom headers; cookies are sent automatically)
      const token = localStorage.getItem('kazma.ws.token') ||
        document.querySelector('meta[name="kazma-ws-token"]')?.getAttribute('content') ||
        '';
      if (token) {
        wsUrl += `?token=${encodeURIComponent(token)}`;
      }

      this.connectionStatus = 'connecting';
      try {
        this._socket = new WebSocket(wsUrl);
      } catch (err) {
        console.error('[AgentStore] Failed creating WebSocket:', err);
        this._scheduleReconnect();
        return;
      }

      this._socket.onopen = () => {
        this.connectionStatus = 'connected';
        this._reconnectDelay = 1000;
        if (this._reconnectTimer) {
          clearTimeout(this._reconnectTimer);
          this._reconnectTimer = null;
        }
        console.log(`[AgentStore] Connected to telemetry bus: ${sessionId}`);
        
        // After reconnecting, check if there's an active task that needs visualization
        if (this.sessionId) {
          // Notify chat.js to check for background generation
          const chat = this._chat();
          if (chat && typeof chat._checkBackgroundGeneration === 'function') {
            setTimeout(() => chat._checkBackgroundGeneration(), 100);
          }
        }
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
        console.warn(`[AgentStore] Telemetry socket closed for session ${sessionId} (code=${code}, reason=${reason || 'none'})`);

        // Socket died mid-turn → release UI so user is never trapped.
        if (this._turnActive) {
          this._endTurn();
        }

        if (code === 4003) {
          console.warn('[AgentStore] WebSocket connection rejected (4003 Unauthorized). Pausing auto-reconnect.');
          return;
        }

        this._scheduleReconnect();
      };
    },

    disconnect() {
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
    sendPrompt(text, model) {
      if (!text || !text.trim()) return;
      this.pendingApproval = null;
      this._beginTurn();
      this.statusMessage = _ti('thinking', 'Kazma is thinking…');
      this.activeNode = 'Supervisor';

      const payload = {
        action: 'send_prompt',
        text: text.trim(),
        model: model || '',
      };

      this._sendPayload(payload);
    },

    submitApproval(approved = true, scope = 'once', threadId = null) {
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
        // Required for tool-scope grants when interrupt payload is unavailable
        tool: (pending && pending.tool) || '',
      };

      this.pendingApproval = null;
      this._beginTurn();
      this.statusMessage = approved
        ? (scope === 'yolo'
          ? _ti('yolo_running', 'YOLO on — running…')
          : _ti('executing_approved', 'Executing approved action…'))
        : _ti('denying_tool', 'Denying tool…');

      this._sendPayload(payload);
    },

    _sendPayload(payload) {
      if (this._socket && this._socket.readyState === WebSocket.OPEN) {
        this._socket.send(JSON.stringify(payload));
      } else {
        console.warn('[AgentStore] WS not connected, queuing reconnect before send');
        if (this.sessionId) {
          this.connect(this.sessionId);
          setTimeout(() => this._sendPayload(payload), 500);
        } else {
          this._endTurn();
        }
      }
    },

    // ── Deterministic Dual-Schema Event Dispatcher ────────────
    handleSocketMessage(frame) {
      if (!frame || !frame.type) return;

      const type = frame.type;
      const data = frame.data || {};

      switch (type) {
        case 'status':
        case 'status_update': {
          const statusVal = frame.status || data.status;
          if (statusVal === 'thinking') {
            this.isThinking = true;
            this._turnActive = true;
            // Keep one canonical label so Activity does not show
            // "thinking…" (beginTurn) + "thinking..." (server) as two rows.
            this.statusMessage = _ti('thinking', 'Kazma is thinking…');
            if (frame.active_node || data.active_node) this.activeNode = frame.active_node || data.active_node;
            this._progress({
              kind: 'status',
              title: this.statusMessage,
              // Avoid noisy "Node:" detail on every thinking heartbeat —
              // it also broke title-only coalesce in the workbench.
              state: 'running',
            });
            this._syncThinkingBanner();
          } else if (statusVal === 'routing_node') {
            this.isThinking = true;
            this._turnActive = true;
            this.activeNode = frame.active_node || data.active_node || 'Supervisor';
            this.statusMessage = _tiFmt('routing', 'Routing: {node}', { node: this.activeNode });
            this._progress({
              kind: 'plan',
              title: _tiFmt('routing_arrow', 'Routing → {node}', { node: this.activeNode }),
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
          // SSE-compat frames sometimes arrive over mixed transports
          if (!this.pendingApproval) {
            this._endTurn();
          }
          break;

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
          if (text && window.KazmaChat && typeof window.KazmaChat.appendLiveToken === 'function') {
            window.KazmaChat.appendLiveToken(text);
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

        default:
          console.debug('[AgentStore] Unhandled telemetry event type:', type, frame);
      }
    },
  });
});
