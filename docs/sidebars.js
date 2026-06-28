/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  gettingStarted: [
    'getting-started/installation',
    'getting-started/quickstart',
    'getting-started/configuration',
    'getting-started/first-skill',
  ],
  coreConcepts: [
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
    'security/security-policy',
    'security/vulnerability-reporting',
    'security/hardening-guide',
  ],
  contributing: [
    'contributing/development-setup',
    'contributing/code-style',
    'contributing/testing',
    'contributing/pull-requests',
  ],
};

module.exports = sidebars;
