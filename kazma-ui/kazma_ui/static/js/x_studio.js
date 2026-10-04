/* X Studio — compose / schedule / drafts. Mutating calls send X-Requested-With. */

function xStudioPage() {
  return {
    status: { can_post: false, handle: '', caps: {} },
    text: '',
    when: '',
    replyToId: '',
    proposalId: '',
    _draftText: '',
    _intentKey: '',
    _intentSignature: '',
    preview: { chars: 0, max_chars: 280, allow: true, mentions: [], hashtags: [], cashtags: [], reason: '' },
    queue: [],
    queueCount: 0,
    queueNext: '',
    queueBusy: '',
    includeFinished: false,
    drafts: [],
    draftsNext: '',
    draftQuery: '',
    showDismissed: false,
    draftBusy: '',
    audit: [],
    week: [],
    busy: false,
    draftBrief: '',
    draftCount: 1,
    generateBusy: false,
    generateError: '',
    generatedModels: [],
    qualification: null,
    qualificationBusy: false,
    qualificationError: '',
    operations: [],
    operationsNext: '',
    operationQuery: '',
    selectedOperation: null,
    operationError: '',
    deleteBusy: '',

    _previewTimer: null,
    _composerTimer: null,
    _composerLoaded: false,
    _composerSaving: false,
    _composerSaved: '',
    _composerRevision: 0,
    composerError: '',
    composerSavedAt: null,
    _unloadHandler: null,
    _previewSequence: 0,
    _loadSequence: {},
    loadStates: {},

    // Two tabs: the studio (what Kazma posted) and conversations (what it
    // was replying to). A reply read without its parent is a non-sequitur,
    // which is why the posted list alone could never answer "why did it
    // say that".
    tab: 'studio',
    conversations: [],
    conversationsNext: '',
    conversationQuery: '',
    conversationState: '',
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

    reloadSection(section) {
      const actions = { status: 'loadStatus', queue: 'loadQueue', drafts: 'loadDrafts', audit: 'loadAudit', conversations: 'loadConversations', preview: 'refreshPreview', operations: 'loadOperations' };
      if (actions[section]) return this[actions[section]]();
    },

    async loadConversations(opts) {
      const poll = !!(opts && opts.poll);
      this.convLoading = true;
      try {
        if (poll) {
          const resp = await this._mutating('POST', '/api/x/reply/poll', {});
          const pdata = await resp.json().catch(function () { return {}; });
          if (!resp.ok || pdata.ok === false) {
            window.showToast(pdata.error || this.t('x_studio.poll_failed'), 'error');
          } else if (pdata.message) {
            window.showToast(pdata.message, 'success');
          }
        }
        const more = !!(opts && opts.more);
        const url = '/api/x/reply/conversations?limit=30&query=' + encodeURIComponent(this.conversationQuery) + '&state=' + encodeURIComponent(this.conversationState) + (more && this.conversationsNext ? '&cursor=' + encodeURIComponent(this.conversationsNext) : '');
        const data = await this._readSection('conversations', url, 'rows', null, more);
        if (data) this.conversationsNext = data.next_cursor || '';
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
      this.convBusy = id;
      const revision = row.approval_token || '';
      try {
      if (kind === 'approve' || kind === 'deny' || kind === 'delete') {
        const posted = kind === 'delete' && row.status === 'posted' && row.tweet_id;
        const ok = await window.kazmaConfirm({
          title: kind === 'approve' ? this.t('x_studio.confirm_post_reply')
            : (kind === 'delete'
              ? (posted ? this.t('x_studio.confirm_delete_on_x') : this.t('x_studio.confirm_remove_log'))
              : this.t('x_studio.confirm_discard')),
          message: posted
            ? ((row.reply || '') + '\n\n' + this.t('x_studio.removes_tweet'))
            : (row.reply || row.reason || id),
          confirmText: kind === 'approve' ? this.t('common.approve') : (kind === 'delete' ? this.t('common.delete') : this.t('common.deny')),
          danger: kind !== 'approve',
        });
        if (!ok) return;
      }
        const resp = await this._mutating('POST', '/api/x/reply/' + kind,
          { summon_id: id, approval_token: revision });
        const data = await resp.json().catch(function () { return {}; });
        if (resp.ok && data.ok !== false) {
          const msg = kind === 'approve' && data.url
            ? this.t('x_studio.posted_url').replace('{url}', data.url)
            : (kind === 'delete' ? (data.reason || this.t('x_studio.reply_deleted'))
              : (kind === 'deny' ? this.t('x_studio.denied') : (data.action === 'awaiting_approval' ? this.t('x_studio.redrafted') : (data.reason || this.t('x_studio.done')))));
          window.showToast(msg, data.action === 'failed' ? 'error' : 'success');
        } else {
          window.showToast(data.error || data.reason || this.t('common.request_failed'), 'error');
        }
        await this.loadConversations();
      } catch (e) {
        window.showToast(String(e.message || e), 'error');
      } finally {
        this.convBusy = '';
      }
    },

    async loadHistory(row) {
      row.historyOpen = !row.historyOpen;
      if (!row.historyOpen || row.historyLoading) return;
      row.historyLoading = true;
      row.historyError = '';
      try {
        const response = await fetch('/api/x/reply/history/' + encodeURIComponent(row.summon_id), { credentials: 'same-origin' });
        const data = await response.json();
        if (!response.ok || data.ok !== true || !Array.isArray(data.rows)) throw new Error(data.error || this.t('common.request_failed'));
        row.history = data.rows;
      } catch (error) {
        row.historyError = String(error.message || error);
      } finally {
        row.historyLoading = false;
      }
    },

    convWhen(epoch) {
      if (!epoch) return '';
      try {
        return window.KazmaFormat ? window.KazmaFormat.dateTime(Number(epoch)) : new Date(Number(epoch) * 1000).toLocaleString();
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
      this._unloadHandler = (event) => {
        if (this._composerLoaded && this._composerSignature() !== this._composerSaved) {
          event.preventDefault(); event.returnValue = '';
        }
      };
      window.addEventListener('beforeunload', this._unloadHandler);
      await Promise.all([this.loadStatus(), this.loadQueue(), this.loadDrafts(), this.loadAudit(), this.loadComposer(), this.loadQualification(), this.loadOperations()]);
      if (this.$watch) {
        this.$watch('when', () => this.queueComposerSave());
        this.$watch('replyToId', () => { this.queueComposerSave(); this.onInput(); });
      }
      this.onInput();
    },

    destroy() {
      clearTimeout(this._composerTimer);
      clearTimeout(this._previewTimer);
      if (this._unloadHandler) window.removeEventListener('beforeunload', this._unloadHandler);
    },

    _composerPayload() {
      return { text: this.text || '', reply_to_id: this.replyToId || '', when: this.when || '',
        proposal_id: this.proposalId || '', draft_text: this._draftText || '' };
    },
    _composerSignature() { return JSON.stringify(this._composerPayload()); },

    async loadComposer() {
      const before = this._composerSignature();
      try {
        const response = await fetch('/api/x/composer', { credentials: 'same-origin' });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || this.t('common.request_failed'));
        if (before !== this._composerSignature()) throw new Error(this.t('x_studio.composer_load_changed'));
        const saved = data.content || {};
        this.text = saved.text || '';
        this.replyToId = saved.reply_to_id || '';
        this.when = saved.when || this._defaultWhen();
        this.proposalId = saved.proposal_id || '';
        this._draftText = saved.draft_text || '';
        this._composerRevision = data.revision;
        this._composerSaved = this._composerSignature();
        this._composerLoaded = true;
        this.composerError = '';
        this.composerSavedAt = data.updated_at;
      } catch (error) { this.composerError = String(error.message || error); }
    },

    queueComposerSave() {
      if (!this._composerLoaded || this.composerError) return;
      clearTimeout(this._composerTimer);
      this._composerTimer = setTimeout(() => this.saveComposer(), 600);
    },

    async saveComposer() {
      if (!this._composerLoaded || this._composerSaving || this.composerError) return;
      const signature = this._composerSignature();
      if (signature === this._composerSaved) return;
      this._composerSaving = true;
      try {
        const payload = Object.assign({ expected_revision: this._composerRevision }, this._composerPayload());
        const response = await this._mutating('PUT', '/api/x/composer', payload);
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || this.t('common.request_failed'));
        this._composerRevision = data.revision;
        this._composerSaved = signature;
        this.composerSavedAt = data.updated_at;
      } catch (error) { this.composerError = String(error.message || error); }
      finally { this._composerSaving = false; }
      if (this._composerSignature() !== this._composerSaved && !this.composerError) this.queueComposerSave();
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

    async loadOperations(more) {
      const url = '/api/x/operations?query=' + encodeURIComponent(this.operationQuery) + (more && this.operationsNext ? '&cursor=' + encodeURIComponent(this.operationsNext) : '');
      const data = await this._readSection('operations', url, 'operations', null, !!more);
      if (data) this.operationsNext = data.next_cursor || '';
    },

    async inspectOperation(operation) {
      this.selectedOperation = null;
      this.operationError = '';
      try {
        const response = await fetch('/api/x/operations/' + encodeURIComponent(operation.id), { credentials: 'same-origin' });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || this.t('common.request_failed'));
        this.selectedOperation = data;
      } catch (error) { this.operationError = String(error.message || error); }
    },

    async loadQualification() {
      try {
        const response = await fetch('/api/x/reply/qualification', { credentials: 'same-origin' });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || this.t('common.request_failed'));
        this.qualification = data;
        this.qualificationError = '';
      } catch (error) { this.qualificationError = String(error.message || error); }
    },

    async uploadQualification(event) {
      const file = event.target.files && event.target.files[0];
      if (!file || this.qualificationBusy) return;
      this.qualificationBusy = true;
      try {
        if (file.size > 2000000) throw new Error(this.t('x_studio.qualification_too_large'));
        const report = JSON.parse(await file.text());
        const response = await this._mutating('PUT', '/api/x/reply/qualification', { report: report });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || this.t('common.request_failed'));
        await this.loadQualification();
      } catch (error) { this.qualificationError = String(error.message || error); }
      finally { this.qualificationBusy = false; event.target.value = ''; }
    },

    async resumePublishing() {
      if (this.busy) return;
      this.busy = true;
      try {
        const accepted = await window.kazmaConfirm({ title: this.t('x_studio.resume_publishing'),
          message: this.t('x_studio.restore_pause'), confirmText: this.t('common.confirm') });
        if (!accepted) return;
        const response = await this._mutating('POST', '/api/x/resume', {});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || this.t('common.request_failed'));
        await this.loadStatus();
      } catch (error) { window.showToast(String(error.message || error), 'error'); }
      finally { this.busy = false; }
    },

    async generateDrafts() {
      const brief = (this.draftBrief || '').trim();
      if (!brief || this.generateBusy) return;
      this.generateBusy = true;
      this.generateError = '';
      try {
        const response = await this._mutating('POST', '/api/x/generate', { brief: brief, count: Number(this.draftCount) || 1 });
        const data = await response.json();
        if (!response.ok || !data || data.ok !== true) throw new Error((data && data.error) || this.t('common.request_failed'));
        this.generatedModels = data.models || [];
        await this.loadDrafts();
        window.showToast(this.t('x_studio.generated_review'), 'success');
      } catch (error) {
        this.generateError = String(error.message || error);
      } finally {
        this.generateBusy = false;
      }
    },

    parseTweetId(raw) {
      const s = String(raw || '').trim();
      if (!s) return '';
      const m = s.match(/status(?:es)?\/(\d+)/i) || s.match(/^(\d+)$/);
      return m ? m[1] : s;
    },

    onInput() {
      this.queueComposerSave();
      if (this.proposalId && this._draftText && (this.text || '') !== this._draftText) {
        this.proposalId = '';
        this._draftText = '';
      }
      this._previewSequence += 1;
      this.preview = Object.assign({}, this.preview, { allow: false });
      clearTimeout(this._previewTimer);
      this._previewTimer = setTimeout(() => this.refreshPreview(), 250);
    },

    async refreshPreview() {
      const sequence = this._previewSequence;
      this.loadStates.preview = { loading: true, error: '', at: (this.loadStates.preview || {}).at || '' };
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
        if (!resp.ok || !data || data.ok !== true) throw new Error(data.error || this.t('common.request_failed'));
        if (sequence !== this._previewSequence) return;
        this.preview = data;
        this.loadStates.preview = { loading: false, error: '', at: Date.now() };
      } catch (error) {
        if (sequence === this._previewSequence) this.loadStates.preview = { loading: false, error: String(error.message || error), at: (this.loadStates.preview || {}).at || '' };
      }
    },

    async _readSection(name, url, field, transform, append) {
      const sequence = (this._loadSequence[name] || 0) + 1;
      this._loadSequence[name] = sequence;
      this.loadStates[name] = { loading: true, error: '', at: (this.loadStates[name] || {}).at || '' };
      try {
        const response = await fetch(url, { credentials: 'same-origin' });
        const data = await response.json();
        if (!response.ok || !data || data.ok !== true || (field && !Array.isArray(data[field]))) throw new Error(data.error || this.t('common.request_failed'));
        if (sequence !== this._loadSequence[name]) return null;
        const value = transform ? transform(data) : data[field];
        this[name] = append ? this[name].concat(value) : value;
        this.loadStates[name] = { loading: false, error: '', at: Date.now() };
        return data;
      } catch (error) {
        if (sequence === this._loadSequence[name]) this.loadStates[name] = { loading: false, error: String(error.message || error), at: (this.loadStates[name] || {}).at || '' };
        return null;
      }
    },

    // The status the page renders always has its shape: an error answer (a
    // store behind the route failing) kept the last one -- it used to become
    // the status, and the quota pill threw on every render (2026-09-27).
    async loadStatus() {
      await this._readSection('status', '/api/x/status', '', function (data) {
        const caps = (data.caps && typeof data.caps === 'object') ? data.caps : {};
        return Object.assign({ can_post: false, handle: '' }, data, { caps: caps });
      });
    },

    async loadQueue(more) {
      const url = '/api/x/queue?include_finished=' + this.includeFinished + (more && this.queueNext ? '&cursor=' + encodeURIComponent(this.queueNext) : '');
      const data = await this._readSection('queue', url, 'items', (data) => data.items.map((item) => {
        const when = new Date(item.due_at * 1000).toISOString();
        return Object.assign({}, item, { summary: item.text, when: when, editWhen: this.toLocalInput(when) });
      }), !!more);
      if (data) {
        this.queueCount = data.count;
        this.queueNext = data.next_cursor || '';
        this._buildWeek();
      }
    },

    async loadDrafts(more) {
      const url = '/api/x/drafts?dismissed=' + this.showDismissed + '&query=' + encodeURIComponent(this.draftQuery) + (more && this.draftsNext ? '&cursor=' + encodeURIComponent(this.draftsNext) : '');
      const data = await this._readSection('drafts', url, 'drafts', null, !!more);
      if (data) this.draftsNext = data.next_cursor || '';
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
      await this._readSection('audit', '/api/x/audit?limit=20', 'entries');
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
        const d = new Date(now);
        d.setDate(now.getDate() + i);
        const key = this._localKey(d);
        days.push({
          key: key,
          label: d.toLocaleDateString(locale, { weekday: 'short' }),
          count: counts[key] || 0,
        });
      }
      this.week = days;
    },

    _scheduleInstant(raw) {
      const parsed = new Date(raw);
      if (!Number.isFinite(parsed.getTime()) || this.toLocalInput(parsed.toISOString()) !== String(raw).slice(0, 16)) {
        throw new Error(this.t('x_studio.time_gap'));
      }
      // Local datetime controls have no offset selector. Refuse both folds
      // instead of silently choosing the browser's preferred occurrence.
      for (const minutes of [-120, -90, -60, -30, 30, 60, 90, 120]) {
        const alternative = new Date(parsed.getTime() + minutes * 60000);
        if (this.toLocalInput(alternative.toISOString()) === String(raw).slice(0, 16)) throw new Error(this.t('x_studio.time_fold'));
      }
      return parsed.toISOString();
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
          bits.push(window.KazmaFormat ? window.KazmaFormat.dateTime(entry.ts) : new Date(entry.ts).toLocaleString());
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
      const signature = JSON.stringify(body);
      if (!this._intentKey || this._intentSignature !== signature) {
        this._intentSignature = signature;
        this._intentKey = (window.crypto && window.crypto.randomUUID)
          ? window.crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
      }
      body.idempotency_key = this._intentKey;
      return body;
    },

    _resetComposer(nextReplyId) {
      this.text = '';
      this.proposalId = '';
      this._draftText = '';
      this._intentKey = '';
      this._intentSignature = '';
      if (nextReplyId) this.replyToId = String(nextReplyId);
      this.onInput();
    },

    async postNow() {
      if (this.busy) return;
      const payload = this._payload();
      const body = (this.text || '').trim();
      if (!body) {
        window.showToast(this.t('x_studio.text_required'), 'error');
        return;
      }
      this.busy = true;
      try {
      const ok = await window.kazmaConfirm({
        title: this.t('x_studio.post_now'),
        message: this.t('x_studio.confirm_post') + '\n\n' + body,
        confirmText: this.t('x_studio.post_now'),
      });
      if (!ok) return;
      if (payload.text !== (this.text || '').trim() || payload.reply_to_id !== this.parseTweetId(this.replyToId)
          || (payload.proposal_id || '') !== this.proposalId) return;
        const resp = await this._mutating('POST', '/api/x/post', payload);
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          window.showToast(this.t('x_studio.posted'), 'success');
          if (payload.text === (this.text || "").trim() && payload.reply_to_id === this.parseTweetId(this.replyToId)) this._resetComposer(data.tweet_id || "");
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
      if (this.busy) return;
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
        const whenIso = this._scheduleInstant(this.when);
        const payload = this._payload();
        payload.when = whenIso;
        const resp = await this._mutating('POST', '/api/scheduled/x', payload);
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok !== false) {
          window.showToast(this.t('x_studio.scheduled_ok'), 'success');
          if (payload.text === (this.text || "").trim() && payload.reply_to_id === this.parseTweetId(this.replyToId)) this._resetComposer(this.parseTweetId(this.replyToId));
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
      if (this.queueBusy || !item.can_reschedule) return;
      if (!(item.editWhen || '').trim()) {
        window.showToast(this.t('x_studio.timing_required'), 'error');
        return;
      }
      this.queueBusy = item.id;
      try {
        const whenIso = this._scheduleInstant(item.editWhen);
        const resp = await this._mutating('POST', '/api/x/queue/' + item.id + '/reschedule', { when: whenIso, expected_version: item.version });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          window.showToast(this.t('x_studio.rescheduled'), 'success');
          await this.loadQueue();
        } else {
          window.showToast(data.error || this.t('x_studio.reschedule_failed'), 'error');
        }
      } catch (e) {
        window.showToast(this.t('x_studio.reschedule_failed') + ': ' + e.message, 'error');
      } finally {
        this.queueBusy = '';
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
      if (!tid || this.deleteBusy) return;
      this.deleteBusy = tid;
      try {
      const ok = await window.kazmaConfirm({
        title: this.t('x_studio.delete_post'),
        message: this.t('x_studio.confirm_delete') + '\n\n' + (this.auditText(entry) || tid),
        confirmText: this.t('x_studio.delete_post'),
        danger: true,
      });
      if (!ok) return;
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
      } finally { this.deleteBusy = ''; }
    },

    async cancel(item) {
      if (this.queueBusy || !item.can_cancel) return;
      this.queueBusy = item.id;
      const version = item.version;
      try {
      const ok = await window.kazmaConfirm({
        title: this.t('x_studio.cancel'),
        message: item.summary || '',
        confirmText: this.t('x_studio.cancel'),
        danger: true,
      });
      if (!ok) return;
        const resp = await this._mutating('POST', '/api/x/queue/' + item.id + '/cancel', { expected_version: version });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          await this.loadQueue();
        } else {
          window.showToast(data.error || this.t('x_studio.cancel_failed'), 'error');
        }
      } catch (e) {
        window.showToast(this.t('x_studio.cancel_failed') + ': ' + e.message, 'error');
      } finally {
        this.queueBusy = '';
      }
    },
  };
}

window.xStudioPage = xStudioPage;
