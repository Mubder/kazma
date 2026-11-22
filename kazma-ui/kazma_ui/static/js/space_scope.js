/**
 * The page names its space on every call (Spaces S1-06).
 *
 * A page is rendered for one space; <meta name="kazma-space"> says which.
 * Every request this page makes to its own server carries that space: the
 * X-Kazma-Space header on fetch, and a `space` query value where a request
 * cannot carry a header (an event stream, a socket, a download link). The
 * server checks each time that the signed-in person is a member.
 *
 * The space is the tab's, never the session's: two tabs in two spaces send
 * two different names and cannot mix. A tab remembers its space in
 * sessionStorage (per tab), so a reload or a link with no `space` value
 * stays where it was.
 *
 * All of this happens only on an install that enforces Spaces (the page
 * says so in <meta name="kazma-spaces-enforced">). On any other install the
 * page names no space, reloads nothing and offers no switcher: its requests
 * are exactly what they were before Spaces, and the server runs them in the
 * tenant it always did.
 *
 * Loaded once by the page shell, before any script that makes a request.
 */
(function () {
    'use strict';

    var HEADER = 'X-Kazma-Space';
    var QUERY = 'space';
    var STORAGE_KEY = 'kazma.space';
    var ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

    function validId(value) {
        return typeof value === 'string' && ID_RE.test(value);
    }

    function metaSpace(doc) {
        var meta = doc && doc.querySelector ? doc.querySelector('meta[name="kazma-space"]') : null;
        var value = meta ? (meta.getAttribute('content') || '').trim() : '';
        return validId(value) ? value : '';
    }

    function remembered(storage) {
        try {
            var value = storage ? storage.getItem(STORAGE_KEY) : '';
            return validId(value) ? value : '';
        } catch (e) {
            return '';
        }
    }

    function remember(storage, id) {
        try {
            if (storage && validId(id)) storage.setItem(STORAGE_KEY, id);
        } catch (e) { /* a tab with storage off still names its space from the page */ }
    }

    /** True for a URL of this page's own server. */
    function sameOrigin(url, origin) {
        try {
            return new URL(String(url), origin + '/').origin === origin;
        } catch (e) {
            return false;
        }
    }

    /** `url` with the space as a query value; an existing value is kept. */
    function withSpace(url, id, origin) {
        if (!validId(id) || !sameOrigin(url, origin)) return url;
        try {
            var parsed = new URL(String(url), origin + '/');
            if (!parsed.searchParams.has(QUERY)) parsed.searchParams.set(QUERY, id);
            var text = String(url);
            // Keep a relative URL relative: other code compares and logs it.
            if (/^[a-z][a-z0-9+.-]*:/i.test(text) || text.indexOf('//') === 0) return parsed.toString();
            return parsed.pathname + parsed.search + parsed.hash;
        } catch (e) {
            return url;
        }
    }

    /** fetch() that names the space on every request to this server. */
    function wrapFetch(nativeFetch, getId, origin) {
        return function kazmaFetch(input, init) {
            var id = getId();
            var url = (input && typeof input === 'object' && 'url' in input) ? input.url : input;
            if (!validId(id) || !sameOrigin(url, origin)) return nativeFetch.call(this, input, init);
            var options = Object.assign({}, init || {});
            var headers = new Headers(
                options.headers || ((input && typeof input === 'object' && input.headers) || undefined)
            );
            if (!headers.has(HEADER)) headers.set(HEADER, id);
            options.headers = headers;
            return nativeFetch.call(this, input, options);
        };
    }

    /** A constructor that adds the space to the URL it is given. */
    function wrapUrlConstructor(Native, getId, origin, httpOrigin) {
        if (typeof Native !== 'function') return Native;
        function Wrapped(url, second) {
            var named = withSpace(url, getId(), httpOrigin || origin);
            return second === undefined ? new Native(named) : new Native(named, second);
        }
        Wrapped.prototype = Native.prototype;
        ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (name) {
            if (name in Native) Wrapped[name] = Native[name];
        });
        return Wrapped;
    }

    /**
     * Fill the header's switcher with the person's spaces and show it when
     * there is more than one. Asked only on an install that enforces Spaces
     * (the page says so): one operator has one space and nothing to switch.
     */
    function fillSwitcher(select, spaces, currentId, onChoose) {
        if (!select || !Array.isArray(spaces) || spaces.length < 2) return false;
        var personal = select.getAttribute('data-personal-label') || '';
        while (select.firstChild) select.removeChild(select.firstChild);
        spaces.forEach(function (space) {
            if (!space || !validId(space.id)) return;
            var option = select.ownerDocument.createElement('option');
            option.value = space.id;
            // A space's name is the person's own text; a personal space with
            // no name is shown by the page's word for it.
            option.textContent = space.name || (space.personal ? personal : space.id);
            if (space.id === currentId) option.selected = true;
            select.appendChild(option);
        });
        select.addEventListener('change', function () { onChoose(select.value); });
        select.hidden = false;
        return true;
    }

    /**
     * Say which space this tab is in, in the header (Spaces S2-12): its kind
     * always (personal, shared, shared with a project folder), its name when
     * there is no switcher to show it, and "New private chat" in a shared
     * space, which opens a new chat in the person's own space. Returns the
     * person's personal space, or null.
     */
    function showSpace(doc, spaces, currentId) {
        if (!doc || !doc.getElementById || !Array.isArray(spaces)) return null;
        var current = null;
        var personal = null;
        spaces.forEach(function (space) {
            if (!space || !validId(space.id)) return;
            if (space.id === currentId) current = space;
            if (space.personal && !personal) personal = space;
        });
        if (!current) return personal;
        var kind = doc.getElementById('space-kind');
        if (kind) {
            var word = current.personal ? kind.getAttribute('data-personal')
                : (current.kind === 'host' ? kind.getAttribute('data-project') : kind.getAttribute('data-shared'));
            kind.textContent = word || '';
            kind.setAttribute('data-kind', current.personal ? 'personal' : (current.kind === 'host' ? 'project' : 'shared'));
            kind.hidden = !word;
        }
        var name = doc.getElementById('space-name');
        var select = doc.getElementById('space-switcher');
        if (name) {
            var switcher = select && !select.hidden;
            var label = select ? (select.getAttribute('data-personal-label') || '') : '';
            name.textContent = current.name || (current.personal ? label : current.id);
            name.hidden = !!switcher;
        }
        var privateChat = doc.getElementById('new-private-chat');
        if (privateChat) privateChat.hidden = !(personal && !current.personal);
        return personal;
    }

    /** True when the page says this install enforces Spaces. */
    function pageEnforces(doc) {
        var flag = doc && doc.querySelector
            ? doc.querySelector('meta[name="kazma-spaces-enforced"]') : null;
        return !!flag && flag.getAttribute('content') === '1';
    }

    function offerSwitcher(win, currentId, state) {
        var doc = win.document;
        if (!pageEnforces(doc)) return;
        function load() {
            var select = doc.getElementById ? doc.getElementById('space-switcher') : null;
            if (!select) return;
            win.fetch('/api/account/spaces', { headers: { Accept: 'application/json' } })
                .then(function (resp) { return resp && resp.ok ? resp.json() : null; })
                .then(function (body) {
                    if (body && body.ok) {
                        fillSwitcher(select, body.spaces, currentId, function (id) {
                            win.kazmaSpace.switchTo(id);
                        });
                        if (state) {
                            state.spaces = body.spaces;
                            state.personal = showSpace(doc, body.spaces, currentId);
                            if (doc.dispatchEvent && typeof win.CustomEvent === 'function') {
                                doc.dispatchEvent(new win.CustomEvent('kazma:spaces', { detail: body.spaces }));
                            }
                        }
                    }
                })
                .catch(function () { /* no switcher; the tab stays in its space */ });
        }
        if (doc.readyState === 'loading' && doc.addEventListener) {
            doc.addEventListener('DOMContentLoaded', load);
        } else {
            load();
        }
    }

    function install(win) {
        var doc = win.document;
        var origin = win.location.origin;
        var pageSpace = metaSpace(doc);
        var enforced = pageEnforces(doc);
        var storage = null;
        try { storage = win.sessionStorage; } catch (e) { storage = null; }
        // Not enforced: the tab has no space of its own. A space remembered
        // from another sign-in in this tab must not send the page anywhere.
        var tabSpace = enforced ? remembered(storage) : '';

        // The tab was in another space than the one this page was rendered
        // for (a reload, or a link with no space): load the page for the
        // tab's space, so what the server drew and what the page asks agree.
        var current = new URL(win.location.href);
        if (tabSpace && pageSpace && tabSpace !== pageSpace && !current.searchParams.has(QUERY)) {
            current.searchParams.set(QUERY, tabSpace);
            win.location.replace(current.toString());
            return;
        }
        if (enforced && pageSpace) remember(storage, pageSpace);

        var state = { id: pageSpace };
        // The space to name on a request: none unless Spaces is enforced.
        function getId() { return enforced ? state.id : ''; }

        if (typeof win.fetch === 'function') win.fetch = wrapFetch(win.fetch, getId, origin);
        if (typeof win.EventSource === 'function') {
            win.EventSource = wrapUrlConstructor(win.EventSource, getId, origin);
        }
        if (typeof win.WebSocket === 'function') {
            // A socket URL is ws(s)://host: the same host as this page.
            var Native = win.WebSocket;
            var Wrapped = function (url, protocols) {
                var id = getId();
                var named = url;
                try {
                    var parsed = new URL(String(url), origin.replace(/^http/, 'ws') + '/');
                    if (validId(id) && parsed.host === win.location.host
                            && !parsed.searchParams.has(QUERY)) {
                        parsed.searchParams.set(QUERY, id);
                        named = parsed.toString();
                    }
                } catch (e) { named = url; }
                return protocols === undefined ? new Native(named) : new Native(named, protocols);
            };
            Wrapped.prototype = Native.prototype;
            ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (name) {
                Wrapped[name] = Native[name];
            });
            win.WebSocket = Wrapped;
        }

        offerSwitcher(win, state.id, state);

        win.kazmaSpace = {
            /** The id of the space this tab is in. */
            get id() { return state.id; },
            /** The person's spaces, once read (else an empty list). */
            get spaces() { return Array.isArray(state.spaces) ? state.spaces.slice() : []; },
            /** `url` with this tab's space, for a link or a download. */
            url: function (url) { return withSpace(url, getId(), origin); },
            /** A new chat in the person's own space, from a shared one
             * (S2-12): the tab moves to that space with an empty chat. */
            newPrivateChat: function () {
                var own = state.personal;
                if (!enforced || !own || !validId(own.id)) return;
                remember(storage, own.id);
                var next = new URL('/chat', origin + '/');
                next.searchParams.set('new', '1');
                next.searchParams.set(QUERY, own.id);
                win.location.assign(next.toString());
            },
            /** Switch this tab to another space: the page loads again for it. */
            switchTo: function (id) {
                if (!enforced || !validId(id) || id === state.id) return;
                remember(storage, id);
                var next = new URL(win.location.href);
                next.searchParams.set(QUERY, id);
                win.location.assign(next.toString());
            },
        };
    }

    var api = {
        HEADER: HEADER,
        QUERY: QUERY,
        validId: validId,
        sameOrigin: sameOrigin,
        withSpace: withSpace,
        wrapFetch: wrapFetch,
        wrapUrlConstructor: wrapUrlConstructor,
        fillSwitcher: fillSwitcher,
        showSpace: showSpace,
        install: install,
    };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else if (typeof window !== 'undefined') {
        window.KazmaSpaceScope = api;
        install(window);
    }
})();
