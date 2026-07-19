/* ═══════════════════════════════════════════════════════
   Kazma Voice — Browser voice recording + STT/TTS

   Features:
   - Hold-to-record microphone button (turn-based)
   - STT via backend providers (OpenAI, Groq, Cohere, NVIDIA)
   - TTS playback of assistant responses
   - Voice provider switching via /voice commands
   - Live streaming mode via WebSocket (/ws/voice) with VAD
   ═══════════════════════════════════════════════════════ */

(function() {
  'use strict';

  var mediaRecorder = null;
  var audioChunks = [];
  var isRecording = false;
  var stream = null;

  // Config (persisted in localStorage)
  var STT_PROVIDER_KEY = 'kazma.sttProvider';
  var TTS_PROVIDER_KEY = 'kazma.ttsProvider';
  var TTS_ENABLED_KEY = 'kazma.ttsEnabled';

  function getSttProvider() {
    return localStorage.getItem(STT_PROVIDER_KEY) || 'openai';
  }

  function getTtsProvider() {
    return localStorage.getItem(TTS_PROVIDER_KEY) || 'edgetts';
  }

  function isTtsEnabled() {
    return localStorage.getItem(TTS_ENABLED_KEY) !== 'false'; // default true
  }

  // ── Recording ─────────────────────────────────────────

  async function startRecording() {
    if (isRecording) return;
    if (isStreaming) {
      showToast('Please stop Live Voice Mode first', 'warning');
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunks = [];

      // Prefer webm/opus, fall back to whatever the browser supports
      var mimeType = 'audio/webm;codecs=opus';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = 'audio/webm';
        if (!MediaRecorder.isTypeSupported(mimeType)) {
          mimeType = ''; // let the browser decide
        }
      }

      var opts = mimeType ? { mimeType: mimeType } : {};
      mediaRecorder = new MediaRecorder(stream, opts);

      mediaRecorder.ondataavailable = function(e) {
        if (e.data.size > 0) audioChunks.push(e.data);
      };

      mediaRecorder.onstop = async function() {
        var blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
        await sendForTranscription(blob);
        cleanup();
      };

      mediaRecorder.start(100); // collect in 100ms chunks
      isRecording = true;
      updateUI(true);
    } catch (err) {
      console.error('[Voice] Microphone access denied:', err);
      showToast('Microphone access denied. Please allow microphone access.', 'error');
    }
  }

  function stopRecording() {
    if (!isRecording || !mediaRecorder) return;
    mediaRecorder.stop();
    isRecording = false;
    updateUI(false);
  }

  function cleanup() {
    if (stream) {
      stream.getTracks().forEach(function(t) { t.stop(); });
      stream = null;
    }
    mediaRecorder = null;
  }

  function updateUI(recording) {
    var micIcon = document.getElementById('mic-icon');
    var recIcon = document.getElementById('mic-recording-icon');
    var voiceBtn = document.getElementById('voice-btn');
    if (micIcon) micIcon.style.display = recording ? 'none' : 'block';
    if (recIcon) recIcon.style.display = recording ? 'block' : 'none';
    if (voiceBtn) {
      voiceBtn.style.background = recording ? 'rgba(239,68,68,0.2)' : '';
      voiceBtn.style.borderColor = recording ? '#ef4444' : '';
    }
  }

  // ── STT ───────────────────────────────────────────────

  async function sendForTranscription(blob) {
    var provider = getSttProvider();
    var formData = new FormData();
    var ext = 'webm';
    if (blob.type.includes('ogg')) ext = 'ogg';
    else if (blob.type.includes('mp3')) ext = 'mp3';
    else if (blob.type.includes('wav')) ext = 'wav';
    formData.append('file', blob, 'voice.' + ext);
    formData.append('provider', provider);
    formData.append('language', 'auto');

    try {
      showToast('Transcribing (' + provider + ')...', 'info', 2000);
      var resp = await fetch('/api/voice/stt', { method: 'POST', body: formData });
      if (!resp.ok) {
        var err = await resp.json().catch(function() { return { detail: 'STT failed' }; });
        showToast('Transcription failed: ' + (err.detail || resp.statusText), 'error');
        return;
      }
      var data = await resp.json();
      if (data.text) {
        // Insert transcribed text into the chat input
        var inputEl = document.getElementById('chat-input');
        if (inputEl) {
          var current = inputEl.value.trim();
          inputEl.value = current ? current + ' ' + data.text : data.text;
          inputEl.dispatchEvent(new Event('input'));
          inputEl.focus();
        }
        showToast('Transcribed: "' + data.text.substring(0, 60) + '..."', 'success', 3000);
      }
    } catch (err) {
      console.error('[Voice] STT request failed:', err);
      showToast('Transcription request failed', 'error');
    }
  }

  // ── TTS Playback ──────────────────────────────────────

  async function playTTS(text, provider) {
    if (!isTtsEnabled()) return;
    provider = provider || getTtsProvider();

    try {
      var formData = new FormData();
      formData.append('text', text);
      formData.append('provider', provider);
      formData.append('voice', 'default');
      formData.append('output_format', 'mp3');

      var resp = await fetch('/api/voice/tts', { method: 'POST', body: formData });
      if (!resp.ok) {
        console.warn('[Voice] TTS failed:', resp.status);
        return;
      }
      var audioBlob = await resp.blob();
      var url = URL.createObjectURL(audioBlob);
      var audio = new Audio(url);
      audio.onended = function() { URL.revokeObjectURL(url); };
      await audio.play();
    } catch (err) {
      console.warn('[Voice] TTS playback failed:', err);
    }
  }

  // ── Voice command handling ────────────────────────────

  function handleVoiceCommand(text) {
    var lower = text.trim().toLowerCase();
    if (lower === '/voice on' || lower === '/voice enable') {
      localStorage.setItem(TTS_ENABLED_KEY, 'true');
      showToast('Voice replies enabled', 'success');
      return true;
    }
    if (lower === '/voice off' || lower === '/voice disable') {
      localStorage.setItem(TTS_ENABLED_KEY, 'false');
      showToast('Voice replies disabled', 'info');
      return true;
    }
    if (lower.startsWith('/voice stt ')) {
      var p = text.trim().substring(11).trim();
      localStorage.setItem(STT_PROVIDER_KEY, p);
      showToast('STT provider set to: ' + p, 'success');
      return true;
    }
    if (lower.startsWith('/voice tts ')) {
      var p2 = text.trim().substring(11).trim();
      localStorage.setItem(TTS_PROVIDER_KEY, p2);
      showToast('TTS provider set to: ' + p2, 'success');
      return true;
    }
    if (lower === '/voice live' || lower === '/voice stream') {
      showToast('Starting live streaming mode...', 'info', 2000);
      startStreaming();
      return true;
    }
    if (lower === '/voice stop' || lower === '/voice exit') {
      stopStreaming();
      showToast('Live mode stopped', 'info');
      return true;
    }
    if (lower === '/voice' || lower === '/voice status') {
      var live = window.KazmaVoice.isStreaming && window.KazmaVoice.isStreaming();
      showToast(
        'STT: ' + getSttProvider() + ' | TTS: ' + getTtsProvider() +
        ' | Replies: ' + (isTtsEnabled() ? 'ON' : 'OFF') +
        (live ? ' | LIVE' : ''),
        'info', 5000
      );
      return true;
    }
    return false;
  }

  // ── Show mic button if browser supports it ────────────

  function initVoiceButton() {
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      var voiceBtn = document.getElementById('voice-btn');
      if (voiceBtn) voiceBtn.style.display = 'block';
      var liveBtn = document.getElementById('voice-live-btn');
      if (liveBtn) liveBtn.style.display = 'block';
    }
  }

  // ── Init on DOM ready ─────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVoiceButton);
  } else {
    initVoiceButton();
  }

  // ──────────────────────────────────────────────────────
  // Streaming mode — WebSocket live conversation
  // ──────────────────────────────────────────────────────

  var ws = null;
  var audioContext = null;
  var mediaStreamSource = null;
  var audioProcessor = null;
  var micStream = null;
  var isStreaming = false;
  var ttsAudioChunks = [];

  async function startStreaming() {
    if (isStreaming) return;
    try {
      // Open WebSocket
      var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      // WebSocket — browser sends cookies automatically, so the
      // kazma-secret cookie (set at login) will be used by the server
      // to authenticate the connection.
      ws = new WebSocket(proto + '//' + window.location.host + '/ws/voice');

      ws.onopen = function() {
        var sessionId = window.KazmaChat ? window.KazmaChat.getOrCreateSessionId() : '';
        var startMsg = {
          type: 'start',
          session_id: sessionId,
          stt_provider: getSttProvider(),
          tts_provider: getTtsProvider(),
          sample_rate: 16000
        };
        ws.send(JSON.stringify(startMsg));
        _captureAudioForStreaming();
        isStreaming = true;
        showToast('Live voice mode active — speak naturally', 'success', 3000);
        updateStreamingUI(true);
      };

      ws.onmessage = function(ev) {
        try {
          var msg = JSON.parse(ev.data);
          _handleStreamMessage(msg);
        } catch (err) {
          console.error('[Voice] WS message parse error', err);
        }
      };

      ws.onerror = function() {
        console.error('[Voice] WebSocket error');
        showToast('Voice connection error', 'error');
      };

      ws.onclose = function() {
        _cleanupStreaming();
        isStreaming = false;
        updateStreamingUI(false);
      };

    } catch (err) {
      console.error('[Voice] Failed to start streaming:', err);
      showToast('Cannot access microphone for streaming', 'error');
    }
  }

  function stopStreaming() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'stop' }));
    }
    _cleanupStreaming();
    if (ws) {
      try { ws.close(); } catch (e) {}
      ws = null;
    }
    isStreaming = false;
    updateStreamingUI(false);
  }

  async function _captureAudioForStreaming() {
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });

      audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      mediaStreamSource = audioContext.createMediaStreamSource(micStream);

      // Use ScriptProcessorNode for simplicity (AudioWorklet is more modern
      // but requires a separate module file). 4096 sample buffer.
      audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);
      audioProcessor.onaudioprocess = function(e) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        var input = e.inputBuffer.getChannelData(0);

        // Convert float32 [-1,1] to 16-bit PCM
        var pcm = new Int16Array(input.length);
        for (var i = 0; i < input.length; i++) {
          var s = Math.max(-1, Math.min(1, input[i]));
          pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        // Base64 encode and send
        var bytes = new Uint8Array(pcm.buffer);
        var b64 = _bytesToBase64(bytes);
        ws.send(JSON.stringify({ type: 'audio', data: b64 }));
      };

      mediaStreamSource.connect(audioProcessor);
      audioProcessor.connect(audioContext.destination); // needed for processing to run

    } catch (err) {
      console.error('[Voice] Audio capture error:', err);
      showToast('Microphone capture failed', 'error');
      stopStreaming();
    }
  }

  function _handleStreamMessage(msg) {
    var type = msg.type;
    if (type === 'ready') { /* connection accepted */ }
    else if (type === 'listening') showToast('Listening...', 'info', 1000);
    else if (type === 'transcribing') showToast('Transcribing...', 'info', 1000);
    else if (type === 'transcribed') {
      showToast('You: "' + (msg.text || '').substring(0, 60) + '..."', 'info', 2000);
      if (window.KazmaChat && window.KazmaChat.onUserTranscription) {
        window.KazmaChat.onUserTranscription(msg.text);
      }
    }
    else if (type === 'token') {
      // Stream tokens into the chat (similar to SSE chat)
      if (window.KazmaChat && window.KazmaChat.onStreamToken) {
        window.KazmaChat.onStreamToken(msg.content);
      }
    }
    else if (type === 'tool_call') {
      showToast('Tool: ' + msg.name, 'info', 2000);
    }
    else if (type === 'tool_result') {
      /* tool completed */
    }
    else if (type === 'tts_chunk') {
      // Accumulate TTS audio chunks
      var bytes = _base64ToBytes(msg.data);
      ttsAudioChunks.push(bytes);
    }
    else if (type === 'tts_done') {
      _playTtsChunks();
    }
    else if (type === 'done') {
      if (window.KazmaChat && window.KazmaChat.onStreamDone) {
        window.KazmaChat.onStreamDone();
      }
    }
    else if (type === 'error') {
      showToast('Voice error: ' + (msg.content || ''), 'error');
    }
    else if (type === 'config_updated') {
      showToast('Voice config updated', 'info', 1500);
    }
  }

  function _playTtsChunks() {
    if (!ttsAudioChunks.length) return;
    var blob = new Blob(ttsAudioChunks, { type: 'audio/mpeg' });
    var url = URL.createObjectURL(blob);
    var audio = new Audio(url);
    audio.onended = function() { URL.revokeObjectURL(url); };
    audio.play();
    ttsAudioChunks = [];
  }

  function _cleanupStreaming() {
    if (audioProcessor) { try { audioProcessor.disconnect(); } catch (e) {} audioProcessor = null; }
    if (mediaStreamSource) { try { mediaStreamSource.disconnect(); } catch (e) {} mediaStreamSource = null; }
    if (audioContext) { try { audioContext.close(); } catch (e) {} audioContext = null; }
    if (micStream) { micStream.getTracks().forEach(function(t) { t.stop(); }); micStream = null; }
    ttsAudioChunks = [];
  }

  function updateStreamingUI(streaming) {
    var voiceBtn = document.getElementById('voice-btn');
    var liveBtn = document.getElementById('voice-live-btn');
    var micIcon = document.getElementById('mic-icon');
    if (streaming) {
      if (voiceBtn) {
        voiceBtn.style.opacity = '0.5';
        voiceBtn.style.pointerEvents = 'none';
      }
      if (liveBtn) {
        liveBtn.style.background = 'rgba(34,197,94,0.2)';
        liveBtn.style.color = '#22c55e';
        liveBtn.title = 'Stop Live Voice Stream';
      }
    } else {
      if (voiceBtn) {
        voiceBtn.style.opacity = '';
        voiceBtn.style.pointerEvents = '';
        voiceBtn.style.background = isRecording ? 'rgba(239,68,68,0.2)' : '';
      }
      if (liveBtn) {
        liveBtn.style.background = '';
        liveBtn.style.color = '';
        liveBtn.title = 'Start Live Voice Stream';
      }
    }
  }

  // Base64 encoder for bytes
  function _bytesToBase64(bytes) {
    var binary = '';
    for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  function _base64ToBytes(b64) {
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function toggleLiveVoice() {
    if (isStreaming) {
      stopStreaming();
      showToast('Live voice mode stopped', 'info');
    } else {
      startStreaming();
    }
  }

  // ── Public API ────────────────────────────────────────

  window.KazmaVoice = {
    startRecording: startRecording,
    stopRecording: stopRecording,
    playTTS: playTTS,
    handleVoiceCommand: handleVoiceCommand,
    getSttProvider: getSttProvider,
    getTtsProvider: getTtsProvider,
    isTtsEnabled: isTtsEnabled,
    startStreaming: startStreaming,
    stopStreaming: stopStreaming,
    toggleLiveVoice: toggleLiveVoice,
    isStreaming: function() { return isStreaming; }
  };

})();
