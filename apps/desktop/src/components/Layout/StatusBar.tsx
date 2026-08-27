/**
 * Status Bar — bottom bar showing live system status.
 */
import { useCoreStatus } from '../../hooks/useCoreStatus';

export function StatusBar() {
  const { isOnline, status, lastChecked } = useCoreStatus();

  const fmt = (d: Date | null) =>
    d ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';

  return (
    <div className="status-bar">
      <div className="status-item">
        <div className={`status-dot ${isOnline ? 'online' : 'offline'}`} />
        <span>{isOnline ? 'Core Running' : 'Core Offline'}</span>
      </div>

      {isOnline && status && (
        <>
          <div className="status-item">
            <div className={`status-dot ${status.database.status === 'connected' ? 'online' : 'offline'}`} />
            <span>SQLite</span>
          </div>
          <div className="status-item">
            <div className="status-dot info" />
            <span>AI Not Configured</span>
          </div>
        </>
      )}

      <div style={{ marginLeft: 'auto' }} className="status-item">
        <span>Last check: {fmt(lastChecked)}</span>
      </div>

      <div className="status-item">
        <span style={{ color: 'var(--color-text-disabled)' }}>v0.1.0 — Phase 1</span>
      </div>
    </div>
  );
}
