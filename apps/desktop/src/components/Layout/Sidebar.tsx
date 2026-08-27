/**
 * Sidebar — Main navigation panel with all 12 module routes.
 */
import { NavLink } from 'react-router-dom';
import { useCoreStatus } from '../../hooks/useCoreStatus';
import { useAIStatus } from '../../hooks/useAI';

interface NavItem {
  path: string;
  label: string;
  icon: string;
  implemented: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { path: '/', label: 'Dashboard', icon: '⬡', implemented: true },
  { path: '/assistant', label: 'Assistant', icon: '◎', implemented: true },
  { path: '/tasks', label: 'Tasks', icon: '☑', implemented: false },
  { path: '/agents', label: 'Agents', icon: '◈', implemented: false },
  { path: '/files', label: 'Files', icon: '◫', implemented: false },
  { path: '/projects', label: 'Projects', icon: '⬙', implemented: false },
  { path: '/browser', label: 'Browser', icon: '◉', implemented: false },
  { path: '/messages', label: 'Messages', icon: '◳', implemented: false },
  { path: '/automations', label: 'Automations', icon: '⟳', implemented: false },
  { path: '/approvals', label: 'Approvals', icon: '◈', implemented: false },
  { path: '/activity', label: 'Activity', icon: '≡', implemented: true },
  { path: '/settings', label: 'Settings', icon: '⚙', implemented: true },
];

const IMPLEMENTED = NAV_ITEMS.filter(n => n.implemented);
const COMING_SOON = NAV_ITEMS.filter(n => !n.implemented);

export function Sidebar() {
  const { isOnline, status } = useCoreStatus();
  const { status: aiStatus, label: aiLabel } = useAIStatus();

  return (
    <nav className="sidebar">
      {/* Logo */}
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <div className="sidebar-logo-icon">AI</div>
          <div>
            <div className="sidebar-logo-text">AI Operator</div>
            <div className="sidebar-logo-sub">Personal Agent System</div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <div className="sidebar-nav">
        <div className="nav-section">
          {IMPLEMENTED.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) =>
                `nav-item${isActive ? ' active' : ''}`
              }
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </div>

        <div className="nav-section">
          <div className="nav-section-label">Coming Soon</div>
          {COMING_SOON.map(item => (
            <div key={item.path} className="nav-item nav-item-soon">
              <span className="nav-icon">{item.icon}</span>
              {item.label}
              <span className="nav-soon-label">Soon</span>
            </div>
          ))}
        </div>
      </div>

      {/* Footer status */}
      <div className="sidebar-footer">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
          <div className="status-item">
            <div className={`status-dot ${isOnline ? 'online' : 'offline'}`} />
            <span style={{ fontSize: 'var(--text-xs)', color: isOnline ? 'var(--color-success)' : 'var(--color-error)', fontWeight: 600 }}>
              CORE {isOnline ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>
          {isOnline && status && (
            <>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
                Database: <span style={{ color: status.database.status === 'connected' ? 'var(--color-success)' : 'var(--color-error)' }}>
                  {status.database.status === 'connected' ? 'Connected' : 'Error'}
                </span>
              </div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
                AI: <span style={{ color: aiStatus?.ai_ready ? 'var(--color-success)' : 'var(--color-warning)' }}>{aiLabel}</span>
              </div>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
