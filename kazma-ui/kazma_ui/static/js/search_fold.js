/**
 * A text as a search compares it: the one fold of the pages' own searches.
 *
 * The browser's copy of kazma_core.documents.arabic.fold_for_search: the same
 * Arabic word in any usual spelling (hamza, taa marbuta, alef maqsura, marks,
 * stretch, Arabic digits) is one text, and so is a Latin word in any case.
 * The same for every character of the Arabic blocks, the digits and the Latin
 * ones; a few hundred elsewhere (other alphabets' case, controls) differ.
 * The Memory page's map compared lower-cased text, so it found an Arabic name
 * only in the spelling it was stored in (memory plan W6). Held to the Python
 * fold over those blocks by tests/test_search_fold_js.py.
 */
(function () {
  'use strict';

  // Marks, then (after NFKC, as the Python fold) the stretch and the
  // direction marks: dropped.
  var MARKS = /[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]/g;
  var DROPPED = /[\u061c\u0640\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069]/g;
  // [every spelling, the letter or digit it is compared as]
  var SPELLINGS = [
    ['\u0660\u06f0', '0'],
    ['\u0661\u06f1', '1'],
    ['\u0662\u06f2', '2'],
    ['\u0663\u06f3', '3'],
    ['\u0664\u06f4', '4'],
    ['\u0665\u06f5', '5'],
    ['\u0666\u06f6', '6'],
    ['\u0667\u06f7', '7'],
    ['\u0668\u06f8', '8'],
    ['\u0669\u06f9', '9'],
    ['\u0622\u0623\u0625\u0671', '\u0627'],
    ['\u06a9', '\u0643'],
    ['\u0629\u06be', '\u0647'],
    ['\u0624', '\u0648'],
    ['\u0649\u0626\u06cc', '\u064a']
  ];
  var AS = {};
  SPELLINGS.forEach(function (row) {
    row[0].split('').forEach(function (ch) { AS[ch] = row[1]; });
  });

  function once(text) {
    return text.normalize('NFKC').replace(MARKS, '').replace(DROPPED, '')
      .replace(/[\u0600-\u06ff]/g, function (ch) { return AS[ch] || ch; })
      .replace(/[^\S\ufeff]+/g, ' ').replace(/^ | $/g, '').toLowerCase();
  }

  window.kazmaSearchFold = function (value) {
    var folded = once(String(value == null ? '' : value));
    for (var i = 0; i < 4; i++) {
      var again = once(folded);
      if (again === folded) break;
      folded = again;
    }
    return folded;
  };
})();
