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
    hygienePreview: {},
    hygiene: {
      purge_empty_entities: true,
      invalidate_near_dup_noted: false,
      archive_invalidated: false,
    },
    hygieneRunning: false,
    _graphListener: null,

    get tabs() {
      // Graph & health is pinned at the top of the page (not a tab).
      return [
        { id: "entities", label: S.tab_entities || "Entities" },
        { id: "beliefs", label: S.tab_beliefs || "Beliefs" },
        { id: "merges", label: S.tab_merges || "Pending merges" },
        { id: "hygiene", label: S.tab_hygiene || "Hygiene" },
      ];
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

    async init() {
      await this.loadAll();
      // Graph → list: highlight matching entity / belief.
      // Canvas double-click sends this event (single click stays on the graph).
      this._graphListener = (ev) => {
        const d = (ev && ev.detail) || {};
        if (d.type === "entity" && d.id) {
          this.selectedEntityId = d.id;
          this.selectedBeliefId = null;
          if (this.tab !== "entities") {
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
          if (this.tab !== "beliefs") {
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
    },

    destroy() {
      if (this._graphListener) {
        window.removeEventListener("kazma:memory-graph-select", this._graphListener);
        this._graphListener = null;
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

    async loadBeliefs() {
      const q = encodeURIComponent(this.beliefQ || "");
      const d = await api("/api/memory/v2/beliefs?q=" + q + "&limit=100");
      this.beliefs = d.beliefs || [];
      this.selectedBeliefs = [];
    },

    async loadEntities() {
      const params = new URLSearchParams({
        q: this.entityQ || "",
        limit: "150",
        empty_only: this.emptyOnly ? "true" : "false",
        isolated_only: this.isolatedOnly ? "true" : "false",
      });
      const d = await api("/api/memory/v2/entities?" + params.toString());
      this.entities = d.entities || [];
    },

    async loadMerges() {
      const d = await api("/api/memory/v2/entity-merges?limit=50");
      this.merges = d.merges || [];
    },

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
      if (opts.asSource) this.mergeSource = id;
      if (opts.asTarget) this.mergeTarget = id;
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
        toast("Invalidated " + d.invalidated, "success");
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
        toast("Belief updated", "success");
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
        toast("Deleted " + e.id, "success");
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
        toast("Set source and target entity ids", "error");
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
        toast("Merged " + src + " → " + tgt, "success");
        this.mergeSource = "";
        this.selectedEntityId = tgt;
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
        toast("Set source and target for link", "error");
        return;
      }
      const d = await api("/api/memory/v2/entities/link", {
        method: "POST",
        body: JSON.stringify({ subject: src, predicate: pred, object: tgt }),
      });
      if (d.ok) {
        toast("Linked " + src + " —" + pred + "→ " + tgt, "success");
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
