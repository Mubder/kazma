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
    showDismissed: false,
    draftBusy: '',
    audit: [],
    week: [],
    busy: false,
    _previewTimer: null,

    // Two tabs: the studio (what Kazma posted) and conversations (what it
    // was replying to). A reply read without its parent is a non-sequitur,
    // which is why the posted list alone could never answer "why did it
    // say that".
    tab: 'studio',
    conversations: [],
    convLoading: false,
    convBusy: '',
    // The poller now makes two read calls every cycle, so reads drown the
    // posts in a card titled "Posted". Default to the writes.
    auditWritesOnly: true,

    get auditRows() {
      if (!this.auditWritesOnly) return this.audit;
      const writes = ['post', 'reply', 'delete'];
      return (this.audit || []).filter(function (e) {
        return writes.indexOf((e && e.action) || '') !== -1;
      });
    },

    t(key) { return (window.t && window.t(key)) || key; },

    async loadConversations(opts) {
      const poll = !!(opts && opts.poll);
      this.convLoading = true;
      try {
        if (poll) {
          const resp = await this._mutating('POST', '/api/x/reply/poll', {});
          const pdata = await resp.json().catch(function () { return {}; });
          if (!resp.ok || pdata.ok === false) {
            window.showToast(pdata.error || 'Could not poll X', 'error');
          } else if (pdata.message) {
            window.showToast(pdata.message, 'success');
          }
        }
        const r = await fetch('/api/x/reply/conversations?limit=30', {
          credentials: 'same-origin',
        });
        const d = await r.json().catch(function () { return {}; });
        this.conversations = (d && d.rows) || [];
      } catch (e) {
        if (poll) window.showToast(String(e.message || e), 'error');
        this.conversations = this.conversations || [];
      } finally {
        this.convLoading = false;
      }
    },

    async convAction(kind, row) {
      const id = row && row.summon_id;
      if (!id || this.convBusy) return;
      if (kind === 'approve' || kind === 'deny' || kind === 'delete') {
        const posted = kind === 'delete' && row.status === 'posted' && row.tweet_id;
        const ok = await window.kazmaConfirm({
          title: kind === 'approve' ? 'Post this reply?'
            : (kind === 'delete'
              ? (posted ? 'Delete this reply on X?' : 'Remove from the log?')
              : 'Discard this draft?'),
          message: posted
            ? ((row.reply || '') + '\n\nThis removes the tweet from X.')
            : (row.reply || row.reason || id),
          confirmText: kind === 'approve' ? 'Approve' : (kind === 'delete' ? 'Delete' : 'Deny'),
          danger: kind !== 'approve',
        });
        if (!ok) return;
      }
      this.convBusy = id;
      try {
        const resp = await this._mutating('POST', '/api/x/reply/' + kind, { summon_id: id });
        const data = await resp.json().catch(function () { return {}; });
        if (resp.ok && data.ok !== false) {
          const msg = kind === 'approve' && data.url
            ? ('Posted: ' + data.url)
            : (kind === 'delete' ? (data.reason || 'Deleted.')
              : (kind === 'deny' ? 'Denied.' : (data.action === 'awaiting_approval' ? 'Redrafted — approve to post.' : (data.reason || 'Done.'))));
          window.showToast(msg, data.action === 'failed' ? 'error' : 'success');
        } else {
          window.showToast(data.error || data.reason || 'Request failed', 'error');
        }
        await this.loadConversations();
      } catch (e) {
        window.showToast(String(e.message || e), 'error');
      } finally {
        this.convBusy = '';
      }
    },

    convWhen(epoch) {
      if (!epoch) return '';
      try {
        return new Date(Number(epoch) * 1000).toLocaleString();
      } catch (e) {
        return '';
      }
    },

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
        const url = '/api/x/drafts' + (this.showDismissed ? '?dismissed=true' : '');
        const resp = await fetch(url, { credentials: 'same-origin' });
        const data = await resp.json();
        this.drafts = (data && data.drafts) || [];
      } catch (_e) { this.drafts = []; }
    },

    // Dismiss retires an unused draft (it stops being offered here and to
    // the agent's list_proposals); Restore brings a dismissed one back. The
    // server never touches a posted or scheduled draft, so "changed: 0" is
    // reported as-is rather than as success.
    async setDraftDismissed(d, dismissed) {
      const id = d && d.id;
      if (!id || this.draftBusy) return;
      this.draftBusy = id;
      try {
        const resp = await this._mutating('POST', '/api/x/drafts/discard', { id: id, restore: !dismissed });
        const data = await resp.json().catch(function () { return {}; });
        if (resp.ok && data.ok !== false && data.changed) {
          window.showToast(this.t(dismissed ? 'x_studio.draft_dismissed' : 'x_studio.draft_restored'), 'success');
          if (dismissed && this.proposalId === id) {
            // The composer must not stay bound to a draft that can no longer be published.
            this.proposalId = '';
            this._draftText = '';
          }
        } else {
          window.showToast(data.error || this.t('x_studio.draft_unchanged'), 'error');
        }
      } catch (e) {
        window.showToast(String(e.message || e), 'error');
      } finally {
        this.draftBusy = '';
      }
      await this.loadDrafts();
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
      // Falling back to the ACTION NAME was why the list read
      // "read_mentions / read_mentions / verify_credentials" with the same
      // word repeated on the line below it. A row with no text is a call,
      // not a post; say what it did instead of echoing its own label.
      if (entry.text) return entry.text;
      if (entry.error_detail) return entry.error_detail;
      return this.auditLabel(entry);
    },

    auditLabel(entry) {
      const a = (entry && entry.action) || '';
      const map = {
        read_mentions: 'checked mentions',
        read_tweet: 'fetched a post',
        verify_credentials: 'checked the connection',
        tier_probe: 'probed the API tier',
        post: 'posted',
        reply: 'replied',
        delete: 'deleted a post',
      };
      return map[a] || a.replace(/_/g, ' ');
    },

    // The line under each row said `action + tweet_id` and nothing else --
    // no time, no status, on a field literally called "when". The audit
    // store has had ts, status, http_status and duration_ms all along.
    auditWhen(entry) {
      if (!entry) return '';
      const bits = [];
      if (entry.ts) {
        try {
          bits.push(new Date(entry.ts).toLocaleString());
        } catch (_e) { bits.push(String(entry.ts)); }
      }
      if (entry.status && entry.status !== 'success') {
        bits.push(String(entry.status).toUpperCase());
      }
      if (entry.http_status && Number(entry.http_status) >= 400) {
        bits.push('HTTP ' + entry.http_status);
      }
      if (entry.duration_ms) bits.push(Math.round(entry.duration_ms) + 'ms');
      if (entry.tweet_id) bits.push(entry.tweet_id);
      return bits.join(' · ');
    },

    auditFailed(entry) {
      if (!entry) return false;
      return (entry.status && entry.status !== 'success')
        || (entry.http_status && Number(entry.http_status) >= 400);
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
