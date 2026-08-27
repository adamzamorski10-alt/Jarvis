/**
 * Hook — Settings management
 * Fetches, updates, and persists app settings.
 */
import { useState, useEffect, useCallback } from 'react';
import { coreApi } from '../services/coreApi';
import { CoreOfflineError } from '../services/apiClient';

interface SettingsState {
  settings: Record<string, string | null>;
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  savedAt: Date | null;
}

export function useSettings() {
  const [state, setState] = useState<SettingsState>({
    settings: {},
    isLoading: true,
    isSaving: false,
    error: null,
    savedAt: null,
  });

  const fetchSettings = useCallback(async () => {
    setState(prev => ({ ...prev, isLoading: true, error: null }));
    try {
      const data = await coreApi.getSettings();
      setState(prev => ({
        ...prev,
        settings: data.settings,
        isLoading: false,
      }));
    } catch (err) {
      setState(prev => ({
        ...prev,
        isLoading: false,
        error: err instanceof CoreOfflineError
          ? 'Cannot load settings because the local Core could not be reached.'
          : 'Failed to load settings.',
      }));
    }
  }, []);

  const updateSettings = useCallback(
    async (updates: Record<string, string | null>) => {
      setState(prev => ({ ...prev, isSaving: true, error: null }));
      try {
        const data = await coreApi.updateSettings(updates);
        setState(prev => ({
          ...prev,
          settings: data.settings,
          isSaving: false,
          savedAt: new Date(),
        }));
        return true;
      } catch (err) {
        setState(prev => ({
          ...prev,
          isSaving: false,
          error: 'Failed to save settings.',
        }));
        return false;
      }
    },
    [],
  );

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  return { ...state, updateSettings, refresh: fetchSettings };
}
