/* @vitest-environment jsdom */
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import { ApprovalsView } from './ApprovalsView';

const approvalsMock = vi.hoisted(() => vi.fn());

vi.mock('../../hooks/useApprovals', () => ({ useApprovals: approvalsMock }));

function renderWithRouter(ui: React.ReactElement) {
  return render(<BrowserRouter>{ui}</BrowserRouter>);
}

const baseApprovals = (overrides = {}) => ({
  approvals: [],
  total: 0,
  isLoading: false,
  error: null,
  limit: 50,
  offset: 0,
  refresh: vi.fn(),
  fetchPage: vi.fn(),
  ...overrides,
});

describe('ApprovalsView', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    approvalsMock.mockReset();
  });

  it('renders empty state when no approvals', () => {
    approvalsMock.mockReturnValue(baseApprovals());
    renderWithRouter(<ApprovalsView />);

    expect(screen.getByText('No pending approvals')).toBeInTheDocument();
  });

  it('renders pending approvals list', () => {
    const sample = [{
      approval_id: 'a1',
      tool_id: 'shell',
      action: 'run',
      arguments: { command: 'ls' },
      created_at: '2026-09-27T20:00:00Z',
      expires_at: '2026-09-27T21:00:00Z',
      conversation_id: 'conv-1',
    }];
    approvalsMock.mockReturnValue(baseApprovals({ approvals: sample, total: 1 }));
    renderWithRouter(<ApprovalsView />);

    expect(screen.getByText('shell')).toBeInTheDocument();
    expect(screen.getByText('run')).toBeInTheDocument();
    expect(screen.getByText(/conv-1/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  it('calls approve API when approve button clicked', async () => {
    const approve = vi.fn().mockResolvedValue({});
    const { coreApi } = await import('../../services/coreApi');
    vi.spyOn(coreApi, 'approveTool').mockImplementation(approve);

    const sample = [{
      approval_id: 'a1',
      tool_id: 'shell',
      action: 'run',
      arguments: { command: 'ls' },
      created_at: '2026-09-27T20:00:00Z',
      expires_at: '2026-09-27T21:00:00Z',
      conversation_id: 'conv-1',
    }];
    approvalsMock.mockReturnValue(baseApprovals({ approvals: sample, total: 1 }));
    renderWithRouter(<ApprovalsView />);

    window.prompt = vi.fn(() => '');
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));

    await waitFor(() => expect(approve).toHaveBeenCalledWith('a1', undefined));
  });

  it('calls reject API when reject button clicked', async () => {
    const reject = vi.fn().mockResolvedValue({});
    const { coreApi } = await import('../../services/coreApi');
    vi.spyOn(coreApi, 'rejectTool').mockImplementation(reject);

    const sample = [{
      approval_id: 'a2',
      tool_id: 'shell',
      action: 'run',
      arguments: { command: 'rm -rf /' },
      created_at: '2026-09-27T20:00:00Z',
      expires_at: '2026-09-27T21:00:00Z',
      conversation_id: 'conv-2',
    }];
    approvalsMock.mockReturnValue(baseApprovals({ approvals: sample, total: 1 }));
    renderWithRouter(<ApprovalsView />);

    window.prompt = vi.fn(() => '');
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => expect(reject).toHaveBeenCalledWith('a2', undefined));
  });
});
