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

    async function tell() {
        if (!meta('kazma-spaces-enforced') || !meta('kazma-account')) return;
        if (typeof window.kazmaGetJson !== 'function') return;
        const data = await window.kazmaGetJson('/api/account/notices');
        const notices = data && Array.isArray(data.notices) ? data.notices : [];
        if (!notices.length) return;
        let through = 0;
        for (const notice of notices) {
            through = Math.max(through, Number(notice.seq) || 0);
            if (notice.kind !== 'password_reset') continue;
            const when = window.KazmaFormat ? window.KazmaFormat.dateTime(notice.at) : notice.at;
            const message = say('account.notice_reset_body',
                'The admin reset your password on {when}. If you did not ask for this, tell them.',
                { when: when });
            const title = say('account.notice_reset_title', 'Your password was reset');
            if (typeof window.kazmaAlert === 'function') {
                await window.kazmaAlert({ title: title, message: message });
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
