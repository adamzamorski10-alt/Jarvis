/**
 * Core API client — typed HTTP wrapper for Python Core service.
 * Handles timeouts, structured error formatting, dynamic URL resolution, and offline detection.
 */

import { invoke } from '@tauri-apps/api/core';

export const DEFAULT_TIMEOUT_MS = 15000;
export const CHAT_TIMEOUT_MS = 120000;

export interface CoreDiagnostics {
  url: string | null;
  pid: number | null;
  running: boolean;
  last_error: string | null;
  retry_attempts: number;
}

export interface StructuredErrorPayload {
  code: string;
  message: string;
  provider?: string;
  retryable?: boolean;
}

export class CoreOfflineError extends Error {
  public readonly code = 'core_unreachable';
  public readonly retryable = true;

  constructor(
    message = 'Core service is offline or unreachable',
    public readonly diagnostics?: CoreDiagnostics,
  ) {
    super(message);
    this.name = 'CoreOfflineError';
  }
}

export class ApiTimeoutError extends Error {
  public readonly code = 'core_timeout';
  public readonly retryable = true;

  constructor(message = 'Request timed out while waiting for response') {
    super(message);
    this.name = 'ApiTimeoutError';
  }
}

export class ApiError extends Error {
  public readonly code: string;
  public readonly provider?: string;
  public readonly retryable: boolean;

  constructor(
    public readonly status: number,
    public readonly detail: string,
    structured?: Partial<StructuredErrorPayload>,
  ) {
    super(structured?.message || detail);
    this.name = 'ApiError';
    this.code = structured?.code || (status === 429 ? 'provider_rate_limited' : status === 401 ? 'invalid_api_key' : `http_${status}`);
    this.provider = structured?.provider;
    this.retryable = structured?.retryable ?? (status === 429 || status === 503 || status >= 500);
  }
}

export class CoreAuthError extends Error {
  public readonly code = 'core_session_invalid';
  public readonly retryable = false;

  constructor(message = 'Session token is invalid or expired') {
    super(message);
    this.name = 'CoreAuthError';
  }
}

let cachedCoreUrl: string | null = null;
let coreUrlPromise: Promise<string> | null = null;
let sessionToken: string | null = null;

export function resetCoreUrlForRetry() {
  cachedCoreUrl = null;
  coreUrlPromise = null;
}

export function resetSessionToken() {
  sessionToken = null;
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

export async function getCoreUrl(forceRefresh = false): Promise<string> {
  if (forceRefresh) {
    resetCoreUrlForRetry();
  }

  if (cachedCoreUrl) {
    return cachedCoreUrl;
  }

  if (!coreUrlPromise) {
    console.info('[API Log] Requesting fresh Core URL from Tauri');
    coreUrlPromise = invoke<string>('get_core_url')
      .then(url => {
        console.info(`[API Log] Received Core URL from Tauri: ${url}`);
        cachedCoreUrl = url;
        return url;
      })
      .catch(async error => {
        resetCoreUrlForRetry();
        const diagnostics = await getCoreDiagnostics();
        console.error('[API Log] Failed to fetch Core URL from Tauri:', error, diagnostics);
        throw new CoreOfflineError(`Core URL is unavailable: ${String(error)}`, diagnostics);
      });
  }

  return coreUrlPromise;
}

export async function getCoreToken(): Promise<string> {
  if (sessionToken) {
    return sessionToken;
  }

  const token = await invoke<string>('get_core_token');
  sessionToken = token;
  return token;
}

function sanitizeForLog(text: string): string {
  return text.replace(/("(?:api_key|openrouter_api_key|kimi_api_key|authorization|bearer)"\s*:\s*)"[^"]+"/gi, '$1"***"');
}

function parseStructuredError(body: unknown, status: number): StructuredErrorPayload {
  if (typeof body !== 'object' || body === null) {
    return { code: status === 401 ? 'invalid_api_key' : status === 429 ? 'provider_rate_limited' : status === 503 ? 'provider_unavailable' : `http_${status}`, message: `HTTP ${status}` };
  }

  const b = body as Record<string, unknown>;
  let errObj: Record<string, unknown> | null = null;

  if (typeof b.error === 'object' && b.error !== null) {
    errObj = b.error as Record<string, unknown>;
  } else if (typeof b.detail === 'object' && b.detail !== null) {
    const detailObj = b.detail as Record<string, unknown>;
    if (typeof detailObj.error === 'object' && detailObj.error !== null) {
      errObj = detailObj.error as Record<string, unknown>;
    } else {
      errObj = detailObj;
    }
  } else if (b.code || b.message) {
    errObj = b;
  }

  let code: string | undefined = undefined;
  let message: string | undefined = undefined;
  let provider: string | undefined = undefined;
  let retryable: boolean | undefined = undefined;

  if (errObj) {
    if (errObj.code) code = String(errObj.code);
    else if (typeof b.detail === 'object' && b.detail !== null && (b.detail as Record<string, unknown>).status) {
      code = String((b.detail as Record<string, unknown>).status);
    }

    if (errObj.message) message = String(errObj.message);
    if (errObj.provider) provider = String(errObj.provider);
    if (typeof errObj.retryable === 'boolean') retryable = errObj.retryable;
  }

  if (!message) {
    if (typeof b.detail === 'string') {
      message = b.detail;
    } else if (Array.isArray(b.detail) && b.detail.length > 0) {
      const first = b.detail[0];
      if (typeof first === 'object' && first !== null && first.msg) {
        message = String(first.msg);
      }
    } else if (typeof b.error === 'string') {
      message = b.error;
    }
  }

  if (!code) {
    if (status === 401) code = 'invalid_api_key';
    else if (status === 402) code = 'provider_quota_exceeded';
    else if (status === 403) code = 'unauthorized';
    else if (status === 404) code = 'model_not_found';
    else if (status === 429) code = 'provider_rate_limited';
    else if (status === 503) code = 'provider_unavailable';
    else code = `http_${status}`;
  }

  if (!message) {
    message = `HTTP ${status}`;
  }

  return {
    code,
    message,
    provider,
    retryable: retryable ?? (status === 429 || status === 503 || status >= 500),
  };
}

export interface DiagnosticLogEvent {
  id: string;
  timestamp: string;
  source: 'api-client' | 'status-bar' | 'system';
  component: 'core' | 'database' | 'ai' | 'network';
  level: 'info' | 'warn' | 'error';
  endpoint?: string;
  method?: string;
  httpStatus?: number | string;
  errorCode?: string;
  message: string;
  retryable?: boolean;
}

export interface DiagnosticSnapshot {
  lastSuccessfulHealthCheck: Date | null;
  lastNetworkError: { timestamp: Date; message: string } | null;
  lastRequest: { endpoint: string; method: string; status: number; timestamp: Date } | null;
  lastProviderError: { code: string; provider?: string; retryable: boolean; timestamp: Date } | null;
  recentLogs: DiagnosticLogEvent[];
}

export type CoreStatusEvent = {
  isOnline: boolean;
  coreUrl: string | null;
  error: string | null;
  lastChecked: Date;
  snapshot?: DiagnosticSnapshot;
};

export type CoreStatusListener = (event: CoreStatusEvent) => void;

const statusListeners = new Set<CoreStatusListener>();
const recentLogs: DiagnosticLogEvent[] = [];
let lastSuccessfulHealthCheck: Date | null = null;
let lastNetworkError: { timestamp: Date; message: string } | null = null;
let lastRequest: { endpoint: string; method: string; status: number; timestamp: Date } | null = null;
let lastProviderError: { code: string; provider?: string; retryable: boolean; timestamp: Date } | null = null;

export function addDiagnosticLog(log: Omit<DiagnosticLogEvent, 'id' | 'timestamp'>) {
  const event: DiagnosticLogEvent = {
    ...log,
    id: `log_${Date.now}_${Math.random().toString(36).substring(2, 7)}`,
    timestamp: new Date().toISOString(),
    message: sanitizeForLog(log.message),
  };
  recentLogs.unshift(event);
  if (recentLogs.length > 50) {
    recentLogs.pop();
  }
}

export function getDiagnosticSnapshot(): DiagnosticSnapshot {
  return {
    lastSuccessfulHealthCheck,
    lastNetworkError,
    lastRequest,
    lastProviderError,
    recentLogs: [...recentLogs],
  };
}

export function subscribeCoreStatus(listener: CoreStatusListener) {
  statusListeners.add(listener);
  return () => {
    statusListeners.delete(listener);
  };
}

export function notifyCoreOnline(url: string) {
  const event: CoreStatusEvent = {
    isOnline: true,
    coreUrl: url,
    error: null,
    lastChecked: new Date(),
    snapshot: getDiagnosticSnapshot(),
  };
  for (const listener of statusListeners) {
    listener(event);
  }
}

export function notifyCoreOffline(url: string | null, errorMsg: string) {
  lastNetworkError = { timestamp: new Date(), message: errorMsg };
  addDiagnosticLog({
    source: 'api-client',
    component: 'network',
    level: 'error',
    errorCode: 'core_unreachable',
    message: errorMsg,
    retryable: true,
  });
  const event: CoreStatusEvent = {
    isOnline: false,
    coreUrl: url,
    error: errorMsg,
    lastChecked: new Date(),
    snapshot: getDiagnosticSnapshot(),
  };
  for (const listener of statusListeners) {
    listener(event);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
  isRetry = false,
  isAuthRetry = false,
): Promise<T> {
  const isFromCache = Boolean(cachedCoreUrl) && !isRetry;
  const coreUrl = await getCoreUrl(isRetry);
  const requestUrl = `${coreUrl}${path}`;
  const method = options.method || 'GET';

  let port: string | null = null;
  try {
    const parsed = new URL(coreUrl);
    port = parsed.port;
  } catch {
    // ignore
  }

  console.info(
    `[Assistant API] source=api-client url=${coreUrl} port=${port} endpoint=${path} cached=${isFromCache} retry=${isRetry} auth_retry=${isAuthRetry} method=${method}`
  );

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(requestUrl, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(await buildAuthHeaders()),
        ...options.headers,
      },
    });
    clearTimeout(timer);
  } catch (error) {
    clearTimeout(timer);

    if (error instanceof DOMException && error.name === 'AbortError') {
      console.warn(`[Assistant API] source=api-client url=${coreUrl} port=${port} endpoint=${path} cached=${isFromCache} retry=${isRetry} result=timeout`);
      throw new ApiTimeoutError(timeoutMs >= 30000 ? 'AI response timed out' : 'Request timed out');
    }

    console.warn(`[Assistant API] source=api-client url=${coreUrl} port=${port} endpoint=${path} cached=${isFromCache} retry=${isRetry} result=network_error error=${String(error)}`);

    if (!isRetry) {
      console.info(`[API Log] Auto-retrying request with fresh Core URL refetched from Tauri: endpoint=${path}`);
      return request<T>(path, options, timeoutMs, true);
    }

    resetCoreUrlForRetry();
    const diagnostics = await getCoreDiagnostics();
    console.error(`[Assistant API] source=api-client url=${coreUrl} port=${port} endpoint=${path} cached=${isFromCache} retry=${isRetry} result=error_offline`, diagnostics);
    notifyCoreOffline(coreUrl, 'Core service is offline or unreachable');
    throw new CoreOfflineError('Core service is offline or unreachable', diagnostics);
  }

  console.info(`[Assistant API] source=api-client url=${coreUrl} port=${port} endpoint=${path} cached=${isFromCache} retry=${isRetry} result=success status=${response.status}`);
  lastRequest = { endpoint: path, method, status: response.status, timestamp: new Date() };
  if (path === '/api/system/status' || path === '/api/health') {
    lastSuccessfulHealthCheck = new Date();
  }

  notifyCoreOnline(coreUrl);

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    let structured: Partial<StructuredErrorPayload> | undefined;

    try {
      const responseText = await response.text();
      console.info(`[API Log] Error response body (status ${response.status}):`, sanitizeForLog(responseText));
      if (responseText) {
        const body = JSON.parse(responseText);
        structured = parseStructuredError(body, response.status);
        detail = structured.message || detail;
      }
    } catch (parseError) {
      console.warn(`[API Log] Failed to parse response error body as JSON:`, parseError);
    }

    if (response.status === 401) {
      resetSessionToken();
      resetCoreUrlForRetry();
      try {
        await invoke('reset_core_token');
      } catch {
        // best-effort: if Tauri reset fails, continue with cleared JS cache
      }

      if (!isAuthRetry) {
        return request<T>(path, options, timeoutMs, false, true);
      }

      throw new CoreAuthError();
    }

    const apiError = new ApiError(response.status, detail, structured);
    lastProviderError = {
      code: apiError.code,
      provider: apiError.provider,
      retryable: apiError.retryable,
      timestamp: new Date(),
    };
    addDiagnosticLog({
      source: 'api-client',
      component: path.startsWith('/api/ai') ? 'ai' : path.startsWith('/api/db') ? 'database' : 'core',
      level: 'warn',
      endpoint: path,
      method,
      httpStatus: response.status,
      errorCode: apiError.code,
      message: apiError.message,
      retryable: apiError.retryable,
    });
    console.warn(
      `[API Log] ApiError thrown: status=${response.status} code=${apiError.code} provider=${apiError.provider} message="${apiError.message}"`
    );
    throw apiError;
  }

  addDiagnosticLog({
    source: 'api-client',
    component: path.startsWith('/api/ai') ? 'ai' : path.startsWith('/api/db') ? 'database' : 'core',
    level: 'info',
    endpoint: path,
    method,
    httpStatus: response.status,
    message: `Request ${method} ${path} succeeded`,
  });

  try {
    return (await response.json()) as T;
  } catch (jsonErr) {
    console.error(`[API Log] Failed to parse HTTP 200 response JSON:`, jsonErr);
    throw new ApiError(500, 'Invalid JSON response received from Core', {
      code: 'invalid_json',
      message: 'Invalid JSON response received from Core',
      retryable: false,
    });
  }
}

async function buildAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
  try {
    const token = await getCoreToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
  } catch {
    // proceed without session token if it cannot be retrieved
  }
  return headers;
}

export const apiClient = {
  get: <T>(path: string, timeoutMs?: number) => request<T>(path, { method: 'GET' }, timeoutMs),
  post: <T>(path: string, body: unknown, timeoutMs?: number) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }, timeoutMs),
  put: <T>(path: string, body: unknown, timeoutMs?: number) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }, timeoutMs),
  delete: <T>(path: string, timeoutMs?: number) => request<T>(path, { method: 'DELETE' }, timeoutMs),
  getCoreUrl,
  getCoreDiagnostics,
  resetCoreUrlForRetry,
  getCoreToken,
  getDiagnosticSnapshot,
  addDiagnosticLog,
};
