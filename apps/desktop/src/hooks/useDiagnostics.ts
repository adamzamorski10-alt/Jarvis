/**
 * Hook — useDiagnostics
 * Collects system health data from:
 *  1. useCoreStatus (Core, DB, AI polling via /api/system/status)
 *  2. apiClient.getDiagnosticSnapshot() (live diagnostic log ring buffer)
 *  3. invoke('get_core_diagnostics') via apiClient (PID, port, running state from Tauri)
 *
 * Refreshes every 5 seconds. Does NOT create a second health check loop —
 * re-uses the existing useCoreStatus polling and apiClient event bus.
 */
import { useState, useEffect, useCallback } from 'react';
import { useCoreStatus } from './useCoreStatus';
import {
  getDiagnosticSnapshot,
  getCoreDiagnostics,
  DiagnosticSnapshot,
  DiagnosticLogEvent,
  CoreDiagnostics,
} from '../services/apiClient';

export interface DiagnosticsState {
  // Core
  coreStatus: string;
  coreVersion: string | null;
  coreUptimeSeconds: number | null;
  coreStartTime: string | null;
  coreUrl: string | null;
  corePid: number | null;
  coreRunning: boolean;
  coreLastError: string | null;
  coreLastCheck: Date | null;

  // Database
  dbStatus: string;
  dbEngine: string | null;
  dbMigrationStatus: string | null;
  dbSchemaVersion: string | null;
  dbLastError: string | null;

  // AI Provider
  aiStatus: string;
  aiProvider: string | null;
  aiConfigured: boolean;
  aiSelectedModel: string | null;
  aiMessage: string | null;

  // Diagnostic timeline
  lastSuccessfulHealthCheck: Date | null;
  lastNetworkError: { timestamp: Date; message: string } | null;
  lastRequest: { endpoint: string; method: string; status: number; timestamp: Date } | null;
  lastProviderError: { code: string; provider?: string; retryable: boolean; timestamp: Date } | null;
  recentLogs: DiagnosticLogEvent[];

  // Meta
  appVersion: string;
  isLoading: boolean;
}


export function useDiagnostics() {
  const { isOnline, isLoading, status, coreUrl, lastChecked } = useCoreStatus();
  const [tauriDiag, setTauriDiag] = useState<CoreDiagnostics | null>(null);
  const [snapshot, setSnapshot] = useState<DiagnosticSnapshot | null>(null);

  const refresh = useCallback(async () => {
    const diag = await getCoreDiagnostics();
    setTauriDiag(diag);
    setSnapshot(getDiagnosticSnapshot());
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const diagnostics: DiagnosticsState = {
    coreStatus: isLoading ? 'loading' : isOnline ? (status?.core?.status ?? 'running') : 'offline',
    coreVersion: status?.core?.version ?? '0.1.0',
    coreUptimeSeconds: status?.core?.uptime_seconds ?? null,
    coreStartTime: status?.core?.start_time ?? null,
    coreUrl: coreUrl ?? tauriDiag?.url ?? null,
    corePid: tauriDiag?.pid ?? null,
    coreRunning: tauriDiag?.running ?? isOnline,
    coreLastError: tauriDiag?.last_error ?? null,
    coreLastCheck: lastChecked,
    dbStatus: status?.database?.status ?? 'unknown',
    dbEngine: status?.database?.engine ?? null,
    dbMigrationStatus: status?.database?.migration_status ?? null,
    dbSchemaVersion: status?.database?.schema_version ?? null,
    dbLastError: status?.database?.last_error ?? null,
    aiStatus: status?.ai?.status ?? 'unknown',
    aiProvider: status?.ai?.provider ?? null,
    aiConfigured: status?.ai?.configured ?? false,
    aiSelectedModel: status?.ai?.selected_model ?? null,
    aiMessage: status?.ai?.message ?? null,
    lastSuccessfulHealthCheck: snapshot?.lastSuccessfulHealthCheck ?? null,
    lastNetworkError: snapshot?.lastNetworkError ?? null,
    lastRequest: snapshot?.lastRequest ?? null,
    lastProviderError: snapshot?.lastProviderError ?? null,
    recentLogs: snapshot?.recentLogs ?? [],
    appVersion: '0.1.0',
    isLoading: isLoading && tauriDiag === null,
  };

  return { diagnostics, refresh };
}
