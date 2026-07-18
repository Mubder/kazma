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
        <div style={{display: 'flex', gap: '12px', justifyContent: 'center', marginTop: '24px', flexWrap: 'wrap'}}>
          <Link className="button button--secondary button--lg" to="/docs/guide/quickstart">
            Quickstart
          </Link>
          <Link className="button button--secondary button--outline button--lg" to="/docs/guide/architecture">
            Architecture
          </Link>
          <Link className="button button--secondary button--outline button--lg" to="/docs/kazma-hub/overview">
            Skills Hub
          </Link>
        </div>
      </div>
    </header>
  );
}

function Features() {
  const features = [
    {
      title: 'Skill Marketplace & Signatures',
      description: 'Discover, publish, and manage skills. Features HMAC-SHA256 timing-safe checksum verification against supply-chain injection attacks.'
    },
    {
      title: 'Triple-Wired Safety Gates',
      description: 'Human-in-the-Loop (HITL) authorization gates for danger tools, integrated over real-time Web SSE, Telegram, Discord, and Slack.'
    },
    {
      title: 'Corporate Command Console (TUI)',
      description: 'Vim/Tmux-inspired TUI terminal with scrolling sparkline telemetry, ASCII topology maps, and interactive hitl approval screens.'
    },
    {
      title: 'Sandbox-Isolated Web DAGs',
      description: 'Crash-proof, isolated Mermaid.js visual pipelines rendering. Uses standard SVG attributes to completely eliminate syntax conflicts.'
    },
    {
      title: 'Arabic RTL Mirroring',
      description: 'First-class dialect detection, bi-directional text-wrapping, and complete RTL grid mirroring across Web and TUI interfaces.'
    },
    {
      title: 'Enterprise Reliability',
      description: 'SQLite WAL mode and ConfigStore singletons prevent database locking under high-concurrency swarm operations.'
    },
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

function QuickStart() {
  return (
    <section style={{padding: '4rem 0'}}>
      <div className="container" style={{maxWidth: '800px', margin: '0 auto'}}>
        <h2 style={{textAlign: 'center', marginBottom: '2rem'}}>Quick Start</h2>
        <div style={{
          background: 'var(--ifm-pre-background)',
          padding: '1.5rem',
          borderRadius: '8px',
          fontFamily: 'monospace',
          fontSize: '0.9rem',
          border: '1px solid var(--ifm-color-emphasis-200)',
          color: 'var(--ifm-code-color)',
          lineHeight: '1.6'
        }}>
          <div><span style={{color: 'var(--ifm-color-primary)'}}># Create a new secure Kazma project</span></div>
          <div>npx -y create-kazma-app@latest ./my-swarm</div>
          <br />
          <div><span style={{color: 'var(--ifm-color-primary)'}}># Start the backend uvicorn coordinator</span></div>
          <div>uv run python -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 8090</div>
          <br />
          <div><span style={{color: 'var(--ifm-color-primary)'}}># Launch the terminal dash dashboard</span></div>
          <div>uv run kazma-tui</div>
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
        <QuickStart />
      </main>
    </Layout>
  );
}
