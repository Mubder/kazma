/* Ready-made automations (REMAINING 4.3): the Scheduled page's gallery.
 * The cards are the page's (scheduled.html); this fills each time from the
 * recipe (GET /api/scheduled/recipes) and books one with a click
 * (POST /api/scheduled/recipes/<id>), then refreshes the page's list.
 */
(function () {
  'use strict';

  function tr(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  window.automationGallery = function automationGallery() {
    return {
      times: {},
      topics: {},
      booking: '',
      bookLabel: '',
      bookingLabel: '',

      async init() {
        var root = this.$root || this.$el;
        this.bookLabel = (root && root.dataset.bookLabel) || tr('scheduled.recipe_book', 'Set it up');
        this.bookingLabel = (root && root.dataset.bookingLabel) || tr('scheduled.recipe_booking', 'Setting up…');
        var data = window.kazmaGetJson ? await window.kazmaGetJson('/api/scheduled/recipes') : null;
        var recipes = (data && data.recipes) || [];
        var times = {};
        recipes.forEach(function (r) { times[r.id] = r.at; });
        this.times = Object.assign(times, this.times);
      },

      canBook(id, needsTopic) {
        if (this.booking !== '' || !this.times[id]) return false;
        return !needsTopic || !!String(this.topics[id] || '').trim();
      },

      async book(id) {
        this.booking = id;
        try {
          await window.kazmaSave('/api/scheduled/recipes/' + encodeURIComponent(id), {
            method: 'POST', body: { at: this.times[id] || '', topic: this.topics[id] || '' },
          });
          if (typeof window.showToast === 'function') {
            window.showToast(tr('scheduled.recipe_booked', 'Set up: it is in your scheduled tasks.'), 'success');
          }
          if (typeof this.refreshAll === 'function') this.refreshAll();
        } catch (e) {
          // A refusal the server names by its code is said in the page's
          // language (no_own_address: link a chat app of your own, S3-05).
          var why = (e && e.message) || '';
          var code = e && e.body && e.body.code;
          if (code) why = tr('scheduled.err_' + code, why);
          if (typeof window.showToast === 'function') {
            window.showToast(tr('scheduled.recipe_failed', 'Not set up: {why}', { why: why }), 'error');
          }
        } finally {
          this.booking = '';
        }
      },
    };
  };
})();
