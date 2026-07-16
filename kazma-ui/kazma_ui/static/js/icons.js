/* ═══════════════════════════════════════════════════════
   Kazma Icons — SVG line-icon registry.
   Usage in templates: <span x-html="KazmaIcons.save()"></span>
   Usage in JS: KazmaIcons.save() → returns SVG string
   All icons: fill="none" stroke="currentColor" stroke-width="1.5"
   ═══════════════════════════════════════════════════════ */

var KazmaIcons = (function () {
  // Base SVG wrapper — caller controls size via CSS (svg inherits currentColor).
  function wrap(paths, opts) {
    opts = opts || {};
    var sw = opts.strokeWidth || '1.5';
    var fill = opts.fill || 'none';
    var cls = opts.class ? ' class="' + opts.class + '"' : '';
    return '<svg' + cls + ' fill="' + fill + '" stroke="currentColor" viewBox="0 0 24 24" stroke-width="' + sw + '" stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>';
  }

  var icons = {
    // ── File operations ──
    save: function (o) { return wrap('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>', o); },
    'file-plus': function (o) { return wrap('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M12 18v-6"/><path d="M9 15h6"/>', o); },
    'file': function (o) { return wrap('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>', o); },
    'trash': function (o) { return wrap('<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>', o); },
    'folder': function (o) { return wrap('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>', o); },
    'folder-open': function (o) { return wrap('<path d="M6 14l1.45-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6A2 2 0 0 1 18.45 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h6"/>', o); },
    'folder-plus': function (o) { return wrap('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>', o); },

    // ── Actions ──
    'play': function (o) { return wrap('<polygon points="5 3 19 12 5 21 5 3"/>', o); },
    'send': function (o) { return wrap('<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>', o); },
    'refresh': function (o) { return wrap('<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>', o); },
    'plus': function (o) { return wrap('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>', o); },
    'x': function (o) { return wrap('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>', o); },
    'check': function (o) { return wrap('<polyline points="20 6 9 17 4 12"/>', o); },
    'check-circle': function (o) { return wrap('<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>', o); },
    'square': function (o) { return wrap('<rect x="6" y="6" width="12" height="12" rx="1"/>', o); },

    // ── IDE / dev ──
    'code': function (o) { return wrap('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>', o); },
    'diff': function (o) { return wrap('<path d="M12 3v12"/><path d="M9 7h6"/><path d="M9 21h6"/><path d="M6 17h12"/>', o); },
    'git-branch': function (o) { return wrap('<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>', o); },
    'terminal': function (o) { return wrap('<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>', o); },
    'wrench': function (o) { return wrap('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>', o); },
    'message': function (o) { return wrap('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>', o); },
    'sparkles': function (o) { return wrap('<path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/>', o); },

    // ── Status / alerts ──
    'alert': function (o) { return wrap('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>', o); },
    'alert-circle': function (o) { return wrap('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>', o); },
    'info': function (o) { return wrap('<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>', o); },

    // ── Metrics ──
    'dollar-sign': function (o) { return wrap('<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>', o); },
    'hash': function (o) { return wrap('<line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/>', o); },
    'plug': function (o) { return wrap('<path d="M12 22v-5"/><path d="M9 7V2"/><path d="M15 7V2"/><path d="M6 7h12v3a6 6 0 0 1-12 0V7z"/>', o); },
    'clock': function (o) { return wrap('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>', o); },
    'brain': function (o) { return wrap('<path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z"/>', o); },
    'zap': function (o) { return wrap('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>', o); },
    'bar-chart': function (o) { return wrap('<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>', o); },
    'trending-up': function (o) { return wrap('<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>', o); },

    // ── GitHub / git ──
    'github': function (o) { return wrap('<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>', o); },
    'git-fork': function (o) { return wrap('<circle cx="12" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><path d="M18 9v1c0 1.66-1.34 3-3 3H9c-1.66 0-3-1.34-3-3V9"/>', o); },
    'git-pull-request': function (o) { return wrap('<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" y1="9" x2="6" y2="21"/>', o); },
    'star': function (o) { return wrap('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 20.91 12 17.77 5.82 20.91 7 14.14 2 9.27 8.91 8.26 12 2"/>', o); },
    'bug': function (o) { return wrap('<rect x="8" y="6" width="8" height="14" rx="4"/><path d="M19 7l-3 2"/><path d="M5 7l3 2"/><path d="M19 13h-3"/><path d="M5 13h3"/><path d="M19 19l-3-2"/><path d="M5 19l3-2"/><path d="M12 3v3"/>', o); },
    'key': function (o) { return wrap('<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.778-7.778zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3"/>', o); },
    'lock': function (o) { return wrap('<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>', o); },

    // ── Navigation / misc ──
    'arrow-up': function (o) { return wrap('<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>', o); },
    'chevron-right': function (o) { return wrap('<polyline points="9 18 15 12 9 6"/>', o); },
    'chevron-down': function (o) { return wrap('<polyline points="6 9 12 15 18 9"/>', o); },
    'settings': function (o) { return wrap('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>', o); },
    'inbox': function (o) { return wrap('<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>', o); },
    'bot': function (o) { return wrap('<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/>', o); },
    'eye': function (o) { return wrap('<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>', o); },
    'eye-off': function (o) { return wrap('<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>', o); },
    'database': function (o) { return wrap('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>', o); },
    'cloud': function (o) { return wrap('<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>', o); },
    'layers': function (o) { return wrap('<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>', o); },
    'hexagon': function (o) { return wrap('<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>', o); },
  };

  return {
    get: function (name, opts) {
      var fn = icons[name];
      return fn ? fn(opts) : icons['info'](opts);
    },
    // Returns the inner SVG path string for embedding inside an existing <svg> wrapper.
    raw: function (name) {
      var fn = icons[name];
      if (!fn) return '';
      // Extract the inner content by calling with a marker and stripping the wrapper.
      var full = fn({});
      return full.replace(/^<svg[^>]*>/, '').replace(/<\/svg>$/, '');
    },
    // Register a custom icon at runtime.
    register: function (name, paths) {
      icons[name] = function (o) { return wrap(paths, o); };
    },
  };
})();

// Expose globally for non-module scripts.
window.KazmaIcons = KazmaIcons;
