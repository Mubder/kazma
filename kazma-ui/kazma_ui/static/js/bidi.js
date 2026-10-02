/**
 * Kazma bidirectional text helper.
 *
 * English UI (dir=ltr) and Arabic UI (dir=rtl) both must render mixed
 * Arabic+Latin text in the correct order.
 *
 * Strategy:
 *  1. If content is Arabic-dominant → base direction rtl (even on English UI)
 *  2. If content is Latin-dominant → base direction ltr
 *  3. Isolate Latin runs inside RTL content (and Arabic runs inside LTR)
 *     so product names / acronyms (LASIK, PDF) keep correct letter order.
 */
(function () {
  'use strict';

  var ARABIC_RE = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;
  var LATIN_RE = /[A-Za-z0-9]/;

  function hasArabic(text) {
    return !!(text && ARABIC_RE.test(String(text)));
  }

  function isArabicDominant(text) {
    var s = String(text || '');
    var ar = (s.match(/[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/g) || []).length;
    var en = (s.match(/[A-Za-z]/g) || []).length;
    if (ar === 0) return false;
    // Arabic wins when it is the majority of letters, or significant share
    return ar >= en || (ar > 20 && ar >= en * 0.4);
  }

  // Letters of the right-to-left scripts (Hebrew, Arabic, Syriac, Thaana,
  // N'Ko, Samaritan, Mandaic, and the presentation forms). Every other
  // letter is left-to-right; digits and punctuation are neither.
  var RTL_LETTER_RE = /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/;
  var LETTER_RE = /\p{L}/u;
  // Arabic punctuation: a word carrying one was written in Arabic.
  var ARABIC_PUNCT_RE = /[\u060C\u061B\u061F\u06D4]/;
  var UPPER_RE = /\p{Lu}/u;

  /**
   * The direction of one paragraph: the language its words are written in,
   * and on a tie its first letter's. '' when it has no letters (a number,
   * a rule).
   *
   * Text follows its own language, whatever the UI's (the owner's rule,
   * 2026-10-02). dir="auto" asks only the first letter, so "PDF \u0627\u0644\u0645\u0644\u0641
   * \u062C\u0627\u0647\u0632" came out left-to-right; counting letters lays an Arabic sentence
   * holding one long path out left-to-right. So words are counted: a URL, a
   * path or an identifier is one word, and inline code and a link's target
   * are not the paragraph's words. A word with an Arabic letter or Arabic
   * punctuation is Arabic ("\u0648DOCX", "Telegram\u060C": Arabic grammar around a
   * term). A Latin word that looks like a name -- an acronym, CamelCase, a
   * digit in it, a capital away from a sentence's start (PDF, MyCloud, OS5,
   * Python) -- counts a quarter: Arabic technical prose is full of them,
   * and "WebDAV \u064A\u0639\u0645\u0644 \u0645\u0639 WD MyCloud OS5 \u0648\u0623\u064A \u062C\u0647\u0627\u0632 NAS" is an Arabic sentence.
   */
  function blockDir(text) {
    var s = String(text || '').replace(/`[^`]*`/g, ' ').replace(/\]\([^)]*\)/g, '] ');
    var words = s.split(/\s+/);
    var rtl = 0, ltr = 0, first = '', sentenceStart = true;
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      var r = 0, l = 0, uppers = 0, lead = '', firstLetterUpper = false, digit = /\d/.test(w);
      for (var ch of w) {
        if (!LETTER_RE.test(ch)) continue;
        if (RTL_LETTER_RE.test(ch)) { r++; if (!lead) lead = 'rtl'; }
        else {
          l++;
          if (UPPER_RE.test(ch)) { uppers++; if (!lead) firstLetterUpper = true; }
          if (!lead) lead = 'ltr';
        }
      }
      var startsSentence = sentenceStart;
      // A sentence ends at . ! ? : (and their Arabic forms); the next word
      // may start with a capital without being a name.
      sentenceStart = /[.!?:\u061F\u06D4]["')\]]*$/.test(w) || (!lead && sentenceStart);
      if (!lead) {
        if (ARABIC_PUNCT_RE.test(w)) rtl += 1;
        continue;
      }
      if (!first) first = lead;
      if (r || ARABIC_PUNCT_RE.test(w)) { rtl += 1; continue; }
      var nameLike = digit || uppers >= 2 || (firstLetterUpper && !startsSentence);
      ltr += nameLike ? 0.25 : 1;
    }
    if (!rtl && !ltr) return '';
    if (!rtl) return 'ltr';
    if (!ltr) return 'rtl';
    if (Math.abs(rtl - ltr) > 1e-9) return rtl > ltr ? 'rtl' : 'ltr';
    return first;
  }

  /** The direction a run stands against: its nearest block's own, else *fallback*. */
  function runBase(node, root, fallback) {
    for (var n = node; n && n !== root; n = n.parentNode) {
      var d = n.getAttribute ? n.getAttribute('dir') : null;
      if (d === 'rtl' || d === 'ltr') return d;
    }
    return fallback;
  }

  /**
   * Walk text nodes under el and wrap Latin-only runs in dir=ltr isolate
   * when the base direction is RTL (and vice-versa for Arabic runs).
   */
  function isolateRuns(el, baseDir) {
    if (!el || !el.childNodes) return;
    var walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    var nodes = [];
    while (walk.nextNode()) nodes.push(walk.currentNode);
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var parent = node.parentNode;
      if (!parent || !node.nodeValue) continue;
      // Skip script/style/code (code already LTR)
      var tag = (parent.tagName || '').toLowerCase();
      if (tag === 'script' || tag === 'style' || tag === 'code' || tag === 'pre') continue;
      // A run this pass or an earlier one already isolated.
      if (parent.classList && parent.classList.contains('bidi-isolate')) continue;
      // Don't re-process text inside list items that already have dir="auto"
      // — the browser handles their directionality natively
      if ((tag === 'li' || tag === 'ul' || tag === 'ol') && parent.getAttribute && parent.getAttribute('dir') === 'auto') continue;

      // A run stands out against its own paragraph, never the message: the
      // renderer gives each block its direction (blockDir). Isolated against
      // the message, an English-first reply's Arabic paragraph had its own
      // words hidden from its dir="auto" and came out left-to-right.
      var dir = runBase(parent, el, baseDir);
      var text = node.nodeValue;
      if (dir === 'rtl') {
        if (!LATIN_RE.test(text) || !ARABIC_RE.test(text) && !/[A-Za-z]{2,}/.test(text)) {
          // pure Arabic or no multi-letter Latin — leave alone unless pure Latin acronym line
          if (/^[A-Za-z0-9][A-Za-z0-9 .,\-_/()%+]{1,}$/.test(text.trim()) && /[A-Za-z]{2,}/.test(text)) {
            wrapNode(node, 'ltr');
          }
          continue;
        }
        // Split mixed text into Arabic vs Latin chunks
        splitAndWrap(node, 'rtl');
      } else if (dir === 'ltr' && ARABIC_RE.test(text)) {
        splitAndWrap(node, 'ltr');
      }
    }
  }

  function wrapNode(textNode, dir) {
    var span = document.createElement('span');
    span.setAttribute('dir', dir);
    span.className = 'bidi-isolate';
    span.style.unicodeBidi = 'isolate';
    textNode.parentNode.insertBefore(span, textNode);
    span.appendChild(textNode);
  }

  function splitAndWrap(textNode, baseDir) {
    var text = textNode.nodeValue;
    // Chunk: sequences of Latin/digits/punct vs Arabic
    var re = /([A-Za-z0-9][A-Za-z0-9 .,\-_/()%+]*[A-Za-z0-9]|[A-Za-z0-9]+)|([\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF][\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\s\u060C\u061B\u061F\u0640]*)/g;
    var parts = [];
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) {
        parts.push({ t: text.slice(last, m.index), kind: 'other' });
      }
      if (m[1]) parts.push({ t: m[1], kind: 'latin' });
      else if (m[2]) parts.push({ t: m[2], kind: 'arabic' });
      last = m.index + m[0].length;
    }
    if (last < text.length) parts.push({ t: text.slice(last), kind: 'other' });
    if (parts.length <= 1) {
      if (parts[0] && parts[0].kind === 'latin' && baseDir === 'rtl') wrapNode(textNode, 'ltr');
      if (parts[0] && parts[0].kind === 'arabic' && baseDir === 'ltr') wrapNode(textNode, 'rtl');
      return;
    }
    var frag = document.createDocumentFragment();
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (p.kind === 'latin' && baseDir === 'rtl') {
        var s = document.createElement('span');
        s.setAttribute('dir', 'ltr');
        s.className = 'bidi-isolate';
        s.style.unicodeBidi = 'isolate';
        s.textContent = p.t;
        frag.appendChild(s);
      } else if (p.kind === 'arabic' && baseDir === 'ltr') {
        var s2 = document.createElement('span');
        s2.setAttribute('dir', 'rtl');
        s2.className = 'bidi-isolate';
        s2.style.unicodeBidi = 'isolate';
        s2.textContent = p.t;
        frag.appendChild(s2);
      } else {
        frag.appendChild(document.createTextNode(p.t));
      }
    }
    textNode.parentNode.replaceChild(frag, textNode);
  }

  function apply(el, textHint) {
    if (!el || !el.setAttribute) return;
    var sample = textHint != null ? String(textHint) : (el.textContent || '');
    el.classList.add('bidi-content');
    var arDom = isArabicDominant(sample);
    var hasAr = hasArabic(sample);
    if (arDom) {
      el.setAttribute('dir', 'rtl');
      el.classList.add('ar-dominant');
      el.classList.remove('en-dominant');
      try { isolateRuns(el, 'rtl'); } catch (e) { /* ignore */ }
    } else if (hasAr) {
      el.setAttribute('dir', 'auto');
      el.classList.remove('ar-dominant');
      el.classList.remove('en-dominant');
      try { isolateRuns(el, 'ltr'); } catch (e) { /* ignore */ }
    } else {
      el.setAttribute('dir', 'ltr');
      el.classList.add('en-dominant');
      el.classList.remove('ar-dominant');
    }
  }

  function isolateMathSpans(root) {
    root = root || document;
    try {
      var mathNodes = root.querySelectorAll ? root.querySelectorAll('.katex, .math, .math-inline, .math-block, [data-math]') : [];
      for (var i = 0; i < mathNodes.length; i++) {
        var m = mathNodes[i];
        m.setAttribute('dir', 'ltr');
        m.style.unicodeBidi = 'isolate';
        m.style.direction = 'ltr';
        m.classList.add('math-bidi-isolate');
      }
    } catch (e) { /* ignore */ }
  }

  function applyAll(root, selector) {
    root = root || document;
    selector = selector || [
      '.message-text',
      '.markdown-body',
      '#research-detail-output',
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
      isolateMathSpans(root);
    } catch (e) { /* ignore */ }
  }

  /**
   * Cron/HITL wrappers bury the tweet the operator wants to read:
   *   Call x_post with EXACTLY this text: "كاظمه…"
   * First-strong-char dir=auto stays LTR because the wrapper is English.
   */
  // A quoted fragment is the text only when the wrapper hands it over (a
  // colon before it, nothing after it) or when it is most of the message
  // (60+ characters, or half of it); a blockquote only where it is handed
  // over -- a mid-sentence ">" is an arrow. The same rule as
  // kazma_core.text_display.extract_post_body: both are held to
  // tests/fixtures/post_body_cases.json. A reminder saying 'Reply "done" to
  // stop' showed on the Scheduled page as "done" (2026-09-28).
  var MIN_BODY_CHARS = 60, MIN_BODY_SHARE = 0.5;
  function cpLen(s) { return Array.from(String(s)).length; }  // code points, as Python counts
  function handedOver(before) {
    var b = before.replace(/\s+$/, '');
    return b.charAt(b.length - 1) === ':';
  }

  function extractPostBody(text) {
    var raw = String(text || '').replace(/\s+/g, ' ').trim();
    if (!raw) return '';
    var found = [];  // [fragment, introduced]
    var re = /["“]([^"”]{4,})["”]/g;
    var m;
    while ((m = re.exec(raw))) {
      var after = raw.slice(m.index + m[0].length).trim();
      found.push([m[1].trim(), handedOver(raw.slice(0, m.index)) && cpLen(after) <= 2]);
    }
    m = raw.match(/:\s*["“]([^"”]{4,})\s*$/);
    if (m) found.push([m[1].trim(), true]);
    m = /[>]\s*(.+)$/.exec(raw);
    if (m && (handedOver(raw.slice(0, m.index)) || !raw.slice(0, m.index).trim())) {
      found.push([m[1].replace(/^[\s"“”]+|[\s"“”]+$/g, ''), true]);
    }
    var candidates = [];
    for (var k = 0; k < found.length; k++) {
      var c = found[k][0];
      if (found[k][1] || cpLen(c) >= MIN_BODY_CHARS || cpLen(c) >= MIN_BODY_SHARE * cpLen(raw)) {
        candidates.push(c);
      }
    }
    var arabic = [];
    for (var i = 0; i < candidates.length; i++) {
      if (hasArabic(candidates[i])) arabic.push(candidates[i]);
    }
    var pool = arabic.length ? arabic : candidates;
    if (!pool.length) return raw;
    var best = pool[0];
    for (var j = 1; j < pool.length; j++) {
      if (cpLen(pool[j]) > cpLen(best)) best = pool[j];
    }
    return best;
  }

  function displayKicker(text, body) {
    var raw = String(text || '').replace(/\s+/g, ' ').trim();
    if (!raw) return '';
    var batch = raw.match(/batch\s+job\s+(\d+\s*\/\s*\d+)/i);
    if (batch) return 'Batch ' + batch[1].replace(/\s+/g, '');
    body = body != null ? String(body) : extractPostBody(raw);
    if (!body || body === raw) return '';
    var kicker = raw.split(body).join(' ').replace(/["“”]/g, '');
    kicker = kicker.replace(/\s+/g, ' ').trim().replace(/^[\s—\-:;]+|[\s—\-:;]+$/g, '');
    if (kicker.indexOf(' — ') !== -1) kicker = kicker.split(' — ')[0];
    else if (kicker.indexOf('. ') !== -1) kicker = kicker.split('. ')[0];
    return kicker.slice(0, 80);
  }

  function shortenOutcome(text) {
    var raw = String(text || '').replace(/\s+/g, ' ').trim();
    if (!raw) return '';
    var bold = raw.match(/\*\*([^*]+)\*\*/);
    if (bold) return bold[1].trim().slice(0, 120);
    var head = raw.split(/[:：]\s*["“>]/)[0];
    head = head.replace(/^\s*Status for\s+/i, '').replace(/^[\s—\-]+|[\s—\-]+$/g, '');
    return head.slice(0, 120);
  }

  function pageDir() {
    try {
      return (document.documentElement.getAttribute('dir') || 'ltr').toLowerCase();
    } catch (e) {
      return 'ltr';
    }
  }

  function textDir(text) {
    // Any Arabic → rtl. Empty inherits the PAGE dir (Arabic UI is rtl;
    // forcing ltr on an empty composer was why /x stayed LTR). Latin-only
    // tweets isolate ltr so they don't pick up the page rtl.
    var raw = String(text || '');
    var sample = extractPostBody(raw) || raw;
    if (hasArabic(sample) || hasArabic(raw)) return 'rtl';
    if (!raw.trim()) return pageDir();
    return 'ltr';
  }

  window.KazmaBidi = {
    hasArabic: hasArabic,
    isArabicDominant: isArabicDominant,
    blockDir: blockDir,
    apply: apply,
    applyAll: applyAll,
    isolateRuns: isolateRuns,
    extractPostBody: extractPostBody,
    displayKicker: displayKicker,
    shortenOutcome: shortenOutcome,
    textDir: textDir,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      applyAll(document);
    });
  } else {
    applyAll(document);
  }
})();
