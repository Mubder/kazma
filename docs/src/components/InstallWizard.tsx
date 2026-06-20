import React, { useState } from 'react';

interface Step {
  id: string;
  title: string;
  description: string;
}

const WIZARD_STEPS: Step[] = [
  { id: 'welcome', title: 'Welcome', description: 'Welcome to the Kazma Skill Installation Wizard' },
  { id: 'select_skill', title: 'Select Skill', description: 'Browse and choose a skill from the hub' },
  { id: 'review_manifest', title: 'Review Manifest', description: 'Review the skill manifest and configuration' },
  { id: 'security_check', title: 'Security Check', description: 'Run security validation on the skill' },
  { id: 'confirm_install', title: 'Confirm', description: 'Confirm installation' },
  { id: 'install', title: 'Install', description: 'Installing the skill' },
  { id: 'verify', title: 'Verify', description: 'Verifying installation' },
  { id: 'success', title: 'Success', description: 'Skill installed successfully' },
];

export function InstallWizard(): JSX.Element {
  const [currentStep, setCurrentStep] = useState(0);

  const step = WIZARD_STEPS[currentStep];
  const progress = ((currentStep + 1) / WIZARD_STEPS.length) * 100;

  return (
    <div style={{ padding: '20px', border: '1px solid var(--ifm-color-emphasis-300)', borderRadius: '8px' }}>
      <h3>Kazma Skill Installation Wizard</h3>
      <div style={{
        height: '4px',
        background: 'var(--ifm-color-emphasis-200)',
        borderRadius: '2px',
        marginBottom: '20px',
        overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${progress}%`,
          background: 'var(--ifm-color-primary)',
          transition: 'width 0.3s ease',
        }} />
      </div>
      <div style={{ marginBottom: '8px', opacity: 0.6, fontSize: '0.85rem' }}>
        Step {currentStep + 1} of {WIZARD_STEPS.length}
      </div>
      <h4>{step.title}</h4>
      <p>{step.description}</p>
      <div style={{ display: 'flex', gap: '10px', marginTop: '20px' }}>
        {currentStep > 0 && (
          <button
            onClick={() => setCurrentStep((s) => s - 1)}
            style={{
              padding: '8px 16px',
              border: '1px solid var(--ifm-color-emphasis-300)',
              borderRadius: '6px',
              background: 'transparent',
              cursor: 'pointer',
            }}
          >
            Back
          </button>
        )}
        {currentStep < WIZARD_STEPS.length - 1 && (
          <button
            onClick={() => setCurrentStep((s) => s + 1)}
            style={{
              padding: '8px 16px',
              border: 'none',
              borderRadius: '6px',
              background: 'var(--ifm-color-primary)',
              color: 'white',
              cursor: 'pointer',
            }}
          >
            Next
          </button>
        )}
      </div>
      <div style={{ marginTop: '20px' }}>
        <details>
          <summary>All Steps</summary>
          <ol style={{ paddingLeft: '20px', marginTop: '10px' }}>
            {WIZARD_STEPS.map((s, i) => (
              <li key={s.id} style={{
                opacity: i <= currentStep ? 1 : 0.5,
                fontWeight: i === currentStep ? 'bold' : 'normal',
                marginBottom: '4px',
              }}>
                {s.title}
              </li>
            ))}
          </ol>
        </details>
      </div>
    </div>
  );
}
