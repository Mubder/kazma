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
        this.toast("Failed to load documents", "error");
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
          this.toast("Admin privileges required to run garbage collection", "error");
          return;
        }
        const dj = await dr.json();
        if (!dj.ok) {
          this.toast("Garbage-collection dry-run failed", "error");
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
          this.toast("Nothing to reclaim — the store is already clean", "info");
          await this.loadOps();
          return;
        }
        const proceed = await window.kazmaConfirm({
          title: "Run garbage collection?",
          message:
            `Dry-run found ${wouldDelete} item(s) to delete ` +
            `(~${this.fmtBytes(rep.reclaimed_bytes)} reclaimable). ` +
            `Referenced content and current versions are never removed. Proceed?`,
          confirmText: "Run GC",
          cancelText: "Cancel",
          danger: true,
        });
        if (!proceed) return;
        const rr = await fetch("/api/documents/ops/maintenance/run", { method: "POST" });
        const rj = await rr.json();
        if (rj.ok) {
          this.gcReport = rj.report;
          this.toast(
            `GC reclaimed ${rj.report.deleted_blobs} blob(s), ` +
              `${this.fmtBytes(rj.report.reclaimed_bytes)}`,
            "success",
          );
          await this.loadOps();
        } else {
          this.toast("Garbage collection failed", "error");
        }
      } catch (e) {
        this.toast("Garbage collection error", "error");
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
        const url = "/api/documents" + (this.forceOcr ? "?force_ocr=1" : "");
        const r = await fetch(url, {
          method: "POST",
          headers: {
            "X-Document-Filename": file.name,
            "Content-Type": "application/octet-stream",
          },
          body: file,
        });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          this.toast(j.error || "Upload failed", "error");
          return;
        }
        this.toast("Uploaded — processing started", "success");
        await this.loadDocuments();
        await this.openDocument(j.document_id);
      } catch (e) {
        this.toast("Upload failed", "error");
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
          this.toast(j.error || "Failed to load document", "error");
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
        this.toast("Failed to load document", "error");
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
        this.toast("Failed to load events", "error");
      }
    },

    async cancelJob(jobId) {
      try {
        const r = await fetch(`/api/documents/jobs/${jobId}/cancel`, { method: "POST" });
        const j = await r.json();
        if (j.ok) {
          this.toast("Cancellation requested", "info");
          await this.refreshDetail();
        } else {
          this.toast(j.error || "Cancel failed", "error");
        }
      } catch (e) {
        this.toast("Cancel failed", "error");
      }
    },

    async retryJob(jobId) {
      try {
        const r = await fetch(`/api/documents/jobs/${jobId}/retry`, { method: "POST" });
        const j = await r.json();
        if (j.ok) {
          this.toast("Retry enqueued", "success");
          await this.refreshDetail();
          this._startPoll();
        } else {
          this.toast(j.error || "Retry failed", "error");
        }
      } catch (e) {
        this.toast("Retry failed", "error");
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
        this.toast(`${label} — artifact ready`, "success");
      } else {
        this.toast(`${label} complete`, "success");
      }
    },

    async convertDoc() {
      if (!this.selected || this.acting) return;
      const fmt = (this.convertFormat || "").trim();
      if (!fmt) {
        this.toast("Choose a target format", "error");
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
          this.toast(j.error || "Convert failed", "error");
          return;
        }
        this._artifactToast(`Converted to ${fmt}`, j.artifact);
        await this.refreshDetail();
      } catch (e) {
        this.toast("Convert failed", "error");
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
          this.toast(j.error || "PDF info failed", "error");
          return;
        }
        const rep = j.report || {};
        this.preview = JSON.stringify(rep, null, 2);
        this.toast("PDF info loaded", "success");
      } catch (e) {
        this.toast("PDF info failed", "error");
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
          this.toast(j.error || "Split failed", "error");
          return;
        }
        this._artifactToast("Split", j.artifact);
        await this.refreshDetail();
      } catch (e) {
        this.toast("Split failed", "error");
      } finally {
        this.acting = false;
      }
    },

    async redactDoc() {
      if (!this.selected || this.acting) return;
      const raw = await window.kazmaPrompt({
        title: "Redact document",
        message:
          "Enter terms to redact (comma-separated). Redaction creates a new immutable artifact. Mixed image/vector PDFs fail closed and are refused.",
        placeholder: "e.g. account number, SSN",
      });
      if (raw === null) return;
      const terms = raw
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t.length > 0);
      if (terms.length === 0) {
        this.toast("Enter at least one term", "error");
        return;
      }
      const ok = await window.kazmaConfirm({
        title: "Confirm redaction",
        message: `Physically redact ${terms.length} term(s)? This produces a new, independently-verified immutable artifact and cannot alter the original.`,
        confirmText: "Redact",
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
          this.toast(j.error || "Redaction failed", "error");
          return;
        }
        this._artifactToast("Redacted", j.artifact);
        await this.refreshDetail();
      } catch (e) {
        this.toast("Redaction failed", "error");
      } finally {
        this.acting = false;
      }
    },

    async indexDoc() {
      const lib = (this.libraryId || "").trim();
      if (!lib) {
        this.toast("Enter a library_id first", "error");
        return;
      }
      try {
        const r = await fetch(`/api/documents/${this.selected.document_id}/index`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ library_id: lib }),
        });
        const j = await r.json();
        if (j.ok) {
          this.toast(`Indexed ${j.index.chunk_count} chunk(s) into ${lib}`, "success");
        } else {
          this.toast(j.error || "Index failed", "error");
        }
      } catch (e) {
        this.toast("Index failed", "error");
      }
    },
  };
}

if (typeof window !== "undefined") {
  window.documentsPage = documentsPage;
}
