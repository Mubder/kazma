/**
 * Dates, times and numbers in the page's language (2026-09-28).
 *
 * Pages formatted times with the browser's own locale (toLocaleString())
 * and wrote "msgs", "ago" and "am" by hand, so an Arabic page read
 * "web · 2 msgs · 2h ago" and "9/3/2026, 5:15:32 PM". Every page formats
 * through here instead, in the language the page was rendered in
 * (<html lang>). Arabic keeps Latin digits, like every other number the
 * interface shows.
 *
 * Loaded by base.html on every page, before page scripts: window.KazmaFormat.
 */
(function (root) {
  'use strict';

  var STEPS = [
    ['year', 31536000], ['month', 2592000], ['week', 604800],
    ['day', 86400], ['hour', 3600], ['minute', 60],
  ];

  function lang() {
    var doc = root.document;
    var l = (doc && doc.documentElement && doc.documentElement.lang) || 'en';
    return String(l).toLowerCase().split('-')[0] || 'en';
  }

  function locale() {
    return lang() === 'ar' ? 'ar-u-nu-latn' : lang();
  }

  /* A Date from a Date, an ISO string, or epoch seconds / milliseconds. */
  function toDate(value) {
    if (value === null || value === undefined || value === '') return null;
    var d;
    if (value instanceof Date) d = value;
    else if (typeof value === 'number') d = new Date(value < 1e12 ? value * 1000 : value);
    else d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }

  function dateTime(value, options) {
    var d = toDate(value);
    if (!d) return '';
    try {
      return new Intl.DateTimeFormat(locale(), options || { dateStyle: 'medium', timeStyle: 'short' }).format(d);
    } catch (e) {
      return d.toISOString();
    }
  }

  function date(value) { return dateTime(value, { dateStyle: 'medium' }); }

  function time(value) { return dateTime(value, { timeStyle: 'short' }); }

  /* "2 hours ago" / "in 3 days" / "yesterday", in the page's language. */
  function relative(value, now) {
    var d = toDate(value);
    if (!d) return '';
    var secs = (d.getTime() - (now === undefined ? Date.now() : now)) / 1000;
    try {
      var rtf = new Intl.RelativeTimeFormat(locale(), { numeric: 'auto' });
      for (var i = 0; i < STEPS.length; i++) {
        if (Math.abs(secs) >= STEPS[i][1]) return rtf.format(Math.round(secs / STEPS[i][1]), STEPS[i][0]);
      }
      return rtf.format(Math.round(secs), 'second');
    } catch (e) {
      return dateTime(d);
    }
  }

  function number(n, options) {
    try {
      return new Intl.NumberFormat(locale(), options).format(n);
    } catch (e) {
      return String(n);
    }
  }

  /* 1.2K / 3.4M (and their Arabic forms). */
  function compact(n) {
    return number(n, { notation: 'compact', maximumFractionDigits: 1 });
  }

  /* CLDR plural category, the rule i18n.t_plural uses: six forms in Arabic
   * (zero, one, two, few 3-10, many 11-99, other), one/other elsewhere. */
  function pluralCategory(n) {
    var x = Math.abs(Number(n) || 0);
    if (lang() !== 'ar') return x === 1 ? 'one' : 'other';
    if (x === 0) return 'zero';
    if (x === 1) return 'one';
    if (x === 2) return 'two';
    var mod100 = Math.floor(x) % 100;
    if (mod100 >= 3 && mod100 <= 10) return 'few';
    if (mod100 >= 11 && mod100 <= 99) return 'many';
    return 'other';
  }

  /* A count with its noun from the catalog's forms (plural_forms(key) in a
   * template): "4539 مقطعاً", "2 chunks". A hand-built "n + ' ' + noun"
   * read "4539 مقطع" on the Knowledge page (2026-09-28). */
  function count(forms, n, fallback) {
    forms = forms || {};
    var s = forms[pluralCategory(n)] || forms.other || fallback || '{n}';
    return String(s).replace(/\{n\}/g, String(n));
  }

  root.KazmaFormat = {
    lang: lang,
    locale: locale,
    dateTime: dateTime,
    date: date,
    time: time,
    relative: relative,
    number: number,
    compact: compact,
    pluralCategory: pluralCategory,
    count: count,
  };
})(typeof window !== 'undefined' ? window : globalThis);
