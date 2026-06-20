import React from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import {SkillCard} from '../components/SkillCard';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary')} style={{padding: '4rem 0'}}>
      <div className="container">
        <h1 className="hero__title">{siteConfig.title}</h1>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <div style={{display: 'flex', gap: '12px', justifyContent: 'center', marginTop: '24px'}}>
          <Link className="button button--secondary button--lg" to="/docs/getting-started/installation">
            Get Started
          </Link>
          <Link className="button button--secondary button--outline button--lg" to="/docs/kazma-hub/overview">
            Browse Skills
          </Link>
        </div>
      </div>
    </header>
  );
}

function Features() {
  const features = [
    {title: 'Skill Marketplace', description: 'Discover, publish, and manage AI agent skills through Kazma Hub.'},
    {title: 'Multi-Agent Delegation', description: 'Delegate tasks to specialized sub-agents with automatic capability matching.'},
    {title: 'Arabic RTL Support', description: 'First-class Arabic dialect detection and right-to-left rendering.'},
    {title: 'Security by Default', description: 'Sandboxing, permission checks, audit trails, and automated security auditing.'},
    {title: 'Context Management', description: 'Automatic context compaction and checkpointing for long conversations.'},
    {title: 'MCP Integration', description: 'Connect to Model Context Protocol servers for external tool access.'},
  ];

  return (
    <section style={{padding: '4rem 0'}}>
      <div className="container">
        <h2 style={{textAlign: 'center', marginBottom: '2rem'}}>Key Features</h2>
        <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '24px'}}>
          {features.map((f) => (
            <div key={f.title} style={{padding: '1.5rem', border: '1px solid var(--ifm-color-emphasis-200)', borderRadius: '8px'}}>
              <h3 style={{marginTop: 0}}>{f.title}</h3>
              <p>{f.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function HubPreview() {
  const sampleSkills = [
    {name: 'weather-skill', author: 'kazma-team', version: '1.0.0', description: 'Real-time weather data and forecasting for any location.', certification: 'standard' as const, securityScore: 95, capabilities: ['weather_data', 'forecasting']},
    {name: 'drone-inspector', author: 'aerial-ai', version: '2.1.0', description: 'Autonomous drone inspection with image analysis and reporting.', certification: 'premium' as const, securityScore: 98, capabilities: ['drone_control', 'image_analysis']},
    {name: 'data-analyst', author: 'data-lab', version: '0.9.0', description: 'Statistical analysis and visualization of structured datasets.', certification: 'basic' as const, securityScore: 82, capabilities: ['data_processing', 'visualization']},
  ];

  return (
    <section style={{padding: '4rem 0', background: 'var(--ifm-color-emphasis-100)'}}>
      <div className="container">
        <h2 style={{textAlign: 'center', marginBottom: '2rem'}}>Explore Kazma Hub</h2>
        <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px'}}>
          {sampleSkills.map((s) => <SkillCard key={s.name} {...s} />)}
        </div>
        <div style={{textAlign: 'center', marginTop: '24px'}}>
          <Link className="button button--primary button--lg" to="/docs/kazma-hub/finding-skills">
            Browse All Skills
          </Link>
        </div>
      </div>
    </section>
  );
}

export default function Home() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout title={siteConfig.title} description={siteConfig.tagline}>
      <HomepageHeader />
      <main>
        <Features />
        <HubPreview />
      </main>
    </Layout>
  );
}
