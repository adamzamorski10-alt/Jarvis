/**
 * Dashboard — Main overview screen.
 * Shows real system status, recent activity, and quick actions.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCoreStatus } from '../../hooks/useCoreStatus';
import { useActivity } from '../../hooks/useActivity';
import { ErrorState } from '../../components/Common/ErrorState';
import { coreApi } from '../../services/coreApi';
import { useAIStatus, formatAIStatus } from '../../hooks/useAI';

function StatusCard({
  label,
  value,
  statusClass,
  detail,
}: {
  label: string;
  value: string;
  statusClass: string;
  detail?: string;
}) {
  return (
    <div className="stat-card">
      <div className="stat-card-label">{label}</div>
      <div className={`stat-card-value status-value ${statusClass}`} style={{ fontSize: 'var(--text-lg)' }}>
        {value}
      </div>
      {detail && <div className="stat-card-sub">{detail}</div>}
    </div>
  );
}

function ActivityItem({ event }: { event: { id: number; timestamp: string; level: string; category: string; message: string } }) {
  const time = new Date(event.timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  const levelColors: Record<string, string> = {
    INFO: 'var(--color-info)',
    WARNING: 'var(--color-warning)',
    ERROR: 'var(--color-error)',
    DEBUG: 'var(--color-text-muted)',
  };

  return (
    <div className="activity-item">
      <div
        className="activity-level-dot"
        style={{
          width: 6, height: 6, borderRadius: '50%', marginTop: 5,
          background: levelColors[event.level] ?? 'var(--color-text-muted)',
          flexShrink: 0,
        }}
      />
      <div className="activity-content">
        <div className="activity-message">{event.message}</div>
        <div className="activity-meta">
          <span className="badge badge-neutral" style={{ fontSize: '10px' }}>{event.category}</span>
        </div>
      </div>
      <div className="activity-timestamp">{time}</div>
    </div>
  );
}

export function DashboardView() {
  const { isOnline, status, isLoading, retry, coreUrl, diagnostics } = useCoreStatus();
  const { status: aiStatus } = useAIStatus();
  const { events, isLoading: activityLoading } = useActivity(8);
  const navigate = useNavigate();
  const [testingConnection, setTestingConnection] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<number | null>(null);
  const [activeTasks, setActiveTasks] = useState<number | null>(null);

  const handleTestConnection = useCallback(async () => {
    setTestingConnection(true);
    setTestResult(null);
    try {
      await coreApi.getSystemStatus();
      setTestResult('HEALTHY');
    } catch {
      setTestResult('FAILED');
    } finally {
      setTestingConnection(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    coreApi.getPendingApprovalCount()
      .then(res => { if (!cancelled) setPendingApprovals(res.count); })
      .catch(() => { if (!cancelled) setPendingApprovals(0); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    coreApi.getTasks({ status: 'RUNNING', limit: 50, offset: 0 })
      .then(res => { if (!cancelled) setActiveTasks(res.total); })
      .catch(() => { if (!cancelled) setActiveTasks(0); });
    return () => { cancelled = true; };
  }, []);

  if (!isLoading && !isOnline) {
    return (
      <div className="view-container">
        <div className="view-header">
          <h1 className="view-title">Dashboard</h1>
          <p className="view-subtitle">System overview and quick actions</p>
        </div>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
          <ErrorState
            title="Core Startup Failed"
            description="The local AI Operator Core could not be reached."
            diagnostics={diagnostics}
            onRetry={retry}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="view-container fade-in">
      <div className="view-header">
        <h1 className="view-title">Dashboard</h1>
        <p className="view-subtitle">System overview and recent activity</p>
      </div>

      <div className="view-body">

        {/* System Status Row */}
        <div>
          <div className="card-header" style={{ marginBottom: 'var(--space-3)' }}>
            <span className="card-title">
              <span className="card-icon">◈</span>
              System Status
            </span>
            {isLoading && <div className="spinner" style={{ width: 16, height: 16 }} />}
          </div>

          <div className="grid-4">
            <StatusCard
              label="Core Service"
              value={isLoading ? 'Checking…' : isOnline ? 'Running' : 'Offline'}
              statusClass={isLoading ? 'muted' : isOnline ? 'success' : 'error'}
            />
            <StatusCard
              label="Database"
              value={
                isLoading ? '…'
                : !isOnline ? '—'
                : status?.database.status === 'connected' ? 'Connected' : 'Error'
              }
              statusClass={
                isLoading ? 'muted'
                : !isOnline ? 'muted'
                : status?.database.status === 'connected' ? 'success' : 'error'
              }
              detail="SQLite"
            />
            <StatusCard
              label="AI Provider"
              value={aiStatus?.provider ?? '—'}
              statusClass={aiStatus?.ai_ready ? 'success' : ['online', 'ready'].includes(aiStatus?.provider_status ?? '') ? 'warning' : 'muted'}
              detail={formatAIStatus(aiStatus)}
            />
            <StatusCard
              label="Active Tasks"
              value={isLoading ? '…' : activeTasks === null ? '0' : String(activeTasks)}
              statusClass={isLoading ? 'muted' : activeTasks && activeTasks > 0 ? 'success' : 'muted'}
              detail="Autonomous tasks"
            />
          </div>
        </div>

        {/* Two-column: Activity + Quick Actions */}
        <div className="grid-2" style={{ alignItems: 'start' }}>

          {/* Recent Activity */}
          <div className="card">
            <div className="card-header">
              <span className="card-title">
                <span className="card-icon">≡</span>
                Recent Activity
              </span>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => navigate('/activity')}
              >
                View all →
              </button>
            </div>

            {activityLoading ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-6)' }}>
                <div className="spinner" />
              </div>
            ) : events.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 'var(--space-6)', color: 'var(--color-text-muted)', fontSize: 'var(--text-sm)' }}>
                No activity events yet
              </div>
            ) : (
              <div className="activity-list">
                {events.map(event => (
                  <ActivityItem key={event.id} event={event} />
                ))}
              </div>
            )}
          </div>

          {/* Quick Actions + System Info */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>

            {/* Quick Actions */}
            <div className="card">
              <div className="card-header">
                <span className="card-title">
                  <span className="card-icon">⚡</span>
                  Quick Actions
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                <button className="quick-action-btn" onClick={() => navigate('/settings')}>
                  <span className="icon">⚙</span>
                  Open Settings
                </button>
                <button className="quick-action-btn" onClick={() => navigate('/tasks')}>
                  <span className="icon">☑</span>
                  Open Tasks
                </button>
                <button className="quick-action-btn" onClick={() => navigate('/files')}>
                  <span className="icon">◫</span>
                  Open Files
                </button>
                <button className="quick-action-btn" onClick={() => navigate('/approvals')}>
                  <span className="icon">◈</span>
                  Open Approvals
                </button>
                <button className="quick-action-btn" onClick={() => navigate('/activity')}>
                  <span className="icon">≡</span>
                  View Activity Log
                </button>
                <button
                  className="quick-action-btn"
                  onClick={handleTestConnection}
                  disabled={testingConnection}
                >
                  <span className="icon">{testingConnection ? '…' : '◎'}</span>
                  Test Core Connection
                </button>
                {testResult && (
                  <div className={`badge ${testResult.includes('HEALTHY') || testResult.includes('OK') ? 'badge-success' : 'badge-error'}`}
                    style={{ alignSelf: 'flex-start', padding: 'var(--space-2) var(--space-3)' }}>
                    {testResult}
                  </div>
                )}
              </div>
            </div>

            {/* System Info */}
            <div className="card">
              <div className="card-header">
                <span className="card-title">
                  <span className="card-icon">ℹ</span>
                  System Information
                </span>
              </div>
              <div>
                <div className="info-row">
                  <span className="info-key">Application</span>
                  <span className="info-value">AI Operator</span>
                </div>
                <div className="info-row">
                  <span className="info-key">Version</span>
                  <span className="info-value">0.1.0</span>
                </div>
                <div className="info-row">
                  <span className="info-key">Phase</span>
                  <span className="info-value">1 — Foundation MVP</span>
                </div>
                <div className="info-row">
                  <span className="info-key">Core URL</span>
                  <span className="info-value">{coreUrl ?? 'Detecting'}</span>
                </div>
                <div className="info-row">
                  <span className="info-key">Database</span>
                  <span className="info-value">SQLite</span>
                </div>
                <div className="info-row">
                  <span className="info-key">Pending Approvals</span>
                  <span className="info-value">{pendingApprovals === null ? '…' : pendingApprovals}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
