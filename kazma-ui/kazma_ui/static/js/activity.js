/* What was done in this space, under each person's name (Spaces S2-09).
 * Route: kazma_ui/activity.py (/api/activity); the rows:
 * kazma_core/spaces/activity.py. A row is a route and its answer, never
 * what was sent: the route is shown as written (code), a person's name as
 * they wrote it, both in a <bdi translate="no">.
 */
(function () {
  'use strict';

  function say(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  window.activityPage = function activityPage() {
    return {
      actions: [],
      loaded: false,
      failed: false,
      more: false,
      busy: false,

      init() {
        this.load();
      },

      async _read(before) {
        var url = '/api/activity' + (before ? '?before=' + encodeURIComponent(before) : '');
        var data = typeof window.kazmaGetJson === 'function' ? await window.kazmaGetJson(url) : null;
        if (!data || !Array.isArray(data.actions)) throw new Error('read');
        return data;
      },

      async load() {
        try {
          var data = await this._read(null);
          this.actions = data.actions;
          this.more = !!data.more;
          this.failed = false;
          this.loaded = true;
        } catch (_e) {
          this.failed = true;
        }
      },

      async loadMore() {
        if (this.busy || !this.actions.length) return;
        this.busy = true;
        try {
          var data = await this._read(this.actions[this.actions.length - 1].id);
          this.actions = this.actions.concat(data.actions);
          this.more = !!data.more;
        } catch (_e) {
          if (typeof window.showToast === 'function') {
            window.showToast(say('activity.failed', 'The activity could not be read. Try again.'), 'error');
          }
        } finally {
          this.busy = false;
        }
      },

      isoOf(at) {
        return new Date(Number(at) * 1000).toISOString();
      },

      whenOf(at) {
        var ms = Number(at) * 1000;
        return window.KazmaFormat ? window.KazmaFormat.dateTime(ms) : new Date(ms).toISOString();
      },

      _kind(status) {
        var n = Number(status);
        if (n >= 200 && n < 400) return 'done';
        if (n >= 500) return 'failed';
        return 'refused';
      },

      badgeOf(status) {
        return { done: 'badge-success', refused: 'badge-warning', failed: 'badge-danger' }[this._kind(status)];
      },

      answerOf(status) {
        var words = {
          done: ['activity.done', 'Done'],
          refused: ['activity.refused', 'Refused'],
          failed: ['activity.failed_one', 'Failed'],
        }[this._kind(status)];
        return say(words[0], words[1]);
      },
    };
  };
})();
