/**
 * A dialog a person typed into is not closed by a click outside it or by
 * Escape without asking: "Add a provider" closed on any click outside and
 * lost the key, the address and everything else (the owner, 2026-11-22), and
 * so did the connector, profile, MCP, scheduled and workspace dialogs.
 *
 * A dialog's overlay carries:
 *
 *   data-dialog-guard
 *   x-effect="open && KazmaDialogGuard.opened($el)"
 *   @click.self="KazmaDialogGuard.close($el, () => open = false)"
 *   @keydown.escape.window="open && KazmaDialogGuard.escape($event, $el, () => open = false)"
 *
 * What was typed is an `input` or `change` event inside it since it opened.
 * Its own Cancel and x close it as they say. tests/test_dialogs_keep_typed_input.py
 * finds every overlay a click outside closes and asks for the guard.
 */

/** A catalog string in the page's language, else the English given. */
function uiText(key, fallback) {
    const translate = window.kazmaT;
    return typeof translate === 'function' ? translate(key, fallback) : fallback;
}

const touched = new WeakSet();
let asking = false;
/** This Escape belongs to what is above the dialog (a confirmation, the
 *  search palette), read before that closes itself. */
let escapeTaken = false;

function storeOpen(name) {
    const store = window.Alpine && window.Alpine.store ? window.Alpine.store(name) : null;
    return !!(store && store.open);
}

function markTyped(event) {
    const overlay = event.target && event.target.closest ? event.target.closest('[data-dialog-guard]') : null;
    if (overlay) touched.add(overlay);
}

/** Asks with the styled confirmation, one question at a time; what
 *  kazmaConfirm answers, or null when one is already up. */
async function ask(options) {
    if (asking) return null;
    asking = true;
    try {
        return await window.kazmaConfirm(options);
    } finally {
        // The key press that answered it is still going round: escapeTaken,
        // read before anything closed, is what says it was the question's.
        asking = false;
    }
}

async function close(overlay, done) {
    if (!touched.has(overlay)) {
        done();
        return true;
    }
    const answer = await ask({
        title: uiText('common.discard_title', 'Discard what you typed?'),
        message: uiText('common.discard_body', 'What you typed here is not saved yet.'),
        confirmText: uiText('common.discard', 'Discard'),
        cancelText: uiText('common.keep_editing', 'Go on editing'),
        danger: true,
    });
    if (answer !== true) return false;
    touched.delete(overlay);
    done();
    return true;
}

/** Escape pressed over a dialog: nothing when it was meant for what is above. */
function escape(event, overlay, done) {
    if (escapeTaken || event.defaultPrevented) return false;
    return close(overlay, done);
}

/** Tab stays in an open dialog: from its last control to its first, and
 *  back (aria-modal says the page behind it is not there), and into it from a
 *  control that was disabled while it had the focus. A question above it keeps
 *  its own. */
function keepFocus(event, dialog) {
    if (storeOpen('modal') || storeOpen('search')) return;
    const controls = [...dialog.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null);
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    const at = document.activeElement;
    if (event.shiftKey && (at === first || !dialog.contains(at))) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && (at === last || !dialog.contains(at))) {
        event.preventDefault();
        first.focus();
    }
}

export function installDialogGuard() {
    if (window.KazmaDialogGuard) return;
    document.addEventListener('input', markTyped, true);
    document.addEventListener('change', markTyped, true);
    window.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') escapeTaken = asking || storeOpen('modal') || storeOpen('search');
    }, true);
    window.KazmaDialogGuard = {
        opened(overlay) { touched.delete(overlay); },
        typedIn(overlay) { return touched.has(overlay); },
        /** True when this Escape is not for the dialog. */
        escapeIsTaken() { return escapeTaken; },
        ask: ask,
        keepFocus: keepFocus,
        close: close,
        escape: escape,
    };
}
