/* @vitest-environment jsdom */
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import { DashboardView } from './DashboardView';

const coreStatusMock = vi.hoisted(() => vi.fn());
const aiStatusMock = vi.hoisted(() => vi.fn());
const activityMock = vi.hoisted(() => vi.fn());

vi.mock('../../hooks/useCoreStatus', () => ({ useCoreStatus: coreStatusMock }));
vi.mock('../../hooks/useAI', async () => {
  const actual = await vi.importActual<typeof import('../../hooks/useAI')>('../../hooks/useAI');
  return { ...actual, useAIStatus: aiStatusMock };
});
vi.mock('../../hooks/useActivity', () => ({ useActivity: activityMock }));

function renderWithRouter(ui: React.ReactElement) {
  return render(<BrowserRouter>{ui}</BrowserRouter>);
}

describe('DashboardView', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    coreStatusMock.mockReturnValue({
      isOnline: true,
      isLoading: false,
      status: { core: { status: 'running' }, database: { status: 'connected' }, ai: { status: 'ready' } },
      retry: vi.fn(),
      coreUrl: 'http://127.0.0.1:49153',
      diagnostics: null,
      lastChecked: new Date('2026-09-27T20:00:00Z'),
    });
    aiStatusMock.mockReturnValue({
      status: {
        provider: 'openrouter',
        provider_status: 'ready',
        configured: true,
        model: 'openrouter/free',
        model_status: 'ready',
        models_count: 2,
        ai_ready: true,
      },
      label: 'AI Ready',
    });
    activityMock.mockReturnValue({
      events: [],
      isLoading: false,
      refresh: vi.fn(),
    });
  });

  it('renders the actual AI provider instead of hardcoded Ollama when status is available', () => {
    renderWithRouter(<DashboardView />);
    expect(screen.getByText('openrouter')).toBeInTheDocument();
    expect(screen.queryByText('Ollama')).not.toBeInTheDocument();
  });

  it('renders a dash instead of hardcoded Ollama when AI status is unavailable', () => {
    aiStatusMock.mockReturnValue({ status: null, label: 'AI Not Configured' });
    renderWithRouter(<DashboardView />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('Ollama')).not.toBeInTheDocument();
  });

  it('renders Pending Approvals from backend count instead of hardcoded 0', async () => {
    const { coreApi } = await import('../../services/coreApi');
    vi.spyOn(coreApi, 'getPendingApprovalCount').mockResolvedValueOnce({ count: 3 });

    renderWithRouter(<DashboardView />);

    const pendingLabel = await screen.findByText('Pending Approvals');
    const value = pendingLabel.closest('.info-row')?.querySelector('.info-value');
    expect(value).toHaveTextContent('3');
  });

  it('renders ellipsis while pending approval count is loading', async () => {
    const { coreApi } = await import('../../services/coreApi');
    vi.spyOn(coreApi, 'getPendingApprovalCount').mockImplementation(
      () => new Promise(() => {})
    );

    renderWithRouter(<DashboardView />);
    expect(screen.getByText('…')).toBeInTheDocument();
  });
});
