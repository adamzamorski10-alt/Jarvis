/**
 * Activity View — Full activity log with pagination and filtering.
 */
import { useState, useCallback } from 'react';
import { useActivity } from '../../hooks/useActivity';
import { ActivityEvent } from '../../services/coreApi';

const LEVEL_COLORS: Record<string, string> = {
  INFO: 'var(--color-info)',
  WARNING: 'var(--color-warning)',
  ERROR: 'var(--color-error)',
  DEBUG: 'var(--color-text-muted)',
  CRITICAL: 'var(--color-error)',
};

const CATEGORIES = ['', 'system', 'database', 'application', 'api', 'settings'];
const LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR'];

function ActivityRow({ event }: { event: ActivityEvent }) {
  const ts = new Date(event.timestamp);
  const dateStr = ts.toLocaleDateString([], { month: 'short', day: 'numeric' });
  const timeStr = ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  return (
    <div className="activity-item">
      <div
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          marginTop: 7,
          flexShrink: 0,
          background: LEVEL_COLORS[event.level] ?? 'var(--color-text-muted)',
        }}
      />
      <div className="activity-content">
        <div className="activity-message selectable">{event.message}</div>
        <div className="activity-meta">
          <span className="badge badge-neutral" style={{ fontSize: '10px' }}>{event.category}</span>
          <span style={{ color: 'var(--color-text-muted)', fontSize: 'var(--text-xs)' }}>
            {event.level}
          </span>
          {event.details && (
            <span style={{ color: 'var(--color-text-muted)', fontSize: 'var(--text-xs)' }}>
              has details
            </span>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2, flexShrink: 0 }}>
        <div className="activity-timestamp">{timeStr}</div>
        <div style={{ fontSize: '10px', color: 'var(--color-text-disabled)' }}>{dateStr}</div>
      </div>
    </div>
  );
}

export function ActivityView() {
  const [categoryFilter, setCategoryFilter] = useState('');
  const [levelFilter, setLevelFilter] = useState('');
  const { events, total, isLoading, error, offset, limit, refresh, fetchPage } = useActivity(50);

  const applyFilters = useCallback(
    (cat: string, lvl: string) => {
      fetchPage(50, 0, cat || undefined, lvl || undefined);
    },
    [fetchPage],
  );

  const handleCategory = (val: string) => {
    setCategoryFilter(val);
    applyFilters(val, levelFilter);
  };

  const handleLevel = (val: string) => {
    setLevelFilter(val);
    applyFilters(categoryFilter, val);
  };

  const hasMore = offset + limit < total;
  const hasPrev = offset > 0;

  return (
    <div className="view-container fade-in">
      <div className="view-header">
        <h1 className="view-title">Activity Log</h1>
        <p className="view-subtitle">Real-time audit trail of all system events</p>
      </div>

      <div className="view-body">
        {/* Filters + Controls */}
        <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', flexWrap: 'wrap' }}>
          <select
            className="form-select"
            value={categoryFilter}
            onChange={e => handleCategory(e.target.value)}
            style={{ width: 140 }}
          >
            <option value="">All Categories</option>
            {CATEGORIES.slice(1).map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>

          <select
            className="form-select"
            value={levelFilter}
            onChange={e => handleLevel(e.target.value)}
            style={{ width: 120 }}
          >
            <option value="">All Levels</option>
            {LEVELS.slice(1).map(l => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>

          <button className="btn btn-secondary btn-sm" onClick={refresh} disabled={isLoading}>
            ↻ Refresh
          </button>

          <div style={{ marginLeft: 'auto', fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)' }}>
            {isLoading ? 'Loading…' : `${total} events`}
          </div>
        </div>

        {/* Event List */}
        <div className="card" style={{ padding: 0 }}>
          {isLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-8)' }}>
              <div className="spinner" />
            </div>
          ) : error ? (
            <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-error)' }}>
              {error}
            </div>
          ) : events.length === 0 ? (
            <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--color-text-muted)' }}>
              {categoryFilter || levelFilter ? 'No activity events match the current filters.' : 'No activity yet.'}
            </div>
          ) : (
            <div className="activity-list" style={{ padding: 'var(--space-2)' }}>
              {events.map(event => (
                <ActivityRow key={event.id} event={event} />
              ))}
            </div>
          )}
        </div>

        {/* Pagination */}
        {(hasPrev || hasMore) && (
          <div style={{ display: 'flex', gap: 'var(--space-2)', justifyContent: 'center' }}>
            <button
              className="btn btn-secondary btn-sm"
              disabled={!hasPrev}
              onClick={() => fetchPage(limit, offset - limit, categoryFilter || undefined, levelFilter || undefined)}
            >
              ← Previous
            </button>
            <span style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', padding: 'var(--space-2) var(--space-3)' }}>
              {Math.floor(offset / limit) + 1} / {Math.ceil(total / limit)}
            </span>
            <button
              className="btn btn-secondary btn-sm"
              disabled={!hasMore}
              onClick={() => fetchPage(limit, offset + limit, categoryFilter || undefined, levelFilter || undefined)}
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
