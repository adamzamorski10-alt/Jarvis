/**
 * Core API client — typed HTTP wrapper for Python Core service.
 * Handles timeouts, error formatting, and offline detection.
 */

import { invoke } from '@tauri-apps/api/core';

const DEFAULT_TIMEOUT_MS = 8000;

export interface CoreDiagnostics {
  url: string | null;
  pid: number | null;
  running: boolean;
  last_error: string | null;
  retry_attempts: number;
}

export class CoreOfflineError extends Error {
  constructor(
    message = 'Core service is offline or unreachable',
    public readonly diagnostics?: CoreDiagnostics,
  ) {
    super(message);
    this.name = 'CoreOfflineError';
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API Error ${status}: ${detail}`);
    this.name = 'ApiError';
  }
}

function objectValue(value: object, key: string): unknown {
  return Object.prototype.hasOwnProperty.call(value, key)
    ? (value as Record<string, unknown>)[key]
    : undefined;
}

let coreUrlPromise: Promise<string> | null = null;

export function resetCoreUrlForRetry() {
  coreUrlPromise = null;
}

export async function getCoreDiagnostics(): Promise<CoreDiagnostics> {
  try {
    return await invoke<CoreDiagnostics>('get_core_diagnostics');
  } catch (error) {
    return {
      url: null,
      pid: null,
      running: false,
      last_error: `Unable to read Core diagnostics: ${String(error)}`,
      retry_attempts: 0,
    };
  }
}

export async function getCoreUrl(): Promise<string> {
  if (!coreUrlPromise) {
    console.info('[API] Requesting Core URL from Tauri');
    coreUrlPromise = invoke<string>('get_core_url')
      .then(url => {
        console.info('[API] Received Core URL:', url);
        console.info('[API] API client initialized');
        return url;
      })
      .catch(async error => {
        coreUrlPromise = null;
        const diagnostics = await getCoreDiagnostics();
        throw new CoreOfflineError(`Core URL is unavailable: ${String(error)}`, diagnostics);
      });
  }

  return coreUrlPromise;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const coreUrl = await getCoreUrl();
    const requestUrl = `${coreUrl}${path}`;
    console.info('[API] Health check URL:', path === '/api/health' ? requestUrl : coreUrl);

    const response = await fetch(requestUrl, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });

    clearTimeout(timer);

    if (!response.ok) {
      let detail: unknown = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body.detail ?? body.error ?? detail;
        if (typeof detail === 'object' && detail !== null) {
          detail = String(objectValue(detail, 'message') ?? objectValue(detail, 'status') ?? `HTTP ${response.status}`);
        }
      } catch {
        // Use default detail if JSON parse fails
      }
      throw new ApiError(response.status, String(detail));
    }

    return response.json() as Promise<T>;
  } catch (error) {
    clearTimeout(timer);

    if (error instanceof ApiError) throw error;

    // AbortError = timeout, TypeError = network failure
    if (
      error instanceof DOMException && error.name === 'AbortError' ||
      error instanceof TypeError
    ) {
      throw new CoreOfflineError(undefined, await getCoreDiagnostics());
    }

    throw error;
  }
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  getCoreUrl,
  getCoreDiagnostics,
  resetCoreUrlForRetry,
};
