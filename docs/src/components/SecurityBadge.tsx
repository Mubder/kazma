import React from 'react';

interface SecurityBadgeProps {
  score: number;
  showLabel?: boolean;
}

export function SecurityBadge({ score, showLabel = true }: SecurityBadgeProps): JSX.Element {
  const label =
    score >= 90 ? 'Excellent' :
    score >= 70 ? 'Good' :
    score >= 50 ? 'Fair' : 'Poor';

  const level =
    score >= 90 ? 'excellent' :
    score >= 70 ? 'good' :
    score >= 50 ? 'fair' : 'poor';

  return (
    <span className={`security-badge ${level}`}>
      {showLabel ? `${label} (${score}/100)` : `${score}/100`}
    </span>
  );
}
