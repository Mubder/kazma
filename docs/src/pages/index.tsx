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
      title: 'Commitment / Resolve-before-Act',
      description: 'A policy gate between the LLM and durable mutations. Intent is resolved against memory before acting — the model cannot invent a date and schedule it over a real belief. Semantic clarify/confirm cards disambiguate on every platform.'
    },
    {
      title: 'Triple-Wired HITL Safety',
      description: 'Three independent human-in-the-loop gates — graph interrupt, swarm bus, and pipeline checkpoints — pause danger tools before execution. Approve/deny from Web SSE, Telegram, Discord, and Slack. Fail-closed by default.'
    },
    {
      title: 'Non-Stop & Self-Healing',
      description: 'A supervisor watchdog tracks node heartbeats, detects stalls, and rolls back to a clean checkpoint with reflection injection. Model failover chains, a durable LLM call ledger, orphan-task recovery, and an HITL approval timeout keep long-horizon tasks running.'
    },
    {
      title: 'Document Intelligence',
      description: 'A secure, durable document platform: streamed intake → content-addressed quarantine → policy sniff → isolated subprocess parse/OCR → durable jobs → optional Knowledge index, plus generate/convert/redact and ops (capacity, GC, audit, cert).'
    },
    {
      title: 'V2 Cognitive Memory',
      description: 'Bi-temporal belief tracking, Local Ego-Graph Personalized PageRank recall, hybrid FTS + vector episode retrieval, and prompt-fenced per-turn context injection. SQLite is the zero-config default; Postgres/Qdrant/Neo4j optional.'
    },
    {
      title: 'Arabic-Native',
      description: 'A custom Arabic tokenizer, full RTL UI mirroring, Kuwaiti-dialect support, and the Majlis cultural protocol. Built in Kuwait — first-class multilingual intelligence, not an afterthought.'
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
          <div><span style={{color: 'var(--ifm-color-primary)'}}># Clone and install all extras</span></div>
          <div>git clone https://github.com/Mubder/kazma.git</div>
          <div>cd kazma && uv sync --all-extras</div>
          <br />
          <div><span style={{color: 'var(--ifm-color-primary)'}}># Configure at least one LLM key</span></div>
          <div>cp .env.example .env   # then set OPENAI_API_KEY=sk-...</div>
          <br />
          <div><span style={{color: 'var(--ifm-color-primary)'}}># Start the Web UI (http://127.0.0.1:9090)</span></div>
          <div>kazma serve</div>
          <br />
          <div><span style={{color: 'var(--ifm-color-primary)'}}># Or launch the terminal dashboard</span></div>
          <div>kazma-tui</div>
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
