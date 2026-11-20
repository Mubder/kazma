/* "Memory here is shared with ..." (Spaces S1-11, decision N35).
 *
 * In a shared space every member's facts and turns are recalled for every
 * member, each under its owner's name. The space says so before anyone types
 * something private: every element marked [data-shared-memory-notice] (near
 * the chat's composer, on the Memory page) shows the other members' names, in
 * the page's language. A personal space, and an install that does not enforce
 * Spaces, show nothing and ask nothing.
 */
(function () {
  "use strict";

  function enforced() {
    var meta = document.querySelector('meta[name="kazma-spaces-enforced"]');
    return !!meta && meta.getAttribute("content") === "1";
  }

  function names(list) {
    var lang = document.documentElement.getAttribute("lang") || "en";
    try {
      return new Intl.ListFormat(lang, { style: "long", type: "conjunction" }).format(list);
    } catch (e) {
      return list.join(", ");
    }
  }

  // The names are content (each person's own), the sentence the page's: the
  // names go in a <bdi translate="no"> so an Arabic sentence keeps Latin
  // names whole and in order, and the reverse.
  var MARK = "\u0001";

  function show(members) {
    var text = window.kazmaT
      ? window.kazmaT("memory.shared_notice", "Memory here is shared with {names}.", { names: MARK })
      : "Memory here is shared with " + MARK + ".";
    var parts = text.split(MARK);
    document.querySelectorAll("[data-shared-memory-notice]").forEach(function (el) {
      el.textContent = "";
      parts.forEach(function (part, i) {
        if (i > 0) {
          var who = document.createElement("bdi");
          who.setAttribute("translate", "no");
          who.textContent = names(members);
          el.appendChild(who);
        }
        if (part) el.appendChild(document.createTextNode(part));
      });
      el.hidden = false;
    });
  }

  function hide() {
    document.querySelectorAll("[data-shared-memory-notice]").forEach(function (el) {
      el.hidden = true;
      el.textContent = "";
    });
  }

  async function refresh() {
    if (!document.querySelector("[data-shared-memory-notice]")) return;
    var shown = false;
    if (enforced()) {
      var data = window.kazmaGetJson ? await window.kazmaGetJson("/api/memory/v2/profile") : null;
      shown = !!(data && data.shared && Array.isArray(data.members) && data.members.length);
      if (shown) show(data.members);
    }
    if (!shown) hide();
    // Says the notice was decided (shown or not), for a page that waits on it.
    document.documentElement.setAttribute("data-shared-memory-state", shown ? "shown" : "none");
  }

  window.kazmaSharedMemoryNotice = { refresh: refresh, names: names };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", refresh);
  else refresh();
})();
