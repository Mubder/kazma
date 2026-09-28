/**
 * Documents page logic.
 *
 * Backed by /api/documents/* (see kazma_ui/documents_api.py). Every operation
 * delegates to the shared DocumentIngestionService; this file adds no parsing
 * or business logic. Uses the unified toast helper (window.showToast) and the
 * unified confirm helper (window.kazmaConfirm) — never native dialogs.
 */

function documentsPage() {
  return {
    documents: [],
    selected: null,
    versions: [],
    jobs: [],
    artifacts: [],
    preview: "",
    pageCount: 0,
    currentState: "",
    health: null,
    dragover: false,
    uploading: false,
    forceOcr: false,
    libraryId: "",
    libraries: [],
    _librariesLoaded: false,
    events: [],
    eventsFor: null,
    convertFormat: "pdf",
    splitStart: 1,
    splitEnd: 0,
    acting: false,
    _poll: null,
    // Phase 9 operations panel
    capacity: null,
    ops: null,
    readiness: null,
    auditEvents: [],
    gcReport: null,
    maintenanceRunning: false,

    toast(msg, type = "info") {
      if (window.showToast) window.showToast(msg, type);
      else console.log(`[documents:${type}]`, msg);
    },

    async init() {
      await this.loadDocuments();
      await this.loadHealth();
      await this.loadOps();
    },

    stateClass(state) {
      if (!state) return "state-idle";
      if (state === "ready") return "state-ready";
      if (["dead_letter", "rejected", "cancelled"].includes(state)) return "state-fail";
      return "state-active";
    },

    capClass(readiness) {
      if (readiness === "ready") return "cap-ready";
      if (readiness === "degraded") return "cap-degraded";
      return "cap-unavailable";
    },

    async loadDocuments() {
      try {
        const r = await fetch("/api/documents");
        const j = await r.json();
        if (j.ok) this.documents = j.documents || [];
      } catch (e) {
        this.toast(kazmaT('documents.js.failed_to_load_documents', "Failed to load documents"), "error");
      }
    },

    async loadHealth() {
      try {
        const r = await fetch("/api/documents/health");
        const j = await r.json();
        if (j.ok) this.health = j.health;
      } catch (e) {
        /* non-fatal */
      }
    },

    async loadOps() {
      // Capacity/queue snapshot, storage metrics, readiness, and audit page.
      try {
        const [cap, met, rdy, aud] = await Promise.all([
          fetch("/api/documents/ops/capacity").then((r) => r.json()).catch(() => ({})),
          fetch("/api/documents/ops/metrics").then((r) => r.json()).catch(() => ({})),
          fetch("/api/documents/ops/readiness").then((r) => r.json()).catch(() => ({})),
          fetch("/api/documents/ops/audit?limit=25").then((r) => r.json()).catch(() => ({})),
        ]);
        if (cap.ok) this.capacity = cap.capacity;
        if (met.ok) this.ops = met.metrics;
        if (rdy.ok) this.readiness = rdy.readiness;
        if (aud.ok) this.auditEvents = aud.events || [];
      } catch (e) {
        /* non-fatal */
      }
    },

    fmtBytes(n) {
      if (n === null || n === undefined) return "—";
      const u = ["B", "KB", "MB", "GB", "TB"];
      let i = 0;
      let v = Number(n);
      while (v >= 1024 && i < u.length - 1) {
        v /= 1024;
        i++;
      }
      return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
    },

    capStatusClass(status) {
      if (status === "ok") return "cap-ready";
      if (status === "degraded") return "cap-degraded";
      return "cap-unavailable";
    },

    async runGc() {
      // Always dry-run first, then confirm with kazmaConfirm before deleting.
      if (this.maintenanceRunning) return;
      this.maintenanceRunning = true;
      try {
        const dr = await fetch("/api/documents/ops/maintenance/dry-run", {
          method: "POST",
        });
        if (dr.status === 401 || dr.status === 403) {
          this.toast(kazmaT('documents.js.admin_privileges_required_to_run', "Admin privileges required to run garbage collection"), "error");
          return;
        }
        const dj = await dr.json();
        if (!dj.ok) {
          this.toast(kazmaT('documents.js.garbage_collection_dry_run_failed', "Garbage-collection dry-run failed"), "error");
          return;
        }
        this.gcReport = dj.report;
        const rep = dj.report || {};
        const wouldDelete =
          (rep.deleted_blobs || 0) +
          (rep.deleted_manifests || 0) +
          (rep.deleted_blob_rows || 0) +
          (rep.deleted_staging || 0);
        if (wouldDelete === 0) {
          this.toast(kazmaT('documents.js.nothing_to_reclaim_the_store', "Nothing to reclaim — the store is already clean"), "info");
          await this.loadOps();
          return;
        }
        const proceed = await window.kazmaConfirm({
          title: kazmaT('documents.js.run_garbage_collection', "Run garbage collection?"),
          message: kazmaT('documents.js.gc_confirm', 'Dry-run found {n} item(s) to delete (~{size} reclaimable). Referenced content and current versions are never removed. Proceed?', { n: wouldDelete, size: this.fmtBytes(rep.reclaimed_bytes) }),
          confirmText: kazmaT('documents.js.run_gc', "Run GC"),
          cancelText: kazmaT('common.cancel', "Cancel"),
          danger: true,
        });
        if (!proceed) return;
        const rr = await fetch("/api/documents/ops/maintenance/run", { method: "POST" });
        const rj = await rr.json();
        if (rj.ok) {
          this.gcReport = rj.report;
          this.toast(
            kazmaT('documents.js.gc_done', 'GC reclaimed {n} blob(s), {size}', { n: rj.report.deleted_blobs, size: this.fmtBytes(rj.report.reclaimed_bytes) }),
            "success",
          );
          await this.loadOps();
        } else {
          this.toast(kazmaT('documents.js.garbage_collection_failed', "Garbage collection failed"), "error");
        }
      } catch (e) {
        this.toast(kazmaT('documents.js.garbage_collection_error', "Garbage collection error"), "error");
      } finally {
        this.maintenanceRunning = false;
      }
    },

    onPick(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (file) this.upload(file);
      ev.target.value = "";
    },

    onDrop(ev) {
      this.dragover = false;
      const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (file) this.upload(file);
    },

    async upload(file) {
      this.uploading = true;
      try {
        // Encode filename: HTTP headers are Latin-1 only. Arabic/emoji/smart
        // quotes in PDF names used to throw in fetch → bare "Upload failed".
        const encodedName = encodeURIComponent(file.name || "upload.bin");
        const qs = new URLSearchParams();
        if (this.forceOcr) qs.set("force_ocr", "1");
        qs.set("filename", encodedName);
        const url = "/api/documents?" + qs.toString();
        const r = await fetch(url, {
          method: "POST",
          headers: {
            "X-Document-Filename": encodedName,
            "Content-Type": "application/octet-stream",
            Accept: "application/json",
          },
          body: file,
        });
        let j = {};
        try {
          j = await r.json();
        } catch (_) {
          this.toast(
            r.status === 401
              ? kazmaT('documents.js.upload_not_authed', 'Upload failed: not authenticated (re-login / set secret)')
              : kazmaT('documents.js.upload_http', 'Upload failed (HTTP {status})', { status: r.status || 'network' }),
            "error",
          );
          return;
        }
        if (!r.ok || !j.ok) {
          const msg =
            j.error ||
            (r.status === 401
              ? kazmaT('documents.js.not_authed', 'Not authenticated — re-login or check KAZMA_SECRET')
              : kazmaT('documents.js.upload_http', 'Upload failed (HTTP {status})', { status: r.status }));
          this.toast(msg, "error");
          return;
        }
        this.toast(kazmaT('documents.js.uploaded_processing_started', "Uploaded — processing started"), "success");
        await this.loadDocuments();
        await this.openDocument(j.document_id);
      } catch (e) {
        console.warn("[documents] upload error", e);
        this.toast(
          e && e.message
            ? kazmaT('documents.js.upload_failed_msg', 'Upload failed: {error}', { error: e.message })
            : kazmaT('documents.js.upload_failed_network', 'Upload failed (network)'),
          "error",
        );
      } finally {
        this.uploading = false;
      }
    },

    async openDocument(documentId) {
      this.selected = this.documents.find((d) => d.document_id === documentId) || {
        document_id: documentId,
        title: documentId,
      };
      this.eventsFor = null;
      await this.refreshDetail();
      this._startPoll();
    },

    async refreshDetail() {
      if (!this.selected) return;
      try {
        const r = await fetch(`/api/documents/${this.selected.document_id}`);
        const j = await r.json();
        if (!j.ok) {
          this.toast(j.error || kazmaT('documents.js.failed_to_load_document', "Failed to load document"), "error");
          return;
        }
        const doc = j.document;
        this.versions = doc.versions || [];
        this.jobs = doc.jobs || [];
        this.artifacts = doc.artifacts || [];
        this.currentState = this.jobs.length ? this.jobs[0].state : "";
        if (this.currentState === "ready") {
          await this.loadContent();
        } else {
          this.preview = "";
          this.pageCount = 0;
        }
      } catch (e) {
        this.toast(kazmaT('documents.js.failed_to_load_document', "Failed to load document"), "error");
      }
    },

    async loadContent() {
      try {
        const r = await fetch(`/api/documents/${this.selected.document_id}/content?max_chars=8000`);
        const j = await r.json();
        if (j.ok) {
          this.preview = j.content.text || "";
          this.pageCount = j.content.page_count || 0;
        }
      } catch (e) {
        /* non-fatal */
      }
    },

    _startPoll() {
      this._stopPoll();
      this._poll = setInterval(async () => {
        if (!this.selected) return this._stopPoll();
        const terminal = ["ready", "cancelled", "dead_letter", "rejected"];
        if (this.currentState && terminal.includes(this.currentState)) {
          return this._stopPoll();
        }
        await this.refreshDetail();
        await this.loadDocuments();
      }, 1500);
    },

    _stopPoll() {
      if (this._poll) {
        clearInterval(this._poll);
        this._poll = null;
      }
    },

    async showEvents(jobId) {
      if (this.eventsFor === jobId) {
        this.eventsFor = null;
        return;
      }
      try {
        const r = await fetch(`/api/documents/jobs/${jobId}/events`);
        const j = await r.json();
        if (j.ok) {
          this.events = j.events || [];
          this.eventsFor = jobId;
        }
      } catch (e) {
        this.toast(kazmaT('documents.js.failed_to_load_events', "Failed to load events"), "error");
      }
    },

    async cancelJob(jobId) {
      try {
        const r = await fetch(`/api/documents/jobs/${jobId}/cancel`, { method: "POST" });
        const j = await r.json();
        if (j.ok) {
          this.toast(kazmaT('documents.js.cancellation_requested', "Cancellation requested"), "info");
          await this.refreshDetail();
        } else {
          this.toast(j.error || kazmaT('documents.js.cancel_failed', "Cancel failed"), "error");
        }
      } catch (e) {
        this.toast(kazmaT('documents.js.cancel_failed', "Cancel failed"), "error");
      }
    },

    async retryJob(jobId) {
      try {
        const r = await fetch(`/api/documents/jobs/${jobId}/retry`, { method: "POST" });
        const j = await r.json();
        if (j.ok) {
          this.toast(kazmaT('documents.js.retry_enqueued', "Retry enqueued"), "success");
          await this.refreshDetail();
          this._startPoll();
        } else {
          this.toast(j.error || kazmaT('documents.js.retry_failed', "Retry failed"), "error");
        }
      } catch (e) {
        this.toast(kazmaT('documents.js.retry_failed', "Retry failed"), "error");
      }
    },

    rendererReady() {
      const rs = (this.health && this.health.renderers) || [];
      return rs.some((r) => r.readiness === "ready");
    },

    mutatorReady() {
      const ms = (this.health && this.health.mutators) || [];
      return ms.some((m) => m.readiness === "ready");
    },

    artifactUrl(artifactId) {
      return `/api/documents/artifacts/${artifactId}/download`;
    },

    _artifactToast(label, data) {
      const id = data && data.artifact_id;
      if (id) {
        this.toast(kazmaT('documents.js.artifact_ready', '{label} — artifact ready', { label: label }), "success");
      } else {
        this.toast(kazmaT('documents.js.op_complete', '{label} complete', { label: label }), "success");
      }
    },

    async convertDoc() {
      if (!this.selected || this.acting) return;
      const fmt = (this.convertFormat || "").trim();
      if (!fmt) {
        this.toast(kazmaT('documents.js.choose_a_target_format', "Choose a target format"), "error");
        return;
      }
      this.acting = true;
      try {
        const r = await fetch(`/api/documents/${this.selected.document_id}/convert`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_format: fmt }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          this.toast(j.error || kazmaT('documents.js.convert_failed', "Convert failed"), "error");
          return;
        }
        this._artifactToast(kazmaT('documents.js.converted_to', 'Converted to {format}', { format: fmt }), j.artifact);
        await this.refreshDetail();
      } catch (e) {
        this.toast(kazmaT('documents.js.convert_failed', "Convert failed"), "error");
      } finally {
        this.acting = false;
      }
    },

    async pdfInfo() {
      if (!this.selected || this.acting) return;
      this.acting = true;
      try {
        const r = await fetch(`/api/documents/${this.selected.document_id}/pdf-info`);
        const j = await r.json();
        if (!r.ok || !j.ok) {
          this.toast(j.error || kazmaT('documents.js.pdf_info_failed', "PDF info failed"), "error");
          return;
        }
        const rep = j.report || {};
        this.preview = JSON.stringify(rep, null, 2);
        this.toast(kazmaT('documents.js.pdf_info_loaded', "PDF info loaded"), "success");
      } catch (e) {
        this.toast(kazmaT('documents.js.pdf_info_failed', "PDF info failed"), "error");
      } finally {
        this.acting = false;
      }
    },

    async splitDoc() {
      if (!this.selected || this.acting) return;
      this.acting = true;
      try {
        const r = await fetch(`/api/documents/${this.selected.document_id}/split`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start_page: Number(this.splitStart) || 1,
            end_page: Number(this.splitEnd) || 0,
          }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          this.toast(j.error || kazmaT('documents.js.split_failed', "Split failed"), "error");
          return;
        }
        this._artifactToast("Split", j.artifact);
        await this.refreshDetail();
      } catch (e) {
        this.toast(kazmaT('documents.js.split_failed', "Split failed"), "error");
      } finally {
        this.acting = false;
      }
    },

    async redactDoc() {
      if (!this.selected || this.acting) return;
      const raw = await window.kazmaPrompt({
        title: kazmaT('documents.js.redact_document', "Redact document"),
        message: kazmaT('documents.js.redact_prompt', 'Enter terms to redact (comma-separated). Redaction creates a new immutable artifact. Mixed image/vector PDFs fail closed and are refused.'),
        placeholder: kazmaT('documents.js.redact_ph', 'e.g. account number, SSN'),
      });
      if (raw === null) return;
      const terms = raw
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t.length > 0);
      if (terms.length === 0) {
        this.toast(kazmaT('documents.js.enter_at_least_one_term', "Enter at least one term"), "error");
        return;
      }
      const ok = await window.kazmaConfirm({
        title: kazmaT('documents.js.confirm_redaction', "Confirm redaction"),
        message: kazmaT('documents.js.redact_confirm', 'Physically redact {n} term(s)? This produces a new, independently-verified immutable artifact and cannot alter the original.', { n: terms.length }),
        confirmText: kazmaT('documents.redact', "Redact"),
      });
      if (!ok) return;
      this.acting = true;
      try {
        const r = await fetch(`/api/documents/${this.selected.document_id}/redact`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ terms }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          this.toast(j.error || kazmaT('documents.js.redaction_failed', "Redaction failed"), "error");
          return;
        }
        this._artifactToast("Redacted", j.artifact);
        await this.refreshDetail();
      } catch (e) {
        this.toast(kazmaT('documents.js.redaction_failed', "Redaction failed"), "error");
      } finally {
        this.acting = false;
      }
    },

    /* The Knowledge libraries a document can be added to, loaded once. */
    async loadLibraries(force = false) {
      if (this._librariesLoaded && !force) return;
      const data = await window.kazmaGetJson("/api/kb/libraries");
      if (!data || !data.ok) return;
      this.libraries = (data.libraries || []).map((lib) => ({ id: lib.id, name: lib.name || lib.id }));
      this._librariesLoaded = true;
    },

    _tr(key, fallback, vars) {
      let text = typeof window.t === "function" ? window.t(key) : key;
      if (!text || text === key) text = fallback;
      for (const [k, v] of Object.entries(vars || {})) text = text.split("{" + k + "}").join(String(v));
      return text;
    },

    /* Add the selected document to a Knowledge library -- an existing one,
       or a new one named here (the library must exist before a document
       can be indexed into it). */
    async addToLibrary() {
      if (!this.selected || this.acting) return;
      let lib = (this.libraryId || "").trim();
      if (!lib) return;
      this.acting = true;
      try {
        if (lib === "__new__") {
          const name = await window.kazmaPrompt({
            title: this._tr("documents.library_new_title", "New library"),
            message: this._tr("documents.library_new_prompt", "Name of the library to add this document to:"),
            defaultValue: this.selected.title || "",
          });
          if (!name || !name.trim()) return;
          const created = await fetch("/api/kb/libraries", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: name.trim(), name: name.trim() }),
          }).then((r) => r.json()).catch(() => ({ ok: false, error: "request failed" }));
          if (!created.ok) {
            this.toast(this._tr("documents.library_failed", "Could not add it to the library: {error}", { error: created.error || "" }), "error");
            return;
          }
          lib = created.library.id;
          await this.loadLibraries(true);
          this.libraryId = lib;
        }
        const r = await fetch(`/api/documents/${this.selected.document_id}/index`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ library_id: lib }),
        });
        const j = await r.json().catch(() => ({ ok: false, error: "request failed" }));
        const shown = (this.libraries.find((l) => l.id === lib) || { name: lib }).name;
        if (j.ok) {
          const n = (j.index && (j.index.chunk_count ?? j.index.chunks)) || 0;
          this.toast(this._tr("documents.library_added", "Added {n} passage(s) to “{library}”.", { n, library: shown }), "success");
        } else {
          this.toast(this._tr("documents.library_failed", "Could not add it to the library: {error}", { error: j.error || "" }), "error");
        }
      } finally {
        this.acting = false;
      }
    },

    /**
     * Soft-delete (archive) a document. Unindexes + tombstones metadata;
     * physical content is reclaimed later by garbage collection.
     */
    async deleteDocument(documentId, title) {
      if (!documentId || this.acting) return;
      const label = (title || documentId).toString().slice(0, 80);
      let proceed = false;
      try {
        if (typeof window.kazmaConfirm === "function") {
          proceed = !!(await window.kazmaConfirm({
            title: kazmaT('documents.js.delete_archive_document', "Delete / archive document?"),
            message: kazmaT('documents.js.archive_confirm', 'Archive "{label}"?\n\nThe document leaves your library (soft-delete). Any search index entries are removed. Original bytes stay until garbage collection reclaims unreferenced storage — this cannot be undone from the UI.', { label: label }),
            confirmText: kazmaT('documents.delete_archive', "Delete / Archive"),
            cancelText: kazmaT('common.cancel', "Cancel"),
            danger: true,
          }));
        } else {
          proceed = await window.confirm(`Archive "${label}"?`);
        }
      } catch (e) {
        proceed = await window.confirm(`Archive "${label}"?`);
      }
      if (!proceed) return;
      this.acting = true;
      try {
        const r = await fetch(`/api/documents/${encodeURIComponent(documentId)}/delete`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ reason: "user_requested" }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) {
          const msg = j.error || j.message || kazmaT('documents.js.delete_failed_http', 'Delete failed (HTTP {status})', { status: r.status });
          this.toast(msg, "error");
          console.warn("[documents] delete failed", r.status, j);
          return;
        }
        this.toast(kazmaT('documents.js.document_archived_soft_deleted', "Document archived (soft-deleted)"), "success");
        // Optimistically drop from the local list so the UI updates even if
        // a follow-up list call races.
        this.documents = (this.documents || []).filter(
          (d) => d.document_id !== documentId,
        );
        if (this.selected && this.selected.document_id === documentId) {
          this.selected = null;
          this.versions = [];
          this.jobs = [];
          this.artifacts = [];
          this.preview = "";
          this.pageCount = 0;
          this.currentState = "";
          this._stopPoll();
        }
        await this.loadDocuments();
        await this.loadOps();
      } catch (e) {
        this.toast(kazmaT('documents.js.delete_failed_network', "Delete failed (network)"), "error");
        console.warn("[documents] delete error", e);
      } finally {
        this.acting = false;
      }
    },
  };
}

if (typeof window !== "undefined") {
  window.documentsPage = documentsPage;
}
