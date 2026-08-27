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

  it('automatically retries once with fresh Core URL when fetch encounters a network error', async () => {
    // Attempt 1: Core URL port 40001, fetch throws TypeError
    invokeMock.mockResolvedValueOnce('http://127.0.0.1:40001');
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'));

    // Attempt 2 (Auto retry): Tauri returns new dynamic port 50002, fetch succeeds
    invokeMock.mockResolvedValueOnce('http://127.0.0.1:50002');
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: 'healthy' }),
    } as Response);

    const { apiClient } = await import('./apiClient');

    const res = await apiClient.get<{ status: string }>('/api/health');
    expect(res.status).toBe('healthy');
    expect(fetch).toHaveBeenLastCalledWith(
      'http://127.0.0.1:50002/api/health',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('throws CoreOfflineError only when both initial request and auto-retry fail', async () => {
    invokeMock.mockResolvedValueOnce('http://127.0.0.1:40001');
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'));

    invokeMock.mockResolvedValueOnce('http://127.0.0.1:40001');
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch again'));
    invokeMock.mockResolvedValueOnce({
      url: 'http://127.0.0.1:40001',
      pid: 1234,
      running: false,
      last_error: 'Connection refused',
      retry_attempts: 1,
    });

    const { apiClient, CoreOfflineError } = await import('./apiClient');

    await expect(apiClient.get('/api/health')).rejects.toBeInstanceOf(CoreOfflineError);
  });

  it('correctly maps structured backend error payload to ApiError', async () => {
    invokeMock.mockResolvedValueOnce('http://127.0.0.1:49153');
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 409,
      text: () => Promise.resolve(JSON.stringify({
        detail: {
          message: 'OpenRouter rate limit reached',
          status: 'provider_rate_limited',
          error: {
            code: 'provider_rate_limited',
            message: 'OpenRouter rate limit reached',
            provider: 'openrouter',
            retryable: true,
          },
        },
      })),
    } as Response);

    const { apiClient, ApiError } = await import('./apiClient');

    try {
      await apiClient.post('/api/ai/chat', { message: 'hello' });
      expect.unreachable('Should have thrown ApiError');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as InstanceType<typeof ApiError>;
      expect(apiErr.code).toBe('provider_rate_limited');
      expect(apiErr.message).toBe('OpenRouter rate limit reached');
      expect(apiErr.provider).toBe('openrouter');
      expect(apiErr.retryable).toBe(true);
    }
  });

  it('identifies client-side timeouts as ApiTimeoutError without claiming Core is offline', async () => {
    invokeMock.mockResolvedValueOnce('http://127.0.0.1:49153');
    const abortErr = new DOMException('The user aborted a request.', 'AbortError');
    vi.mocked(fetch).mockRejectedValueOnce(abortErr);

    const { apiClient, ApiTimeoutError } = await import('./apiClient');

    await expect(apiClient.post('/api/ai/chat', { message: 'long request' })).rejects.toBeInstanceOf(ApiTimeoutError);
  });
});
