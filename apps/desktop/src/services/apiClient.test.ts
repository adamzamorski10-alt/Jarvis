import { beforeEach, describe, expect, it, vi } from 'vitest';

const invokeMock = vi.hoisted(() => vi.fn());

vi.mock('@tauri-apps/api/core', () => ({
  invoke: invokeMock,
}));

describe('apiClient', () => {
  beforeEach(() => {
    vi.resetModules();
    invokeMock.mockReset();
    vi.stubGlobal('fetch', vi.fn());
  });

  it('gets the Core URL from the Tauri command', async () => {
    invokeMock.mockResolvedValueOnce('http://127.0.0.1:49152');
    const { getCoreUrl } = await import('./apiClient');

    await expect(getCoreUrl()).resolves.toBe('http://127.0.0.1:49152');
    expect(invokeMock).toHaveBeenCalledWith('get_core_url');
  });

  it('uses the Tauri-provided URL for API requests', async () => {
    invokeMock.mockResolvedValueOnce('http://127.0.0.1:49153');
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: 'healthy' }),
    } as Response);
    const { apiClient } = await import('./apiClient');

    await apiClient.get('/api/health');

    expect(fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:49153/api/health',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('does not silently fall back to port 8000 when Core URL is missing', async () => {
    invokeMock
      .mockRejectedValueOnce('Core URL is not available')
      .mockResolvedValueOnce({
        url: null,
        pid: null,
        running: false,
        last_error: 'not started',
        retry_attempts: 0,
      });
    const { apiClient, CoreOfflineError } = await import('./apiClient');

    await expect(apiClient.get('/api/health')).rejects.toBeInstanceOf(CoreOfflineError);
    expect(fetch).not.toHaveBeenCalled();
  });
});
