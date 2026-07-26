/**
 * Central Alpine.js Reactive Store & WebSocket Client Manager for Kazma Agent Telemetry.
 *
 * Exposes `Alpine.store('agent')` to drive chat UI templates, "Kazma is Thinking..." status,
 * active tool execution badges, and HITL approval dialogs reactively.
 */

document.addEventListener('alpine:init', () => {
  Alpine.store('agent', {
    // ── Reactive State Properties ───────────────────────────
    isThinking: false,
    statusMessage: 'Kazma is thinking...',
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

    // ── Connection Lifecycle ─────────────────────────────────
    connect(sessionId) {
      if (!sessionId) return;
      if (this.sessionId === sessionId && this._socket && this._socket.readyState === WebSocket.OPEN) {
        return;
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

        if (code === 4003) {
          console.warn('[AgentStore] WebSocket connection rejected (4003 Unauthorized). Pausing auto-reconnect.');
          return;
        }

        this._scheduleReconnect();
      };
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
      this.isThinking = true;
      this.statusMessage = 'Kazma is thinking...';
      this.activeNode = 'Supervisor';
      this.pendingApproval = null;

      const payload = {
        action: 'send_prompt',
        text: text.trim(),
        model: model || '',
      };

      this._sendPayload(payload);
    },

    submitApproval(approved = true, scope = 'once', threadId = null) {
      this.isThinking = true;
      this.statusMessage = 'Executing approved action...';
      const targetThreadId = threadId || (this.pendingApproval ? this.pendingApproval.thread_id : null) || this.sessionId;

      const payload = {
        action: 'approve_tool',
        thread_id: targetThreadId,
        approved: !!approved,
        scope: scope || 'once',
      };

      this.pendingApproval = null;
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
        case 'status_update':
          const statusVal = frame.status || data.status;
          if (statusVal === 'thinking') {
            this.isThinking = true;
            this.statusMessage = frame.message || data.message || 'Kazma is thinking...';
            if (frame.active_node || data.active_node) this.activeNode = frame.active_node || data.active_node;
          } else if (statusVal === 'routing_node') {
            this.isThinking = true;
            this.activeNode = frame.active_node || data.active_node || 'Supervisor';
            this.statusMessage = `Routing: ${this.activeNode}`;
          } else if (statusVal === 'paused_for_approval') {
            this.isThinking = false;
            this.pendingApproval = {
              thread_id: frame.thread_id || data.thread_id || this.sessionId,
              tool: frame.tool || data.tool || '',
              args: frame.args || data.args || {},
              tools: frame.tools || data.tools || [],
              message: frame.message || data.message || '',
            };
          } else if (statusVal === 'idle') {
            this.isThinking = false;
            this.activeNode = '';
            this.activeTool = null;
          }
          break;

        case 'tool_start':
        case 'tool_lifecycle':
          this.isThinking = true;
          const toolStatus = frame.status || data.status || 'tool_running';
          if (type === 'tool_start' || toolStatus === 'tool_running') {
            this.activeTool = {
              name: frame.tool_name || data.tool_name || 'tool',
              status: 'running',
              inputs: frame.input || data.inputs || null,
              result: null,
              error: null,
            };
          } else if (toolStatus === 'tool_completed') {
            if (this.activeTool) {
              this.activeTool.status = 'completed';
              this.activeTool.result = frame.output || data.result || 'Done';
            }
          } else if (toolStatus === 'tool_failed') {
            if (this.activeTool) {
              this.activeTool.status = 'failed';
              this.activeTool.error = frame.output || data.error || 'Execution failed';
            }
          }
          break;

        case 'tool_completed':
          this.isThinking = true;
          if (this.activeTool) {
            this.activeTool.status = 'completed';
            this.activeTool.result = frame.output || 'Done';
          }
          break;

        case 'approval_required':
          this.isThinking = false;
          this.pendingApproval = {
            thread_id: frame.thread_id || this.sessionId,
            tool: frame.tool || '',
            args: frame.args || {},
            tools: frame.tools || [],
            message: frame.message || '',
          };
          break;

        case 'token':
        case 'llm_delta':
          this.isThinking = true;
          const text = frame.content || data.content;
          if (text && window.KazmaChat && typeof window.KazmaChat.appendLiveToken === 'function') {
            window.KazmaChat.appendLiveToken(text);
          }
          break;

        case 'stream_end':
          this.isThinking = false;
          this.activeNode = '';
          this.activeTool = null;
          break;

        case 'error':
        case 'graph_error':
          console.error('[AgentStore] Graph error:', frame);
          this.isThinking = false;
          this.activeNode = '';
          this.activeTool = null;
          const errMsg = frame.message || data.message || 'Graph execution error';
          if (window.KazmaChat && typeof window.KazmaChat.appendErrorMessage === 'function') {
            window.KazmaChat.appendErrorMessage(errMsg);
          }
          break;

        default:
          console.debug('[AgentStore] Unhandled telemetry event type:', type, frame);
      }
    },
  });
});
