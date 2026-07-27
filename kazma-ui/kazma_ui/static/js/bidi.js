/**
 * Kazma bidirectional text helper.
 *
 * English UI (dir=ltr) and Arabic UI (dir=rtl) both must render mixed
 * Arabic+English text in the correct visual order. We use:
 *   - unicode-bidi: plaintext (CSS) on content containers
 *   - dir="auto" on elements that contain Arabic script
 *
 * Safe to call repeatedly after streaming markdown updates.
 */
(function () {
  'use strict';

  var ARABIC_RE = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;

  function hasArabic(text) {
    return !!(text && ARABIC_RE.test(String(text)));
  }

  /**
   * Mark an element for correct mixed-script layout.
   * @param {Element|null} el
   * @param {string} [textHint] optional raw text if el is empty yet
   */
  function apply(el, textHint) {
    if (!el || !el.setAttribute) return;
    var sample = textHint != null ? String(textHint) : (el.textContent || '');
    el.classList.add('bidi-content');
    // dir=auto lets the browser pick base direction from first strong char
    // for *this* block, independent of page UI language.
    if (hasArabic(sample) || (el.getAttribute('dir') === 'auto')) {
      el.setAttribute('dir', 'auto');
    } else if (!el.getAttribute('dir')) {
      // Keep page default for pure Latin; still use plaintext CSS.
      el.setAttribute('dir', 'auto');
    }
  }

  /**
   * Apply to all matching nodes under root (default: document).
   * @param {ParentNode} [root]
   * @param {string} [selector]
   */
  function applyAll(root, selector) {
    root = root || document;
    selector = selector || [
      '.message-text',
      '.message-content',
      '.hitl-approval-message',
      '.skill-desc',
      '.step-detail',
      '.agent-plan-item .plan-text',
      '[data-bidi="auto"]'
    ].join(',');
    try {
      var nodes = root.querySelectorAll ? root.querySelectorAll(selector) : [];
      for (var i = 0; i < nodes.length; i++) apply(nodes[i]);
      if (root.nodeType === 1 && root.matches && root.matches(selector)) {
        apply(root);
      }
    } catch (e) { /* ignore */ }
  }

  window.KazmaBidi = {
    hasArabic: hasArabic,
    apply: apply,
    applyAll: applyAll,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      applyAll(document);
    });
  } else {
    applyAll(document);
  }
})();
