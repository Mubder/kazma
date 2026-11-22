/**
 * The changes someone else made to the signed-in person's account, told at
 * their next page load after signing in (Spaces S1-25): a reset of their
 * password by the admin is never silent.
 *
 * Only on an install that enforces Spaces and for a signed-in account
 * (<meta name="kazma-spaces-enforced">, <meta name="kazma-account">); every
 * other page asks nothing. Each notice is shown once, then marked read on
 * the server (POST /api/account/notices/seen), so another browser or a
 * reload does not show it again.
 */
(function () {
    'use strict';

    function meta(name) {
        const el = document.querySelector('meta[name="' + name + '"]');
        return el ? (el.getAttribute('content') || '') : '';
    }

    function say(key, english, vars) {
        return typeof window.kazmaT === 'function' ? window.kazmaT(key, english, vars) : english;
    }

    // Each kind of notice, in the page's language: a reset of their password
    // (S1-25), being added to a shared space or taken out of one (S2-12).
    function wording(notice) {
        const when = window.KazmaFormat ? window.KazmaFormat.dateTime(notice.at) : notice.at;
        const space = '\u2068' + (notice.space_name || '') + '\u2069';
        if (notice.kind === 'password_reset') {
            return {
                title: say('account.notice_reset_title', 'Your password was reset'),
                message: say('account.notice_reset_body',
                    'The admin reset your password on {when}. If you did not ask for this, tell them.',
                    { when: when }),
            };
        }
        if (notice.kind === 'member_added') {
            return {
                title: say('account.notice_added_title', 'You were added to a space'),
                message: say('account.notice_added_body',
                    'The admin added you to {space} on {when}. Switch to it from the space menu at the top of the page.',
                    { space: space, when: when }),
            };
        }
        if (notice.kind === 'member_removed') {
            return {
                title: say('account.notice_removed_title', 'You were taken out of a space'),
                // The facts are said forgotten only once they were (the
                // server reads it: notice.forgotten).
                message: notice.forgotten
                    ? say('account.notice_removed_body',
                        'You were taken out of {space} on {when}. Kazma forgot the facts it filed under you there; your messages stay in its chats.',
                        { space: space, when: when })
                    : say('account.notice_removed_body_pending',
                        'You were taken out of {space} on {when}. Kazma is forgetting the facts it filed under you there; your messages stay in its chats.',
                        { space: space, when: when }),
            };
        }
        return null;
    }

    async function tell() {
        if (!meta('kazma-spaces-enforced') || !meta('kazma-account')) return;
        if (typeof window.kazmaGetJson !== 'function') return;
        const data = await window.kazmaGetJson('/api/account/notices');
        const notices = data && Array.isArray(data.notices) ? data.notices : [];
        if (!notices.length) return;
        let through = 0;
        for (const notice of notices) {
            through = Math.max(through, Number(notice.seq) || 0);
            const text = wording(notice);
            if (!text) continue;
            if (typeof window.kazmaAlert === 'function') {
                await window.kazmaAlert({ title: text.title, message: text.message });
            }
        }
        // Not marked read (refused, or no network): shown again at the next
        // page load, which is what "never silent" wants.
        try {
            const resp = await fetch('/api/account/notices/seen', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ through: through }),
            });
            if (!resp.ok && window.console) console.warn('[account] notices not marked read', resp.status);
        } catch (e) {
            if (window.console) console.warn('[account] notices not marked read');
        }
    }

    window.addEventListener('load', function () { tell(); });
})();
