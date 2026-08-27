import { useCallback, useEffect, useState } from 'react';
import { AIConfigResponse, AIConversation, AIModelResponse, AIProviderItem, AIStatusResponse, UpdateAIConfigRequest, coreApi } from '../services/coreApi';

export function formatAIStatus(status: AIStatusResponse | null) {
  if (!status) return 'AI Not Configured';
  if (status.ai_ready) return 'AI Ready';
  const provider = status.provider === 'kimi' ? 'Kimi' : 'Ollama';
  const online = status.provider_status === 'online' || status.provider_status === 'ready';
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
  return { status, isLoading, error, refresh, label: formatAIStatus(status) };
}

export function useAISettings() {
  const [providers, setProviders] = useState<AIProviderItem[]>([]);
  const [config, setConfig] = useState<AIConfigResponse | null>(null);
  const [models, setModels] = useState<AIModelResponse[]>([]);
  const [status, setStatus] = useState<AIStatusResponse | null>(null);
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [providerList, cfg, aiStatus] = await Promise.all([
        coreApi.getAIProviders(),
        coreApi.getAIConfig(),
        coreApi.getAIStatus(),
      ]);
      setProviders(providerList);
      setConfig(cfg);
      setStatus(aiStatus);
      if (aiStatus.provider_status === 'online' || aiStatus.provider_status === 'ready') setModels(await coreApi.getAIModels());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load AI settings');
    } finally {
      setLoading(false);
    }
  }, []);

  const save = useCallback(async (next: UpdateAIConfigRequest) => {
    setConfig(await coreApi.updateAIConfig(next));
    await load();
  }, [load]);

  const testConnection = useCallback(async () => {
    setStatus(await coreApi.testAIConnection());
  }, []);

  const refreshModels = useCallback(async () => {
    setModels(await coreApi.refreshAIModels());
    setStatus(await coreApi.getAIStatus());
  }, []);

  useEffect(() => { load(); }, [load]);
  return { providers, config, models, status, isLoading, error, load, save, testConnection, refreshModels };
}

export function useAssistant() {
  const { status, label, refresh } = useAIStatus();
  const [conversations, setConversations] = useState<AIConversation[]>([]);
  const [active, setActive] = useState<AIConversation | null>(null);
  const [isLoading, setLoading] = useState(true);
  const [isSending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await coreApi.getConversations();
      setConversations(list);
      if (!active && list[0]) setActive(await coreApi.getConversation(list[0].id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load conversations');
    } finally {
      setLoading(false);
    }
  }, [active]);

  const newConversation = useCallback(async () => {
    const created = await coreApi.createConversation();
    setActive(created);
    await load();
  }, [load]);

  const selectConversation = useCallback(async (id: string) => {
    setActive(await coreApi.getConversation(id));
  }, []);

  const send = useCallback(async (message: string) => {
    setSending(true);
    setError(null);
    try {
      const result = await coreApi.sendChat({ conversation_id: active?.id, message });
      setActive(result.conversation);
      await load();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message');
    } finally {
      setSending(false);
    }
  }, [active, load, refresh]);

  useEffect(() => { load(); }, []);
  return { status, label, conversations, active, isLoading, isSending, error, load, newConversation, selectConversation, send };
}
