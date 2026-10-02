import { useState, useCallback } from 'react';
import { coreApi, TaskListItem, TaskDetailResponse } from '../../services/coreApi';

type Filter = 'all' | 'active' | 'completed' | 'failed';

const FILTER_STATUS_MAP: Record<Filter, string | undefined> = {
  all: undefined,
  active: 'RUNNING',
  completed: 'COMMITTED',
  failed: 'FAILED',
};

function formatDate(iso: string | null | undefined) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    PENDING: { label: 'Pending', cls: 'badge-neutral' },
    RUNNING: { label: 'Running', cls: 'badge-primary' },
    VERIFYING: { label: 'Verifying', cls: 'badge-primary' },
    PASSED: { label: 'Passed', cls: 'badge-info' },
    REPAIR_REQUIRED: { label: 'Repair', cls: 'badge-warning' },
    COMMITTED: { label: 'Completed', cls: 'badge-success' },
    PAUSED: { label: 'Paused', cls: 'badge-neutral' },
    FAILED: { label: 'Failed', cls: 'badge-error' },
    CANCELLED: { label: 'Cancelled', cls: 'badge-neutral' },
    ABORTED: { label: 'Aborted', cls: 'badge-error' },
  };
  const info = map[status] || { label: status, cls: 'badge-neutral' };
  return <span className={`badge ${info.cls}`}>{info.label}</span>;
}

export function TasksView() {
  const [filter, setFilter] = useState<Filter>('all');
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<TaskDetailResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const fetchTasks = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await coreApi.getTasks({ status: FILTER_STATUS_MAP[filter], limit: 50, offset: 0 });
      setTasks(data.tasks);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks');
    } finally {
      setIsLoading(false);
    }
  }, [filter]);

  const openTask = useCallback(async (taskId: string) => {
    setLoadingDetail(true);
    setSelectedTask(null);
    try {
      const detail = await coreApi.getTask(taskId);
      setSelectedTask(detail);
    } catch {
      setSelectedTask(null);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  return (
    <div className="view-container fade-in">
      <div className="view-header">
        <h1 className="view-title">Tasks</h1>
        <p className="view-subtitle">Autonomous development task inspection</p>
      </div>

      <div className="view-body">
        <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-3)', flexWrap: 'wrap', alignItems: 'center' }}>
          {(['all', 'active', 'completed', 'failed'] as Filter[]).map(f => (
            <button
              key={f}
              className={`btn btn-sm ${filter === f ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? 'All' : f === 'active' ? 'Active' : f === 'completed' ? 'Completed' : 'Failed'}
            </button>
          ))}
          <button className="btn btn-secondary btn-sm" onClick={fetchTasks} disabled={isLoading}>
            Refresh
          </button>
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
            {isLoading ? 'Loading…' : `${total} task${total === 1 ? '' : 's'}`}
          </span>
        </div>

        {error && (
          <div className="card" style={{ padding: 'var(--space-4)', color: 'var(--color-error)', marginBottom: 'var(--space-3)' }}>
            {error}
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: selectedTask ? '1fr 1fr' : '1fr', gap: 'var(--space-4)' }}>
          {/* Task List */}
          <div>
            {isLoading ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-8)' }}>
                <div className="spinner" />
              </div>
            ) : tasks.length === 0 ? (
              <div className="card" style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                No tasks found
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                {tasks.map(task => (
                  <div
                    key={task.task_id}
                    className="card"
                    style={{ padding: 'var(--space-3)', cursor: 'pointer' }}
                    onClick={() => openTask(task.task_id)}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', marginBottom: 'var(--space-1)' }}>
                          {task.title}
                        </div>
                        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                          <span>{task.task_id}</span>
                          {task.plan_id && <span>Plan: {task.plan_id}</span>}
                          {task.project_id && <span>Project: {task.project_id}</span>}
                        </div>
                        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', marginTop: 'var(--space-1)' }}>
                          Created: {formatDate(task.created_at)}
                          {task.started_at && <span> · Started: {formatDate(task.started_at)}</span>}
                          {task.completed_at && <span> · Completed: {formatDate(task.completed_at)}</span>}
                        </div>
                        {task.failure_reason && (
                          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-error)', marginTop: 'var(--space-1)' }}>
                            {task.failure_reason}
                          </div>
                        )}
                      </div>
                      <StatusBadge status={task.status} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Task Detail */}
          {(selectedTask || loadingDetail) && (
            <div className="card" style={{ padding: 'var(--space-4)' }}>
              {loadingDetail ? (
                <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-6)' }}>
                  <div className="spinner" />
                </div>
              ) : selectedTask ? (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-3)' }}>
                    <h3 style={{ margin: 0, fontSize: 'var(--text-base)' }}>{selectedTask.title}</h3>
                    <StatusBadge status={selectedTask.status} />
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 'var(--text-sm)' }}>
                    <div><strong>Task ID:</strong> <code>{selectedTask.task_id}</code></div>
                    <div><strong>Task Key:</strong> {selectedTask.task_key || '—'}</div>
                    <div><strong>Status:</strong> {selectedTask.status}</div>
                    <div><strong>Project:</strong> {selectedTask.project_id || '—'}</div>
                    <div><strong>Plan:</strong> {selectedTask.plan_id || '—'}</div>
                    <div><strong>Parent:</strong> {selectedTask.parent_task_id || '—'}</div>
                    <div><strong>Attempt:</strong> {selectedTask.attempt_count} / {selectedTask.max_attempts}</div>
                    <div><strong>Priority:</strong> {selectedTask.priority}</div>
                    <div><strong>Created:</strong> {formatDate(selectedTask.created_at)}</div>
                    <div><strong>Started:</strong> {formatDate(selectedTask.started_at)}</div>
                    <div><strong>Completed:</strong> {formatDate(selectedTask.completed_at)}</div>
                    {selectedTask.failure_reason && (
                      <div style={{ color: 'var(--color-error)' }}><strong>Failure:</strong> {selectedTask.failure_reason}</div>
                    )}
                    {selectedTask.workspace_path && (
                      <div><strong>Workspace:</strong> <code style={{ fontSize: 'var(--text-xs)' }}>{selectedTask.workspace_path}</code></div>
                    )}
                    {selectedTask.execution_token && (
                      <div><strong>Execution:</strong> <code style={{ fontSize: 'var(--text-xs)' }}>{selectedTask.execution_token}</code></div>
                    )}
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
