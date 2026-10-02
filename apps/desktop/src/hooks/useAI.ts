import { useCallback, useEffect, useState } from 'react';
import {
  AIConfigResponse,
  AIConversation,
  AIModelResponse,
  AIProviderItem,
  AIStatusResponse,
  OllamaProcessStatusResponse,
  OllamaStorageResponse,
  PullStatusResponse,
  UpdateAIConfigRequest,
  coreApi,
} from '../services/coreApi';

export function formatAIStatus(status: AIStatusResponse | null) {
  if (!status) return 'AI Not Configured';
  if (status.ai_ready) return 'AI Ready';
  const providerNames: Record<string, string> = {
    kimi: 'Kimi',
    openrouter: 'OpenRouter',
    ollama: 'Ollama',
  };
  const provider = providerNames[status.provider] ?? (status.provider ? status.provider.charAt(0).toUpperCase() + status.provider.slice(1) : 'AI');
  const online = status.provider_status === 'online' || status.provider_status === 'ready';
  if (status.provider_status === 'not_installed') return `${provider} Not Installed`;
  if (status.provider_status === 'stopped') return `${provider} Service Stopped`;
  if (status.provider_status === 'no_models') return `${provider} Connected - No Models Installed`;
  if (online && status.model_status === 'not_selected') return `${provider} Connected - No Model Selected`;
  if (online && status.model_status === 'no_models') return `${provider} Connected - No Models`;
  if (status.provider_status === 'online' && status.model_status === 'missing') return 'Selected Model Missing';
  if (status.provider_status === 'ready' && status.model_status === 'missing') return 'Selected Model Missing';
  if (status.provider_status === 'not_configured') return `${provider} Not Configured`;
  if (status.provider_status === 'invalid_api_key' || status.provider_status === 'unauthorized') return `${provider} Authentication Failed`;
  if (status.provider_status === 'rate_limited') return `${provider} Rate Limited`;
  if (status.provider_status === 'model_not_found') return `${provider} Model Not Found`;
  if (['unreachable', 'network_error', 'timeout', 'server_error'].includes(status.provider_status)) return `${provider} Offline`;
  return 'AI Not Configured';
}

export function useAIStatus() {
  const [status, setStatus] = useState<AIStatusResponse | null>(null);
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await coreApi.getAIStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load AI status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    const interval = setInterval(() => {
      refresh();
    }, 30000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { status, isLoading, error, refresh, label: formatAIStatus(status) };
}

export interface PullErrorDetails {
  message: string;
  required_bytes?: number;
  available_bytes?: number;
  drive?: string;
}

export function useAISettings() {
  const [providers, setProviders] = useState<AIProviderItem[]>([]);
  const [config, setConfig] = useState<AIConfigResponse | null>(null);
  const [models, setModels] = useState<AIModelResponse[]>([]);
  const [status, setStatus] = useState<AIStatusResponse | null>(null);
  const [pullStatus, setPullStatus] = useState<PullStatusResponse | null>(null);
  const [storageInfo, setStorageInfo] = useState<OllamaStorageResponse | null>(null);
  const [pullError, setPullError] = useState<PullErrorDetails | null>(null);
  const [processStatus, setProcessStatus] = useState<OllamaProcessStatusResponse | null>(null);
  const [processActionState, setProcessActionState] = useState<'starting' | 'stopping' | 'restarting' | null>(null);
  const [processError, setProcessError] = useState<string | null>(null);
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshStorage = useCallback(async () => {
    try {
      setStorageInfo(await coreApi.getOllamaStorage());
    } catch {
      // ignore
    }
  }, []);

  const refreshProcessStatus = useCallback(async () => {
    try {
      setProcessStatus(await coreApi.getOllamaProcessStatus());
    } catch {
      // ignore
    }
  }, []);

  const refreshModels = useCallback(async () => {
    setModels(await coreApi.refreshAIModels());
    setStatus(await coreApi.getAIStatus());
    await Promise.all([refreshStorage(), refreshProcessStatus()]);
  }, [refreshStorage, refreshProcessStatus]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [providerList, cfg, aiStatus, pStatus, stInfo, procStatus] = await Promise.all([
        coreApi.getAIProviders(),
        coreApi.getAIConfig(),
        coreApi.getAIStatus(),
        coreApi.getPullStatus().catch(() => ({ active: false })),
        coreApi.getOllamaStorage().catch(() => ({})),
        coreApi.getOllamaProcessStatus().catch(() => null),
      ]);
      setProviders(providerList);
      setConfig(cfg);
      setStatus(aiStatus);
      setPullStatus(pStatus);
      setStorageInfo(stInfo);
      setProcessStatus(procStatus);
      if (aiStatus.provider_status === 'online' || aiStatus.provider_status === 'ready') {
        setModels(await coreApi.getAIModels());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load AI settings');
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll pull status if active
  useEffect(() => {
    if (!pullStatus?.active) return;
    const interval = setInterval(async () => {
      try {
        const nextStatus = await coreApi.getPullStatus();
        setPullStatus(nextStatus);
        if (!nextStatus.active && nextStatus.status === 'success') {
          await refreshModels();
        }
      } catch {
        // ignore
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [pullStatus?.active, refreshModels]);

  // Periodic refresh for process status (5 seconds)
  useEffect(() => {
    if (config?.provider !== 'ollama') return;
    refreshProcessStatus();
    const interval = setInterval(() => {
      refreshProcessStatus();
    }, 5000);
    return () => clearInterval(interval);
  }, [config?.provider, refreshProcessStatus]);

  const startOllamaProcess = useCallback(async () => {
    setProcessActionState('starting');
    setProcessError(null);
    try {
      await coreApi.startOllamaProcess();
      await load();
      await refreshModels();
    } catch (err: unknown) {
      if (typeof err === 'object' && err !== null && 'code' in err) {
        const apiErr = err as { code?: string; message?: string };
        setProcessError(apiErr.message || 'Failed to start Ollama service.');
      } else {
        setProcessError(err instanceof Error ? err.message : 'Failed to start Ollama service.');
      }
      await refreshProcessStatus();
    } finally {
      setProcessActionState(null);
    }
  }, [load, refreshModels, refreshProcessStatus]);

  const stopOllamaProcess = useCallback(async () => {
    setProcessActionState('stopping');
    setProcessError(null);
    try {
      await coreApi.stopOllamaProcess();
      await load();
      await refreshModels();
    } catch (err: unknown) {
      if (typeof err === 'object' && err !== null && 'code' in err) {
        const apiErr = err as { code?: string; message?: string };
        if (apiErr.code === 'not_managed') {
          setProcessError('Ollama is managed externally and cannot be stopped by AI Operator.');
        } else {
          setProcessError(apiErr.message || 'Failed to stop Ollama service.');
        }
      } else {
        setProcessError(err instanceof Error ? err.message : 'Failed to stop Ollama service.');
      }
      await refreshProcessStatus();
    } finally {
      setProcessActionState(null);
    }
  }, [load, refreshModels, refreshProcessStatus]);

  const restartOllamaProcess = useCallback(async () => {
    setProcessActionState('restarting');
    setProcessError(null);
    try {
      await coreApi.restartOllamaProcess();
      await load();
      await refreshModels();
    } catch (err: unknown) {
      if (typeof err === 'object' && err !== null && 'code' in err) {
        const apiErr = err as { code?: string; message?: string };
        if (apiErr.code === 'not_managed') {
          setProcessError('Ollama is managed externally and cannot be restarted by AI Operator.');
        } else {
          setProcessError(apiErr.message || 'Failed to restart Ollama service.');
        }
      } else {
        setProcessError(err instanceof Error ? err.message : 'Failed to restart Ollama service.');
      }
      await refreshProcessStatus();
    } finally {
      setProcessActionState(null);
    }
  }, [load, refreshModels, refreshProcessStatus]);

  const startPull = useCallback(async (modelName: string) => {
    setPullError(null);
    try {
      const initial = await coreApi.pullAIModel(modelName);
      setPullStatus(initial);
    } catch (err: unknown) {
      if (typeof err === 'object' && err !== null && 'code' in err) {
        const apiErr = err as { code?: string; message?: string; required_bytes?: number; available_bytes?: number; drive?: string };
        if (apiErr.code === 'insufficient_disk_space') {
          setPullError({
            message: apiErr.message || `Not enough free disk space to download ${modelName}.`,
            required_bytes: apiErr.required_bytes,
            available_bytes: apiErr.available_bytes,
            drive: apiErr.drive || 'F:',
          });
          return;
        }
        if (apiErr.code === 'model_download_in_progress') {
          setPullError({ message: 'Another model is currently downloading.' });
          return;
        }
      }
      setPullError({ message: err instanceof Error ? err.message : 'Failed to start download' });
    }
  }, []);

  const cancelPull = useCallback(async () => {
    try {
      const res = await coreApi.cancelPull();
      setPullStatus(res);
      setPullError(null);
    } catch (err) {
      setPullError({ message: err instanceof Error ? err.message : 'Failed to cancel download' });
    }
  }, []);

  const deleteModel = useCallback(async (modelName: string) => {
    try {
      await coreApi.deleteAIModel(modelName);
      await load();
      await refreshModels();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to delete model ${modelName}`);
    }
  }, [load, refreshModels]);

  const save = useCallback(async (next: UpdateAIConfigRequest) => {
    setConfig(await coreApi.updateAIConfig(next));
    await load();
  }, [load]);

  const testConnection = useCallback(async () => {
    setStatus(await coreApi.testAIConnection());
  }, []);

  useEffect(() => { load(); }, [load]);
  return {
    providers,
    config,
    models,
    status,
    pullStatus,
    storageInfo,
    pullError,
    isLoading,
    error,
    load,
    save,
    testConnection,
    refreshModels,
    startPull,
    cancelPull,
    deleteModel,
    refreshStorage,
    processStatus,
    processActionState,
    isProcessOperating: processActionState !== null,
    processError,
    startOllamaProcess,
    stopOllamaProcess,
    restartOllamaProcess,
    refreshProcessStatus,
    clearPullError: () => setPullError(null),
    clearProcessError: () => setProcessError(null),
  };
}

export interface AssistantErrorInfo {
  message: string;
  code?: string;
  provider?: string;
  retryable: boolean;
}

export function formatAssistantError(err: unknown, currentProvider = 'AI'): AssistantErrorInfo {
  if (!err) return { message: '', retryable: false };

  const providerName =
    currentProvider === 'openrouter'
      ? 'OpenRouter'
      : currentProvider === 'kimi'
      ? 'Kimi'
      : currentProvider === 'ollama'
      ? 'Ollama'
      : currentProvider || 'AI';

  if (typeof err === 'object' && err !== null && 'name' in err) {
    const errorObj = err as { name: string; message: string; code?: string; provider?: string; retryable?: boolean };

    if (errorObj.name === 'ApiTimeoutError') {
      return {
        message: 'The AI provider took too long to respond. Please try again.',
        code: 'core_timeout',
        provider: providerName,
        retryable: true,
      };
    }

    if (errorObj.name === 'CoreOfflineError') {
      return {
        message: 'AI Core is not running. Try restarting the application.',
        code: 'core_unreachable',
        provider: providerName,
        retryable: true,
      };
    }

    if (errorObj.name === 'ApiError') {
      const code = errorObj.code;
      const rawProv = errorObj.provider || currentProvider;
      const targetProvider =
        rawProv === 'openrouter'
          ? 'OpenRouter'
          : rawProv === 'kimi'
          ? 'Kimi'
          : rawProv === 'ollama'
          ? 'Ollama'
          : rawProv ? rawProv.charAt(0).toUpperCase() + rawProv.slice(1) : 'AI';

      if (code === 'ollama_not_installed') {
        return {
          message: 'Ollama is not installed on this computer. Please install Ollama or switch to OpenRouter Cloud in Settings.',
          code,
          provider: 'Ollama',
          retryable: false,
        };
      }
      if (code === 'ollama_service_stopped') {
        return {
          message: 'Ollama service is stopped on 127.0.0.1:11434. Please start Ollama.',
          code,
          provider: 'Ollama',
          retryable: true,
        };
      }
      if (code === 'ollama_no_models') {
        return {
          message: 'No AI models are installed in Ollama. Run "ollama pull <model>" in terminal to download one.',
          code,
          provider: 'Ollama',
          retryable: false,
        };
      }
      if (code === 'ollama_model_missing') {
        return {
          message: errorObj.message || 'Selected model is not installed in Ollama.',
          code,
          provider: 'Ollama',
          retryable: false,
        };
      }
      if (code === 'invalid_api_key' || code === 'unauthorized' || code === 'authentication_failed') {
        return {
          message: `Your ${targetProvider} API key is invalid. Please check Settings → AI.`,
          code,
          provider: targetProvider,
          retryable: false,
        };
      }
      if (code === 'provider_quota_exceeded' || code === 'quota_exceeded' || code === 'insufficient_quota') {
        return {
          message: `Your ${targetProvider} account has no available credits or quota.`,
          code,
          provider: targetProvider,
          retryable: false,
        };
      }
      if (code === 'rate_limited' || code === 'provider_rate_limited') {
        return {
          message: `The ${targetProvider} service is currently rate limited. Please try again in a moment.`,
          code,
          provider: targetProvider,
          retryable: true,
        };
      }
      if (code === 'model_unavailable' || code === 'provider_unavailable') {
        return {
          message: `The selected ${targetProvider} model is temporarily unavailable. Please try again or switch models.`,
          code,
          provider: targetProvider,
          retryable: true,
        };
      }
      if (code === 'model_not_found') {
        return {
          message: `The selected ${targetProvider} model was not found. Please refresh and select a valid model in Settings.`,
          code,
          provider: targetProvider,
          retryable: false,
        };
      }
      if (code === 'provider_unreachable' || code === 'network_error') {
        return {
          message: `Cannot connect to ${targetProvider}. Please check your internet connection.`,
          code,
          provider: targetProvider,
          retryable: true,
        };
      }
      if (code === 'provider_timeout' || code === 'timeout') {
        return {
          message: `The ${targetProvider} service took too long to respond. Please try again.`,
          code,
          provider: targetProvider,
          retryable: true,
        };
      }
      if (code === 'provider_not_configured' || code === 'not_configured') {
        return {
          message: `${targetProvider} is not configured. Please enter your API key in Settings → AI.`,
          code,
          provider: targetProvider,
          retryable: false,
        };
      }
      if (code === 'ai_not_ready') {
        return {
          message: errorObj.message || `AI is not ready. Please select a model in Settings → AI.`,
          code,
          provider: targetProvider,
          retryable: false,
        };
      }

      return {
        message: errorObj.message || `AI error (${code || 'unknown'})`,
        code: code || 'api_error',
        provider: targetProvider,
        retryable: Boolean(errorObj.retryable),
      };
    }
  }

  const rawMessage = err instanceof Error ? err.message : String(err);
  return {
    message: rawMessage || 'Failed to send message',
    retryable: true,
  };
}

function generateClientMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `msg-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

export function useAssistant() {
  const { status, label, refresh } = useAIStatus();
  const [conversations, setConversations] = useState<AIConversation[]>([]);
  const [active, setActive] = useState<AIConversation | null>(null);
  const [isLoading, setLoading] = useState(true);
  const [isSending, setSending] = useState(false);
  const [errorInfo, setErrorInfo] = useState<AssistantErrorInfo | null>(null);
  const [lastFailedPrompt, setLastFailedPrompt] = useState<string | null>(null);
  const [lastFailedClientMessageId, setLastFailedClientMessageId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await coreApi.getConversations();
      setConversations(list);
      if (active?.id) {
        const current = await coreApi.getConversation(active.id);
        setActive(current);
      } else if (list[0]) {
        setActive(await coreApi.getConversation(list[0].id));
      }
    } catch (err) {
      setErrorInfo(formatAssistantError(err, status?.provider));
    } finally {
      setLoading(false);
    }
  }, [active?.id, status?.provider]);

  const newConversation = useCallback(async () => {
    if (isSending) return;
    try {
      const created = await coreApi.createConversation();
      setActive(created);
      setErrorInfo(null);
      setLastFailedPrompt(null);
      setLastFailedClientMessageId(null);
      const list = await coreApi.getConversations();
      setConversations(list);
    } catch (err) {
      setErrorInfo(formatAssistantError(err, status?.provider));
    }
  }, [isSending, status?.provider]);

  const selectConversation = useCallback(async (id: string) => {
    if (isSending) return;
    try {
      setErrorInfo(null);
      setLastFailedPrompt(null);
      setLastFailedClientMessageId(null);
      setActive(await coreApi.getConversation(id));
    } catch (err) {
      setErrorInfo(formatAssistantError(err, status?.provider));
    }
  }, [isSending, status?.provider]);

  const send = useCallback(async (message: string, retryClientMessageId?: string) => {
    if (isSending) return;
    setSending(true);
    setErrorInfo(null);

    const client_message_id = retryClientMessageId || generateClientMessageId();

    try {
      const result = await coreApi.sendChat({
        conversation_id: active?.id,
        message,
        client_message_id,
      });
      setActive(result.conversation);
      setLastFailedPrompt(null);
      setLastFailedClientMessageId(null);
      await load();
      await refresh();
    } catch (err) {
      const formatted = formatAssistantError(err, status?.provider);
      setErrorInfo(formatted);
      if (formatted.retryable) {
        setLastFailedPrompt(message);
        setLastFailedClientMessageId(client_message_id);
      }
      // Reload conversation so user message is visible if it was saved
      if (active?.id) {
        try {
          const reloaded = await coreApi.getConversation(active.id);
          setActive(reloaded);
        } catch {
          // ignore
        }
      }
    } finally {
      setSending(false);
    }
  }, [active?.id, isSending, load, refresh, status?.provider]);

  const retry = useCallback(async () => {
    if (!lastFailedPrompt || isSending) return;
    const prompt = lastFailedPrompt;
    const clientMsgId = lastFailedClientMessageId || undefined;
    await send(prompt, clientMsgId);
  }, [lastFailedPrompt, lastFailedClientMessageId, isSending, send]);

  useEffect(() => { load(); }, []);

  return {
    status,
    label,
    conversations,
    active,
    isLoading,
    isSending,
    error: errorInfo?.message ?? null,
    errorInfo,
    lastFailedPrompt,
    load,
    newConversation,
    selectConversation,
    send,
    retry,
  };
}
