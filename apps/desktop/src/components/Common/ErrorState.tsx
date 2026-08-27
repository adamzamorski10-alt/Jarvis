/**
 * ErrorState — shown when Core is offline or an error occurred.
 */
import { CoreDiagnostics } from '../../services/apiClient';

interface ErrorStateProps {
  title?: string;
  description?: string;
  diagnostics?: CoreDiagnostics | null;
  onRetry?: () => void;
}

export function ErrorState({
  title = 'Core Offline',
  description = 'Unable to connect to local AI Operator Core. Make sure the Python Core service is running.',
  diagnostics,
  onRetry,
}: ErrorStateProps) {
  return (
    <div className="error-state fade-in">
      <div className="error-state-icon">⚠</div>
      <div className="error-state-title">{title}</div>
      <p className="error-state-desc">{description}</p>
      {onRetry && (
        <button className="btn btn-secondary" onClick={onRetry}>
          ↻ Retry Connection
        </button>
      )}
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', fontFamily: 'var(--font-mono)', textAlign: 'left' }}>
        <div>Status:</div>
        <div>Core process: {diagnostics?.running ? 'Running' : diagnostics?.pid ? 'Started' : 'Unknown'}</div>
        <div>Connection: Failed</div>
        <div>Retry attempts: {diagnostics?.retry_attempts ?? 0}/30</div>
        <div>Core URL: {diagnostics?.url ?? 'Unknown'}</div>
        {diagnostics?.last_error && <div>Last error: {diagnostics.last_error}</div>}
      </div>
    </div>
  );
}
