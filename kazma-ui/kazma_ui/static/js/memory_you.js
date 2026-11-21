/* What Kazma knows about you (the memory framework audit's F14, part H):
 * the asker's own facts, each with where it came from, since when, what it
 * was before, and the asker's own correction. Routes: kazma_ui/memory_api.py
 * (/api/memory/v2/you); the facts: kazma_core/memory/about_you.py.
 *
 * Values are the person's own words: shown in a <bdi translate="no">, never
 * translated. A refusal is a code worded here, in the page's language.
 */
(function () {
  'use strict';

  function say(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  function day(epoch) {
    if (!epoch) return '';
    var ms = Number(epoch) * 1000;
    return window.KazmaFormat ? window.KazmaFormat.date(ms) : new Date(ms).toISOString().slice(0, 10);
  }

  var SOURCE_TEXT = {
    you: ['memory.you.source_you', 'You said it'],
    reading: ['memory.you.source_reading', "Kazma's reading of a conversation"],
    agent: ['memory.you.source_agent', 'Kazma noted it'],
    tool: ['memory.you.source_tool', 'A tool reported it'],
    unknown: ['memory.you.source_unknown', 'Source unknown'],
  };

  var REFUSALS = {
    not_found: ['memory.you.err_not_found', 'This fact has changed since the page was loaded. Reload the page.'],
    no_account: ['memory.you.err_no_account', 'Sign in as yourself to see what Kazma knows about you here.'],
    empty: ['memory.you.err_empty', 'Write the value.'],
    too_long: ['memory.you.err_too_long', 'That is too long for one fact.'],
    unchanged: ['memory.you.err_unchanged', 'That is what Kazma already holds.'],
    refused: ['memory.you.err_refused', 'Kazma could not change this fact.'],
  };

  function refusalText(code) {
    var pair = REFUSALS[code] || ['memory.you.err_failed', 'Kazma could not save this. Try again.'];
    return say(pair[0], pair[1]);
  }

  window.memoryYou = function memoryYou() {
    return {
      facts: [],
      loaded: false,
      failed: false,
      editing: '',
      draft: '',
      busy: '',
      error: '',
      errorFor: '',
      loadError: '',
      said: '',

      init() {
        this.load();
      },

      async load() {
        try {
          var res = await fetch('/api/memory/v2/you', { credentials: 'same-origin' });
          var data = await res.json().catch(function () { return null; });
          if (!data) throw new Error('read');
          if (!res.ok || !data.ok) {
            this.failed = true;
            this.loadError = data.error ? refusalText(data.error) : '';
            return;
          }
          this.facts = Array.isArray(data.facts) ? data.facts : [];
          this.failed = false;
          this.loaded = true;
        } catch (_e) {
          this.failed = true;
        }
      },

      sourceText(source) {
        var pair = SOURCE_TEXT[source] || SOURCE_TEXT.unknown;
        return say(pair[0], pair[1]);
      },

      sourceBadge(source) {
        return source === 'you' ? 'badge-success' : (source === 'unknown' ? 'badge-secondary' : 'badge-info');
      },

      datesText(fact) {
        if (fact.started) return say('memory.you.since_started', 'Since {started} (noted {since})',
          { started: fact.started, since: day(fact.since) });
        return say('memory.you.since', 'Since {since}', { since: day(fact.since) });
      },

      historyText(fact) {
        return window.kazmaCount ? window.kazmaCount('memory.you.history_count', fact.history.length)
          : 'Before (' + fact.history.length + ')';
      },

      periodText(old) {
        return say('memory.you.period', '{from} to {until}', { from: day(old.from), until: day(old.until) });
      },

      edit(fact) {
        this.editing = fact.id;
        this.draft = fact.value;
        this.error = '';
        this.errorFor = '';
        this.said = '';
        this.$nextTick(function () {
          var input = document.getElementById('memory-you-input-' + fact.id);
          if (input) { input.focus(); input.select(); }
        });
      },

      cancel() {
        this.editing = '';
        this.draft = '';
      },

      async save(fact) {
        if (!this.draft.trim() || this.busy) return;
        this.busy = fact.id;
        this.error = '';
        this.errorFor = '';
        try {
          var res = await fetch('/api/memory/v2/you/' + encodeURIComponent(fact.id) + '/correct', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value: this.draft }),
          });
          var data = await res.json().catch(function () { return {}; });
          if (!res.ok || !data.ok) {
            this.error = refusalText(data && data.error);
            this.errorFor = fact.id;
            return;
          }
          this.editing = '';
          this.draft = '';
          this.said = say('memory.you.corrected', 'Corrected. You can undo it under Changes on the Memory page.');
          await this.load();
        } catch (_e) {
          this.error = refusalText('');
          this.errorFor = fact.id;
        } finally {
          this.busy = '';
        }
      },

      async end(fact) {
        if (this.busy) return;
        var ok = window.kazmaConfirm ? await window.kazmaConfirm({
          title: say('memory.you.end_title', 'No longer true?'),
          message: say('memory.you.end_message', 'Kazma will stop treating this as true. It stays in the history.'),
          confirmText: say('memory.you.end', 'No longer true'),
          danger: false,
        }) : true;
        if (!ok) return;
        this.busy = fact.id;
        this.error = '';
        this.errorFor = '';
        try {
          var res = await fetch('/api/memory/v2/you/' + encodeURIComponent(fact.id) + '/end', {
            method: 'POST', credentials: 'same-origin',
          });
          var data = await res.json().catch(function () { return {}; });
          if (!res.ok || !data.ok) {
            this.error = refusalText(data && data.error);
            this.errorFor = fact.id;
            return;
          }
          this.said = say('memory.you.ended', 'Ended. You can undo it under Changes on the Memory page.');
          await this.load();
        } catch (_e) {
          this.error = refusalText('');
          this.errorFor = fact.id;
        } finally {
          this.busy = '';
        }
      },
    };
  };
})();
