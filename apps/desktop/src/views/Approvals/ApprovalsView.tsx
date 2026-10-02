import { useState, useCallback } from 'react';
import { useApprovals } from '../../hooks/useApprovals';
import { coreApi } from '../../services/coreApi';

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function TruncatedJson({ data, maxLength = 300 }: { data: Record<string, unknown>; maxLength?: number }) {
  const raw = JSON.stringify(data, null, 2);
  const truncated = raw.length > maxLength;
  const display = truncated ? raw.slice(0, maxLength) + '... [TRUNCATED]' : raw;

  return (
    <pre className="file-content" style={{ fontSize: 'var(--text-xs)', maxHeight: 120, overflow: 'auto' }}>
      {display}
    </pre>
  );
}

function ApprovalCard({ approval, onUpdated }: { approval: {
  approval_id: string;
  tool_id: string;
  action: string;
  arguments: Record<string, unknown>;
  created_at: string;
  expires_at: string;
  conversation_id: string;
}, onUpdated: () => void }) {
  const [processing, setProcessing] = useState<string | null>(null);

  const handleApprove = async () => {
    const reason = window.prompt('Optional decision reason:');
    if (reason === null) return;
    setProcessing(approval.approval_id);
    try {
      await coreApi.approveTool(approval.approval_id, reason || undefined);
      onUpdated();
    } finally {
      setProcessing(null);
    }
  };

  const handleReject = async () => {
    const reason = window.prompt('Optional rejection reason:');
    if (reason === null) return;
    setProcessing(approval.approval_id);
    try {
      await coreApi.rejectTool(approval.approval_id, reason || undefined);
      onUpdated();
    } finally {
      setProcessing(null);
    }
  };

  const busy = processing === approval.approval_id;

  return (
    <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-3)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center', marginBottom: 'var(--space-2)', flexWrap: 'wrap' }}>
            <span className="badge badge-primary">{approval.tool_id}</span>
            <span className="badge badge-neutral">{approval.action}</span>
          </div>
          <TruncatedJson data={approval.arguments} maxLength={400} />
          <div style={{ display: 'flex', gap: 'var(--space-3)', fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', flexWrap: 'wrap', marginTop: 'var(--space-2)' }}>
            <span>Created: {formatDate(approval.created_at)}</span>
            <span>Expires: {formatDate(approval.expires_at)}</span>
            <span>Conversation: {approval.conversation_id}</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', flexShrink: 0 }}>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleApprove}
            disabled={busy}
          >
            {busy ? '...' : 'Approve'}
          </button>
          <button
            className="btn btn-secondary btn-sm"
            onClick={handleReject}
            disabled={busy}
          >
            {busy ? '...' : 'Reject'}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ApprovalsView() {
  const { approvals, total, isLoading, error, refresh } = useApprovals(50);

  const handleRefresh = useCallback(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="view-container fade-in">
      <div className="view-header">
        <h1 className="view-title">Approvals</h1>
        <p className="view-subtitle">Review and act on pending AI actions</p>
      </div>

      <div className="view-body">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-3)' }}>
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)' }}>
            {isLoading ? 'Loading...' : `${total} pending`}
          </div>
          <button className="btn btn-secondary btn-sm" onClick={handleRefresh} disabled={isLoading}>
            Refresh
          </button>
        </div>

        {error && (
          <div className="card" style={{ padding: 'var(--space-4)', color: 'var(--color-error)', marginBottom: 'var(--space-3)' }}>
            {error}
          </div>
        )}

        {isLoading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-8)' }}>
            <div className="spinner" />
          </div>
        ) : approvals.length === 0 ? (
          <div className="card" style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--color-text-muted)' }}>
            No pending approvals
          </div>
        ) : (
          approvals.map(approval => (
            <ApprovalCard key={approval.approval_id} approval={approval} onUpdated={handleRefresh} />
          ))
        )}
      </div>
    </div>
  );
}
