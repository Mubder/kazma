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

  // Decorate a library row with the per-row UI strings (so Alpine x-text
  // binds can read them without re-fetching translations per row).
  function withStrings(lib) {
    return {
      ...lib,
      _t_chunks: S.chunks || "chunks",
      _t_auto_inject: S.auto_inject || "auto-inject",
      _t_ai_on: S.auto_inject_on || "Auto-inject ON",
      _t_ai_off: S.auto_inject_off || "Auto-inject OFF",
      _t_test: S.test || "🔍 Test",
      _t_refresh: S.refresh || "↻ Refresh",
      _t_browse: S.browse || "📋 Browse",
      _t_delete: S.delete || "🗑",
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

  return {
    loading: false,
    creating: false,
    libraries: [],
    form: { id: "", name: "", seed_url: "" },
    activeJob: null,    // latest ProgressUpdate from /api/kb/jobs/{id}
    _pollTimer: null,

    init() {
      // Register an in-flight-work predicate so the language switch warns
      // before reloading (which would orphan this crawl's polling timer).
      // Done in init() so the registry always points at *this* instance.
      if (window.__kazma_inflight_checkers) {
        window.__kazma_inflight_checkers.push(() => !!this.activeJob && !this.jobDone());
      }
      this.load();
    },

    async load() {
      this.loading = true;
      try {
        const r = await fetch("/api/kb/libraries");
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || "failed");
        this.libraries = (data.libraries || []).map(withStrings);
      } catch (e) {
        toast("Failed to load libraries: " + e.message, "error");
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
        toast(data.error || "create failed", "error");
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
        if (!data.ok) throw new Error(data.error || "ingest failed");

        if (mode === "page") {
          toast(`Ingested 1 page — ${data.chunks_new} new chunks.`, "success");
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

    _pollJob(jobId) {
      if (this._pollTimer) clearInterval(this._pollTimer);
      this._pollTimer = setInterval(async () => {
        try {
          const r = await fetch(`/api/kb/jobs/${jobId}`);
          const data = await r.json();
          if (!data.ok) return;
          this.activeJob = data.job;
          if (this.jobDone()) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
            await this.load();
            toast((S.crawl_finished || "Crawl finished: ") + (data.job.message || "done"), "success");
          }
        } catch (e) { /* keep polling */ }
      }, 2000);
    },

    jobTitle() {
      if (!this.activeJob) return "";
      const p = this.activeJob.phase;
      if (p === "done") return "✅ Done";
      if (p === "error") return "⚠️ Error";
      return "🕷️ Crawling…";
    },
    jobDone() {
      return this.activeJob && (this.activeJob.phase === "done" || this.activeJob.phase === "error");
    },

    resetForm() { this.form = { id: "", name: "", seed_url: "" }; },

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
        if (!data.ok) throw new Error(data.error || "search failed");
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
          if (!data.ok) throw new Error(data.error || "browse failed");
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
          cancelText: "Cancel",
        });
      }
      if (!ok) return;
      try {
        const r = await fetch(`/api/kb/libraries/${lib.id}/refresh`, { method: "POST" });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || "refresh failed");
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
        if (!data.ok) throw new Error(data.error || "update failed");
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
          confirmText: "Delete",
          cancelText: "Cancel",
          danger: true,
        });
      }
      if (!ok) return;
      try {
        const r = await fetch(`/api/kb/libraries/${lib.id}`, { method: "DELETE" });
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || "delete failed");
        toast(S.library_deleted || "Library deleted.", "success");
        await this.load();
      } catch (e) {
        toast(e.message, "error");
      }
    },
  };
}
