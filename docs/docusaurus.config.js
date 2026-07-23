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
  // Mermaid diagrams (architecture, swarm, HITL flows)
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
            sidebarId: 'docs',
            position: 'left',
            label: 'Docs',
          },
          {
            type: 'docSidebar',
            sidebarId: 'skills',
            position: 'left',
            label: 'Skills',
          },
          {
            type: 'docSidebar',
            sidebarId: 'security',
            position: 'left',
            label: 'Security',
          },
          {
            type: 'docSidebar',
            sidebarId: 'contributing',
            position: 'left',
            label: 'Contributing',
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
            title: 'Docs',
            items: [
              { label: 'Home', to: '/docs/' },
              { label: 'Quickstart', to: '/docs/guide/quickstart' },
              { label: 'Architecture', to: '/docs/guide/architecture' },
              { label: 'Tools catalog', to: '/docs/reference/tools-catalog' },
              { label: 'Security & Safety', to: '/docs/guide/security-and-safety' },
            ],
          },
          {
            title: 'Products & Ops',
            items: [
              { label: 'Web UI', to: '/docs/products/web-ui' },
              { label: 'IDE', to: '/docs/products/ide' },
              { label: 'Production checklist', to: '/docs/ops/production-checklist' },
              { label: 'Troubleshooting', to: '/docs/guide/troubleshooting-and-workarounds' },
              { label: 'FAQ', to: '/docs/guide/faq' },
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
        copyright: `Copyright © ${new Date().getFullYear()} Kazma AI. Unified docs under docs/ (v0.6.1+). Built with Docusaurus.`,
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
