/**
 * Hook — Core status polling
 * Polls /api/system/status every 5 seconds.
 * Returns isOnline, statuses, and error state.
 */
import { useState, useEffect, useCallback } from 'react';
import { coreApi, SystemStatusResponse } from '../services/coreApi';
import { CoreDiagnostics, CoreOfflineError, getCoreDiagnostics, getCoreUrl, resetCoreUrlForRetry } from '../services/apiClient';

interface CoreStatusState {
  isOnline: boolean;
  isLoading: boolean;
  status: SystemStatusResponse | null;
  coreUrl: string | null;
  diagnostics: CoreDiagnostics | null;
  error: string | null;
  lastChecked: Date | null;
}

const POLL_INTERVAL_MS = 5000;

export function useCoreStatus() {
  const [state, setState] = useState<CoreStatusState>({
    isOnline: false,
    isLoading: true,
    status: null,
    coreUrl: null,
    diagnostics: null,
    error: null,
    lastChecked: null,
  });

  const check = useCallback(async () => {
    try {
      const coreUrl = await getCoreUrl();
      const status = await coreApi.getSystemStatus();
      setState({
        isOnline: true,
        isLoading: false,
        status,
        coreUrl,
        diagnostics: await getCoreDiagnostics(),
        error: null,
        lastChecked: new Date(),
      });
    } catch (err) {
      const isOffline = err instanceof CoreOfflineError;
      const diagnostics = isOffline && err.diagnostics ? err.diagnostics : await getCoreDiagnostics();
      setState(prev => ({
        ...prev,
        isOnline: false,
        isLoading: false,
        status: null,
        coreUrl: diagnostics.url,
        diagnostics,
        error: isOffline
          ? 'The local AI Operator Core could not be reached.'
          : String(err),
        lastChecked: new Date(),
      }));
    }
  }, []);

  useEffect(() => {
    check();
    const interval = setInterval(check, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [check]);

  const retry = useCallback(() => {
    resetCoreUrlForRetry();
    return check();
  }, [check]);

  return { ...state, retry };
}
