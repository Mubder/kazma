/**
 * Turn-step detail formatting — pure functions, no DOM.
 *
 * A tool step used to be logged with the RAW argument JSON as its detail:
 *
 *     {"query":"auth middleware","path":"C:/x/y","max_results":50}
 *
 * while the Live Task Card was handed a summary and showed
 * `file_search “auth middleware”`. One event, two renderings — and the ugly
 * one was the one persisted into the TurnDocument, so every reloaded
 * transcript showed blobs forever.
 *
 * The raw text is still worth keeping. `.step-detail` clamps to three lines
 * and offers "Show more", so arguments and results stay inspectable; the raw
 * value just should not be the first thing anyone reads. Leading with the
 * gist fixes what you see without discarding anything, and needs no change to
 * the stored part shape — the gist IS part of the stored string, so a live
 * panel and a restored one render identically.
 *
 * These live outside chat.js because they are pure and therefore testable;
 * chat.js is 7,700 lines wrapped in an IIFE around a DOM, and logic that can
 * only be verified by reading it is logic that drifts.
 */

var KazmaTurnDetail = window.KazmaTurnDetail = (function () {
    'use strict';

    // Argument names worth leading with, most identifying first.
    var PREFER = ['query', 'path', 'file_path', 'url', 'name', 'command', 'text', 'q', 'prompt'];
    var SKIP = { task_id: 1, thread_id: 1, session_id: 1, turn_id: 1 };
    var QUOTE_MAX = 48;

    function truncate(text, max) {
        var s = String(text);
        return s.length <= max ? s : s.slice(0, max - 1) + '\u2026';
    }

    /** Collapse whitespace and quote — what a gist looks like. */
    function quote(value) {
        var s = String(value).replace(/\s+/g, ' ').trim();
        return s ? '\u201c' + truncate(s, QUOTE_MAX) + '\u201d' : '';
    }

    /** One-line gist of a tool's ARGUMENTS. */
    function argSummary(inputs) {
        var obj = inputs;
        if (typeof obj === 'string') {
            var s = obj.trim();
            if (!s) return '';
            if (s.charAt(0) === '{' || s.charAt(0) === '[') {
                try { obj = JSON.parse(s); } catch (e) { return quote(s); }
            } else {
                return quote(s);
            }
        }
        if (!obj || typeof obj !== 'object') return '';
        if (Array.isArray(obj)) return obj.length ? argSummary(obj[0]) : '';
        var i, k, v;
        for (i = 0; i < PREFER.length; i++) {
            k = PREFER[i];
            if (typeof obj[k] === 'string' && obj[k].trim()) return quote(obj[k]);
            if (typeof obj[k] === 'number') return quote(String(obj[k]));
        }
        var keys = Object.keys(obj);
        for (i = 0; i < keys.length; i++) {
            k = keys[i];
            if (SKIP[k]) continue;
            v = obj[k];
            if (typeof v === 'string' && v.trim()) return quote(v);
            if (typeof v === 'number' || typeof v === 'boolean') return quote(String(v));
        }
        return '';
    }

    /** One-line gist of a tool's RESULT. */
    function resultSummary(result) {
        var s = String(result == null ? '' : result).trim();
        if (!s) return '';
        if (s.charAt(0) === '{' || s.charAt(0) === '[') {
            try {
                var obj = JSON.parse(s);
                if (Array.isArray(obj)) {
                    return obj.length + ' result' + (obj.length === 1 ? '' : 's');
                }
                var inner = argSummary(obj);
                if (inner) return inner;
            } catch (e) { /* not JSON after all */ }
        }
        return quote(s.split('\n')[0]);
    }

    /**
     * The stored detail, led by its gist.
     * @returns {string} gist on its own first line, then the raw value.
     */
    function withGist(gist, raw) {
        var body = String(raw == null ? '' : raw);
        var lead = String(gist || '').trim();
        if (!lead) return body;
        if (!body.trim()) return lead;
        // Nothing gained when the raw value already reads as the gist.
        if (body.replace(/\s+/g, ' ').trim() === lead.replace(/^\u201c|\u201d$/g, '')) {
            return body;
        }
        return lead + '\n' + body;
    }

    return {
        argSummary: argSummary,
        resultSummary: resultSummary,
        withGist: withGist,
        forArgs: function (inputs, raw) {
            // Default the body to a SERIALISED form. Passing the object
            // through unchanged made String(obj) produce "[object Object]" —
            // which is worse than the raw JSON this exists to demote.
            var body = raw;
            if (body === undefined) {
                if (inputs && typeof inputs === 'object') {
                    try { body = JSON.stringify(inputs); } catch (e) { body = String(inputs); }
                } else {
                    body = inputs;
                }
            }
            return withGist(argSummary(inputs), body);
        },
        forResult: function (result) {
            return withGist(resultSummary(result), result);
        },
    };
})();
