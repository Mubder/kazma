/**
 * Memory admin page — beliefs, entities, merge/link, hygiene.
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

  return {
    S,
    loading: false,
    error: "",
    tab: "entities",
    summary: {},
    beliefs: [],
    beliefQ: "",
    selectedBeliefs: [],
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
        toast("Invalidated " + id.slice(0, 16), "success");
        await this.loadBeliefs();
        await this.loadSummary();
      } else toast(d.error || "Failed", "error");
    },

    async invalidateSelected() {
      if (!this.selectedBeliefs.length) return;
      const ok = await confirm({
        title: S.invalidate || "Invalidate",
        message: (S.confirm_invalidate || "Invalidate selected?") + " (" + this.selectedBeliefs.length + ")",
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
      } else toast(d.error || "Failed", "error");
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
        await this.loadEntities();
        await this.loadSummary();
        await this.loadHygiene();
      } else toast(d.error || "Failed", "error");
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
        message: (S.confirm_merge || "Merge source into target? Beliefs rewired.") +
          "\n" + src + " → " + tgt,
      });
      if (!ok) return;
      const d = await api("/api/memory/v2/entities/merge", {
        method: "POST",
        body: JSON.stringify({ source_id: src, target_id: tgt }),
      });
      if (d.ok) {
        toast("Merged " + src + " → " + tgt, "success");
        this.mergeSource = "";
        await this.loadEntities();
        await this.loadSummary();
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
        } else toast(d.error || "Hygiene failed", "error");
      } finally {
        this.hygieneRunning = false;
      }
    },
  };
}
