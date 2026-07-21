/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  // Single primary product docs tree
  docs: [
    'intro',
    {
      type: 'category',
      label: 'Start & Guide',
      collapsed: false,
      items: [
        'guide/quickstart',
        'guide/architecture',
        'guide/configuration',
        'guide/deployment',
        'guide/gateways-and-platforms',
        'guide/cli-reference',
        'guide/skills-mcp-and-tools',
        'guide/api-and-extension-points',
        'guide/swarm-orchestration',
        'guide/memory-and-rag',
        'guide/security-and-safety',
        'guide/arabic-cultural-features',
        'guide/troubleshooting-and-workarounds',
        'guide/development',
        'guide/faq',
        'guide/glossary',
        'guide/roadmap-and-future',
      ],
    },
    {
      type: 'category',
      label: 'Products',
      items: [
        'products/web-ui',
        'products/ide',
        'products/tui',
        'products/command-center-swarm',
        'products/multi-user-saas',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'reference/tools-catalog',
        'reference/slash-commands',
        'reference/environment-variables',
        'reference/api-routes',
        'reference/skill-manifest',
        'reference/system-map',
      ],
    },
    {
      type: 'category',
      label: 'Ops',
      items: [
        'ops/production-checklist',
        'ops/postgres-and-saas',
        'ops/disaster-recovery',
        'ops/multi-region',
        'ops/oidc-setup',
      ],
    },
  ],

  skills: [
    'skill-development/creating-skills',
    'skill-development/skill-manifest',
    'skill-development/mcp-integration',
    'skill-development/testing-skills',
    'skill-development/certification',
    {
      type: 'category',
      label: 'Kazma Hub',
      items: [
        'kazma-hub/overview',
        'kazma-hub/publishing-skills',
        'kazma-hub/finding-skills',
        'kazma-hub/security-auditing',
      ],
    },
  ],

  security: [
    'guide/security-and-safety',
    'security/security-policy',
    'security/vulnerability-reporting',
    'security/hardening-guide',
  ],

  contributing: [
    'guide/development',
    'contributing/development-setup',
    'contributing/code-style',
    'contributing/testing',
    'contributing/pull-requests',
  ],
};

module.exports = sidebars;
