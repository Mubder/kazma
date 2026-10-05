/* Human annotation only. Recorded model judgments cannot be edited here. */
function xDatasetPage() {
  return {
    rows: [], dataset: null, form: null, selectedId: '', name: '', purpose: 'collection',
    query: '', page: 0, busy: false, loading: true, error: '', notice: '', reviewed: false, critical: '',
    categories: ['gulf_arabic', 'mixed_scripts', 'sarcasm', 'negation', 'quotation', 'multiple_entities', 'injection', 'source_contradiction', 'checker_outage'],
    _saved: '', _sequence: 0, _unload: null,
    t(key) { return window.t('x_dataset.' + key); },
    showSaveError(message) {
      this.error = message;
      this.$nextTick(() => {
        const alert = this.$refs && this.$refs.saveError;
        if (alert) { alert.scrollIntoView({ block: 'center' }); alert.focus(); }
      });
    },
    invalidField(event) {
      const input = event.target;
      const label = input.id && document.querySelector('label[for="' + input.id + '"]');
      const field = label ? label.textContent.trim() : this.t('edit');
      this.notice = '';
      this.showSaveError(this.t('invalid_field').replace('{field}', field)
        + ' ' + (input.validationMessage || this.t('request_failed')));
    },
    signature() { return JSON.stringify({form: this.form, reviewed: this.reviewed, critical: this.critical}); },
    get dirty() { return !!this.form && this.signature() !== this._saved; },
    get filtered() {
      const q = this.query.trim().toLocaleLowerCase();
      return this.dataset ? this.dataset.cases.filter(c => !c.archived && (!q || (c.id + ' ' + c.context.text).toLocaleLowerCase().includes(q))) : [];
    },
    get visible() { return this.filtered.slice(this.page * 20, (this.page + 1) * 20); },
    get stats() {
      const cases = this.dataset ? this.dataset.cases.filter(c => !c.archived) : [];
      return { total: cases.length, reviewed: cases.filter(c => c.human_reviewed).length,
        en: cases.filter(c => c.language === 'en').length, ar: cases.filter(c => c.language === 'ar').length,
        groups: new Set(cases.map(c => c.group)).size };
    },
    async init() {
      this._unload = e => { if (this.dirty) { e.preventDefault(); e.returnValue = ''; } };
      window.addEventListener('beforeunload', this._unload);
      this.$watch('query', () => { this.page = 0; });
      await this.load();
    },
    destroy() { window.removeEventListener('beforeunload', this._unload); },
    async request(url, method, body) {
      const response = await fetch(url, { method: method || 'GET', credentials: 'same-origin',
        headers: body ? { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } : {},
        body: body ? JSON.stringify(body) : undefined });
      let data;
      try { data = await response.json(); }
      catch (_) { throw new Error(this.t('invalid_response').replace('{status}', String(response.status))); }
      if (!data || typeof data !== 'object') {
        throw new Error(this.t('invalid_response').replace('{status}', String(response.status)));
      }
      if (!response.ok || !data.ok) {
        const detail = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail)
          ? data.detail.map(item => (item.loc || []).join('.') + ': ' + item.msg).join('; ') : '';
        throw new Error(data.error || detail || this.t('invalid_response').replace('{status}', String(response.status)));
      }
      return data.dataset;
    },
    async load() {
      if (this._listing) return;
      this._listing = true; this.loading = true;
      try { this.rows = await this.request('/api/x/datasets'); } catch (e) { this.error = String(e.message || e); }
      finally { this._listing = false; this.loading = false; }
    },
    async leaveEditor() {
      if (!this.dirty) return true;
      return window.kazmaConfirm({ title: this.t('unsaved'), message: this.t('unsaved_help'), confirmText: this.t('discard_edits'), danger: true });
    },
    async open(id) {
      if (this.busy || !await this.leaveEditor()) return;
      const sequence = ++this._sequence;
      this.busy = true; this.error = ''; this.notice = '';
      try {
        const data = await this.request('/api/x/datasets/' + encodeURIComponent(id));
        if (sequence !== this._sequence) return;
        this.dataset = data; this.selectedId = id; this.form = null; this.query = ''; this.page = 0;
      } catch (e) { this.error = String(e.message || e); }
      finally { if (sequence === this._sequence) this.busy = false; }
    },
    async create(document) {
      if (this.busy || !await this.leaveEditor()) return;
      this.busy = true; this.error = ''; this.notice = '';
      try {
        this.dataset = await this.request('/api/x/datasets', 'POST', { name: this.name, purpose: this.purpose, document: document || null });
        this.selectedId = this.dataset.id; this.form = null; this.page = 0; this.query = '';
        await this.load(); this.notice = this.t('saved');
      } catch (e) { this.error = String(e.message || e); }
      finally { this.busy = false; }
    },
    async importFile(event) {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        if (file.size > 2000000) throw new Error(this.t('file_large'));
        const doc = JSON.parse(await file.text());
        if (!this.name.trim()) this.name = file.name.replace(/\.json$/i, '').slice(0, 120);
        await this.create(doc);
      } catch (e) { this.error = String(e.message || e); }
      finally { event.target.value = ''; }
    },
    async collect() {
      if (!this.dataset || this.busy || !await this.leaveEditor()) return;
      this.busy = true; this.error = ''; this.notice = '';
      try {
        this.dataset = await this.request('/api/x/datasets/' + this.dataset.id + '/collect', 'POST', { expected_revision: this.dataset.revision });
        this.form = null; await this.load(); this.notice = this.t('collected');
      } catch (e) { this.error = String(e.message || e); }
      finally { this.busy = false; }
    },
    async edit(caseData) {
      if (this.busy || !await this.leaveEditor()) return;
      const c = JSON.parse(JSON.stringify(caseData || { id: 'case:' + crypto.randomUUID(), language: '', categories: [],
        held_out: false, expected: { target: null, auto: null, evidence: null, safety: null },
        context: { source_id: '', author_handle: '', text: '', verified_source: false, author_resolved: false,
          truncated: false, missing_quote: false, media_present: false, fallback_text: false, quotes: [] },
        summon: { id: '', author: '', text: '', conversation_id: '', target_followers: null }, rationale: '', review_notes: '' }));
      c.fault = c.fault || '';
      this.form = { case: c, targetLabeled: c.expected.target !== null, target: c.expected.target || '',
        auto: c.expected.auto === null ? '' : String(c.expected.auto),
        evidence: c.expected.evidence === null ? '' : String(c.expected.evidence),
        safety: c.expected.safety === null ? '' : String(c.expected.safety),
        followers: c.summon.target_followers === null || c.summon.target_followers === undefined ? '' : String(c.summon.target_followers) };
      this.reviewed = false; this.critical = ''; this._saved = this.signature();
      this.$nextTick(() => document.getElementById('xd-source-text').focus());
    },
    async save() {
      if (this.busy || !this.form) return;
      if (this.reviewed) {
        const missing = [];
        if (!['en', 'ar', 'mixed'].includes(this.form.case.language)) missing.push(this.t('language'));
        if (!this.form.case.categories.length) missing.push(this.t('categories'));
        if (!this.form.targetLabeled) missing.push(this.t('target_labeled'));
        for (const label of ['auto', 'evidence', 'safety']) {
          if (!['true', 'false'].includes(this.form[label])) missing.push(this.t('expected_' + label));
        }
        if (!this.form.case.rationale.trim()) missing.push(this.t('rationale'));
        if (this.form.case.actual && this.critical === '') missing.push(this.t('critical'));
        if (missing.length) {
          this.notice = '';
          this.showSaveError(this.t('review_incomplete').replace('{fields}', missing.join('; ')));
          return;
        }
      }
      const frozen = JSON.parse(JSON.stringify(this.form));
      const c = frozen.case;
      c.fault = c.fault || null;
      c.expected = { target: frozen.targetLabeled ? frozen.target.trim() : null,
        auto: frozen.auto === '' ? null : frozen.auto === 'true',
        evidence: frozen.evidence === '' ? null : frozen.evidence === 'true',
        safety: frozen.safety === '' ? null : frozen.safety === 'true' };
      c.summon.target_followers = frozen.followers === '' ? null : Number(frozen.followers);
      const body = { expected_revision: this.dataset.revision, case: c, reviewed: this.reviewed,
        critical_violations: this.critical === '' ? null : Number(this.critical) };
      this.busy = true; this.error = ''; this.notice = '';
      try {
        this.dataset = await this.request('/api/x/datasets/' + this.dataset.id + '/case', 'PUT', body);
        this.form = null; await this.load(); this.notice = this.t('saved');
        if (window.showToast) window.showToast(this.notice, 'success');
      } catch (e) { this.showSaveError(String(e.message || e)); }
      finally { this.busy = false; }
    },
    async download(report) {
      if (!this.dataset || this.busy) return;
      if (this.dirty) { this.error = this.t('save_before_export'); return; }
      this.busy = true; this.error = '';
      try {
        const data = await this.request('/api/x/datasets/' + this.dataset.id + '/export?report=' + (report ? 'true' : 'false'));
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a'); link.href = url;
        link.download = 'x-' + (report ? 'reviewed-report' : 'cases') + '-' + this.dataset.id + '.json';
        link.click(); URL.revokeObjectURL(url);
      } catch (e) { this.error = String(e.message || e); }
      finally { this.busy = false; }
    }
  };
}
