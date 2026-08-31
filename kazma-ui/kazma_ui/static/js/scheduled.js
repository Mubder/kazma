/* Scheduled Tasks page — aggregates cron jobs + scheduled X posts.
 *
 * Backed by /api/scheduled/*. Mutating calls send the same-origin CSRF pair
 * (X-Requested-With) the server requires. Dialogs use the unified
 * window.kazmaConfirm / window.showToast helpers (see AGENTS.md UI rules).
 */

function scheduledPage() {
    return {
        tasks: [],
        loading: false,

        t(key) { return (window.t && window.t(key)) || key; },

        // Upcoming by default. The page previously showed everything at
        // once, so a single pending task sat under forty finished ones and
        // the thing you came to check was the hardest thing to find.
        filter: 'upcoming',

        // Newest first by default. History arrived oldest-first, so the run
        // that just happened -- the one you open the page to check -- was
        // at the bottom of thirty-four rows.
        sortDir: 'desc',

        // X activity — the audit log, moved here from Settings. It answers
        // "what did Kazma actually post", which is an operational question,
        // not a configuration one: it belongs beside the schedule that
        // produced it rather than inside the connector setup form.
        audit: { entries: [], loading: false, open: null, loaded: false },

        // Times on this page render in the VIEWER's zone; recurring jobs
        // ("daily at 9am") are interpreted in the SCHEDULER's zone. When
        // those differ, "9am" means neither what the row shows nor what the
        // operator assumed, and nothing on the page said so.
        cronTz: '',

        get counts() {
            const c = { upcoming: 0, failed: 0, done: 0, overdue: 0 };
            for (const t of this.tasks) {
                const st = t.status || '';
                if (st === 'pending' || st === 'running') {
                    c.upcoming++;
                    if (this.isOverdue(t)) c.overdue++;
                } else if (st === 'failed') c.failed++;
                else c.done++;
            }
            return c;
        },

        get filtered() {
            let rows;
            if (this.filter === 'all') {
                rows = this.tasks.slice();
            } else if (this.filter === 'history') {
                rows = this.tasks.filter(t => !['pending', 'running'].includes(t.status || ''));
            } else {
                rows = this.tasks.filter(t => ['pending', 'running'].includes(t.status || ''));
            }
            // Sort on a copy: the API's own order is the fallback for rows
            // with no usable timestamp, and mutating this.tasks would make
            // the sort compound every time the getter re-ran.
            const dir = this.sortDir === 'asc' ? 1 : -1;
            return rows.slice().sort((a, b) => {
                const ta = this._ms(a), tb = this._ms(b);
                if (ta === tb) return 0;
                if (ta === null) return 1;   // undated rows sink, either way
                if (tb === null) return -1;
                return (ta - tb) * dir;
            });
        },

        _ms(task) {
            const d = this._date(task);
            return d ? d.getTime() : null;
        },

        toggleSort() {
            this.sortDir = this.sortDir === 'desc' ? 'asc' : 'desc';
        },

        _locale() {
            return document.documentElement.lang || 'en';
        },

        _date(task) {
            const raw = task && task.when;
            if (!raw) return null;
            const d = new Date(raw);
            return isNaN(d.getTime()) ? null : d;
        },

        /* Absolute time in the VIEWER's timezone.
         *
         * The API returns UTC ISO strings and the page printed them raw:
         * "2026-08-30T09:00:00+00:00". Unreadable, and silently three hours
         * wrong for an operator in +03:00 -- the kind of error you only
         * notice when a task fires when you did not expect it. */
        fmtAbs(task) {
            const d = this._date(task);
            if (!d) return '—';
            try {
                return new Intl.DateTimeFormat(this._locale(), {
                    dateStyle: 'medium', timeStyle: 'short',
                }).format(d);
            } catch (e) {
                return d.toLocaleString();
            }
        },

        /* "in 3 days" / "2 weeks ago" -- the part you actually read. */
        fmtRel(task) {
            const d = this._date(task);
            if (!d) return '';
            const secs = (d.getTime() - Date.now()) / 1000;
            const steps = [
                ['year', 31536000], ['month', 2592000], ['week', 604800],
                ['day', 86400], ['hour', 3600], ['minute', 60],
            ];
            try {
                const rtf = new Intl.RelativeTimeFormat(this._locale(), { numeric: 'auto' });
                for (const [unit, size] of steps) {
                    if (Math.abs(secs) >= size) return rtf.format(Math.round(secs / size), unit);
                }
                return rtf.format(Math.round(secs), 'second');
            } catch (e) {
                return '';
            }
        },

        /* A pending task whose time has passed is the one row that needs
         * attention, and it used to look identical to every other row. */
        isOverdue(task) {
            if (!['pending', 'running'].includes(task.status || '')) return false;
            const d = this._date(task);
            return !!d && d.getTime() < Date.now();
        },

        /* Prompts are stored with the indentation they were written with,
         * and the cell rendered it verbatim -- pages of leading whitespace
         * inside a table. Batch-job wrappers also bury the tweet in English
         * instructions; show the body the operator can actually read. */
        cleanSummary(task) {
            const raw = String((task && task.summary) || '').replace(/\s+/g, ' ').trim();
            if (!raw) return '—';
            const bidi = window.KazmaBidi;
            if (bidi && bidi.extractPostBody) return bidi.extractPostBody(raw) || raw;
            return raw;
        },

        summaryKicker(task) {
            if (task && task.kicker) return String(task.kicker);
            const bidi = window.KazmaBidi;
            if (bidi && bidi.displayKicker) {
                return bidi.displayKicker((task && task.summary) || '');
            }
            return '';
        },

        summaryDir(task) {
            if (task && task.dir) return task.dir;
            const bidi = window.KazmaBidi;
            if (bidi && bidi.textDir) return bidi.textDir(this.cleanSummary(task));
            const s = this.cleanSummary(task);
            if (/[\u0600-\u06FF]/.test(s)) return 'rtl';
            return 'ltr';
        },

        /* Real failures only. last_result is the agent's wrap-up essay and
         * used to paint the whole cell red even on a successful post. */
        errorText(task) {
            const err = String((task && task.error) || '').replace(/\s+/g, ' ').trim();
            if (err) return err;
            if ((task && task.status) === 'failed') {
                const last = String((task.last_result || '')).replace(/\s+/g, ' ').trim();
                return last.slice(0, 160);
            }
            return '';
        },

        outcomeText(task) {
            if (this.errorText(task)) return '';
            if (task && task.outcome) return String(task.outcome);
            const bidi = window.KazmaBidi;
            const raw = String((task && task.last_result) || '');
            if (bidi && bidi.shortenOutcome) return bidi.shortenOutcome(raw);
            return '';
        },

        /* @deprecated kept so older tests/templates that call reason() still
         * see a string; the template now uses errorText / outcomeText. */
        reason(task) {
            return this.errorText(task);
        },

        statusLabel(task) {
            const st = (task && task.status) || 'pending';
            const key = 'scheduled.st_' + st;
            const label = window.t ? window.t(key) : st;
            return (label && label !== key) ? label : st;
        },

        // Cron task form
        showCronModal: false,
        cronSaving: false,
        cronForm: { job_id: '', timing: '', prompt: '' },

        // X post form
        showXModal: false,
        xSaving: false,
        xForm: { id: null, text: '', when: '' },

        async init() {
            await this.load();
            this.loadCronTz();
        },

        async loadCronTz() {
            try {
                const resp = await fetch('/api/settings/cron-timezone', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                this.cronTz = String((data && data.timezone) || '');
            } catch (e) {
                this.cronTz = '';
            }
        },

        viewerTz() {
            try {
                return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
            } catch (e) {
                return '';
            }
        },

        // Only worth saying when the two disagree.
        tzMismatch() {
            const v = this.viewerTz();
            return !!(this.cronTz && v && this.cronTz !== v);
        },

        async refreshAll() {
            await this.load();
            if (this.filter === 'x') await this.loadAudit(true);
        },

        // Loaded on first view rather than at page load: the audit query
        // is unbounded work nobody asked for while looking at the schedule.
        async showXActivity() {
            this.filter = 'x';
            if (!this.audit.loaded) await this.loadAudit();
        },

        async loadAudit(force) {
            if (this.audit.loading) return;
            if (this.audit.loaded && !force) return;
            this.audit.loading = true;
            try {
                const resp = await fetch('/api/x/audit?limit=100', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    this.audit.entries = data.entries || [];
                    this.audit.loaded = true;
                } else {
                    window.showToast(data.error || window.t('scheduled.x_activity_failed'), 'error');
                }
            } catch (e) {
                window.showToast(window.t('scheduled.x_activity_failed') + ': ' + e.message, 'error');
            } finally {
                this.audit.loading = false;
            }
        },

        /* Audit rows carry a local timestamp already; format it the same
         * way as everything else rather than slicing the ISO string. */
        fmtAuditWhen(entry) {
            return this.fmtAbs({ when: entry && entry.ts });
        },

        _auditOpen() {
            return (this.audit.open != null && this.audit.entries[this.audit.open]) || null;
        },

        _tweetFromEntry(e) {
            if (!e) return '';
            let raw = e.text || '';
            if (!raw && e.request_body) {
                try {
                    const body = typeof e.request_body === 'string'
                        ? JSON.parse(e.request_body) : e.request_body;
                    if (body && typeof body.text === 'string') raw = body.text;
                    else if (body && body.data && typeof body.data.text === 'string') {
                        raw = body.data.text;
                    }
                } catch (_err) { /* keep empty rather than dump JSON */ }
            }
            return this.cleanSummary({ summary: raw });
        },

        auditDetailText() {
            return this._tweetFromEntry(this._auditOpen());
        },

        auditDetailError() {
            const e = this._auditOpen();
            if (!e) return '';
            const msg = String(e.error_detail || e.error || '').trim();
            if (!msg) return '';
            if (msg.charAt(0) === '{' || msg.charAt(0) === '[') {
                try {
                    const obj = JSON.parse(msg);
                    if (obj && typeof obj.detail === 'string') return obj.detail;
                    if (obj && typeof obj.error === 'string') return obj.error;
                    if (obj && typeof obj.title === 'string') return obj.title;
                } catch (_err) { /* fall through */ }
            }
            return msg;
        },

        auditDetailMeta() {
            const e = this._auditOpen();
            if (!e) return '';
            const bits = [e.action || 'call'];
            if (e.status && e.status !== 'success') bits.push(e.status);
            if (e.http_status != null) bits.push('HTTP ' + e.http_status);
            if (e.tweet_id) bits.push('#' + e.tweet_id);
            if (e.duration_ms != null) bits.push(e.duration_ms + ' ms');
            if (e.reply_to) bits.push('reply-to ' + e.reply_to);
            return bits.join(' · ');
        },

        auditDetailUrl() {
            const e = this._auditOpen();
            return (e && e.post_url) || '';
        },

        async load() {
            this.loading = true;
            try {
                const resp = await fetch('/api/scheduled/tasks', { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    this.tasks = data.tasks || [];
                } else {
                    window.showToast(data.error || 'Failed to load scheduled tasks', 'error');
                }
            } catch (e) {
                window.showToast('Failed to load scheduled tasks: ' + e.message, 'error');
            } finally {
                this.loading = false;
            }
        },

        canEdit(task) {
            if (task.source === 'x') return task.status === 'pending';
            return task.status === 'pending' || task.status === 'running';
        },

        canDelete(task) {
            return task.status === 'pending' || task.status === 'running';
        },

        openCreateCron() {
            this.cronForm = { job_id: '', timing: '', prompt: '' };
            this.showCronModal = true;
        },

        openCreateX() {
            this.xForm = { id: null, text: '', when: '' };
            this.showXModal = true;
        },

        openEdit(task) {
            if (task.source === 'x') {
                this.xForm = { id: task.id, text: task.summary, when: '' };
                this.showXModal = true;
            } else {
                this.cronForm = { job_id: task.id, timing: task.timing || '', prompt: task.summary || '' };
                this.showCronModal = true;
            }
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

        async saveCron() {
            const isEdit = !!this.cronForm.job_id;
            if (!this.cronForm.prompt.trim()) {
                window.showToast(window.t('scheduled.prompt_required'), 'error');
                return;
            }
            if (!this.cronForm.timing.trim()) {
                window.showToast(window.t('scheduled.timing_required'), 'error');
                return;
            }
            this.cronSaving = true;
            try {
                const url = isEdit
                    ? '/api/scheduled/cron/' + encodeURIComponent(this.cronForm.job_id)
                    : '/api/scheduled/cron';
                const resp = await this._mutating(isEdit ? 'PUT' : 'POST', url, {
                    timing: this.cronForm.timing.trim(),
                    prompt: this.cronForm.prompt.trim(),
                });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok !== false && (data.ok || data.status === 'rescheduled' || data.status === 'scheduled')) {
                    window.showToast(isEdit ? window.t('scheduled.updated') : window.t('scheduled.created'), 'success');
                    this.showCronModal = false;
                    await this.load();
                } else {
                    window.showToast(data.error || 'Save failed', 'error');
                }
            } catch (e) {
                window.showToast('Save failed: ' + e.message, 'error');
            } finally {
                this.cronSaving = false;
            }
        },

        async saveX() {
            const isEdit = !!this.xForm.id;
            if (!this.xForm.when.trim()) {
                window.showToast(window.t('scheduled.timing_required'), 'error');
                return;
            }
            if (!isEdit && !this.xForm.text.trim()) {
                window.showToast(window.t('scheduled.x_text_required'), 'error');
                return;
            }
            this.xSaving = true;
            try {
                let resp;
                if (isEdit) {
                    resp = await this._mutating('PUT', '/api/scheduled/x/' + this.xForm.id, {
                        when: this.xForm.when.trim(),
                    });
                } else {
                    resp = await this._mutating('POST', '/api/scheduled/x', {
                        text: this.xForm.text.trim(),
                        when: this.xForm.when.trim(),
                    });
                }
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok !== false) {
                    window.showToast(isEdit ? window.t('scheduled.updated') : window.t('scheduled.x_booked'), 'success');
                    this.showXModal = false;
                    await this.load();
                } else {
                    window.showToast(data.error || 'Save failed', 'error');
                }
            } catch (e) {
                window.showToast('Save failed: ' + e.message, 'error');
            } finally {
                this.xSaving = false;
            }
        },

        async remove(task) {
            const label = task.source === 'x' ? window.t('scheduled.confirm_delete_x') : window.t('scheduled.confirm_delete_task');
            const ok = await window.kazmaConfirm({
                title: window.t('scheduled.delete'),
                message: label,
                confirmText: window.t('scheduled.delete'),
                danger: true,
            });
            if (!ok) return;
            try {
                const url = task.source === 'x'
                    ? '/api/scheduled/x/' + task.id
                    : '/api/scheduled/cron/' + encodeURIComponent(task.id);
                const resp = await this._mutating('DELETE', url);
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    window.showToast(window.t('scheduled.deleted'), 'success');
                    await this.load();
                } else {
                    window.showToast(data.error || 'Delete failed', 'error');
                }
            } catch (e) {
                window.showToast('Delete failed: ' + e.message, 'error');
            }
        },
    };
}

window.scheduledPage = scheduledPage;
