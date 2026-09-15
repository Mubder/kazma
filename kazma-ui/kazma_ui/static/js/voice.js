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
  // Hold-to-record: a click (mousedown+mouseup in <700ms) used to send a
  // tiny WebM clip; Whisper invents a word ("you", "Thank you"). Cancel
  // in-flight getUserMedia if the button is released before recording
  // actually starts, and drop clips shorter than the hold floor.
  var _micGen = 0;
  var _micWanted = false;
  var _micStartedAt = 0;
  var _MIN_HOLD_MS = 700;
  var _MIN_BLOB_BYTES = 1500;

  // Config (persisted in localStorage)
  var STT_PROVIDER_KEY = 'kazma.sttProvider';
  var TTS_PROVIDER_KEY = 'kazma.ttsProvider';
  var TTS_ENABLED_KEY = 'kazma.ttsEnabled';

  async function syncVoiceSettingsWithBackend() {
    try {
      var resp = await fetch('/api/settings/voice');
      if (resp.ok) {
        var settings = await resp.json();
        if (settings) {
          if (settings.stt_provider) {
            localStorage.setItem(STT_PROVIDER_KEY, settings.stt_provider);
          }
          if (settings.tts_provider) {
            localStorage.setItem(TTS_PROVIDER_KEY, settings.tts_provider);
          }
          if (settings.enabled !== undefined) {
            localStorage.setItem(TTS_ENABLED_KEY, settings.enabled ? 'true' : 'false');
          }
        }
      }
    } catch (e) {
      console.warn('[Voice] Failed to sync voice settings with backend:', e);
    }
  }

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
    if (isRecording || _micWanted) return;
    if (isStreaming) {
      showToast('Please stop Live Voice Mode first', 'warning');
      return;
    }
    _micWanted = true;
    var gen = ++_micGen;
    try {
      var mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!_micWanted || gen !== _micGen) {
        mic.getTracks().forEach(function(t) { t.stop(); });
        return;
      }
      stream = mic;
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
        var held = this._heldMs || 0;
        var blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
        cleanup();
        if (held < _MIN_HOLD_MS || blob.size < _MIN_BLOB_BYTES) {
          showToast('Hold the mic to record', 'info', 2000);
          return;
        }
        await sendForTranscription(blob);
      };

      if (!_micWanted || gen !== _micGen) {
        cleanup();
        return;
      }
      mediaRecorder.start(100); // collect in 100ms chunks
      _micStartedAt = Date.now();
      isRecording = true;
      updateUI(true);
    } catch (err) {
      _micWanted = false;
      console.error('[Voice] Microphone access denied:', err);
      showToast('Microphone access denied. Please allow microphone access.', 'error');
    }
  }

  function stopRecording() {
    _micWanted = false;
    if (!isRecording || !mediaRecorder) {
      _micGen += 1; // cancel an in-flight getUserMedia start
      return;
    }
    mediaRecorder._heldMs = Date.now() - _micStartedAt;
    try { mediaRecorder.stop(); } catch (e) {}
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
      showToast('Transcribing...', 'info', 2000);
      var resp = await fetch('/api/voice/stt', { method: 'POST', body: formData });
      if (!resp.ok) {
        var err = await resp.json().catch(function() { return { detail: 'STT failed' }; });
        showToast('Transcription failed: ' + (err.detail || resp.statusText), 'error');
        return;
      }
      var data = await resp.json();
      var said = data && data.text ? String(data.text).trim() : '';
      if (!said || data.ignored) {
        showToast('No speech detected', 'info', 2000);
        return;
      }
      // Insert transcribed text into the chat input
      var inputEl = document.getElementById('chat-input');
      if (inputEl) {
        var current = inputEl.value.trim();
        inputEl.value = current ? current + ' ' + said : said;
        inputEl.dispatchEvent(new Event('input'));
        inputEl.focus();
      }
      showToast('Transcribed: "' + said.substring(0, 60) + '..."', 'success', 3000);
    } catch (err) {
      console.error('[Voice] STT request failed:', err);
      showToast('Transcription request failed', 'error');
    }
  }

  // ── TTS Playback ──────────────────────────────────────

  // Latch survives a page reload (the mid-turn "flicker" refresh resets a
  // plain in-memory flag, which made the yellow "TTS unavailable" toast fire
  // on EVERY response for servers without edge-tts). sessionStorage lasts the
  // tab session; `/voice on` clears it for an explicit retry.
  var _ttsUnavailable = false;
  try { _ttsUnavailable = sessionStorage.getItem('kazma_tts_unavailable') === '1'; } catch (e) {}

  async function playTTS(text, provider) {
    if (!isTtsEnabled() || _ttsUnavailable) return;
    provider = provider || getTtsProvider();

    try {
      var formData = new FormData();
      formData.append('text', text);
      formData.append('provider', provider);
      formData.append('voice', 'default');
      formData.append('output_format', 'mp3');

      var resp = await fetch('/api/voice/tts', { method: 'POST', body: formData });
      if (!resp.ok) {
        // 503 = TTS not configured/usable on the server (misconfig hint).
        // Latch it off for the session instead of re-probing on every
        // reply; `/voice on` clears the latch for an explicit retry.
        if (resp.status === 503) {
          _ttsUnavailable = true;
          try { sessionStorage.setItem('kazma_tts_unavailable', '1'); } catch (e1) {}
          try {
            showToast('Voice output unavailable (TTS not configured) — silenced until /voice on.', 'info', 4500);
          } catch (e0) { /* toast system absent */ }
        }
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
        _ttsUnavailable = false;  // explicit opt-in — re-probe the server
        try { sessionStorage.removeItem('kazma_tts_unavailable'); } catch (e1) {}
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

  function initVoiceButton() {
    // Sync settings from backend
    syncVoiceSettingsWithBackend();

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
  //
  // Turn Delivery V2: the journal is the source of truth for the ASSISTANT
  // turn and the chat UI is its projection. This socket authors the USER
  // row on `transcribed` (like Send does for typed text) and otherwise
  // carries status + audio only — it never paints assistant tokens.
  // ──────────────────────────────────────────────────────

  var ws = null;
  var audioContext = null;
  var mediaStreamSource = null;
  var audioProcessor = null;
  var muteGain = null;
  var micStream = null;
  var isStreaming = false;
  var ttsQueue = [];      // pending sentence clips (each a complete MP3)
  var ttsPlaying = false; // a clip is currently playing
  var ttsPlayer = null;
  var lkRoom = null;
  var bargeFrames = 0;

  async function startStreaming() {
    if (isStreaming) return;
    try {
      // Open WebSocket
      var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      // WebSocket — browser sends cookies automatically, so the
      // kazma-session cookie (set at login) will be used by the server
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
        _maybeJoinLiveKit(sessionId);
        isStreaming = true;
        showToast('Live voice mode active — speak; you can interrupt', 'success', 3000);
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

  function _downsampleTo16k(float32, inRate) {
    // Chrome/Windows often ignores {sampleRate: 16000} and gives 44100/48000.
    // Sending that PCM labeled as 16 kHz made Whisper fail or hallucinate.
    var outRate = 16000;
    if (!inRate || Math.abs(inRate - outRate) < 50) return float32;
    var ratio = inRate / outRate;
    var outLen = Math.floor(float32.length / ratio);
    if (outLen < 1) return new Float32Array(0);
    var out = new Float32Array(outLen);
    for (var i = 0; i < outLen; i++) {
      var src = i * ratio;
      var i0 = Math.floor(src);
      var i1 = Math.min(i0 + 1, float32.length - 1);
      var f = src - i0;
      out[i] = float32[i0] * (1 - f) + float32[i1] * f;
    }
    return out;
  }

  async function _captureAudioForStreaming() {
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
          // AGC pumps room tone into the VAD as "speech".
          autoGainControl: false
        }
      });

      audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      if (audioContext.state === 'suspended') {
        try { await audioContext.resume(); } catch (eR) { /* autoplay policy */ }
      }
      mediaStreamSource = audioContext.createMediaStreamSource(micStream);

      // Use ScriptProcessorNode for simplicity (AudioWorklet is more modern
      // but requires a separate module file). 4096 sample buffer.
      audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);
      audioProcessor.onaudioprocess = function(e) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        var native = e.inputBuffer.getChannelData(0);
        var input = _downsampleTo16k(native, audioContext.sampleRate);

        if (ttsPlayer && !ttsPlayer.paused) {
          var sum = 0;
          for (var bi = 0; bi < input.length; bi++) sum += input[bi] * input[bi];
          var rms = Math.sqrt(sum / Math.max(1, input.length));
          if (rms > 0.045) bargeFrames += 1;
          else bargeFrames = 0;
          if (bargeFrames >= 3) {
            bargeFrames = 0;
            _bargeIn();
          }
        } else {
          bargeFrames = 0;
        }

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
      // Must be in the graph to fire, but must NOT play the mic (that
      // was feeding the VAD its own output as "speech").
      muteGain = audioContext.createGain();
      muteGain.gain.value = 0;
      audioProcessor.connect(muteGain);
      muteGain.connect(audioContext.destination);

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
      // This socket authors the USER row (like Send); the journal authors
      // the assistant. Without the user row, the next turn's tokens latch
      // onto the previous assistant bubble.
      var said = String(msg.text || '').trim();
      if (said && window.KazmaChat && typeof window.KazmaChat.beginVoiceTurn === 'function') {
        window.KazmaChat.beginVoiceTurn(said);
      }
    }
    else if (type === 'tool_call') {
      showToast('Tool: ' + msg.name, 'info', 2000);
    }
    else if (type === 'tool_result') {
      /* tool completed */
    }
    else if (type === 'hitl_paused') {
      showToast('Approval needed — answer the card in chat to continue', 'info', 5000);
    }
    else if (type === 'tts_chunk') {
      // One COMPLETE sentence clip (valid MP3) — play as it arrives.
      _enqueueTtsClip(_base64ToBytes(msg.data));
    }
    else if (type === 'tts_done') {
      /* queue drains itself clip by clip */
    }
    else if (type === 'interrupted') {
      _clearTts();
    }
    else if (type === 'done') {
      /* turn finished server-side; the journal projection closes the bubble */
    }
    else if (type === 'error') {
      showToast('Voice error: ' + (msg.content || ''), 'error');
    }
    else if (type === 'config_updated') {
      showToast('Voice config updated', 'info', 1500);
    }
  }

  function _enqueueTtsClip(bytes) {
    if (!bytes || !bytes.length) return;
    ttsQueue.push(bytes);
    _drainTtsQueue();
  }

  function _drainTtsQueue() {
    if (ttsPlaying || !ttsQueue.length) return;
    var bytes = ttsQueue.shift();
    var blob = new Blob([bytes], { type: 'audio/mpeg' });
    var url = URL.createObjectURL(blob);
    var audio = new Audio(url);
    ttsPlayer = audio;
    ttsPlaying = true;
    audio.onended = function() {
      URL.revokeObjectURL(url);
      ttsPlaying = false;
      if (ttsPlayer === audio) ttsPlayer = null;
      _drainTtsQueue();
    };
    audio.play().catch(function() {
      ttsPlaying = false;
      if (ttsPlayer === audio) ttsPlayer = null;
    });
    _publishTtsToLiveKit(blob);
  }

  function _clearTts() {
    ttsQueue = [];
    ttsPlaying = false;
    if (ttsPlayer) { try { ttsPlayer.pause(); } catch (e) {} ttsPlayer = null; }
  }

  async function _publishTtsToLiveKit(blob) {
    if (!lkRoom || !blob) return;
    try {
      var LK = window.LivekitClient || window.livekit;
      if (!LK) return;
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      var buf = await blob.arrayBuffer();
      var audioBuf = await ctx.decodeAudioData(buf.slice(0));
      var dest = ctx.createMediaStreamDestination();
      var src = ctx.createBufferSource();
      src.buffer = audioBuf;
      src.connect(dest);
      src.start();
      var mediaTrack = dest.stream.getAudioTracks()[0];
      if (!mediaTrack) { ctx.close(); return; }
      var track = new LK.LocalAudioTrack(mediaTrack);
      await lkRoom.localParticipant.publishTrack(track);
      src.onended = function() {
        try { lkRoom.localParticipant.unpublishTrack(track); } catch (e) {}
        try { ctx.close(); } catch (e) {}
      };
    } catch (err) {
      console.warn('[Voice] TTS room publish skip:', err);
    }
  }

  function _bargeIn() {
    _clearTts();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'interrupt' }));
    }
  }

  function _loadLiveKit() {
    return new Promise(function(resolve, reject) {
      if (window.LivekitClient) { resolve(window.LivekitClient); return; }
      var s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.umd.js';
      s.onload = function() { resolve(window.LivekitClient || window.livekit); };
      s.onerror = function() { reject(new Error('livekit-client CDN failed')); };
      document.head.appendChild(s);
    });
  }

  async function _maybeJoinLiveKit(sessionId) {
    try {
      var st = await fetch('/api/voice/livekit/status');
      if (!st.ok) return;
      var info = await st.json();
      if (!info || !info.enabled) return;
      var tokResp = await fetch('/api/voice/livekit/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId || 'web', identity: 'web-user' })
      });
      if (!tokResp.ok) return;
      var data = await tokResp.json();
      var LK = await _loadLiveKit();
      if (!LK || !LK.Room) return;
      lkRoom = new LK.Room();
      await lkRoom.connect(data.url, data.token);
      await lkRoom.localParticipant.setMicrophoneEnabled(true);
      showToast('Duplex: LiveKit WebRTC (brain is still Kazma)', 'success', 2500);
    } catch (err) {
      console.warn('[Voice] LiveKit optional skip:', err);
    }
  }

  function _cleanupStreaming() {
    if (audioProcessor) { try { audioProcessor.disconnect(); } catch (e) {} audioProcessor = null; }
    if (muteGain) { try { muteGain.disconnect(); } catch (e) {} muteGain = null; }
    if (mediaStreamSource) { try { mediaStreamSource.disconnect(); } catch (e) {} mediaStreamSource = null; }
    if (audioContext) { try { audioContext.close(); } catch (e) {} audioContext = null; }
    if (micStream) { micStream.getTracks().forEach(function(t) { t.stop(); }); micStream = null; }
    if (ttsPlayer) { try { ttsPlayer.pause(); } catch (e) {} ttsPlayer = null; }
    if (lkRoom) {
      try { lkRoom.disconnect(); } catch (e) {}
      lkRoom = null;
    }
    ttsQueue = [];
    ttsPlaying = false;
    bargeFrames = 0;
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
