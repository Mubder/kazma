import React from 'react';

interface SkillCardProps {
  name: string;
  author: string;
  version: string;
  description: string;
  certification?: 'basic' | 'standard' | 'premium';
  securityScore?: number;
  capabilities?: string[];
  installed?: boolean;
}

export function SkillCard({
  name,
  author,
  version,
  description,
  certification,
  securityScore,
  capabilities = [],
  installed = false,
}: SkillCardProps): JSX.Element {
  const securityLabel =
    securityScore >= 90 ? 'excellent' :
    securityScore >= 70 ? 'good' :
    securityScore >= 50 ? 'fair' : 'poor';

  return (
    <div className="skill-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h3 style={{ margin: '0 0 4px 0' }}>{name}</h3>
          <p style={{ margin: 0, opacity: 0.7, fontSize: '0.9rem' }}>
            by {author} v{version}
          </p>
        </div>
        {certification && (
          <span className={`certification-badge ${certification}`}>
            {certification === 'premium' ? '\u2B50' : certification === 'standard' ? '\u2B50' : '\u2714'}
            {' '}{certification.charAt(0).toUpperCase() + certification.slice(1)}
          </span>
        )}
      </div>
      <p style={{ margin: '12px 0' }}>{description}</p>
      {capabilities.length > 0 && (
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '8px' }}>
          {capabilities.map((cap) => (
            <span key={cap} style={{
              padding: '2px 8px',
              background: 'var(--ifm-color-emphasis-100)',
              borderRadius: '4px',
              fontSize: '0.8rem',
            }}>
              {cap}
            </span>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {securityScore !== undefined && (
          <span className={`security-badge ${securityLabel}`}>
            Security: {securityScore}/100
          </span>
        )}
        {installed && (
          <span style={{ color: '#16a34a', fontWeight: 600, fontSize: '0.85rem' }}>
            Installed
          </span>
        )}
      </div>
    </div>
  );
}
