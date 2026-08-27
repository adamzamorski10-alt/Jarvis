/**
 * ComingSoon — Placeholder for unimplemented modules.
 */
interface ComingSoonProps {
  moduleName: string;
  description?: string;
  icon?: string;
  phase?: string;
}

export function ComingSoon({
  moduleName,
  description,
  icon = '◈',
  phase,
}: ComingSoonProps) {
  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="view-title">{moduleName}</h1>
      </div>
      <div className="coming-soon fade-in">
        <div className="coming-soon-icon">{icon}</div>
        <div className="coming-soon-title">Module not yet initialized</div>
        <p className="coming-soon-desc">
          {description ??
            `The ${moduleName} module will be implemented in a future phase of AI Operator. The foundation and architecture are in place.`}
        </p>
        {phase && (
          <div className="badge badge-info" style={{ marginTop: 'var(--space-2)' }}>
            Planned: {phase}
          </div>
        )}
      </div>
    </div>
  );
}
