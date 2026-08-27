// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCoreStatus } from './useCoreStatus';
import { coreApi } from '../services/coreApi';
import {
  resetCoreUrlForRetry,
  notifyCoreOnline,
  CoreOfflineError,
  ApiError,
} from '../services/apiClient';
import { invoke } from '@tauri-apps/api/core';

vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(),
}));

describe('useCoreStatus', () => {
  beforeEach(() => {
    resetCoreUrlForRetry();
    vi.clearAllMocks();
    vi.mocked(invoke).mockImplementation(async (cmd: string) => {
      if (cmd === 'get_core_url') return 'http://127.0.0.1:49153';
      if (cmd === 'get_core_diagnostics') {
        return {
          url: 'http://127.0.0.1:49153',
          pid: 1234,
          running: true,
          last_error: null,
          retry_attempts: 1,
        };
      }
      throw new Error(`Unknown command: ${cmd}`);
    });
  });

  it('updates state to ONLINE when system status check succeeds', async () => {
    vi.spyOn(coreApi, 'getSystemStatus').mockResolvedValueOnce({
      core: { status: 'running' },
      database: { status: 'connected' },
      ai: { status: 'ready' },
    });

    const { result } = renderHook(() => useCoreStatus());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.isOnline).toBe(true);
    expect(result.current.coreUrl).toBe('http://127.0.0.1:49153');
    expect(result.current.status?.database.status).toBe('connected');
  });

  it('immediately updates to ONLINE when Assistant or apiClient fires notifyCoreOnline', async () => {
    vi.spyOn(coreApi, 'getSystemStatus').mockImplementation(() => new Promise(() => {})); // pending

    const { result } = renderHook(() => useCoreStatus());

    act(() => {
      notifyCoreOnline('http://127.0.0.1:55555');
    });

    expect(result.current.isOnline).toBe(true);
    expect(result.current.coreUrl).toBe('http://127.0.0.1:55555');
  });

  it('does NOT mark Core as OFFLINE when getSystemStatus returns an HTTP 500 ApiError', async () => {
    vi.spyOn(coreApi, 'getSystemStatus').mockRejectedValueOnce(
      new ApiError(500, 'Internal DB Exception', { code: 'db_error' })
    );

    const { result } = renderHook(() => useCoreStatus());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.isOnline).toBe(true);
    expect(result.current.error).toContain('Internal DB Exception');
  });

  it('marks Core as OFFLINE only when a genuine network CoreOfflineError occurs', async () => {
    vi.spyOn(coreApi, 'getSystemStatus').mockRejectedValueOnce(
      new CoreOfflineError('Network refused')
    );

    const { result } = renderHook(() => useCoreStatus());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.isOnline).toBe(false);
    expect(result.current.error).toContain('The local AI Operator Core could not be reached.');
  });

  it('handles recovery transition OFFLINE -> ONLINE seamlessly', async () => {
    vi.spyOn(coreApi, 'getSystemStatus').mockRejectedValueOnce(
      new CoreOfflineError('Network refused')
    );

    const { result } = renderHook(() => useCoreStatus());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.isOnline).toBe(false);

    // Simulate recovery event from apiClient
    act(() => {
      notifyCoreOnline('http://127.0.0.1:49153');
    });

    expect(result.current.isOnline).toBe(true);
    expect(result.current.error).toBeNull();
  });
});
