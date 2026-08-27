/**
 * App Router — defines all routes and view mappings.
 */
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppShell } from './components/Layout/AppShell';
import { DashboardView } from './views/Dashboard/DashboardView';
import { ActivityView } from './views/Activity/ActivityView';
import { SettingsView } from './views/Settings/SettingsView';
import { AssistantView } from './views/Assistant/AssistantView';
import { ComingSoon } from './components/Common/ComingSoon';

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardView />} />
          <Route path="/activity" element={<ActivityView />} />
          <Route path="/settings" element={<SettingsView />} />

          <Route path="/assistant" element={<AssistantView />} />
          <Route path="/tasks" element={
            <ComingSoon moduleName="Tasks" icon="☑"
              description="Task queue with statuses, retry logic, scheduling, and task plans."
              phase="Phase 5 — Task Engine" />
          } />
          <Route path="/agents" element={
            <ComingSoon moduleName="Agents" icon="◈"
              description="Agent monitoring panel showing active agents, their plans, and tool calls."
              phase="Phase 3 — Tool System" />
          } />
          <Route path="/files" element={
            <ComingSoon moduleName="Files" icon="◫"
              description="File indexing, semantic search, safe operations with recycle-bin protection."
              phase="Phase 4 — File Agent" />
          } />
          <Route path="/projects" element={
            <ComingSoon moduleName="Projects" icon="⬙"
              description="Project management with memory, tasks, decisions, Git integration."
              phase="Phase 6 — Project System" />
          } />
          <Route path="/browser" element={
            <ComingSoon moduleName="Browser" icon="◉"
              description="Browser automation via Playwright with session management and audit trail."
              phase="Phase 8 — Browser Agent" />
          } />
          <Route path="/messages" element={
            <ComingSoon moduleName="Messages" icon="◳"
              description="Email integration with classification, summaries, and controlled sending."
              phase="Phase 10 — Email Integration" />
          } />
          <Route path="/automations" element={
            <ComingSoon moduleName="Automations" icon="⟳"
              description="Trigger-based workflow automation with scheduler and failure recovery."
              phase="Phase 9 — Automations" />
          } />
          <Route path="/approvals" element={
            <ComingSoon moduleName="Approvals" icon="◈"
              description="Approval Center for reviewing AI actions that require your decision."
              phase="Phase 3 — Tool System" />
          } />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
