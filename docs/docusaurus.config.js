// @ts-check
const {themes: prismThemes} = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Kazma Hub',
  tagline: 'Autonomous AI Agent Framework — Skills, Hub & Marketplace',
  favicon: 'img/logo.svg',
  url: 'https://kazma-ai.github.io',
  baseUrl: '/kazma/',
  organizationName: 'kazma-ai',
  projectName: 'kazma',
  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },
  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve('./sidebars.js'),
          editUrl: 'https://github.com/kazma-ai/kazma/tree/main/docs/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],
  themes: [
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
  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/logo.svg',
      navbar: {
        title: 'Kazma Hub',
        logo: {
          alt: 'Kazma Logo',
          src: 'img/logo.svg',
        },
        items: [
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
            label: 'Core Concepts',
          },
          {
            type: 'docSidebar',
            sidebarId: 'skillDev',
            position: 'left',
            label: 'Skill Development',
          },
          {
            type: 'docSidebar',
            sidebarId: 'apiReference',
            position: 'left',
            label: 'API Reference',
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
            title: 'Documentation',
            items: [
              { label: 'Getting Started', to: '/docs/getting-started/installation' },
              { label: 'Core Concepts', to: '/docs/core-concepts/architecture' },
              { label: 'Skill Development', to: '/docs/skill-development/creating-skills' },
            ],
          },
          {
            title: 'Community',
            items: [
              { label: 'GitHub', href: 'https://github.com/kazma-ai/kazma' },
              { label: 'Discord', href: 'https://discord.gg/kazma' },
              { label: 'Twitter', href: 'https://twitter.com/kazma_ai' },
            ],
          },
          {
            title: 'More',
            items: [
              { label: 'Hub', to: '/docs/kazma-hub/overview' },
              { label: 'Security', to: '/docs/security/security-policy' },
              { label: 'Contributing', to: '/docs/contributing/development-setup' },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} Kazma AI. Built with Docusaurus.`,
      },
      prism: {
        theme: prismThemes.github,
        darkTheme: prismThemes.dracula,
        additionalLanguages: ['python', 'bash', 'yaml', 'json', 'typescript'],
      },
    }),
};

module.exports = config;
