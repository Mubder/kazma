/**
 * Memory admin page — beliefs, entities, merge/link, hygiene.
 * Bidirectional bridge with the V2 graph canvas (memory_console.js).
 * APIs under /api/memory/v2/* (see memory_api.py).
 */
function memoryPage() {
  const S = window.__MEM_STRINGS || {};

  // Text this page builds: the catalog's text in the page's language, else
  // the English fallback, {name} placeholders filled from vars.
  function tx(key, fallback, vars) {
    if (window.kazmaT) return window.kazmaT(key, fallback, vars);
    let text = fallback;
    if (vars) for (const k in vars) text = text.split("{" + k + "}").join(String(vars[k]));
    return text;
  }
  const num = (n) => (window.KazmaFormat ? window.KazmaFormat.number(n) : Number(n).toLocaleString());

  function toast(msg, type) {
    if (window.showToast) window.showToast(msg, type || "info");
    else console.warn(`[memory ${type || "info"}] ${msg}`);  // fallback only (toast system not loaded)
  }

  /** Show a toast with an inline [Undo] button that POSTs /undo/{token}.
   *  Falls back to a plain toast if no container is present. Single-use:
   *  the button disables itself after the first click. */
  function undoToast(message, undoToken, { kind, duration } = {}) {
    const container = document.querySelector('.toast-container');
    if (!container || !undoToken) {
      toast(message + (undoToken ? tx('memory.page.undo_available', ' (undo available)') : ''), 'success');
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
    btn.textContent = tx('memory.page.undo', 'Undo');
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
          toast(tx('memory.page.undone', 'Undone: {what}', { what: r.label || kind || tx('memory.page.action', 'action') }), 'success');
          // Trigger the standard post-ops refresh so lists/graph update.
          try { window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'undo', kind } })); } catch (_) { /* */ }
        } else {
          toast((r && r.error) || tx('memory.page.undo_failed', 'Undo failed'), 'error');
        }
      } catch (e) {
        toast(tx('memory.page.undo_failed_error', 'Undo failed: {error}', { error: e }), 'error');
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
    return window.confirm(opts.message || opts.title || tx("memory.console.confirm_q", "Confirm?"));
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
        { k: tx("memory.page.chip_beliefs", "Beliefs"), v: s.beliefs_live ?? "—" },
        { k: tx("memory.page.chip_invalidated", "Invalidated"), v: s.beliefs_invalidated ?? "—" },
        { k: tx("memory.page.chip_entities", "Entities"), v: s.entities ?? "—" },
        { k: tx("memory.page.chip_empty", "Empty"), v: s.entities_empty ?? "—" },
        { k: tx("memory.page.chip_isolated", "Isolated"), v: s.entities_isolated ?? "—" },
        { k: tx("memory.page.chip_episodes", "Episodes"), v: s.episodes ?? "—" },
      ];
    },

    /** "Showing 1–150 of 3,412" for a collection's pager. */
    rangeText(page, len) {
      const total = (page && page.total) || 0;
      if (!total) return "";
      const start = ((page && page.offset) || 0) + 1;
      const end = Math.min(start + len - 1, total);
      return tx("memory.page.showing", "Showing {start}–{end} of {total}", { start: num(start), end: num(end), total: num(total) });
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
        toast(tx("memory.page.node_not_on_graph", "Node not on graph (filtered out or no beliefs)"), "info");
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
        toast(tx("memory.page.belief_not_on_graph", "Belief endpoints not on graph (try refresh)"), "info");
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
        toast(tx("memory.page.invalidated_one", "Invalidated {id}", { id: String(id).slice(0, 16) }), "success");
        await this.loadBeliefs();
        await this.loadSummary();
        await refreshGraph();
      } else toast(d.error || tx("memory.console.failed", "Failed"), "error");
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
          window.kazmaCount("memory.page.invalidated_n", d.invalidated),
          d.undo_token,
          { kind: "invalidate" }
        );
        await this.loadBeliefs();
        await this.loadSummary();
        await refreshGraph();
      } else toast(d.error || tx("memory.console.failed", "Failed"), "error");
    },

    async editBelief(b) {
      if (!b || !b.id) return;
      // Single modal form (replaces the old 3-prompt wizard). One overlay,
      // three fields, one Save — the universal edit pattern. Uses the shared
      // modal store directly so the body can carry a form (kazmaPrompt only
      // does one input). Falls back to native prompts if Alpine isn't up.
      const store = window.Alpine && Alpine.store('modal');
      if (!store) {
        // Legacy fallback (pre-Alpine) — keep the 3-step native prompt.
        let object = window.prompt(tx("memory.page.prompt_object", "Object (fact text)"), b.object || "");
        if (object == null) return;
        let predicate = window.prompt(tx("memory.page.prompt_predicate", "Predicate"), b.predicate || "");
        if (predicate == null) return;
        let subject = window.prompt(tx("memory.page.prompt_subject", "Subject"), b.subject || "");
        if (subject == null) return;
        return this._submitBeliefEdit(b, subject, predicate, object);
      }
      const fid = "mem-edit-" + b.id;
      const fieldStyle = "width:100%;padding:6px 10px;border-radius:6px;border:1px solid var(--border-subtle);background:rgba(255,255,255,0.04);color:var(--text-primary);font-size:0.85rem;box-sizing:border-box;margin-top:3px;";
      const labelStyle = "font-size:0.72rem;color:var(--text-muted);";
      const escHtml = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      store.show({
        title: S.edit || tx("memory.page.edit_title", "Edit belief"),
        size: 'sm',
        body:
          '<div style="display:flex;flex-direction:column;gap:10px;">' +
          '<div><label style="' + labelStyle + '">' + escHtml(tx("memory.page.label_subject", "Subject (entity id)")) + '</label>' +
          '<input id="' + fid + '-subject" type="text" value="' + escHtml(b.subject) + '" style="' + fieldStyle + '" placeholder="user"></div>' +
          '<div><label style="' + labelStyle + '">' + escHtml(tx("memory.page.prompt_predicate", "Predicate")) + '</label>' +
          '<input id="' + fid + '-predicate" type="text" value="' + escHtml(b.predicate) + '" style="' + fieldStyle + '" placeholder="has_project"></div>' +
          '<div><label style="' + labelStyle + '">' + escHtml(tx("memory.page.prompt_object", "Object (fact text)")) + '</label>' +
          '<input id="' + fid + '-object" type="text" value="' + escHtml(b.object) + '" style="' + fieldStyle + '" placeholder="' + escHtml(tx("memory.page.label_object_ph", "e.g. what the fact says…")) + '"></div>' +
          '</div>',
        actions: [
          { label: tx('memory.page.cancel', 'Cancel'), variant: 'btn-secondary', close: true },
          {
            label: tx('memory.console.save', 'Save'), variant: 'btn-primary', close: true,
            handler: () => {
              const subject = (document.getElementById(fid + '-subject') || {}).value;
              const predicate = (document.getElementById(fid + '-predicate') || {}).value;
              const object = (document.getElementById(fid + '-object') || {}).value;
              this._submitBeliefEdit(b, subject, predicate, object);
            },
          },
        ],
      });
    },

    async _submitBeliefEdit(b, subject, predicate, object) {
      subject = String(subject || "").trim();
      predicate = String(predicate || "").trim();
      object = String(object || "").trim();
      if (!subject || !predicate || !object) {
        toast(tx("memory.console.spo_required", "Subject, predicate, and object are required"), "error");
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
        undoToast(tx("memory.page.belief_updated", "Belief updated."), d.undo_token, { kind: "edit" });
        await this.loadBeliefs();
        await this.loadEntities();
        await this.loadSummary();
        await refreshGraph();
        this.selectedBeliefId = b.id;
        this.focusBelief(
          { id: b.id, subject, predicate, object },
          { quiet: true }
        );
      } else toast(d.error || tx("memory.console.edit_failed", "Edit failed"), "error");
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
        undoToast(tx("memory.page.deleted_entity", "Deleted entity {id}.", { id: e.id }), d.undo_token, { kind: "delete-entity" });
        if (this.selectedEntityId === e.id) this.selectedEntityId = null;
        await this.loadEntities();
        await this.loadSummary();
        await this.loadHygiene();
        await refreshGraph();
      } else toast(d.error || tx("memory.console.failed", "Failed"), "error");
    },

    /* Protect an entity from delete and merge, or lift that. The page
       showed the flag and disabled Delete on it, and nothing could set or
       clear it (2026-09-28). Core entities are always protected. */
    async toggleProtect(e) {
      if (!e || !e.id || e.core) return;
      const want = !e.protected;
      const d = await api("/api/memory/v2/entities/" + encodeURIComponent(e.id) + "/protect", {
        method: "POST",
        body: JSON.stringify({ protected: want }),
      });
      if (d.ok) {
        toast(
          want
            ? tx("memory.page.protected_entity", "Protected {id}: it cannot be deleted or merged away.", { id: e.id })
            : tx("memory.page.unprotected_entity", "{id} is no longer protected.", { id: e.id }),
          "success",
        );
        await this.loadEntities();
      } else toast(d.error || tx("memory.console.failed", "Failed"), "error");
    },

    async renameEntity(e) {
      if (!e || !e.id) return;
      const current = e.name || e.id;
      let name;
      if (window.kazmaPrompt) {
        name = await window.kazmaPrompt({
          title: S.rename || tx("memory.page.rename_title", "Rename entity"),
          message:
            tx("memory.page.rename_hint", "Display name only — id stays the same so beliefs keep linking.") +
            "\n" + tx("memory.page.id_line", "id: {id}", { id: e.id }),
          defaultValue: current,
          confirmText: S.rename || tx("memory.console.rename", "Rename"),
          placeholder: tx("memory.console.rename_ph", "e.g. a project name"),
        });
      } else {
        name = window.prompt(tx("memory.page.rename_native", "New display name for {id}", { id: e.id }), current);
      }
      if (name == null) return;
      name = String(name).trim();
      if (!name) {
        toast(tx("memory.console.name_empty", "Name cannot be empty"), "error");
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
        toast(tx("memory.console.renamed_to", "Renamed to “{name}”", { name: name }), "success");
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
      } else toast(d.error || tx("memory.console.rename_failed", "Rename failed"), "error");
    },

    async doMerge() {
      const src = (this.mergeSource || "").trim();
      const tgt = (this.mergeTarget || "").trim();
      if (!src || !tgt) {
        toast(tx("memory.page.merge_need_slots", "Set source and target entity ids (or pick on the graph)"), "error");
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
          window.kazmaCount("memory.page.merged_n", rewired, { source: src, target: tgt }),
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
      } else toast(d.error || tx("memory.console.merge_failed", "Merge failed"), "error");
    },

    async doLink() {
      const src = (this.mergeSource || "").trim();
      const tgt = (this.mergeTarget || "").trim();
      const pred = (this.linkPredicate || "related_to").trim() || "related_to";
      if (!src || !tgt) {
        toast(tx("memory.page.link_need_slots", "Set source and target for link (or pick on the graph)"), "error");
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
          tx(d.already ? "memory.page.linked_already" : "memory.page.linked",
            d.already ? "Linked {source} —{predicate}→ {target} (already linked)." : "Linked {source} —{predicate}→ {target}.",
            { source: src, predicate: pred, target: tgt }),
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
      } else toast(d.error || tx("memory.console.link_failed", "Link failed"), "error");
    },

    async decideMerge(id, approve) {
      const d = await api("/api/memory/v2/entity-merges/" + encodeURIComponent(id), {
        method: "POST",
        body: JSON.stringify({ action: approve ? "approve" : "reject" }),
      });
      if (d.ok) {
        toast(approve ? tx("memory.page.merge_approved", "Merge approved") : tx("memory.page.merge_rejected", "Merge rejected"), "success");
        await this.loadMerges();
        await this.loadEntities();
        await refreshGraph();
      } else toast(d.error || tx("memory.console.failed", "Failed"), "error");
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
          toast(tx("memory.page.hygiene_complete", "Hygiene complete"), "success");
          await this.loadAll();
          await refreshGraph();
        } else toast(d.error || tx("memory.page.hygiene_failed", "Hygiene failed"), "error");
      } finally {
        this.hygieneRunning = false;
      }
    },
  };
}
if (typeof window !== "undefined") window.memoryPage = memoryPage;
