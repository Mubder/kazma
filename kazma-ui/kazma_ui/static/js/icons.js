/* ═══════════════════════════════════════════════════════
   Kazma Icons — SVG line-icon registry.
   Usage in templates: <span x-html="KazmaIcons.save()"></span>
   Usage in JS: KazmaIcons.save() → returns SVG string
   All icons: fill="none" stroke="currentColor" stroke-width="1.5"
   ═══════════════════════════════════════════════════════ */

var KazmaIcons = (function () {
  // Base SVG wrapper — icons default to 1em (inherit surrounding font-size, like
  // emoji). The .icon-sm/.icon-md/.icon-lg CSS classes override when needed.
  function wrap(paths, opts) {
    opts = opts || {};
    var sw = opts.strokeWidth || '1.5';
    var fill = opts.fill || 'none';
    var cls = opts.class ? ' class="' + opts.class + '"' : '';
    return '<svg' + cls + ' width="1em" height="1em" fill="' + fill + '" stroke="currentColor" viewBox="0 0 24 24" stroke-width="' + sw + '" stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>';
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

    // ── Expanded set (emoji replacements) ──
    'rocket': function (o) { return wrap('<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>', o); },
    'clipboard': function (o) { return wrap('<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>', o); },
    'file-text': function (o) { return wrap('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>', o); },
    'swarm': function (o) { return wrap('<circle cx="12" cy="12" r="2"/><circle cx="5" cy="7" r="2"/><circle cx="19" cy="7" r="2"/><circle cx="5" cy="17" r="2"/><circle cx="19" cy="17" r="2"/><path d="M7 8l3 3"/><path d="M14 11l3-3"/><path d="M7 16l3-3"/><path d="M14 13l3 3"/>', o); },
    'target': function (o) { return wrap('<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>', o); },
    'laptop': function (o) { return wrap('<path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0l1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16"/>', o); },
    'scroll': function (o) { return wrap('<path d="M8 21h12a2 2 0 0 0 2-2v-2H10v2a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v3h4"/><path d="M19 17V5a2 2 0 0 0-2-2H4"/>', o); },
    'link': function (o) { return wrap('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>', o); },
    'package': function (o) { return wrap('<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>', o); },
    'flag': function (o) { return wrap('<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>', o); },
    'palette': function (o) { return wrap('<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>', o); },
    'gamepad': function (o) { return wrap('<line x1="6" y1="12" x2="10" y2="12"/><line x1="8" y1="10" x2="8" y2="14"/><line x1="15" y1="13" x2="15.01" y2="13"/><line x1="18" y1="11" x2="18.01" y2="11"/><rect x="2" y="6" width="20" height="12" rx="2"/>', o); },
    'upload': function (o) { return wrap('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>', o); },
    'image': function (o) { return wrap('<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>', o); },
    'search': function (o) { return wrap('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>', o); },
    'edit': function (o) { return wrap('<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>', o); },
    'atom': function (o) { return wrap('<circle cx="12" cy="12" r="1"/><path d="M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5z"/><path d="M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5z"/>', o); },
    'circle': function (o) { return wrap('<circle cx="12" cy="12" r="10"/>', o); },
    'dot': function (o) {
      return wrap('<circle cx="12" cy="12" r="5"/>', Object.assign({ fill: 'currentColor', strokeWidth: '0' }, o || {}));
    },
    'mic': function (o) { return wrap('<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>', o); },
    'volume': function (o) { return wrap('<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>', o); },
    'users': function (o) { return wrap('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>', o); },
    'home': function (o) { return wrap('<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>', o); },
    'list': function (o) { return wrap('<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>', o); },
    'grid': function (o) { return wrap('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>', o); },
    'activity': function (o) { return wrap('<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>', o); },
    'shield': function (o) { return wrap('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>', o); },
    'globe': function (o) { return wrap('<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>', o); },
    'server': function (o) { return wrap('<rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>', o); },
    'cpu': function (o) { return wrap('<rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>', o); },
    'sliders': function (o) { return wrap('<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>', o); },
    'log-out': function (o) { return wrap('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>', o); },
    'user': function (o) { return wrap('<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>', o); },
    'smile': function (o) { return wrap('<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>', o); },
    'book': function (o) { return wrap('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>', o); },
    'pause': function (o) { return wrap('<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>', o); },
    'chevron-left': function (o) { return wrap('<polyline points="15 18 9 12 15 6"/>', o); },
    'chevron-up': function (o) { return wrap('<polyline points="18 15 12 9 6 15"/>', o); },
    'monitor': function (o) { return wrap('<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>', o); },
    'construction': function (o) { return wrap('<rect x="2" y="6" width="20" height="8" rx="1"/><path d="M17 14v7"/><path d="M7 14v7"/><path d="M17 3v3"/><path d="M7 3v3"/><path d="M10 14 2.3 6.3"/><path d="m14 6 7.7 7.7"/><path d="m8 6 8 8"/>', o); },
    'box': function (o) { return wrap('<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.29 7 12 12 20.71 7"/><line x1="12" y1="22" x2="12" y2="12"/>', o); },
  };

  // Emoji glyph → icon name (for JS string cleanup + hydrate)
  var EMOJI_TO_ICON = {
    '⚙️': 'settings', '⚙': 'settings',
    '📊': 'bar-chart', '📝': 'file-text', '🔧': 'wrench', '🛠️': 'wrench', '🛠': 'wrench',
    '🚀': 'rocket', '📋': 'clipboard', '🔀': 'git-branch', '🐝': 'swarm',
    '⚠️': 'alert', '⚠': 'alert', '🙈': 'eye-off',
    '✓': 'check', '✅': 'check-circle', '❌': 'x', '✕': 'x', '✗': 'x',
    '🧠': 'brain', '👁': 'eye', '👁️': 'eye', '🎯': 'target',
    '🔑': 'key', '🔐': 'lock', '🔒': 'lock',
    '💻': 'laptop', '📄': 'file', '▶': 'play',
    '📭': 'inbox', '💬': 'message', '📜': 'scroll', '🔗': 'link',
    '📁': 'folder', '📂': 'folder-open', '📦': 'package',
    '🏁': 'flag', '🔢': 'hash', '🐍': 'code', '💰': 'dollar-sign',
    '▼': 'chevron-down', '🎨': 'palette', '🎮': 'gamepad', '👷': 'bot',
    '📤': 'upload', '🗑️': 'trash', '🗑': 'trash', '🌀': 'refresh',
    '🐙': 'github', '🤖': 'bot', '⚛️': 'atom', '⚛': 'atom',
    '🖼': 'image', '🖼️': 'image', '🔌': 'plug', '💾': 'save',
    '🔍': 'search', '🆕': 'sparkles', '✍️': 'edit', '✍': 'edit',
    '➤': 'chevron-right', '➜': 'chevron-right', '●': 'dot',
    '🛡': 'shield', '🛡️': 'shield',
    '🖥': 'monitor', '🖥️': 'monitor',
    '🔄': 'refresh',
    '🕵': 'search', '🕵️': 'search',
    '✏': 'edit', '✏️': 'edit',
    '🏗': 'construction', '🏗️': 'construction',
    '🐛': 'bug',
    '🟨': 'square',
    '🔷': 'hexagon',
    '🌐': 'globe',
    '🐳': 'box',
    '📢': 'volume',
    '☰': 'list',
    '😊': 'smile', '🙂': 'smile',
    '⚡': 'zap',
    '📚': 'book',
    '🐧': 'bot',
    '⏸': 'pause', '⏸️': 'pause',
    '⏹': 'square', '⏹️': 'square',
    '↻': 'refresh', '↺': 'refresh',
    '←': 'chevron-left', '→': 'chevron-right',
    '▲': 'chevron-up', '▼': 'chevron-down',
    '➜': 'chevron-right',
  };

  // Expose every icon as a direct property (KazmaIcons.save(), KazmaIcons.folder(), …)
  // plus the helper methods. Templates use the direct-property form exclusively.
  var api = Object.assign({}, icons, {
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
      api[name] = icons[name];  // keep the public alias in sync
    },
    /** Map a single emoji glyph (optional FE0F) to an icon name, or null. */
    emojiName: function (glyph) {
      if (!glyph) return null;
      if (EMOJI_TO_ICON[glyph]) return EMOJI_TO_ICON[glyph];
      var stripped = glyph.replace(/\uFE0F/g, '');
      return EMOJI_TO_ICON[stripped] || EMOJI_TO_ICON[stripped + '\uFE0F'] || null;
    },
    /** SVG string for an emoji glyph; falls back to original text. */
    fromEmoji: function (glyph, opts) {
      var name = api.emojiName(glyph);
      return name ? api.get(name, opts) : String(glyph || '');
    },
    /** Markup helper: <span class="ki" data-icon="name">…</span> already hydrated. */
    span: function (name, opts) {
      opts = opts || {};
      var cls = 'ki' + (opts.class ? ' ' + opts.class : '');
      // data-ki-done prevents MutationObserver re-writing SVG forever (CPU peg)
      return '<span class="' + cls + '" data-icon="' + name +
        '" data-ki-done="' + name + '" aria-hidden="true">' +
        api.get(name, opts) + '</span>';
    },
    /**
     * Fill every [data-icon] in root with SVG (once per node).
     * Safe to call after Alpine/dynamic HTML inserts.
     */
    hydrate: function (root) {
      if (api._hydrating) return;
      api._hydrating = true;
      try {
        root = root || document;
        if (!root || !root.querySelectorAll) return;
        var list = [];
        if (root.nodeType === 1 && root.hasAttribute && root.hasAttribute('data-icon')) {
          list.push(root);
        }
        var found = root.querySelectorAll
          ? root.querySelectorAll('[data-icon]')
          : [];
        for (var i = 0; i < found.length; i++) list.push(found[i]);
        for (var j = 0; j < list.length; j++) {
          var el = list[j];
          var name = el.getAttribute('data-icon');
          if (!name) continue;
          // Already filled with the same icon — do NOT set innerHTML again
          // (re-setting SVG children re-triggers MutationObserver → CPU spiral)
          if (el.getAttribute('data-ki-done') === name && el.querySelector('svg')) {
            continue;
          }
          el.innerHTML = api.get(name);
          el.setAttribute('data-ki-done', name);
        }
      } finally {
        api._hydrating = false;
      }
    },
  });

  // Hydrate once on DOM ready only — no MutationObserver.
  // Dynamic HTML should use KazmaIcons.span()/get() (already includes SVG) or
  // call KazmaIcons.hydrate(root) explicitly after insert.
  if (typeof document !== 'undefined') {
    function boot() {
      try { api.hydrate(document); } catch (e) { /* ignore */ }
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }
  }

  return api;
})();

// Expose globally for non-module scripts.
window.KazmaIcons = KazmaIcons;
