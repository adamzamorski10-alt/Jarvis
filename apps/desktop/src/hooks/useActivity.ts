/**
 * Hook — Activity events with pagination
 */
import { useState, useEffect, useCallback } from 'react';
import { coreApi, ActivityEvent } from '../services/coreApi';

interface ActivityState {
  events: ActivityEvent[];
  total: number;
  isLoading: boolean;
  error: string | null;
  limit: number;
  offset: number;
}

export function useActivity(defaultLimit = 50) {
  const [state, setState] = useState<ActivityState>({
    events: [],
    total: 0,
    isLoading: true,
    error: null,
    limit: defaultLimit,
    offset: 0,
  });

  const fetch = useCallback(
    async (limit: number, offset: number, category?: string, level?: string) => {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      try {
        const data = await coreApi.getActivity({ limit, offset, category, level });
        setState(prev => ({
          ...prev,
          events: data.events,
          total: data.total,
          limit,
          offset,
          isLoading: false,
        }));
      } catch (error) {
        setState(prev => ({
          ...prev,
          isLoading: false,
          error: `Failed to load activity log. ${error instanceof Error ? error.message : ''}`.trim(),
        }));
      }
    },
    [],
  );

  const refresh = useCallback(() => {
    fetch(state.limit, state.offset);
  }, [fetch, state.limit, state.offset]);

  useEffect(() => {
    fetch(defaultLimit, 0);
  }, [fetch, defaultLimit]);

  return { ...state, refresh, fetchPage: fetch };
}
