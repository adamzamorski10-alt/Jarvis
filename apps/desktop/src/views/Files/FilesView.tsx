import { useState, useCallback } from 'react';
import { coreApi, DirectoryListResponse, FileReadResponse, FileSearchResponse } from '../../services/coreApi';

type ViewMode = 'list' | 'search' | 'read';

function formatSize(bytes: number | null | undefined) {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function EntryIcon({ type }: { type: string }) {
  if (type === 'directory') return '📁';
  if (type === 'symlink') return '🔗';
  if (type === 'file') return '📄';
  return '📦';
}

export function FilesView() {
  const [mode, setMode] = useState<ViewMode>('list');
  const [currentPath, setCurrentPath] = useState('.');
  const [listing, setListing] = useState<DirectoryListResponse | null>(null);
  const [readResult, setReadResult] = useState<FileReadResponse | null>(null);
  const [searchResult, setSearchResult] = useState<FileSearchResponse | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDirectory = useCallback(async (path: string) => {
    setIsLoading(true);
    setError(null);
    setReadResult(null);
    try {
      const data = await coreApi.getFilesList(path, false);
      setListing(data);
      setCurrentPath(data.path);
      setMode('list');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to list directory');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadParent = useCallback(() => {
    if (currentPath === '.' || currentPath === '') {
      loadDirectory('.');
    } else {
      const parts = currentPath.split('/').filter(Boolean);
      parts.pop();
      const parent = parts.length === 0 ? '.' : parts.join('/');
      loadDirectory(parent);
    }
  }, [currentPath, loadDirectory]);

  const readFile = useCallback(async (name: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const fullPath = currentPath === '.' ? name : `${currentPath}/${name}`;
      const data = await coreApi.getFilesRead(fullPath);
      setReadResult(data);
      setMode('read');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to read file');
    } finally {
      setIsLoading(false);
    }
  }, [currentPath]);

  const doSearch = useCallback(async () => {
    if (!searchQuery.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await coreApi.getFilesSearch(searchQuery.trim(), currentPath === '.' ? undefined : currentPath, 50);
      setSearchResult(data);
      setMode('search');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to search files');
    } finally {
      setIsLoading(false);
    }
  }, [searchQuery, currentPath]);

  const navigateInto = useCallback((name: string, type: string) => {
    if (type === 'directory') {
      const next = currentPath === '.' ? name : `${currentPath}/${name}`;
      loadDirectory(next);
    } else if (type === 'file') {
      readFile(name);
    }
  }, [currentPath, loadDirectory, readFile]);

  return (
    <div className="view-container fade-in">
      <div className="view-header">
        <h1 className="view-title">Files</h1>
        <p className="view-subtitle">Workspace file explorer</p>
      </div>

      <div className="view-body">
        <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-3)', flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => loadDirectory(currentPath)} disabled={isLoading}>
            Refresh
          </button>
          <button className="btn btn-secondary btn-sm" onClick={loadParent} disabled={isLoading || currentPath === '.'}>
            ← Back
          </button>
          <div style={{ display: 'flex', gap: 'var(--space-2)', flex: 1, minWidth: 200 }}>
            <input
              className="form-input"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search files..."
              onKeyDown={e => { if (e.key === 'Enter') doSearch(); }}
              style={{ flex: 1 }}
            />
            <button className="btn btn-primary btn-sm" onClick={doSearch} disabled={isLoading || !searchQuery.trim()}>
              Search
            </button>
          </div>
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
        ) : mode === 'list' && listing ? (
          <div>
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', marginBottom: 'var(--space-2)' }}>
              {listing.path === '.' ? 'Workspace root' : listing.path}
              {listing.truncated && ' (truncated)'}
            </div>
            {listing.entries.length === 0 ? (
              <div className="card" style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                Empty directory
              </div>
            ) : (
              <div className="card" style={{ padding: 0 }}>
                {listing.entries.map((entry, idx) => (
                  <div
                    key={idx}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 'var(--space-3)',
                      padding: 'var(--space-2) var(--space-3)',
                      cursor: 'pointer',
                      borderBottom: idx < listing.entries.length - 1 ? '1px solid var(--color-border-subtle)' : 'none',
                    }}
                    onClick={() => navigateInto(entry.name, entry.type)}
                  >
                    <span style={{ fontSize: 'var(--text-lg)' }}>{EntryIcon({ type: entry.type })}</span>
                    <span style={{ flex: 1, fontSize: 'var(--text-sm)', wordBreak: 'break-all' }}>{entry.name}</span>
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', textTransform: 'capitalize' }}>{entry.type}</span>
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', minWidth: 60, textAlign: 'right' }}>{formatSize(entry.size)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : mode === 'read' && readResult ? (
          <div>
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', marginBottom: 'var(--space-2)' }}>
              {readResult.size} bytes · {readResult.encoding} {readResult.truncated ? '· Truncated' : ''}
            </div>
            <pre className="file-content">{readResult.content}</pre>
          </div>
        ) : mode === 'search' && searchResult ? (
          <div>
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', marginBottom: 'var(--space-2)' }}>
              {searchResult.matches.length} match{searchResult.matches.length === 1 ? '' : 'es'} for "{searchResult.query}" in {searchResult.path}
              {searchResult.truncated ? ' (truncated)' : ''}
            </div>
            {searchResult.matches.length === 0 ? (
              <div className="card" style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                No matches found
              </div>
            ) : (
              <div className="card" style={{ padding: 0 }}>
                {searchResult.matches.map((entry, idx) => (
                  <div
                    key={idx}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 'var(--space-3)',
                      padding: 'var(--space-2) var(--space-3)',
                      cursor: 'pointer',
                      borderBottom: idx < searchResult.matches.length - 1 ? '1px solid var(--color-border-subtle)' : 'none',
                    }}
                    onClick={() => {
                      const parts = entry.path.split('/').filter(Boolean);
                      const name = parts.pop() || entry.name;
                      const dir = parts.join('/');
                      if (dir) {
                        loadDirectory(dir).then(() => {
                          if (entry.type === 'file') readFile(name);
                        });
                      } else {
                        readFile(entry.name);
                      }
                    }}
                  >
                    <span style={{ fontSize: 'var(--text-lg)' }}>{EntryIcon({ type: entry.type })}</span>
                    <span style={{ flex: 1, fontSize: 'var(--text-sm)', fontFamily: 'monospace', wordBreak: 'break-all' }}>{entry.path}</span>
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>{formatSize(entry.size)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
