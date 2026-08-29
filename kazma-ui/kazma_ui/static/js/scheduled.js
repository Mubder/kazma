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

        // Upcoming by default. The page previously showed everything at
        // once, so a single pending task sat under forty finished ones and
        // the thing you came to check was the hardest thing to find.
        filter: 'upcoming',

        // X activity — the audit log, moved here from Settings. It answers
        // "what did Kazma actually post", which is an operational question,
        // not a configuration one: it belongs beside the schedule that
        // produced it rather than inside the connector setup form.
        audit: { entries: [], loading: false, open: null, loaded: false },

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
            if (this.filter === 'all') return this.tasks;
            if (this.filter === 'history') {
                return this.tasks.filter(t => !['pending', 'running'].includes(t.status || ''));
            }
            return this.tasks.filter(t => ['pending', 'running'].includes(t.status || ''));
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
         * inside a table. */
        cleanSummary(task) {
            return String((task && task.summary) || '').replace(/\s+/g, ' ').trim() || '—';
        },

        /* Cron failures carry their reason in last_result, which the page
         * never showed: a row said "failed" and nothing else. */
        reason(task) {
            return String((task && (task.error || task.last_result)) || '').replace(/\s+/g, ' ').trim();
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

        /* The exact bytes sent and returned. Naming a failure is not the
         * same as diagnosing one, and this is the only place the payload
         * is visible. */
        auditDetail() {
            const e = (this.audit.open != null && this.audit.entries[this.audit.open]) || null;
            if (!e) return '';
            const parts = [
                'REQUEST', e.request_body || '(none)',
                '', 'RESPONSE', e.response_body || '(none)',
            ];
            if (e.error_detail) parts.push('', 'ERROR', e.error_detail);
            return parts.join(String.fromCharCode(10));
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
