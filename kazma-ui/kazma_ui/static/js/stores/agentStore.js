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
      const wsUrl = `${protocol}//${window.location.host}/ws/chat/${encodeURIComponent(sessionId)}`;

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
        console.warn('[AgentStore] WebSocket error:', err);
      };

      this._socket.onclose = () => {
        this.connectionStatus = 'disconnected';
        console.log(`[AgentStore] Telemetry socket closed for session ${sessionId}`);
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

    // ── Deterministic Event Dispatcher ───────────────────────
    handleSocketMessage(frame) {
      if (!frame || !frame.type) return;

      const type = frame.type;
      const data = frame.data || {};

      switch (type) {
        case 'status_update':
          if (data.status === 'thinking') {
            this.isThinking = true;
            if (data.active_node) this.activeNode = data.active_node;
          } else if (data.status === 'routing_node') {
            this.isThinking = true;
            this.activeNode = data.active_node || 'Supervisor';
          } else if (data.status === 'paused_for_approval') {
            // Pause thinking banner, present pending approval card
            this.isThinking = false;
            this.pendingApproval = {
              thread_id: data.thread_id || this.sessionId,
              tool: data.tool || '',
              args: data.args || {},
              tools: data.tools || [],
              message: data.message || '',
            };
          } else if (data.status === 'idle') {
            // Strictly set isThinking = false only on explicit idle
            this.isThinking = false;
            this.activeNode = '';
            this.activeTool = null;
          }
          break;

        case 'tool_lifecycle':
          this.isThinking = true;
          if (data.status === 'tool_running') {
            this.activeTool = {
              name: data.tool_name || 'tool',
              status: 'running',
              inputs: data.inputs || null,
              result: null,
              error: null,
            };
          } else if (data.status === 'tool_completed') {
            if (this.activeTool) {
              this.activeTool.status = 'completed';
              this.activeTool.result = data.result || 'Done';
            }
          } else if (data.status === 'tool_failed') {
            if (this.activeTool) {
              this.activeTool.status = 'failed';
              this.activeTool.error = data.error || 'Execution failed';
            }
          }
          break;

        case 'llm_delta':
          // Token streaming delta received
          this.isThinking = true;
          if (window.KazmaChat && typeof window.KazmaChat.appendLiveToken === 'function') {
            window.KazmaChat.appendLiveToken(data.content);
          }
          break;

        case 'graph_error':
          console.error('[AgentStore] Graph error:', data);
          this.isThinking = false;
          this.activeNode = '';
          this.activeTool = null;
          if (window.KazmaChat && typeof window.KazmaChat.appendErrorMessage === 'function') {
            window.KazmaChat.appendErrorMessage(data.message || 'Graph execution error');
          }
          break;

        default:
          console.debug('[AgentStore] Unhandled telemetry event type:', type, data);
      }
    },
  });
});
