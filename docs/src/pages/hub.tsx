import React from 'react';
import Layout from '@theme/Layout';
import {SkillCard} from '../components/SkillCard';

const SKILLS = [
  {name: 'weather-skill', author: 'kazma-team', version: '1.0.0', description: 'Real-time weather data and forecasting.', certification: 'standard' as const, securityScore: 95, capabilities: ['weather_data', 'forecasting']},
  {name: 'drone-inspector', author: 'aerial-ai', version: '2.1.0', description: 'Autonomous drone inspection with image analysis.', certification: 'premium' as const, securityScore: 98, capabilities: ['drone_control', 'image_analysis']},
  {name: 'data-analyst', author: 'data-lab', version: '0.9.0', description: 'Statistical analysis and visualization.', certification: 'basic' as const, securityScore: 82, capabilities: ['data_processing', 'visualization']},
  {name: 'code-reviewer', author: 'kazma-team', version: '1.2.0', description: 'Automated code review with security scanning.', certification: 'standard' as const, securityScore: 91, capabilities: ['code_analysis', 'security_scan']},
  {name: 'translator', author: 'lang-ai', version: '1.0.0', description: 'Multi-language translation with dialect support.', certification: 'basic' as const, securityScore: 88, capabilities: ['translation', 'dialect_detection']},
  {name: 'scheduler', author: 'kazma-team', version: '0.8.0', description: 'Calendar management and meeting scheduling.', certification: 'standard' as const, securityScore: 93, capabilities: ['calendar', 'scheduling']},
];

export default function HubPage(): JSX.Element {
  return (
    <Layout title="Kazma Hub" description="Browse and discover AI agent skills">
      <main style={{padding: '2rem'}}>
        <div style={{maxWidth: '1200px', margin: '0 auto'}}>
          <h1>Kazma Hub</h1>
          <p style={{fontSize: '1.1rem', opacity: 0.8}}>
            Discover and install skills for your Kazma agents.
          </p>
          <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(350px, 1fr))', gap: '16px', marginTop: '2rem'}}>
            {SKILLS.map((s) => <SkillCard key={s.name} {...s} />)}
          </div>
        </div>
      </main>
    </Layout>
  );
}
