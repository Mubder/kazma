/**
 * Knowledge Library page logic.
 *
 * Backed by /api/kb/* (see kazma_ui/kb_api.py). Uses the unified toast +
 * modal helpers (window.showToast / window.kazmaConfirm) per AGENTS.md —
 * never native browser dialogs.
 */
function knowledgePage() {
  return {
    loading: false,
    creating: false,
    libraries: [],
    form: { id: "", name: "", seed_url: "" },
    activeJob: null,    // latest ProgressUpdate from /api/kb/jobs/{id}
    _pollTimer: null,

    async load() {
      this.loading = true;
      try {
        const r = await fetch("/api/kb/libraries");
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || "failed");
        // Augment each library with UI-only state (search/browse panels).
        this.libraries = (data.libraries || []).map((lib) => ({
          ...lib,
          _search_open: false,
          _query: "",
          _searching: false,
          _hits: null,
          _browse_open: false,
          _chunks: null,
          _chunk_total: 0,
        }));
      } catch (e) {
        window.showToast ? window.showToast("Failed to load libraries: " + e.message, "error")
                         : console.error(e);
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
        window.showToast ? window.showToast(data.error || "create failed", "error")
                         : console.error(data);
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
          window.showToast
            ? window.showToast(`Ingested 1 page — ${data.chunks_new} new chunks.`, "success")
            : null;
          this.resetForm();
          await this.load();
        } else {
          // Site crawl: start polling the job.
          this.activeJob = { phase: "starting", message: "starting…", library_id: lib_id };
          this._pollJob(data.job_id);
          window.showToast
            ? window.showToast("Crawl started — watch progress below.", "info")
            : null;
          this.resetForm();
        }
      } catch (e) {
        window.showToast ? window.showToast(e.message, "error") : console.error(e);
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
            window.showToast
              ? window.showToast("Crawl finished: " + (data.job.message || "done"), "success")
              : null;
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
        window.showToast
          ? window.showToast(
              value
                ? "Auto-inject ON — chunks from this library will be folded into every prompt."
                : "Auto-inject OFF.",
              value ? "success" : "info",
            )
          : null;
      } catch (e) {
        lib.auto_inject = prev;
        window.showToast ? window.showToast(e.message, "error") : console.error(e);
      }
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
        window.showToast ? window.showToast(e.message, "error") : console.error(e);
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
          window.showToast ? window.showToast(e.message, "error") : console.error(e);
        }
      }
    },

    async refresh(lib) {
      if (!lib.seed_url) return;
      let ok = true;
      if (window.kazmaConfirm) {
        ok = await window.kazmaConfirm({
          title: "Re-ingest library?",
          message: `This will re-crawl ${lib.seed_url}. Only changed pages are re-indexed (content-hash dedup).`,
          confirmText: "Refresh",
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
        window.showToast ? window.showToast("Refresh started.", "info") : null;
      } catch (e) {
        window.showToast ? window.showToast(e.message, "error") : console.error(e);
      }
    },

    async del(lib) {
      let ok = true;
      if (window.kazmaConfirm) {
        ok = await window.kazmaConfirm({
          title: `Delete "${lib.name}"?`,
          message: `This removes the library and all ${lib.chunk_count} of its chunks. Cannot be undone.`,
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
        window.showToast ? window.showToast("Library deleted.", "success") : null;
        await this.load();
      } catch (e) {
        window.showToast ? window.showToast(e.message, "error") : console.error(e);
      }
    },
  };
}
