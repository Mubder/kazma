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
    // (S1-25), their sessions ended (S2-10), being added to a shared space or
    // taken out of one (S2-12).
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
        if (notice.kind === 'sessions_ended') {
            return {
                title: say('account.notice_signed_out_title', 'You were signed out everywhere'),
                message: say('account.notice_signed_out_body',
                    'The admin ended your sessions on {when}: you sign in again on each device. If you did not expect this, tell them.',
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
        if (notice.kind === 'chat_linked') {
            const labels = { telegram: 'Telegram', discord: 'Discord', slack: 'Slack', whatsapp: 'WhatsApp' };
            const platform = labels[notice.platform] || notice.platform || '';
            return {
                title: say('chat_links.notice_linked_title', 'A chat app was linked to your account'),
                message: say('chat_links.notice_linked_body',
                    'On {when}, {platform} account {user} was linked to your Kazma account with a code you asked for. What it sends runs as you. If that was not you, unlink it now.',
                    { when: when, platform: platform, user: '\u2066' + (notice.chat_user || '') + '\u2069' }),
                unlink: { platform: notice.platform, user_id: notice.chat_user, label: platform },
            };
        }
        // Stronger sign-in (Spaces S4-02).
        if (notice.kind === 'second_factor_reset') {
            return {
                title: say('sign_in.notice_reset_title', 'Your second factor was reset'),
                message: say('sign_in.notice_reset_body',
                    'The admin turned your second factor off on {when}: you sign in with your password alone until you turn a new one on in My account. If you did not ask for this, tell them.',
                    { when: when }),
            };
        }
        if (notice.kind === 'session_ended') {
            return {
                title: say('sign_in.notice_session_title', 'One of your sessions was ended'),
                message: say('sign_in.notice_session_body',
                    'The admin ended one of your sessions on {when}: you sign in again on that device. If you did not expect this, tell them.',
                    { when: when }),
            };
        }
        if (notice.kind === 'second_factor_paused') {
            return {
                title: say('sign_in.notice_codes_paused_title', 'Wrong codes paused your second factor'),
                message: say('sign_in.notice_codes_paused_body',
                    'On {when}, five wrong codes were tried for your account, so its codes were not checked for a while, from every place. Someone who tries codes has your password: if that was not you, change it now.',
                    { when: when }),
            };
        }
        if (notice.kind === 'sign_in_paused') {
            return {
                title: say('sign_in.notice_paused_title', 'Sign-ins to your account were paused'),
                message: say('sign_in.notice_paused_body',
                    'On {when}, so many wrong passwords were tried for your account that sign-ins to it were paused for a while, except from places you signed in from before. If that was not you, change your password and turn on a second factor.',
                    { when: when }),
            };
        }
        return null;
    }

    // A chat-app link the person did not make is undone from the notice
    // itself (the stage 3 review, L2).
    async function offerUnlink(text) {
        if (typeof window.kazmaConfirm !== 'function') {
            if (typeof window.kazmaAlert === 'function') await window.kazmaAlert({ title: text.title, message: text.message });
            return;
        }
        const unlink = await window.kazmaConfirm({
            title: text.title, message: text.message,
            confirmText: say('chat_links.unlink', 'Unlink'),
            cancelText: say('chat_links.notice_linked_keep', 'It was me'),
        });
        if (!unlink) return;
        try {
            const resp = await fetch('/api/account/chat-links', {
                method: 'DELETE', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ platform: text.unlink.platform, user_id: text.unlink.user_id }),
            });
            if (window.showToast) {
                window.showToast(resp.ok
                    ? say('chat_links.unlinked', '{platform} is unlinked.', { platform: text.unlink.label })
                    : say('chat_links.unlink_failed', 'Could not unlink. Try again.'), resp.ok ? 'success' : 'error');
            }
        } catch (e) {
            if (window.showToast) window.showToast(say('chat_links.unlink_failed', 'Could not unlink. Try again.'), 'error');
        }
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
            if (text.unlink) {
                await offerUnlink(text);
            } else if (typeof window.kazmaAlert === 'function') {
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
