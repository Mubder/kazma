// Single source of Ctrl+K page titles — the sidebar's names: `key` is the
// sidebar's catalog key (the page's language), `title` its English.
export const KAZMA_SEARCH_PAGES = [
    { href: "/chat", title: "Chat", key: "nav.chat" },
    { href: "/dashboard", title: "Dashboard", key: "nav.dashboard" },
    { href: "/workspace", title: "Workspace", key: "nav.workspace" },
    { href: "/ide", title: "IDE", key: "nav.ide" },
    { href: "/memory", title: "Memory", key: "nav.memory" },
    { href: "/agents", title: "Agents", key: "nav.agents" },
    { href: "/research", title: "Research", key: "nav.research" },
    { href: "/documents", title: "Documents", key: "nav.documents" },
    { href: "/swarm", title: "Swarm", key: "nav.swarm" },
    { href: "/knowledge", title: "Knowledge", key: "nav.knowledge" },
    { href: "/replay", title: "Time Travel", key: "nav.replay" },
    { href: "/scheduled", title: "Scheduled", key: "nav.scheduled" },
    { href: "/skills", title: "Skills", key: "nav.skills" },
    { href: "/mcp", title: "MCP", key: "nav.mcp" },
    { href: "/x", title: "X Studio", key: "nav.x_studio" },
    { href: "/settings", title: "Settings", key: "nav.settings" },
    // Not in the sidebar: the first-run guide, reached from the chat page's
    // card, Settings -> Providers and here.
    { href: "/setup", title: "Setup guide", key: "setup.reopen", sidebar: false },
];

// Whether a page is offered here: a page the sidebar names is offered only
// while the sidebar shows its link. The server leaves out the link of a
// feature that is off for the person (Spaces S2-11) and of the Development
// pages where they do not exist (S1-20), so the command palette and the
// keyboard shortcuts offer no page the sidebar does not.
export function pageOffered(href) {
    const page = KAZMA_SEARCH_PAGES.find((p) => p.href === href);
    if (!page || page.sidebar === false) return true;
    const nav = document.querySelector('.sidebar .nav-links');
    if (!nav) return true;
    return !!nav.querySelector('a[href="' + href + '"]');
}

// The keyboard shortcuts (nav.js) ask the same rule.
if (typeof window !== 'undefined') window.kazmaPageOffered = pageOffered;
