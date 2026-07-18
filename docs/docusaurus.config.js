// @ts-check
const {themes: prismThemes} = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Kazma',
  tagline: 'Autonomous AI Agent Framework — LangGraph, Swarm, HITL, Arabic RTL',
  favicon: 'img/logo.svg',
  url: 'https://kazma.ai',
  baseUrl: '/kazma/',
  organizationName: 'kazma-ai',
  projectName: 'kazma',
  onBrokenLinks: 'warn',
  onBrokenAnchors: 'warn',
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },
  // Mermaid diagrams from docs-v2 (architecture, swarm, HITL flows)
  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },
  themes: [
    '@docusaurus/theme-mermaid',
    [
      require.resolve('@easyops-cn/docusaurus-search-local'),
      /** @type {import('@easyops-cn/docusaurus-search-local').PluginOptions} */
      ({
        hashed: true,
        indexDocs: true,
        indexBlog: false,
        docsRouteBasePath: '/docs',
        language: ['en'],
      }),
    ],
  ],
  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve('./sidebars.js'),
          editUrl: 'https://github.com/kazma-ai/kazma/tree/main/docs/',
          // Default landing after /docs
          routeBasePath: 'docs',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],
  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/logo.svg',
      colorMode: {
        defaultMode: 'dark',
        respectPrefersColorScheme: true,
      },
      navbar: {
        title: 'Kazma',
        logo: {
          alt: 'Kazma Logo',
          src: 'img/logo.svg',
        },
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'guide',
            position: 'left',
            label: 'Guide',
          },
          {
            type: 'docSidebar',
            sidebarId: 'gettingStarted',
            position: 'left',
            label: 'Getting Started',
          },
          {
            type: 'docSidebar',
            sidebarId: 'coreConcepts',
            position: 'left',
            label: 'Legacy Concepts',
          },
          {
            type: 'docSidebar',
            sidebarId: 'skillDev',
            position: 'left',
            label: 'Skills',
          },
          {
            type: 'docSidebar',
            sidebarId: 'apiReference',
            position: 'left',
            label: 'API',
          },
          {
            type: 'docSidebar',
            sidebarId: 'security',
            position: 'left',
            label: 'Security',
          },
          {
            href: 'https://github.com/kazma-ai/kazma',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Guide (current)',
            items: [
              { label: 'Quickstart', to: '/docs/guide/quickstart' },
              { label: 'Architecture', to: '/docs/guide/architecture' },
              { label: 'Configuration', to: '/docs/guide/configuration' },
              { label: 'Security & Safety', to: '/docs/guide/security-and-safety' },
              { label: 'Swarm', to: '/docs/guide/swarm-orchestration' },
            ],
          },
          {
            title: 'More',
            items: [
              { label: 'Deployment', to: '/docs/guide/deployment' },
              { label: 'Troubleshooting', to: '/docs/guide/troubleshooting-and-workarounds' },
              { label: 'FAQ', to: '/docs/guide/faq' },
              { label: 'Hub', to: '/docs/kazma-hub/overview' },
              { label: 'Contributing', to: '/docs/contributing/development-setup' },
            ],
          },
          {
            title: 'Community',
            items: [
              { label: 'GitHub', href: 'https://github.com/kazma-ai/kazma' },
              { label: 'Discord', href: 'https://discord.gg/kazma' },
              { label: 'Twitter', href: 'https://x.com/kazma_ai' },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} Kazma AI. Docs include the docs-v2 merge (v0.5.0). Built with Docusaurus.`,
      },
      prism: {
        theme: prismThemes.github,
        darkTheme: prismThemes.dracula,
        additionalLanguages: ['python', 'bash', 'yaml', 'json', 'typescript', 'powershell'],
      },
      mermaid: {
        theme: { light: 'neutral', dark: 'dark' },
      },
    }),
};

module.exports = config;
