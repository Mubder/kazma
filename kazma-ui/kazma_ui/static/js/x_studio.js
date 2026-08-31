/* X Studio — compose / schedule / drafts. Mutating calls send X-Requested-With. */

function xStudioPage() {
  return {
    status: { can_post: false, handle: '', caps: {} },
    text: '',
    when: '',
    replyToId: '',
    proposalId: '',
    _draftText: '',
    preview: { chars: 0, max_chars: 280, allow: true, mentions: [], hashtags: [], cashtags: [], reason: '' },
    queue: [],
    drafts: [],
    audit: [],
    week: [],
    busy: false,
    _previewTimer: null,

    t(key) { return (window.t && window.t(key)) || key; },

    displayBody(raw) {
      const bidi = window.KazmaBidi;
      if (bidi && bidi.extractPostBody) return bidi.extractPostBody(raw) || '';
      return String(raw || '').replace(/\s+/g, ' ').trim();
    },
    displayKicker(raw) {
      const bidi = window.KazmaBidi;
      if (bidi && bidi.displayKicker) return bidi.displayKicker(raw) || '';
      return '';
    },
    textDir(raw) {
      const bidi = window.KazmaBidi;
      if (bidi && bidi.textDir) return bidi.textDir(raw);
      const s = String(raw || '');
      if (/[\u0600-\u06FF]/.test(s)) return 'rtl';
      if (!s.trim()) {
        return (document.documentElement.getAttribute('dir') || 'ltr').toLowerCase();
      }
      return 'ltr';
    },

    stampAr(el, raw) {
      if (!el) return;
      const dir = this.textDir(raw);
      const rtl = dir === 'rtl';
      el.setAttribute('dir', dir);
      el.classList.toggle('is-ar', rtl);
      if (rtl) {
        el.style.direction = 'rtl';
        el.style.textAlign = 'right';
        el.style.unicodeBidi = 'isolate';
        el.style.fontFamily = 'var(--font-arabic)';
      } else {
        el.style.direction = '';
        el.style.textAlign = '';
        el.style.unicodeBidi = '';
        el.style.fontFamily = '';
      }
    },

    async init() {
      this.when = this._defaultWhen();
      await Promise.all([this.loadStatus(), this.loadQueue(), this.loadDrafts(), this.loadAudit()]);
      this.onInput();
    },

    _defaultWhen() {
      const d = new Date(Date.now() + 60 * 60 * 1000);
      d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
      return d.toISOString().slice(0, 16);
    },

    _mutating(method, url, body) {
      return fetch(url, {
        method: method,
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: body ? JSON.stringify(body) : undefined,
      });
    },

    parseTweetId(raw) {
      const s = String(raw || '').trim();
      if (!s) return '';
      const m = s.match(/status(?:es)?\/(\d+)/i) || s.match(/^(\d+)$/);
      return m ? m[1] : s;
    },

    onInput() {
      if (this.proposalId && this._draftText && (this.text || '') !== this._draftText) {
        this.proposalId = '';
        this._draftText = '';
      }
      this.preview.chars = (this.text || '').trim().length;
      clearTimeout(this._previewTimer);
      this._previewTimer = setTimeout(() => this.refreshPreview(), 250);
    },

    async refreshPreview() {
      try {
        const resp = await fetch('/api/x/preview', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: this.text || '',
            reply_to_id: this.parseTweetId(this.replyToId),
          }),
        });
        const data = await resp.json().catch(() => ({}));
        if (data && data.ok) this.preview = data;
      } catch (_e) { /* keep last preview */ }
    },

    async loadStatus() {
      try {
        const resp = await fetch('/api/x/status', { credentials: 'same-origin' });
        const data = await resp.json();
        if (data) this.status = data;
      } catch (_e) { this.status = { can_post: false, handle: '', caps: {} }; }
    },

    async loadQueue() {
      try {
        const resp = await fetch('/api/scheduled/tasks', { credentials: 'same-origin' });
        const data = await resp.json();
        const tasks = (data && data.tasks) || [];
        this.queue = tasks.filter(function (t) {
          return t.source === 'x' && (t.status === 'pending' || t.status === 'running');
        }).map((t) => Object.assign({}, t, { editWhen: this.toLocalInput(t.when) }));
        this._buildWeek();
      } catch (_e) { this.queue = []; }
    },

    async loadDrafts() {
      try {
        const resp = await fetch('/api/x/drafts', { credentials: 'same-origin' });
        const data = await resp.json();
        this.drafts = (data && data.drafts) || [];
      } catch (_e) { this.drafts = []; }
    },

    async loadAudit() {
      try {
        const resp = await fetch('/api/x/audit?limit=20', { credentials: 'same-origin' });
        const data = await resp.json();
        this.audit = (data && data.entries) || [];
      } catch (_e) { this.audit = []; }
    },

    _localKey(d) {
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return y + '-' + m + '-' + day;
    },

    _buildWeek() {
      const days = [];
      const now = new Date();
      now.setHours(0, 0, 0, 0);
      const locale = document.documentElement.lang || 'en';
      const counts = {};
      this.queue.forEach((item) => {
        const parsed = item.when ? new Date(item.when) : null;
        if (!parsed || isNaN(parsed.getTime())) return;
        const key = this._localKey(parsed);
        counts[key] = (counts[key] || 0) + 1;
      });
      for (let i = 0; i < 7; i++) {
        const d = new Date(now.getTime() + i * 86400000);
        const key = this._localKey(d);
        days.push({
          key: key,
          label: d.toLocaleDateString(locale, { weekday: 'short' }),
          count: counts[key] || 0,
        });
      }
      this.week = days;
    },

    toLocalInput(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      const shifted = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
      return shifted.toISOString().slice(0, 16);
    },

    fmtWhen(iso) {
      if (!iso) return '';
      try {
        return new Date(iso).toLocaleString(document.documentElement.lang || 'en', {
          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        });
      } catch (_e) { return iso; }
    },

    auditText(entry) {
      if (!entry) return '';
      return entry.text || entry.error_detail || entry.action || '';
    },

    useDraft(d) {
      this.text = (d && d.text) || '';
      this.proposalId = (d && (d.id || d.proposal_id)) || '';
      this._draftText = this.text;
      this.onInput();
    },

    clearThread() {
      this.replyToId = '';
    },

    replyTo(entry) {
      this.replyToId = this.parseTweetId((entry && entry.tweet_id) || '');
    },

    _payload() {
      const body = {
        text: (this.text || '').trim(),
        reply_to_id: this.parseTweetId(this.replyToId),
      };
      if (this.proposalId) body.proposal_id = this.proposalId;
      return body;
    },

    _resetComposer(nextReplyId) {
      this.text = '';
      this.proposalId = '';
      this._draftText = '';
      if (nextReplyId) this.replyToId = String(nextReplyId);
      this.onInput();
    },

    async postNow() {
      const body = (this.text || '').trim();
      if (!body) {
        window.showToast(this.t('x_studio.text_required'), 'error');
        return;
      }
      const ok = await window.kazmaConfirm({
        title: this.t('x_studio.post_now'),
        message: this.t('x_studio.confirm_post') + '\n\n' + body,
        confirmText: this.t('x_studio.post_now'),
      });
      if (!ok) return;
      this.busy = true;
      try {
        const resp = await this._mutating('POST', '/api/x/post', this._payload());
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          window.showToast(this.t('x_studio.posted'), 'success');
          this._resetComposer(data.tweet_id || '');
          await Promise.all([this.loadStatus(), this.loadAudit(), this.loadDrafts()]);
        } else {
          window.showToast(data.error || data.reason || this.t('x_studio.post_failed'), 'error');
        }
      } catch (e) {
        window.showToast(this.t('x_studio.post_failed') + ': ' + e.message, 'error');
      } finally {
        this.busy = false;
      }
    },

    async schedule() {
      const body = (this.text || '').trim();
      if (!body) {
        window.showToast(this.t('x_studio.text_required'), 'error');
        return;
      }
      if (!(this.when || '').trim()) {
        window.showToast(this.t('x_studio.timing_required'), 'error');
        return;
      }
      this.busy = true;
      try {
        const whenIso = new Date(this.when).toISOString();
        const payload = this._payload();
        payload.when = whenIso;
        const resp = await this._mutating('POST', '/api/scheduled/x', payload);
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok !== false) {
          window.showToast(this.t('x_studio.scheduled_ok'), 'success');
          this._resetComposer(this.parseTweetId(this.replyToId));
          await Promise.all([this.loadQueue(), this.loadDrafts()]);
        } else {
          window.showToast(data.error || this.t('x_studio.schedule_failed'), 'error');
        }
      } catch (e) {
        window.showToast(this.t('x_studio.schedule_failed') + ': ' + e.message, 'error');
      } finally {
        this.busy = false;
      }
    },

    async reschedule(item) {
      if (!(item.editWhen || '').trim()) {
        window.showToast(this.t('x_studio.timing_required'), 'error');
        return;
      }
      try {
        const whenIso = new Date(item.editWhen).toISOString();
        const resp = await this._mutating('PUT', '/api/scheduled/x/' + item.id, { when: whenIso });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          window.showToast(this.t('x_studio.rescheduled'), 'success');
          await this.loadQueue();
        } else {
          window.showToast(data.error || this.t('x_studio.reschedule_failed'), 'error');
        }
      } catch (e) {
        window.showToast(this.t('x_studio.reschedule_failed') + ': ' + e.message, 'error');
      }
    },

    canDeletePosted(entry) {
      if (!entry || !entry.tweet_id) return false;
      const action = String(entry.action || '');
      if (action === 'delete') return false;
      return action === 'post' || action === 'reply' || !action;
    },

    async deletePosted(entry) {
      const tid = this.parseTweetId((entry && entry.tweet_id) || '');
      if (!tid) return;
      const ok = await window.kazmaConfirm({
        title: this.t('x_studio.delete_post'),
        message: this.t('x_studio.confirm_delete') + '\n\n' + (this.auditText(entry) || tid),
        confirmText: this.t('x_studio.delete_post'),
        danger: true,
      });
      if (!ok) return;
      try {
        const resp = await this._mutating('POST', '/api/x/delete', { tweet_id: tid });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          window.showToast(this.t('x_studio.deleted'), 'success');
          if (this.replyToId === tid) this.replyToId = '';
          await this.loadAudit();
        } else {
          window.showToast(data.error || this.t('x_studio.delete_failed'), 'error');
        }
      } catch (e) {
        window.showToast(this.t('x_studio.delete_failed') + ': ' + e.message, 'error');
      }
    },

    async cancel(item) {
      const ok = await window.kazmaConfirm({
        title: this.t('x_studio.cancel'),
        message: item.summary || '',
        confirmText: this.t('x_studio.cancel'),
        danger: true,
      });
      if (!ok) return;
      try {
        const resp = await this._mutating('DELETE', '/api/scheduled/x/' + item.id);
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          await this.loadQueue();
        } else {
          window.showToast(data.error || this.t('x_studio.cancel_failed'), 'error');
        }
      } catch (e) {
        window.showToast(this.t('x_studio.cancel_failed') + ': ' + e.message, 'error');
      }
    },
  };
}

window.xStudioPage = xStudioPage;
