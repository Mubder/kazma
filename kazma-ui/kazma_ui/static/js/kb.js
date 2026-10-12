/**
 * Knowledge Library page logic.
 *
 * Backed by /api/kb/* (see kazma_ui/kb_api.py). Uses the unified toast +
 * modal helpers (window.showToast / window.kazmaConfirm) per AGENTS.md —
 * never native browser dialogs.
 *
 * Translations come from window.__KB_STRINGS, injected by the server in
 * knowledge_base.html so the JS-driven per-row strings stay in sync with
 * the Jinja-driven static strings (same i18n source).
 */

// ── In-flight work registry ─────────────────────────────────────────────
// Language switch / page reload can silently orphan a running crawl's UI
// handle.  Components register a predicate here; toggleLanguage() consults
// window.kazmaHasInflightWork() before reloading.
(function () {
  if (typeof window === "undefined") return;
  if (!window.__kazma_inflight_checkers) window.__kazma_inflight_checkers = [];
  // kazmaHasInflightWork is the public read API; do not redefine if another
  // module already installed it.
  if (typeof window.kazmaHasInflightWork !== "function") {
    window.kazmaHasInflightWork = function () {
      return window.__kazma_inflight_checkers.some(function (fn) {
        try { return !!fn(); } catch (e) { return false; }
      });
    };
  }
})();

function knowledgePage() {
  const S = window.__KB_STRINGS || {};

  // How much of a library meaning search reaches (keyword search reaches
  // all of it); empty when the server did not say.
  function meaningLabel(m) {
    if (!m) return "";
    if (!m.available) return S.keyword_only || "Keyword search only";
    return (S.meaning_search || "Meaning search: {v} of {n}")
      .replace("{v}", String(m.vectors))
      .replace("{n}", String(m.chunks));
  }

  // Decorate a library row with the per-row UI strings (so Alpine x-text
  // binds can read them without re-fetching translations per row).
  // "4539 مقطعاً" / "4539 chunks": the catalog's plural forms for the count.
  function chunkCountLabel(n) {
    const f = window.KazmaFormat;
    const forms = S.chunks_count || {};
    return f && f.count ? f.count(forms, n, "{n} chunks") : String(n) + " " + (S.chunks || "chunks");
  }

  function withStrings(lib) {
    return {
      ...lib,
      _t_meaning: meaningLabel(lib.meaning_search),
      _t_chunks: S.chunks || "chunks",
      _t_chunk_count: chunkCountLabel(lib.chunk_count),
      _t_auto_inject: S.auto_inject || "auto-inject",
      _t_ai_on: S.auto_inject_on || "Auto-inject ON",
      _t_ai_off: S.auto_inject_off || "Auto-inject OFF",
      _t_test: S.test || "Test",
      _t_refresh: S.refresh || "Refresh",
      _t_browse: S.browse || "Browse",
      _t_archive: S.archive_label || "Archive",
      _t_unarchive: S.unarchive_label || "Restore",
      _t_delete: S.delete || "Delete",
      _t_search_placeholder: S.search_placeholder || "Ask something…",
      _t_search_btn: S.search_btn || "Search",
      _t_searching: S.searching || "searching…",
      // UI-only panel state
      _search_open: false,
      _query: "",
      _searching: false,
      _hits: null,
      _browse_open: false,
      _chunks: null,
      _chunk_total: 0,
    };
  }

  function toast(msg, type) {
    if (window.showToast) window.showToast(msg, type);
    else console.log(`[${type}] ${msg}`);
  }
  // Text built here: the catalog's text in the page's language, else the
  // English given; {name} placeholders filled from vars.
  function _k(key, en, vars) {
    if (typeof window.kazmaT === "function") return window.kazmaT(key, en, vars);
    let s = en;
    if (vars) for (const v in vars) s = s.split("{" + v + "}").join(String(vars[v]));
    return s;
  }

  return {
    // The page's own strings, for the template's `S.tab_active` and friends.
    // `S` was a closure constant only, so those bindings threw "S is not
    // defined" on every load and the tab labels fell to nothing (2026-09-26,
    // tests/e2e/test_pages_load_clean.py).
    S,
    // A chunk count with its noun in the page's language (the browse view's
    // "100 of 4539 chunks").
    chunkCount(n) {
      return chunkCountLabel(n);
    },
    loading: false,
    creating: false,
    libraries: [],
    viewArchived: false,   // Active (false) / Archived (true) tab state
    form: { id: "", name: "", seed_url: "" },
    activeJob: null,    // latest ProgressUpdate from /api/kb/jobs/{id}
    _pollTimer: null,
    _pollBusy: false,
    _pollEpoch: 0,
    _activeJobId: null,
    _restoreTimer: null,
    _pollAbort: null,
    _restoreAbort: null,
    _resumeAbort: null,
    _creditsAbort: null,
    _destroyed: false,

    init() {
      // Register an in-flight-work predicate so the language switch warns
      // before reloading (which would orphan this crawl's polling timer).
      // Done in init() so the registry always points at *this* instance.
      if (window.__kazma_inflight_checkers) {
        this._inflightChecker = () => !!this.activeJob && !this.jobDone();
        window.__kazma_inflight_checkers.push(this._inflightChecker);
      }
      this.load();
      this.restoreCrawl();
    },

    destroy() {
      this._destroyed = true;
      ++this._pollEpoch;
      if (this._pollTimer) clearInterval(this._pollTimer);
      if (this._restoreTimer) clearTimeout(this._restoreTimer);
      if (this._pollAbort) this._pollAbort.abort();
      if (this._restoreAbort) this._restoreAbort.abort();
      if (this._resumeAbort) this._resumeAbort.abort();
      if (this._creditsAbort) this._creditsAbort.abort();
      if (this._inflightChecker && window.__kazma_inflight_checkers) {
        const index = window.__kazma_inflight_checkers.indexOf(this._inflightChecker);
        if (index !== -1) window.__kazma_inflight_checkers.splice(index, 1);
      }
    },

    async load() {
      this.loading = true;
      try {
        // Fetch active or archived depending on the current tab.
        const url = this.viewArchived
          ? "/api/kb/libraries/archived/list"
          : "/api/kb/libraries";
        const r = await fetch(url);
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || "failed");
        this.libraries = (data.libraries || []).map(withStrings);
      } catch (e) {
        toast(_k("knowledge.ui.load_failed", "Failed to load libraries: {error}", { error: e.message }), "error");
      } finally {
        this.loading = false;
      }
    },

    async ingest(mode) {
      if (!this.form.id || !this.form.seed_url) return;
      const lib_id = this.form.id.trim();
      const name = (this.form.name || lib_id).trim();
      const url = this.form.seed_url.trim();

      // Create the library first.
      let r = await fetch("/api/kb/libraries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: lib_id, name: name, seed_url: url }),
      });
      let data = await r.json();
      if (!data.ok && !(data.error || "").includes("already exists")) {
        toast(data.error || _k("knowledge.ui.create_failed", "Create failed"), "error");
        return;
      }

      this.creating = true;
      try {
        r = await fetch("/api/kb/ingest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ library_id: lib_id, url: url, mode: mode }),
        });
        data = await r.json();
        if (!data.ok) throw new Error(data.error || _k("knowledge.ui.ingest_failed", "Ingest failed"));

        if (mode === "page") {
          // Page ingest: show clean toast from structured fields, not raw internal message.
          if (data.chunks_new > 0) {
            toast(window.kazmaCount("knowledge.page_ingested", data.chunks_new), "success");
          } else if (data.errors && data.errors.length) {
            toast((S.page_ingested_failed || "Ingest failed: {error}").replace("{error}", data.errors[0]), "error");
          } else {
            toast(window.kazmaCount("knowledge.page_ingested", 0), "info");
          }
          this.resetForm();
          await this.load();
        } else {
          // Site crawl: start polling the job.
          this.activeJob = { phase: "starting", message: "starting…", library_id: lib_id };
          this._pollJob(data.job_id);
          toast(S.crawl_started || "Crawl started.", "info");
          this.resetForm();
        }
      } catch (e) {
        toast(e.message, "error");
      } finally {
        this.creating = false;
      }
    },

    async restoreCrawl() {
      if (this._destroyed || this._restoreAbort) return;
      const controller = new AbortController();
      this._restoreAbort = controller;
      const deadline = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch('/api/kb/jobs', { signal: controller.signal });
        const data = await response.json();
        if (this._destroyed) return;
        if (!data.ok) throw new Error(data.error || _k('knowledge.status_unavailable', 'Crawl status unavailable; retrying'));
        const rows = data.jobs || [];
        const root = typeof document !== 'undefined' && document.getElementById ? document.getElementById('knowledge-root') : null;
        const requested = root && typeof root.getAttribute === 'function' ? root.getAttribute('data-job') :
          ((typeof URLSearchParams !== 'undefined' && window.location) ? new URLSearchParams(window.location.search).get('job') : null);
        const pending = rows.find(row => row.status === 'running' || row.status === 'interrupted');
        const job = requested ? rows.find(row => row.job_id === requested) : (pending || rows[0]);
        if (requested && !job) throw new Error(_k('knowledge.status_unavailable', 'Crawl status unavailable; retrying'));  // API sorts retained outcomes newest first.
        if (job && !this._activeJobId) this._pollJob(job.job_id, !!pending);
        else if (!this._activeJobId && this.activeJob?.poll_error) this.activeJob = null;
      } catch (error) {
        if (!this._destroyed && !this._activeJobId) {
          this.activeJob = { phase: 'error', poll_error: error.message || _k('knowledge.status_unavailable', 'Crawl status unavailable; retrying') };
          this._restoreTimer = setTimeout(() => this.restoreCrawl(), 5000);
        }
      } finally {
        clearTimeout(deadline);
        this._restoreAbort = null;
      }
    },

    async resumeCrawl() {
      if (this._destroyed || !this.activeJob || !this.activeJob.resume_available) return;
      const jobId = this.activeJob.job_id;
      this.activeJob.resume_available = false;
      const controller = new AbortController();
      this._resumeAbort = controller;
      const deadline = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(`/api/kb/jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST', signal: controller.signal });
        const data = await response.json();
        if (this._destroyed) return;
        if (!data.ok) throw new Error(data.error || _k('knowledge.resume_failed', 'Could not resume crawl'));
        this.activeJob = { ...this.activeJob, phase: 'starting', poll_error: '' };
        this._pollJob(data.job_id);
      } catch (error) {
        if (this._destroyed) return;
        this.activeJob.resume_available = true;
        this.activeJob.poll_error = error.message;
      } finally {
        clearTimeout(deadline);
        this._resumeAbort = null;
      }
    },

    async refreshCredits() {
      if (this._destroyed || this._creditsAbort) return;
      const controller = new AbortController();
      this._creditsAbort = controller;
      const deadline = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch('/api/kb/services/firecrawl/credits/refresh', { method: 'POST', signal: controller.signal });
        const data = await response.json();
        if (this._destroyed) return;
        if (!data.ok) throw new Error(data.error || _k('knowledge.credits_unavailable', 'Credit balance unavailable'));
        if (this.activeJob?.acquisition) this.activeJob.acquisition.provider_balances = data.balances;
      } catch (error) {
        if (!this._destroyed && this.activeJob) this.activeJob.poll_error = error.message;
      } finally {
        clearTimeout(deadline);
        this._creditsAbort = null;
      }
    },

    _pollJob(jobId, notify = true) {
      if (this._destroyed) return;
      this._activeJobId = jobId;
      if (this._restoreTimer) clearTimeout(this._restoreTimer);
      const epoch = ++this._pollEpoch;
      if (this._pollTimer) clearInterval(this._pollTimer);
      this._pollTimer = setInterval(async () => {
        if (this._pollBusy || epoch !== this._pollEpoch) return;
        this._pollBusy = true;
        const controller = new AbortController();
        this._pollAbort = controller;
        const deadline = setTimeout(() => controller.abort(), 10000);
        try {
          const r = await fetch(`/api/kb/jobs/${encodeURIComponent(jobId)}`, { signal: controller.signal });
          const data = await r.json();
          if (epoch !== this._pollEpoch) return;
          if (!data.ok) throw new Error(data.error || _k('knowledge.status_unavailable', 'Crawl status unavailable; retrying'));
          this.activeJob = { ...data.job, poll_error: '' };
          if (this.jobDone()) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
            await this.load();
            // Clean toast from STRUCTURED fields, not the raw internal job
            // message (which used to dump "done: 8/200 pages, 210 chunks
            // (+0 deduped), 113 failed — first failure: ..." into the toast).
            const j = data.job || {};
            if (!notify) return;  // Reopening a retained receipt is not a new completion.
            if (j.phase === 'interrupted') return;
            if (j.phase === 'error') {
              toast(j.message || _k('knowledge.phase_error', 'Error'), 'error');
              return;
            }
            const fetched = j.fetched || 0;
            const discovered = j.discovered || 0;
            const ingested = j.ingested || 0;
            const failed = j.failed || 0;
            const unchanged = j.pages_unchanged || 0;
            const pruned = j.pruned_urls || 0;
            const extra =
              (unchanged ? " · " + _k("knowledge.crawl_unchanged", "{n} unchanged", { n: unchanged }) : "") +
              (pruned ? " · " + _k("knowledge.crawl_pruned", "{n} pruned", { n: pruned }) : "");
            // Each count in its plural form: "200 pages", "210 مقاطع".
            const pages = window.kazmaCount("knowledge.pages_count", discovered);
            const chunks = chunkCountLabel(ingested);
            if (ingested === 0 && fetched === 0) {
              toast(S.crawl_finished_empty || "Crawl finished but no pages were ingested.", "warning");
            } else if (j.acquisition && j.acquisition.status === 'partial' && failed === 0) {
              toast(_k('knowledge.discovery_incomplete', 'Crawl finished with incomplete discovery; review the retained outcomes.'), 'warning');
            } else if (failed > 0) {
              toast(
                (S.crawl_finished_partial || "Crawl finished: {fetched}/{pages} · {chunks} · {failed} failed")
                  .replace("{fetched}", fetched).replace("{pages}", pages)
                  .replace("{chunks}", chunks).replace("{failed}", failed) + extra,
                "warning",
              );
            } else {
              toast(
                (S.crawl_finished_ok || "Crawl finished: {fetched}/{pages} · {chunks}")
                  .replace("{fetched}", fetched).replace("{pages}", pages)
                  .replace("{chunks}", chunks) + extra,
                "success",
              );
            }
          }
        } catch (error) {
          if (epoch === this._pollEpoch) {
            this.activeJob = { ...(this.activeJob || {}), poll_error: error.message || _k('knowledge.status_unavailable', 'Crawl status unavailable; retrying') };
          }
        } finally {
          clearTimeout(deadline);
          this._pollAbort = null;
          this._pollBusy = false;
        }
      }, 2000);
    },

    jobTitle() {
      if (!this.activeJob) return "";
      const p = this.activeJob.phase;
      if (p === "done") return _k("knowledge.phase_done", "Done");
      if (p === "error") return _k("knowledge.phase_error", "Error");
      if (p === "interrupted") return _k("knowledge.phase_interrupted", "Interrupted");
      return _k("knowledge.phase_running", "Crawling…");
    },
    jobIcon() {
      if (!this.activeJob) return "info";
      if (this.activeJob.phase === "done") return "check-circle";
      if (["error", "interrupted"].includes(this.activeJob.phase)) return "alert";
      return "spider";
    },
    jobDone() {
      return this.activeJob && ["done", "error", "interrupted"].includes(this.activeJob.phase);
    },

    resetForm() { this.form = { id: "", name: "", seed_url: "" }; },

    switchTab(toArchived) {
      if (this.viewArchived === toArchived) return;
      this.viewArchived = toArchived;
      this.load();
    },

    async archive(lib) {
      // Archive hides the library from the Active list without deleting
      // its chunks (they stay searchable). Useful for failed/abandoned
      // crawls. Mirrors the Research panel's archive pattern.
      try {
        const r = await fetch(`/api/kb/libraries/${lib.id}/archive`, { method: "POST" });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || _k("knowledge.ui.archive_failed", "Archive failed"));
        toast(S.archived_msg || "Library archived.", "info");
        await this.load();
      } catch (e) {
        toast(e.message, "error");
      }
    },

    async unarchive(lib) {
      try {
        const r = await fetch(`/api/kb/libraries/${lib.id}/unarchive`, { method: "POST" });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || _k("knowledge.ui.unarchive_failed", "Unarchive failed"));
        toast(S.restored_msg || "Library restored.", "success");
        await this.load();
      } catch (e) {
        toast(e.message, "error");
      }
    },

    testSearch(lib) {
      lib._search_open = !lib._search_open;
      lib._browse_open = false;
    },
    async runSearch(lib) {
      if (!lib._query || !lib._query.trim()) return;
      lib._searching = true;
      lib._hits = null;
      try {
        const r = await fetch("/api/kb/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ library_id: lib.id, query: lib._query, top_k: 5 }),
        });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || _k("knowledge.ui.search_failed", "Search failed"));
        lib._hits = data.hits;
      } catch (e) {
        toast(e.message, "error");
      } finally {
        lib._searching = false;
      }
    },

    async browse(lib) {
      lib._browse_open = !lib._browse_open;
      lib._search_open = false;
      if (lib._browse_open && !lib._chunks) {
        try {
          const r = await fetch(`/api/kb/libraries/${lib.id}/chunks?limit=100`);
          const data = await r.json();
          if (!data.ok) throw new Error(data.error || _k("knowledge.ui.browse_failed", "Browse failed"));
          lib._chunks = data.chunks;
          lib._chunk_total = data.total;
        } catch (e) {
          toast(e.message, "error");
        }
      }
    },

    async refresh(lib) {
      if (!lib.seed_url) return;
      let ok = true;
      if (window.kazmaConfirm) {
        ok = await window.kazmaConfirm({
          title: (S.refresh_confirm_title || "Re-ingest library?"),
          message: (S.refresh_confirm_msg || "Re-crawl seed."),
          confirmText: lib._t_refresh,
          cancelText: _k("knowledge.ui.cancel", "Cancel"),
        });
      }
      if (!ok) return;
      try {
        const r = await fetch(`/api/kb/libraries/${lib.id}/refresh`, { method: "POST" });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || _k("knowledge.ui.refresh_failed", "Refresh failed"));
        this.activeJob = { phase: "starting", message: "refreshing…", library_id: lib.id };
        this._pollJob(data.job_id);
        toast(S.refresh_started || "Refresh started.", "info");
      } catch (e) {
        toast(e.message, "error");
      }
    },

    async toggleAutoInject(lib, value) {
      // Optimistic toggle; revert on failure.
      const prev = lib.auto_inject;
      lib.auto_inject = value;
      try {
        const r = await fetch(`/api/kb/libraries/${lib.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auto_inject: value }),
        });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || _k("knowledge.ui.update_failed", "Update failed"));
        toast(
          value ? (S.auto_on_msg || "Auto-inject ON.") : (S.auto_off_msg || "Auto-inject OFF."),
          value ? "success" : "info",
        );
      } catch (e) {
        lib.auto_inject = prev;
        toast(e.message, "error");
      }
    },

    async del(lib) {
      let ok = true;
      if (window.kazmaConfirm) {
        const title = (S.delete_confirm_title || "Delete \"{name}\"?").replace("{name}", lib.name);
        const msg = (S.delete_confirm_msg || "Delete {n} chunks.").replace("{n}", lib.chunk_count);
        ok = await window.kazmaConfirm({
          title: title,
          message: msg,
          confirmText: _k("knowledge.ui.delete", "Delete"),
          cancelText: _k("knowledge.ui.cancel", "Cancel"),
          danger: true,
        });
      }
      if (!ok) return;
      try {
        const r = await fetch(`/api/kb/libraries/${lib.id}`, { method: "DELETE" });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || _k("knowledge.ui.delete_failed", "Delete failed"));
        toast(S.library_deleted || "Library deleted.", "success");
        await this.load();
      } catch (e) {
        toast(e.message, "error");
      }
    },
  };
}
if (typeof window !== "undefined") window.knowledgePage = knowledgePage;
