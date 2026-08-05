/**
 * Memory admin page — beliefs, entities, merge/link, hygiene.
 * Bidirectional bridge with the V2 graph canvas (memory_console.js).
 * APIs under /api/memory/v2/* (see memory_api.py).
 */
function memoryPage() {
  const S = window.__MEM_STRINGS || {};

  function toast(msg, type) {
    if (window.showToast) window.showToast(msg, type || "info");
    else console.log(`[${type}] ${msg}`);
  }

  /** Show a toast with an inline [Undo] button that POSTs /undo/{token}.
   *  Falls back to a plain toast if no container is present. Single-use:
   *  the button disables itself after the first click. */
  function undoToast(message, undoToken, { kind, duration } = {}) {
    const container = document.querySelector('.toast-container');
    if (!container || !undoToken) {
      toast(message + (undoToken ? ' (undo available)' : ''), 'success');
      return;
    }
    const el = document.createElement('div');
    el.className = 'toast toast-success';
    el.style.cssText = 'display:flex;align-items:center;gap:10px;max-width:440px;';
    const text = document.createElement('span');
    text.textContent = message;
    text.style.cssText = 'flex:1;min-width:0;';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Undo';
    btn.className = 'btn btn-sm btn-secondary';
    btn.style.cssText = 'flex:0 0 auto;font-size:0.74rem;padding:2px 10px;';
    let done = false;
    btn.addEventListener('click', async () => {
      if (done) return;
      done = true;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        const r = await api('/api/memory/v2/undo/' + encodeURIComponent(undoToken), {
          method: 'POST',
        });
        if (r && r.ok) {
          toast('Undone: ' + (r.label || kind || 'action'), 'success');
          // Trigger the standard post-ops refresh so lists/graph update.
          try { window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'undo', kind } })); } catch (_) { /* */ }
        } else {
          toast((r && r.error) || 'Undo failed', 'error');
        }
      } catch (e) {
        toast('Undo failed: ' + e, 'error');
      } finally {
        el.remove();
      }
    });
    el.appendChild(text);
    el.appendChild(btn);
    container.appendChild(el);
    // Auto-dismiss the (still-clickable) toast after the window.
    const ms = duration || 9000;
    setTimeout(() => { if (el.parentNode) el.remove(); }, ms);
  }

  async function confirm(opts) {
    if (window.kazmaConfirm) return window.kazmaConfirm(opts);
    return window.confirm(opts.message || opts.title || "Confirm?");
  }

  async function api(path, opts) {
    const r = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      ...(opts || {}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok && !data.error) data.error = "HTTP " + r.status;
    return data;
  }

  async function refreshGraph() {
    try {
      if (typeof window._v2gForceReload === "function") {
        await window._v2gForceReload();
      } else if (typeof window._v2gLoad === "function") {
        await window._v2gLoad();
      }
    } catch (_) {
      /* graph optional */
    }
  }

  function scrollToConsole() {
    const el = document.getElementById("console") || document.getElementById("v2g-canvas-wrap");
    if (el) {
      try {
        el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (_) {
        /* ignore */
      }
    }
  }

  function scrollRowIntoView(selector) {
    const row = document.querySelector(selector);
    if (row) {
      try {
        row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (_) {
        /* ignore */
      }
    }
  }

  return {
    S,
    loading: false,
    error: "",
    tab: "entities",
    summary: {},
    beliefs: [],
    beliefQ: "",
    selectedBeliefs: [],
    selectedEntityId: null,
    selectedBeliefId: null,
    entities: [],
    entityQ: "",
    emptyOnly: false,
    isolatedOnly: false,
    mergeSource: "",
    mergeTarget: "",
    linkPredicate: "related_to",
    merges: [],
    // Pagination state per collection. pageSize is the per-fetch window;
    // offset/total are (re)set by each load. loadMore appends the next window.
    entitiesPage: { offset: 0, total: 0, pageSize: 150 },
    beliefsPage: { offset: 0, total: 0, pageSize: 100 },
    mergesPage: { offset: 0, total: 0, pageSize: 50 },
    hygienePreview: {},
    hygiene: {
      purge_empty_entities: true,
      invalidate_near_dup_noted: false,
      archive_invalidated: false,
    },
    hygieneRunning: false,
    _graphListener: null,
    _opsListener: null,
    _opsDoneListener: null,
    _syncingSlots: false,

    get tabs() {
      // Graph & health is pinned at the top of the page (not a tab).
      return [
        { id: "entities", label: S.tab_entities || "Entities" },
        { id: "beliefs", label: S.tab_beliefs || "Beliefs" },
        { id: "merges", label: S.tab_merges || "Pending merges" },
        { id: "hygiene", label: S.tab_hygiene || "Hygiene" },
      ];
    },

    get canLinkOrMerge() {
      return !!(this.mergeSource || "").trim() && !!(this.mergeTarget || "").trim();
    },

    get summaryChips() {
      const s = this.summary || {};
      return [
        { k: "Beliefs", v: s.beliefs_live ?? "—" },
        { k: "Invalidated", v: s.beliefs_invalidated ?? "—" },
        { k: "Entities", v: s.entities ?? "—" },
        { k: "Empty", v: s.entities_empty ?? "—" },
        { k: "Isolated", v: s.entities_isolated ?? "—" },
        { k: "Episodes", v: s.episodes ?? "—" },
      ];
    },

    /** "Showing 1–150 of 3,412" for a collection's pager. */
    rangeText(page, len) {
      const total = (page && page.total) || 0;
      if (!total) return "";
      const start = ((page && page.offset) || 0) + 1;
      const end = Math.min(start + len - 1, total);
      return `Showing ${start.toLocaleString()}–${end.toLocaleString()} of ${total.toLocaleString()}`;
    },

    /** Can we load another window? (offset + fetched < total) */
    hasMore(page, len) {
      if (!page) return false;
      const fetched = (page.offset || 0) + len;
      return fetched < (page.total || 0);
    },

    /** Push list source/target/predicate into the graph ops bar. */
    pushSlotsToGraph() {
      if (this._syncingSlots) return;
      const src = (this.mergeSource || "").trim() || null;
      const tgt = (this.mergeTarget || "").trim() || null;
      const pred = (this.linkPredicate || "related_to").trim() || "related_to";
      if (typeof window._v2gSetOpsSlots === "function") {
        window._v2gSetOpsSlots(src, tgt, pred);
      }
      try {
        window.dispatchEvent(
          new CustomEvent("kazma:memory-ops-slots", {
            detail: { sourceId: src, targetId: tgt, predicate: pred, fromList: true },
          })
        );
      } catch (_) {
        /* ignore */
      }
    },

    swapSlots() {
      const s = this.mergeSource;
      this.mergeSource = this.mergeTarget;
      this.mergeTarget = s;
      this.pushSlotsToGraph();
    },

    clearSlots() {
      this.mergeSource = "";
      this.mergeTarget = "";
      this.pushSlotsToGraph();
    },

    async init() {
      await this.loadAll();
      // Graph → list: highlight matching entity / belief.
      // Canvas double-click sends this event (single click stays on the graph).
      this._graphListener = (ev) => {
        const d = (ev && ev.detail) || {};
        if (d.type === "entity" && d.id) {
          this.selectedEntityId = d.id;
          this.selectedBeliefId = null;
          if (d.scrollOps && this.tab !== "entities") {
            this.tab = "entities";
            this.onTab();
          }
          const eid = String(d.id).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
          this.$nextTick(() => {
            scrollRowIntoView('[data-entity-id="' + eid + '"]');
          });
        } else if (d.type === "belief") {
          if (d.id) this.selectedBeliefId = d.id;
          if (d.subject) this.selectedEntityId = d.subject;
          if (d.scrollOps && this.tab !== "beliefs") {
            this.tab = "beliefs";
            this.onTab();
          }
          this.$nextTick(() => {
            if (d.id) {
              const bid = String(d.id).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
              scrollRowIntoView('[data-belief-id="' + bid + '"]');
            }
          });
        }
      };
      window.addEventListener("kazma:memory-graph-select", this._graphListener);

      // Graph ops bar → list slots (skip events we emitted ourselves)
      this._opsListener = (ev) => {
        const d = (ev && ev.detail) || {};
        if (d.fromList) return;
        this._syncingSlots = true;
        try {
          if (d.sourceId !== undefined) this.mergeSource = d.sourceId || "";
          if (d.targetId !== undefined) this.mergeTarget = d.targetId || "";
          if (d.predicate) this.linkPredicate = d.predicate;
        } finally {
          this._syncingSlots = false;
        }
      };
      window.addEventListener("kazma:memory-ops-slots", this._opsListener);

      // After graph link/merge/unlink/edit — refresh list tables
      this._opsDoneListener = async (ev) => {
        const d = (ev && ev.detail) || {};
        try {
          await this.loadEntities();
          await this.loadBeliefs();
          await this.loadSummary();
          if (d.op === "merge" || d.op === "delete") await this.loadHygiene();
        } catch (_) {
          /* ignore */
        }
      };
      window.addEventListener("kazma:memory-ops-done", this._opsDoneListener);
    },

    destroy() {
      if (this._graphListener) {
        window.removeEventListener("kazma:memory-graph-select", this._graphListener);
        this._graphListener = null;
      }
      if (this._opsListener) {
        window.removeEventListener("kazma:memory-ops-slots", this._opsListener);
        this._opsListener = null;
      }
      if (this._opsDoneListener) {
        window.removeEventListener("kazma:memory-ops-done", this._opsDoneListener);
        this._opsDoneListener = null;
      }
    },

    async loadAll() {
      this.loading = true;
      this.error = "";
      try {
        await Promise.all([
          this.loadSummary(),
          this.loadEntities(),
          this.loadBeliefs(),
          this.loadMerges(),
          this.loadHygiene(),
        ]);
      } catch (e) {
        this.error = String(e.message || e);
      } finally {
        this.loading = false;
      }
    },

    onTab() {
      if (this.tab === "beliefs" && !this.beliefs.length) this.loadBeliefs();
      if (this.tab === "entities") this.loadEntities();
      if (this.tab === "merges") this.loadMerges();
      if (this.tab === "hygiene") this.loadHygiene();
    },

    async loadSummary() {
      const d = await api("/api/memory/v2/admin/summary");
      if (d.ok) this.summary = d;
    },

    async loadBeliefs(append) {
      const q = encodeURIComponent(this.beliefQ || "");
      const off = append ? this.beliefsPage.offset + this.beliefsPage.pageSize : 0;
      const d = await api(
        "/api/memory/v2/beliefs?q=" + q + "&limit=" + this.beliefsPage.pageSize + "&offset=" + off
      );
      const rows = d.beliefs || [];
      this.beliefsPage = {
        offset: Number(d.offset || off),
        total: Number(d.total || rows.length),
        pageSize: Number(d.limit || this.beliefsPage.pageSize),
      };
      this.beliefs = append ? this.beliefs.concat(rows) : rows;
      if (!append) this.selectedBeliefs = [];
    },

    async loadEntities(append) {
      const off = append ? this.entitiesPage.offset + this.entitiesPage.pageSize : 0;
      const params = new URLSearchParams({
        q: this.entityQ || "",
        limit: String(this.entitiesPage.pageSize),
        offset: String(off),
        empty_only: this.emptyOnly ? "true" : "false",
        isolated_only: this.isolatedOnly ? "true" : "false",
      });
      const d = await api("/api/memory/v2/entities?" + params.toString());
      const rows = d.entities || [];
      this.entitiesPage = {
        offset: Number(d.offset || off),
        total: Number(d.total || rows.length),
        pageSize: Number(d.limit || this.entitiesPage.pageSize),
      };
      this.entities = append ? this.entities.concat(rows) : rows;
    },

    async loadMerges(append) {
      const off = append ? this.mergesPage.offset + this.mergesPage.pageSize : 0;
      const d = await api(
        "/api/memory/v2/entity-merges?limit=" + this.mergesPage.pageSize + "&offset=" + off
      );
      const rows = d.merges || [];
      this.mergesPage = {
        offset: Number(d.offset || off),
        total: Number(d.total || rows.length),
        pageSize: Number(d.limit || this.mergesPage.pageSize),
      };
      this.merges = append ? this.merges.concat(rows) : rows;
    },

    // One-line load-more wrappers for the pager buttons.
    loadMoreEntities() { return this.loadEntities(true); },
    loadMoreBeliefs() { return this.loadBeliefs(true); },
    loadMoreMerges() { return this.loadMerges(true); },

    async loadHygiene() {
      const d = await api("/api/memory/v2/hygiene/preview");
      this.hygienePreview = d.ok ? d : {};
    },

    toggleAllBeliefs(on) {
      this.selectedBeliefs = on ? this.beliefs.map((b) => b.id) : [];
    },

    /** List → graph: focus entity node on canvas. */
    focusEntity(id, opts) {
      opts = opts || {};
      if (!id) return;
      this.selectedEntityId = id;
      this.selectedBeliefId = null;
      if (opts.asSource) {
        this.mergeSource = id;
        this.pushSlotsToGraph();
      }
      if (opts.asTarget) {
        this.mergeTarget = id;
        this.pushSlotsToGraph();
      }
      if (!opts.asSource && !opts.asTarget && !opts.skipPick) {
        // Soft-pick into merge slots without overwriting both
        this.pickEntity(id);
      }
      // Prefer full entity row (graph_id maps self shells → user hub)
      const ent =
        (this.entities || []).find((e) => e && e.id === id) ||
        opts.entity ||
        null;
      const graphId = (ent && (ent.graph_id || ent.graphId)) || id;
      const isSelf = !!(ent && (ent.is_self || ent.isSelf || graphId === "user"));
      const name = (ent && ent.name) || opts.name || "";
      const ok =
        typeof window._v2gSelectEntity === "function"
          ? window._v2gSelectEntity(id, {
              notify: false,
              graphId: graphId,
              isSelf: isSelf,
              name: name,
            })
          : false;
      if (!ok && !opts.quiet) {
        toast("Node not on graph (filtered out or no beliefs)", "info");
      }
      if (opts.scrollGraph !== false) scrollToConsole();
    },

    /** List → graph: highlight belief edge endpoints. */
    focusBelief(b, opts) {
      opts = opts || {};
      if (!b) return;
      this.selectedBeliefId = b.id || null;
      if (b.subject) this.selectedEntityId = b.subject;
      const ok =
        typeof window._v2gSelectBelief === "function"
          ? window._v2gSelectBelief(b.subject, b.object, b.id, {
              notify: false,
            })
          : false;
      // _v2gSelectBelief notifies list — suppress double-switch by notify path already ok
      if (!ok && !opts.quiet) {
        toast("Belief endpoints not on graph (try refresh)", "info");
      }
      if (opts.scrollGraph !== false) scrollToConsole();
    },

    async invalidateOne(id) {
      const ok = await confirm({
        title: S.invalidate || "Invalidate",
        message: S.confirm_invalidate || "Soft-invalidate this belief?",
      });
      if (!ok) return;
      const d = await api("/api/memory/v2/beliefs/" + encodeURIComponent(id) + "/invalidate", {
        method: "POST",
        body: "{}",
      });
      if (d.ok) {
        toast("Invalidated " + String(id).slice(0, 16), "success");
        await this.loadBeliefs();
        await this.loadSummary();
        await refreshGraph();
      } else toast(d.error || "Failed", "error");
    },

    async invalidateSelected() {
      if (!this.selectedBeliefs.length) return;
      const ok = await confirm({
        title: S.invalidate || "Invalidate",
        message:
          (S.confirm_invalidate || "Invalidate selected?") +
          " (" +
          this.selectedBeliefs.length +
          ")",
      });
      if (!ok) return;
      const d = await api("/api/memory/v2/beliefs/invalidate-batch", {
        method: "POST",
        body: JSON.stringify({ ids: this.selectedBeliefs }),
      });
      if (d.ok) {
        undoToast(
          "Invalidated " + d.invalidated + " belief" + (d.invalidated === 1 ? "" : "s") + ".",
          d.undo_token,
          { kind: "invalidate" }
        );
        await this.loadBeliefs();
        await this.loadSummary();
        await refreshGraph();
      } else toast(d.error || "Failed", "error");
    },

    async editBelief(b) {
      if (!b || !b.id) return;
      let object = b.object;
      let predicate = b.predicate;
      let subject = b.subject;
      if (window.kazmaPrompt) {
        object = await window.kazmaPrompt({
          title: S.edit_belief || "Edit belief — object",
          message:
            "Fact / object text (what the belief says). Cancel aborts the whole edit.",
          defaultValue: b.object || "",
          confirmText: "Next",
          placeholder: "e.g. ShipX platform description…",
        });
        if (object == null) return;
        predicate = await window.kazmaPrompt({
          title: S.edit_belief || "Edit belief — predicate",
          message: "Relation name (snake_case ok).",
          defaultValue: b.predicate || "",
          confirmText: "Next",
          placeholder: "has_project",
        });
        if (predicate == null) return;
        subject = await window.kazmaPrompt({
          title: S.edit_belief || "Edit belief — subject",
          message: "Subject entity id (usually stable slug).",
          defaultValue: b.subject || "",
          confirmText: "Save",
          placeholder: "user",
        });
        if (subject == null) return;
      } else {
        object = window.prompt("Object (fact text)", b.object || "");
        if (object == null) return;
        predicate = window.prompt("Predicate", b.predicate || "");
        if (predicate == null) return;
        subject = window.prompt("Subject", b.subject || "");
        if (subject == null) return;
      }
      subject = String(subject).trim();
      predicate = String(predicate).trim();
      object = String(object).trim();
      if (!subject || !predicate || !object) {
        toast("Subject, predicate, and object are required", "error");
        return;
      }
      if (
        subject === b.subject &&
        predicate === b.predicate &&
        object === b.object
      ) {
        return;
      }
      const d = await api(
        "/api/memory/v2/beliefs/" + encodeURIComponent(b.id),
        {
          method: "PATCH",
          body: JSON.stringify({ subject, predicate, object }),
        }
      );
      if (d.ok) {
        undoToast("Belief updated.", d.undo_token, { kind: "edit" });
        await this.loadBeliefs();
        await this.loadEntities();
        await this.loadSummary();
        await refreshGraph();
        this.selectedBeliefId = b.id;
        this.focusBelief(
          { id: b.id, subject, predicate, object },
          { quiet: true }
        );
      } else toast(d.error || "Edit failed", "error");
    },

    pickEntity(id) {
      if (!this.mergeSource) this.mergeSource = id;
      else if (!this.mergeTarget) this.mergeTarget = id;
      else this.mergeSource = id;
      this.pushSlotsToGraph();
    },

    async deleteEntity(e) {
      if (e.protected) return;
      const ok = await confirm({
        title: S.delete || "Delete entity",
        message: (S.confirm_delete || "Delete entity shell?") + " " + e.id,
      });
      if (!ok) return;
      const d = await api("/api/memory/v2/entities/" + encodeURIComponent(e.id), {
        method: "DELETE",
      });
      if (d.ok) {
        undoToast("Deleted entity " + e.id + ".", d.undo_token, { kind: "delete-entity" });
        if (this.selectedEntityId === e.id) this.selectedEntityId = null;
        await this.loadEntities();
        await this.loadSummary();
        await this.loadHygiene();
        await refreshGraph();
      } else toast(d.error || "Failed", "error");
    },

    async renameEntity(e) {
      if (!e || !e.id) return;
      const current = e.name || e.id;
      let name;
      if (window.kazmaPrompt) {
        name = await window.kazmaPrompt({
          title: S.rename || "Rename entity",
          message:
            (S.rename_hint ||
              "Display name only — id stays the same so beliefs keep linking.") +
            "\nid: " +
            e.id,
          defaultValue: current,
          confirmText: S.rename || "Rename",
          placeholder: "e.g. ShipX / Mubder",
        });
      } else {
        name = window.prompt("New display name for " + e.id, current);
      }
      if (name == null) return;
      name = String(name).trim();
      if (!name) {
        toast("Name cannot be empty", "error");
        return;
      }
      if (name === current) return;
      const d = await api(
        "/api/memory/v2/entities/" + encodeURIComponent(e.id) + "/rename",
        {
          method: "POST",
          body: JSON.stringify({ name: name }),
        }
      );
      if (d.ok) {
        toast("Renamed to “" + name + "”", "success");
        this.selectedEntityId = e.id;
        await this.loadEntities();
        // Force graph reload so hub label (You→Mubder) re-fetches from server
        await refreshGraph();
        const graphId = d.graph_id || e.graph_id || e.id;
        if (typeof window._v2gSelectEntity === "function") {
          window._v2gSelectEntity(e.id, {
            notify: false,
            graphId: graphId,
            isSelf: !!d.hub_synced || graphId === "user" || !!e.is_self,
            name: name,
          });
        }
      } else toast(d.error || "Rename failed", "error");
    },

    async doMerge() {
      const src = (this.mergeSource || "").trim();
      const tgt = (this.mergeTarget || "").trim();
      if (!src || !tgt) {
        toast("Set source and target entity ids (or pick on the graph)", "error");
        scrollToConsole();
        return;
      }
      // Prefer graph helper when available so canvas + list stay in sync
      if (typeof window._v2gDoMerge === "function") {
        const ok = await window._v2gDoMerge(src, tgt);
        if (ok) {
          this.mergeSource = "";
          this.mergeTarget = tgt;
          this.selectedEntityId = tgt;
          this.pushSlotsToGraph();
          await this.loadEntities();
          await this.loadBeliefs();
          await this.loadSummary();
        }
        return;
      }
      const ok = await confirm({
        title: S.merge || "Merge",
        message:
          (S.confirm_merge || "Merge source into target? Beliefs rewired.") +
          "\n" +
          src +
          " → " +
          tgt,
      });
      if (!ok) return;
      const d = await api("/api/memory/v2/entities/merge", {
        method: "POST",
        body: JSON.stringify({ source_id: src, target_id: tgt }),
      });
      if (d.ok) {
        // Merge is not undoable (identity rewrite) — show a detailed receipt
        // instead so the operator sees exactly what moved.
        const rewired = (d.receipt && d.receipt.beliefs_rewired) || 0;
        toast(
          "Merged " + src + " → " + tgt + ": " + rewired + " belief" + (rewired === 1 ? "" : "s") + " rewired.",
          "success"
        );
        this.mergeSource = "";
        this.mergeTarget = tgt;
        this.selectedEntityId = tgt;
        this.pushSlotsToGraph();
        await this.loadEntities();
        await this.loadBeliefs();
        await this.loadSummary();
        await refreshGraph();
        if (typeof window._v2gSelectEntity === "function") {
          window._v2gSelectEntity(tgt, { notify: false });
        }
      } else toast(d.error || "Merge failed", "error");
    },

    async doLink() {
      const src = (this.mergeSource || "").trim();
      const tgt = (this.mergeTarget || "").trim();
      const pred = (this.linkPredicate || "related_to").trim() || "related_to";
      if (!src || !tgt) {
        toast("Set source and target for link (or pick on the graph)", "error");
        scrollToConsole();
        return;
      }
      if (typeof window._v2gDoLink === "function") {
        const ok = await window._v2gDoLink(src, tgt, pred);
        if (ok) {
          await this.loadEntities();
          await this.loadBeliefs();
          await this.loadSummary();
        }
        return;
      }
      const d = await api("/api/memory/v2/entities/link", {
        method: "POST",
        body: JSON.stringify({ subject: src, predicate: pred, object: tgt }),
      });
      if (d.ok) {
        undoToast(
          "Linked " + src + " —" + pred + "→ " + tgt + (d.already ? " (already linked)" : "") + ".",
          d.undo_token,
          { kind: "link" }
        );
        await this.loadEntities();
        await this.loadBeliefs();
        await this.loadSummary();
        await refreshGraph();
        if (typeof window._v2gSelectEntity === "function") {
          window._v2gSelectEntity(tgt, { notify: false });
        }
      } else toast(d.error || "Link failed", "error");
    },

    async decideMerge(id, approve) {
      const d = await api("/api/memory/v2/entity-merges/" + encodeURIComponent(id), {
        method: "POST",
        body: JSON.stringify({ action: approve ? "approve" : "reject" }),
      });
      if (d.ok) {
        toast(approve ? "Merge approved" : "Merge rejected", "success");
        await this.loadMerges();
        await this.loadEntities();
        await refreshGraph();
      } else toast(d.error || "Failed", "error");
    },

    async runHygiene() {
      const ok = await confirm({
        title: S.run_hygiene || "Run hygiene",
        message: S.confirm_hygiene || "Run selected hygiene actions?",
      });
      if (!ok) return;
      this.hygieneRunning = true;
      try {
        const d = await api("/api/memory/v2/hygiene/run", {
          method: "POST",
          body: JSON.stringify(this.hygiene),
        });
        if (d.ok) {
          toast("Hygiene complete", "success");
          await this.loadAll();
          await refreshGraph();
        } else toast(d.error || "Hygiene failed", "error");
      } finally {
        this.hygieneRunning = false;
      }
    },
  };
}
