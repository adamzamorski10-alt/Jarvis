/**
 * Hook — Core status polling and live status management.
 * Subscribes to apiClient events and polls /api/system/status every 5 seconds.
 * Returns isOnline, statuses, and error state.
 */
import { useState, useEffect, useCallback } from 'react';
import { coreApi, SystemStatusResponse } from '../services/coreApi';
import {
  CoreDiagnostics,
  CoreOfflineError,
  getCoreDiagnostics,
  getCoreUrl,
  resetCoreUrlForRetry,
  subscribeCoreStatus,
  notifyCoreOnline,
  notifyCoreOffline,
} from '../services/apiClient';

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
    let coreUrl: string | null = null;
    let port: string | null = null;

    try {
      coreUrl = await getCoreUrl();
      try {
        const parsed = new URL(coreUrl);
        port = parsed.port;
      } catch {
        // ignore
      }

      const status = await coreApi.getSystemStatus();
      const diagnostics = await getCoreDiagnostics();

      console.info(
        `[Core Status] source=status-bar url=${coreUrl} port=${port} endpoint=/api/system/status cached=true result=ONLINE http_status=200 error_type=none`
      );

      notifyCoreOnline(coreUrl);

      setState({
        isOnline: true,
        isLoading: false,
        status,
        coreUrl,
        diagnostics,
        error: null,
        lastChecked: new Date(),
      });
    } catch (err) {
      const isOffline = err instanceof CoreOfflineError;
      const diagnostics = isOffline && err.diagnostics ? err.diagnostics : await getCoreDiagnostics();
      const errorType = isOffline ? 'CoreOfflineError' : err instanceof Error ? err.name : 'UnknownError';

      console.warn(
        `[Core Status] source=status-bar url=${coreUrl} port=${port} endpoint=/api/system/status cached=false result=${isOffline ? 'OFFLINE' : 'ONLINE'} http_status=${isOffline ? 'none' : 'error'} error_type=${errorType}`
      );

      if (isOffline) {
        notifyCoreOffline(coreUrl, 'The local AI Operator Core could not be reached.');
      } else {
        if (coreUrl) notifyCoreOnline(coreUrl);
      }

      setState(prev => ({
        ...prev,
        isOnline: !isOffline,
        isLoading: false,
        status: isOffline ? null : prev.status,
        coreUrl: coreUrl ?? diagnostics.url,
        diagnostics,
        error: isOffline ? 'The local AI Operator Core could not be reached.' : String(err),
        lastChecked: new Date(),
      }));
    }
  }, []);

  useEffect(() => {
    check();
    const unsubscribe = subscribeCoreStatus(evt => {
      setState(prev => ({
        ...prev,
        isOnline: evt.isOnline,
        coreUrl: evt.coreUrl ?? prev.coreUrl,
        error: evt.error,
        lastChecked: evt.lastChecked,
      }));
    });
    const interval = setInterval(check, POLL_INTERVAL_MS);
    return () => {
      unsubscribe();
      clearInterval(interval);
    };
  }, [check]);

  const retry = useCallback(() => {
    resetCoreUrlForRetry();
    return check();
  }, [check]);

  return { ...state, retry };
}
