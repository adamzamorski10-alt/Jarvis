/**
 * AppShell — Root layout wrapper with sidebar, main content, and status bar.
 */
import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { StatusBar } from './StatusBar';

export function AppShell() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-content">
        <Outlet />
      </main>
      <StatusBar />
    </div>
  );
}
