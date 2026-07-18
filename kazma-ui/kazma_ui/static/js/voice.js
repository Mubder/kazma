/* ═══════════════════════════════════════════════════════
   Kazma Voice — Browser voice recording + STT/TTS

   Features:
   - Hold-to-record microphone button
   - STT via backend providers (OpenAI, Groq, Cohere, NVIDIA)
   - TTS playback of assistant responses
   - Voice provider switching via /voice commands
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
    if (lower === '/voice' || lower === '/voice status') {
      showToast(
        'STT: ' + getSttProvider() + ' | TTS: ' + getTtsProvider() + ' | Voice replies: ' + (isTtsEnabled() ? 'ON' : 'OFF'),
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
    }
  }

  // ── Init on DOM ready ─────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVoiceButton);
  } else {
    initVoiceButton();
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
  };

})();
