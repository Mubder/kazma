/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  // Primary: merged docs-v2 (code-audited, July 2026)
  guide: [
    {
      type: 'category',
      label: 'Start here',
      collapsed: false,
      items: [
        'guide/quickstart',
        'guide/architecture',
        'guide/configuration',
        'guide/deployment',
      ],
    },
    {
      type: 'category',
      label: 'Platforms & tools',
      items: [
        'guide/gateways-and-platforms',
        'guide/cli-reference',
        'guide/skills-mcp-and-tools',
        'guide/api-and-extension-points',
      ],
    },
    {
      type: 'category',
      label: 'Agent systems',
      items: [
        'guide/swarm-orchestration',
        'guide/memory-and-rag',
        'guide/security-and-safety',
        'guide/arabic-cultural-features',
      ],
    },
    {
      type: 'category',
      label: 'Ops & community',
      items: [
        'guide/troubleshooting-and-workarounds',
        'guide/development',
        'guide/faq',
        'guide/glossary',
        'guide/roadmap-and-future',
        'guide/roadmap-legacy',
      ],
    },
  ],

  gettingStarted: [
    {
      type: 'html',
      value:
        '<div style="padding:0.5rem 0.75rem;margin:0.25rem 0 0.75rem;border-radius:6px;background:rgba(34,211,238,0.12);font-size:0.8rem;line-height:1.4"><strong>Prefer Guide →</strong> <a href="/kazma/docs/guide/quickstart">current Quickstart</a> (docs-v2 merge)</div>',
      defaultStyle: true,
    },
    'getting-started/installation',
    'getting-started/quickstart',
    'getting-started/configuration',
    'getting-started/first-skill',
  ],

  coreConcepts: [
    {
      type: 'html',
      value:
        '<div style="padding:0.5rem 0.75rem;margin:0.25rem 0 0.75rem;border-radius:6px;background:rgba(34,211,238,0.12);font-size:0.8rem;line-height:1.4"><strong>Prefer Guide →</strong> <a href="/kazma/docs/guide/architecture">Architecture</a> for current engine docs</div>',
      defaultStyle: true,
    },
    'core-concepts/architecture',
    'core-concepts/agent-loop',
    'core-concepts/checkpointing',
    'core-concepts/context-compaction',
    'core-concepts/dialect-routing',
    'core-concepts/delegation-protocol',
  ],

  skillDev: [
    'skill-development/creating-skills',
    'skill-development/skill-manifest',
    'skill-development/mcp-integration',
    'skill-development/testing-skills',
    'skill-development/certification',
  ],

  apiReference: [
    {
      type: 'html',
      value:
        '<div style="padding:0.5rem 0.75rem;margin:0.25rem 0 0.75rem;border-radius:6px;background:rgba(34,211,238,0.12);font-size:0.8rem;line-height:1.4"><strong>Prefer Guide →</strong> <a href="/kazma/docs/guide/api-and-extension-points">API &amp; Extension Points</a> and <a href="/kazma/docs/guide/cli-reference">CLI Reference</a></div>',
      defaultStyle: true,
    },
    'api-reference/core-api',
    'api-reference/hub-api',
    'api-reference/delegation-api',
    'api-reference/cli-reference',
  ],

  kazmaHub: [
    'kazma-hub/overview',
    'kazma-hub/publishing-skills',
    'kazma-hub/finding-skills',
    'kazma-hub/security-auditing',
  ],

  security: [
    {
      type: 'html',
      value:
        '<div style="padding:0.5rem 0.75rem;margin:0.25rem 0 0.75rem;border-radius:6px;background:rgba(34,211,238,0.12);font-size:0.8rem;line-height:1.4"><strong>Prefer Guide →</strong> <a href="/kazma/docs/guide/security-and-safety">Security &amp; Safety</a> (three HITL gates)</div>',
      defaultStyle: true,
    },
    'security/security-policy',
    'security/vulnerability-reporting',
    'security/hardening-guide',
  ],

  contributing: [
    {
      type: 'html',
      value:
        '<div style="padding:0.5rem 0.75rem;margin:0.25rem 0 0.75rem;border-radius:6px;background:rgba(34,211,238,0.12);font-size:0.8rem;line-height:1.4"><strong>Prefer Guide →</strong> <a href="/kazma/docs/guide/development">Development</a></div>',
      defaultStyle: true,
    },
    'contributing/development-setup',
    'contributing/code-style',
    'contributing/testing',
    'contributing/pull-requests',
  ],
};

module.exports = sidebars;
