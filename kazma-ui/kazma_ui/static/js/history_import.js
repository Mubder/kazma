/* Bring your history (REMAINING 4.2): the Memory page's card that imports a
 * ChatGPT or Claude export into memory (kazma_ui/history_import_api.py).
 * Read shows what the export holds; Import runs it in the background and
 * this card follows it; Stop ends it between turns (what was written stays).
 */
(function () {
  'use strict';

  function tr(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  function day(epoch) {
    if (!epoch) return '?';
    return window.KazmaFormat ? window.KazmaFormat.date(Number(epoch) * 1000) : new Date(epoch * 1000).toISOString().slice(0, 10);
  }

  window.historyImport = function historyImport() {
    return {
      file: null,
      busy: '',
      token: '',
      summary: null,
      running: false,
      done: 0,
      total: 0,
      result: '',
      error: '',
      timer: null,

      t: tr,

      init() {
        var self = this;
        this.poll();
        if (typeof window.kazmaAddLeaveHook === 'function') {
          window.kazmaAddLeaveHook(function () { if (self.timer) clearTimeout(self.timer); self.timer = null; });
        }
      },

      choose(event) {
        this.file = (event.target.files || [])[0] || null;
        this.summary = null;
        this.token = '';
        this.result = '';
        this.error = '';
      },

      async read() {
        if (!this.file) return;
        this.busy = 'read';
        this.error = '';
        this.result = '';
        try {
          var res = await fetch('/api/import/history/preview', {
            method: 'POST', credentials: 'same-origin', body: this.file,
            headers: { 'Content-Type': 'application/octet-stream' },
          });
          var data = await res.json().catch(function () { return {}; });
          if (!res.ok || !data.ok) {
            this.error = tr('memory.import.not_read', 'The file could not be read: {why}', { why: data.error || res.status });
            return;
          }
          this.token = data.token;
          this.summary = data.summary;
        } catch (_) {
          this.error = tr('memory.import.failed', 'Kazma did not answer. Try again.');
        } finally {
          this.busy = '';
        }
      },

      summaryText() {
        var s = this.summary;
        if (!s) return '';
        var name = s.source === 'claude' ? 'Claude' : 'ChatGPT';
        return tr('memory.import.holds', 'A {name} export: {chats}, {turns}, from {first} to {last}.', {
          name: name, chats: window.kazmaCount('memory.import.chats_n', s.chats || 0),
          turns: window.kazmaCount('memory.import.turns_n', s.turns || 0), first: day(s.first), last: day(s.last),
        });
      },

      async start() {
        if (!this.token) return;
        this.busy = 'start';
        this.error = '';
        try {
          await window.kazmaSave('/api/import/history/start', { method: 'POST', body: { token: this.token } });
          this.token = '';
          this.running = true;
          this.poll();
        } catch (e) {
          this.error = tr('memory.import.not_started', 'The import did not start: {why}', { why: (e && e.message) || '' });
        } finally {
          this.busy = '';
        }
      },

      async stop() {
        try { await window.kazmaSave('/api/import/history/stop', { method: 'POST', body: {} }); } catch (_) { /* the next poll says */ }
      },

      percent() {
        return this.total ? Math.min(100, Math.round(100 * this.done / this.total)) : 0;
      },

      progressText() {
        return tr('memory.import.progress', 'Turns: {done} / {total}', { done: this.done, total: this.total });
      },

      async poll() {
        var self = this;
        var data = window.kazmaGetJson ? await window.kazmaGetJson('/api/import/history/status') : null;
        if (data) {
          this.done = data.done || 0;
          this.total = data.total || 0;
          this.running = data.state === 'running';
          var r = data.report;
          if (data.state === 'done' || data.state === 'stopped') {
            this.result = tr(data.state === 'done' ? 'memory.import.done' : 'memory.import.stopped',
              'Remembered: {written} · Already there: {already} · Kept out: {kept} · Failed: {failed}.', {
                written: (r && r.written) || 0, already: (r && r.already) || 0,
                kept: (r && r.refused) || 0, failed: (r && r.failed) || 0,
              });
          } else if (data.state === 'failed') {
            this.error = tr('memory.import.job_failed', 'The import stopped with an error ({why}).', { why: data.error || '' });
          }
        }
        if (this.running && document.getElementById('history-import')) {
          this.timer = setTimeout(function () { self.poll(); }, 1500);
        }
      },
    };
  };
})();
