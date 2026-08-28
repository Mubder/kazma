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
