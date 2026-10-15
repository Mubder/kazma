/* Scheduled Tasks page — aggregates cron jobs + scheduled X posts.
 *
 * Backed by /api/scheduled/* (and, for X rows, X Studio's revision-bound
 * queue actions /api/x/queue/{op}/reschedule|cancel). Mutating calls send
 * the same-origin CSRF pair (X-Requested-With) the server requires. Dialogs
 * use the unified window.kazmaConfirm / window.kazmaPrompt / window.showToast
 * helpers (see AGENTS.md UI rules).
 *
 * Scheduled audit 2026-10-13:
 *  - every status maps to a class (STATUS_CLASS); an unknown one needs
 *    attention, never "Completed" (SCH-S04);
 *  - the controls shown are the ones the server's state machine allows
 *    (task.actions), never guessed from the status (SCH-S07);
 *  - the edit dialog reads the whole prompt from the server and sends it back
 *    only when it was edited, with the version it was read at (SCH-S02);
 *  - dates go through window.KazmaFormat, with the zone named (SCH-S12).
 */

/* The page's status classes. kazma_ui/scheduled_api.py SCHEDULED_STATUS_CLASS
 * is the same map (tests/test_scheduled_presentation.py holds them equal). */
const STATUS_CLASS = {
    // cron jobs
    pending: 'upcoming',
    running: 'upcoming',
    delivering: 'upcoming',
    done: 'done',
    failed: 'failed',
    cancelled: 'cancelled',
    undeliverable: 'failed',
    missed: 'attention',
    delivery_unknown: 'attention',
    // X publication operations
    scheduled: 'upcoming',
    deferred: 'upcoming',
    sending: 'attention',
    awaiting_approval: 'attention',
    outcome_unknown: 'attention',
    published: 'done',
    failed_permanent: 'failed',
    expired: 'failed',
};

function statusClass(task) {
    if (task && task.class) return task.class;
    const st = (task && task.status) || '';
    return Object.prototype.hasOwnProperty.call(STATUS_CLASS, st) ? STATUS_CLASS[st] : 'attention';
}

function scheduledPage() {
    return {
        tasks: [],
        loading: false,
        // "Nothing scheduled" only after the server answered
        // (tests/test_empty_states_after_load.py).
        tasksLoaded: false,
        timezone: '',

        t(key) { return (window.t && window.t(key)) || key; },

        // Upcoming by default. The page previously showed everything at
        // once, so a single pending task sat under forty finished ones and
        // the thing you came to check was the hardest thing to find.
        filter: 'upcoming',

        // Newest first by default. History arrived oldest-first, so the run
        // that just happened -- the one you open the page to check -- was
        // at the bottom of thirty-four rows.
        sortDir: 'desc',

        // X activity — the audit log, moved here from Settings.
        audit: { entries: [], loading: false, open: null, loaded: false },

        // Times on this page render in the VIEWER's zone; recurring jobs
        // ("daily at 9am") are interpreted in the SCHEDULER's zone.
        cronTz: '',

        get counts() {
            const c = { upcoming: 0, attention: 0, failed: 0, done: 0, cancelled: 0, overdue: 0 };
            for (const task of this.tasks) {
                const cls = statusClass(task);
                if (Object.prototype.hasOwnProperty.call(c, cls)) c[cls]++;
                else c.attention++;
                if (cls === 'upcoming' && this.isOverdue(task)) c.overdue++;
            }
            return c;
        },

        get filtered() {
            let rows;
            if (this.filter === 'all') {
                rows = this.tasks.slice();
            } else if (this.filter === 'history') {
                rows = this.tasks.filter(task => ['done', 'failed', 'cancelled'].includes(statusClass(task)));
            } else {
                rows = this.tasks.filter(task => ['upcoming', 'attention'].includes(statusClass(task)));
            }
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

        _date(task) {
            const raw = task && task.when;
            if (!raw) return null;
            const d = new Date(raw);
            return isNaN(d.getTime()) ? null : d;
        },

        /* Absolute time in the VIEWER's zone, with the zone named
         * ("14 Oct 2026, 09:00 GMT+3"), through window.KazmaFormat: Latin
         * digits in Arabic, the page's language either way. */
        fmtAbs(task) {
            const d = this._date(task);
            if (!d) return '—';
            const fmt = window.KazmaFormat;
            if (!fmt) return d.toISOString();
            return fmt.dateTime(d, {
                year: 'numeric', month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
            }) || d.toISOString();
        },

        /* "in 3 days" / "2 weeks ago" -- the part you actually read. */
        fmtRel(task) {
            const d = this._date(task);
            if (!d) return '';
            const fmt = window.KazmaFormat;
            return fmt ? fmt.relative(d) : '';
        },

        /* A pending task whose time has passed is the one row that needs
         * attention, and it used to look identical to every other row. */
        isOverdue(task) {
            if (statusClass(task) !== 'upcoming') return false;
            const d = this._date(task);
            return !!d && d.getTime() < Date.now() - 60000;
        },

        /* Prompts are stored with the indentation they were written with;
         * batch-job wrappers also bury the tweet in English instructions. */
        cleanSummary(task) {
            const raw = String((task && task.summary) || '').replace(/\s+/g, ' ').trim();
            if (!raw) return '—';
            const bidi = window.KazmaBidi;
            if (bidi && bidi.extractPostBody) return bidi.extractPostBody(raw) || raw;
            return raw;
        },

        summaryKicker(task) {
            if (task && typeof task.kicker === 'string') return task.kicker;
            const bidi = window.KazmaBidi;
            if (bidi && bidi.displayKicker) {
                return bidi.displayKicker((task && task.summary) || '');
            }
            return '';
        },

        summaryDir(task) {
            if (task && task.dir && task.dir !== 'auto') return task.dir;
            const bidi = window.KazmaBidi;
            if (bidi && bidi.textDir) return bidi.textDir(this.cleanSummary(task));
            const s = this.cleanSummary(task);
            if (/[؀-ۿ]/.test(s)) return 'rtl';
            if (!s || s === '—') {
                return (document.documentElement.getAttribute('dir') || 'ltr').toLowerCase();
            }
            return 'ltr';
        },

        stampAr(el, raw) {
            if (!el) return;
            const dir = this.summaryDir({ summary: raw });
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

        /* Real failures only: a delivery the platform refused, a failed run.
         * last_result is the agent's wrap-up and used to paint the whole
         * cell red even on a successful post. */
        errorText(task) {
            const err = String((task && (task.error || task.delivery_error)) || '').replace(/\s+/g, ' ').trim();
            if (err) return err;
            if (statusClass(task) === 'failed') {
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

        /* The status in the page's language; a status the catalog does not
         * know reads as "Needs attention", never as its raw id (SCH-S04). */
        statusLabel(task) {
            const st = (task && task.status) || 'pending';
            const key = 'scheduled.st_' + st;
            const label = window.t ? window.t(key) : '';
            if (label && label !== key) return label;
            const fallback = window.t ? window.t('scheduled.st_attention') : '';
            return (fallback && fallback !== 'scheduled.st_attention') ? fallback : st;
        },

        statusBadge(task) {
            return 'st-' + statusClass(task);
        },

        /* What the job does when it fires, and how often (SCH-S07). */
        kindLabel(task) {
            if (!task || task.source === 'x') return this.t('scheduled.kind_x');
            return task.kind === 'reminder' ? this.t('scheduled.kind_reminder') : this.t('scheduled.kind_task');
        },

        detailLine(task) {
            if (!task || task.source !== 'cron') return '';
            const bits = [];
            if (task.recurring && task.timing) bits.push(this.t('scheduled.repeats') + ': ' + task.timing);
            if (task.destination) bits.push(this.t('scheduled.destination') + ': ' + task.destination);
            if (task.failure_count) bits.push(this.t('scheduled.failures') + ': ' + task.failure_count);
            if (task.delivery_state) {
                const key = 'scheduled.delivery_' + task.delivery_state;
                const word = this.t(key);
                bits.push(this.t('scheduled.last_delivery') + ': ' + (word !== key ? word : task.delivery_state));
            }
            if (task.edit_pending) bits.push(this.t('scheduled.edit_pending'));
            if (task.cancel_pending) bits.push(this.t('scheduled.cancel_pending'));
            return bits.join(' · ');
        },

        // Cron task form. `original` is the prompt as read from the server:
        // a prompt is sent back only when the operator changed it (SCH-S02).
        showCronModal: false,
        cronSaving: false,
        cronForm: { job_id: '', timing: '', prompt: '', original: null, version: null, kind: 'task', capabilities: 'read' },

        // X post form
        showXModal: false,
        xSaving: false,
        xForm: { id: null, text: '', when: '', version: null },

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
                    this.timezone = String(data.timezone || '');
                    this.tasksLoaded = true;
                } else {
                    window.showToast(data.error || window.kazmaT('scheduled.ui.load_failed', 'Failed to load scheduled tasks'), 'error');
                }
            } catch (e) {
                window.showToast(window.kazmaT('scheduled.ui.load_failed_error', 'Failed to load scheduled tasks: {error}', { error: e.message }), 'error');
            } finally {
                this.loading = false;
            }
        },

        // The controls are the server's: task.actions is what the state
        // machine allows for this row (never guessed from its status).
        canEdit(task) { return !!(task && Array.isArray(task.actions) && task.actions.includes('edit')); },
        canDelete(task) { return !!(task && Array.isArray(task.actions) && task.actions.includes('cancel')); },
        canRestore(task) { return !!(task && Array.isArray(task.actions) && task.actions.includes('restore')); },

        openCreateCron() {
            this.cronForm = { job_id: '', timing: '', prompt: '', original: null, version: null,
                              kind: 'task', capabilities: 'read' };
            this.showCronModal = true;
        },

        openCreateX() {
            this.xForm = { id: null, text: '', when: '', version: null };
            this.showXModal = true;
        },

        /* The edit dialog reads the WHOLE prompt from the server: the list
         * carries a display summary, and saving that back cut a 667-character
         * instruction to 200 and dropped a batch job's wrapper (SCH-S02). */
        async openEdit(task) {
            if (task.source === 'x') {
                this.xForm = { id: task.operation_id || task.id, text: task.summary, when: '', version: task.version };
                this.showXModal = true;
                return;
            }
            try {
                const resp = await fetch('/api/scheduled/cron/' + encodeURIComponent(task.id), { credentials: 'same-origin' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.ok || !data.job) {
                    window.showToast(data.error || window.kazmaT('scheduled.ui.load_failed', 'Failed to load scheduled tasks'), 'error');
                    return;
                }
                const job = data.job;
                this.cronForm = {
                    job_id: job.job_id, timing: job.timing || '', prompt: job.prompt || '',
                    original: job.prompt || '', originalTiming: job.timing || '', version: job.version,
                    kind: job.kind || 'task', capabilities: job.capabilities || 'read',
                };
                this.showCronModal = true;
            } catch (e) {
                window.showToast(window.kazmaT('scheduled.ui.load_failed_error', 'Failed to load scheduled tasks: {error}', { error: e.message }), 'error');
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

        /* The body an edit sends: only what changed, and the version it was
         * read at. A timing-only edit sends no prompt. */
        editBody(form) {
            const body = { expected_version: form.version };
            const timing = String(form.timing || '').trim();
            if (timing && timing !== String(form.originalTiming || '').trim()) body.timing = timing;
            const prompt = String(form.prompt || '');
            if (form.original !== null && prompt.trim() && prompt !== form.original) body.prompt = prompt.trim();
            return body;
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
            let body;
            if (isEdit) {
                body = this.editBody(this.cronForm);
                if (body.timing === undefined && body.prompt === undefined) {
                    this.showCronModal = false;
                    return;
                }
            } else {
                body = {
                    timing: this.cronForm.timing.trim(), prompt: this.cronForm.prompt.trim(),
                    kind: this.cronForm.kind, capabilities: this.cronForm.capabilities,
                };
            }
            this.cronSaving = true;
            try {
                const url = isEdit
                    ? '/api/scheduled/cron/' + encodeURIComponent(this.cronForm.job_id)
                    : '/api/scheduled/cron';
                const resp = await this._mutating(isEdit ? 'PUT' : 'POST', url, body);
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    const msg = resp.status === 202 ? window.t('scheduled.edit_pending')
                        : (isEdit ? window.t('scheduled.updated') : window.t('scheduled.created'));
                    window.showToast(msg, 'success');
                    this.showCronModal = false;
                    await this.load();
                } else {
                    window.showToast(this.errorFor(data), 'error');
                }
            } catch (e) {
                window.showToast(window.kazmaT('scheduled.ui.save_failed_error', 'Save failed: {error}', { error: e.message }), 'error');
            } finally {
                this.cronSaving = false;
            }
        },

        /* A refusal in the page's language when the server names its code. */
        errorFor(data) {
            const code = data && data.code;
            if (code) {
                const key = 'scheduled.err_' + code;
                const word = window.t ? window.t(key) : '';
                if (word && word !== key) return word;
            }
            return (data && data.error) || window.kazmaT('scheduled.ui.save_failed', 'Save failed');
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
                    // X Studio's revision-bound queue action (SCH-S04).
                    resp = await this._mutating('POST', '/api/x/queue/' + encodeURIComponent(this.xForm.id) + '/reschedule', {
                        when: this.xForm.when.trim(), expected_version: this.xForm.version,
                    });
                } else {
                    resp = await this._mutating('POST', '/api/scheduled/x', {
                        text: this.xForm.text.trim(),
                        when: this.xForm.when.trim(),
                    });
                }
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    window.showToast(isEdit ? window.t('scheduled.updated') : window.t('scheduled.x_booked'), 'success');
                    this.showXModal = false;
                    await this.load();
                } else {
                    window.showToast(this.errorFor(data), 'error');
                }
            } catch (e) {
                window.showToast(window.kazmaT('scheduled.ui.save_failed_error', 'Save failed: {error}', { error: e.message }), 'error');
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
                let resp;
                if (task.source === 'x') {
                    resp = await this._mutating('POST', '/api/x/queue/' + encodeURIComponent(task.operation_id || task.id) + '/cancel',
                        { expected_version: task.version });
                } else {
                    resp = await this._mutating('DELETE', '/api/scheduled/cron/' + encodeURIComponent(task.id)
                        + '?expected_version=' + encodeURIComponent(task.version));
                }
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    window.showToast(resp.status === 202 ? window.t('scheduled.cancel_pending') : window.t('scheduled.deleted'), 'success');
                    await this.load();
                } else {
                    window.showToast(this.errorFor(data) || window.kazmaT('scheduled.ui.delete_failed', 'Delete failed'), 'error');
                }
            } catch (e) {
                window.showToast(window.kazmaT('scheduled.ui.delete_failed_error', 'Delete failed: {error}', { error: e.message }), 'error');
            }
        },

        /* "Run again": a finished job comes back only by asking (SCH-S07). */
        async restore(task) {
            const timing = await window.kazmaPrompt({
                title: window.t('scheduled.restore'),
                message: window.t('scheduled.restore_when'),
                defaultValue: task.timing || '',
                confirmText: window.t('scheduled.restore'),
            });
            if (timing === null || timing === undefined || !String(timing).trim()) return;
            try {
                const resp = await this._mutating('POST', '/api/scheduled/cron/' + encodeURIComponent(task.id) + '/restore',
                    { timing: String(timing).trim(), expected_version: task.version });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    window.showToast(window.t('scheduled.restored'), 'success');
                    await this.load();
                } else {
                    window.showToast(this.errorFor(data), 'error');
                }
            } catch (e) {
                window.showToast(window.kazmaT('scheduled.ui.save_failed_error', 'Save failed: {error}', { error: e.message }), 'error');
            }
        },
    };
}

window.scheduledPage = scheduledPage;
window.SCHEDULED_STATUS_CLASS = STATUS_CLASS;
