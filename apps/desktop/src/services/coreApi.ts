/**
 * Typed API functions for all Core service endpoints.
 */
import { apiClient, CHAT_TIMEOUT_MS, StructuredErrorPayload, ApiError, ApiTimeoutError, CoreOfflineError } from './apiClient';

export type { StructuredErrorPayload };
export { ApiError, ApiTimeoutError, CoreOfflineError };

/* ---- Types ---- */
export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  timestamp: string;
}

export interface CoreComponentStatus {
  status: string;
  version?: string;
  uptime_seconds?: number;
  start_time?: string;
  message?: string;
}

export interface DatabaseComponentStatus {
  status: string;
  engine?: string;
  migration_status?: string;
  schema_version?: string;
  last_error?: string | null;
  message?: string;
}

export interface AIComponentStatus {
  status: string;
  provider?: string;
  configured?: boolean;
  selected_model?: string | null;
  message?: string;
  installed?: boolean | null;
  service_running?: boolean | null;
  api_responding?: boolean | null;
  models_available?: boolean | null;
  executable_path?: string | null;
}

export interface SystemStatusResponse {
  core: CoreComponentStatus;
  database: DatabaseComponentStatus;
  ai: AIComponentStatus;
}

export interface SettingsResponse {
  settings: Record<string, string | null>;
}

export interface ActivityEvent {
  id: number;
  timestamp: string;
  level: string;
  category: string;
  message: string;
  details?: Record<string, unknown> | null;
}

export interface PaginatedActivityResponse {
  events: ActivityEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface AIStatusResponse {
  provider: string;
  provider_status: string;
  configured: boolean;
  provider_url: string | null;
  provider_version?: string | null;
  message?: string | null;
  model: string | null;
  model_status: string;
  models_count: number;
  ai_ready: boolean;
  installed?: boolean | null;
  service_running?: boolean | null;
  api_responding?: boolean | null;
  models_available?: boolean | null;
  executable_path?: string | null;
}

export interface AIProviderItem {
  id: string;
  label: string;
  available: boolean;
}

export interface AIConfigResponse {
  provider: string;
  ollama_url: string;
  openrouter_base_url: string;
  openrouter_api_key_configured: boolean;
  openrouter_api_key_hint?: string | null;
  kimi_base_url: string;
  kimi_api_key_configured: boolean;
  kimi_api_key_hint?: string | null;
  selected_model: string | null;
}

export interface UpdateAIConfigRequest {
  provider: string;
  ollama_url?: string | null;
  openrouter_base_url?: string | null;
  openrouter_api_key?: string | null;
  kimi_base_url?: string | null;
  kimi_api_key?: string | null;
  selected_model?: string | null;
}

export interface AIModelResponse {
  name: string;
  size?: number | null;
  modified_at?: string | null;
}

export interface AIMessage {
  id: number;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string;
}

export interface PullStatusResponse {
  active: boolean;
  model?: string | null;
  status?: string | null;
  phase?: string | null;
  completed?: number | null;
  total?: number | null;
  percentage?: number | null;
  message?: string | null;
  error?: Record<string, unknown> | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface OllamaStorageResponse {
  location?: string | null;
  drive?: string | null;
  available_bytes?: number | null;
  used_bytes?: number | null;
}

export interface AIConversation {
  id: string;
  title: string;
  provider: string;
  model: string | null;
  created_at: string;
  updated_at: string;
  messages: AIMessage[];
}

/* ---- API Functions ---- */
export const coreApi = {
  getHealth: () => apiClient.get<HealthResponse>('/api/health'),

  getSystemStatus: () => apiClient.get<SystemStatusResponse>('/api/system/status'),

  getSettings: () => apiClient.get<SettingsResponse>('/api/settings'),

  updateSettings: (settings: Record<string, string | null>) =>
    apiClient.put<SettingsResponse>('/api/settings', { settings }),

  getActivity: (params?: { limit?: number; offset?: number; category?: string; level?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit !== undefined) q.set('limit', String(params.limit));
    if (params?.offset !== undefined) q.set('offset', String(params.offset));
    if (params?.category) q.set('category', params.category);
    if (params?.level) q.set('level', params.level);
    const qs = q.toString();
    return apiClient.get<PaginatedActivityResponse>(`/api/activity${qs ? `?${qs}` : ''}`);
  },

  createActivityEvent: (event: { message: string; level?: string; category?: string; details?: Record<string, unknown> }) =>
    apiClient.post<ActivityEvent>('/api/activity', event),

  getAIStatus: () => apiClient.get<AIStatusResponse>('/api/ai/status'),
  getAIProviders: () => apiClient.get<AIProviderItem[]>('/api/ai/providers'),
  getAIConfig: () => apiClient.get<AIConfigResponse>('/api/ai/config'),
  updateAIConfig: (config: UpdateAIConfigRequest) => apiClient.put<AIConfigResponse>('/api/ai/config', config),
  testAIConnection: () => apiClient.post<AIStatusResponse>('/api/ai/test-connection', {}, CHAT_TIMEOUT_MS),
  getAIModels: () => apiClient.get<AIModelResponse[]>('/api/ai/models'),
  refreshAIModels: () => apiClient.post<AIModelResponse[]>('/api/ai/models/refresh', {}, CHAT_TIMEOUT_MS),
  pullAIModel: (model: string) => apiClient.post<PullStatusResponse>('/api/ai/models/pull', { model }),
  getPullStatus: () => apiClient.get<PullStatusResponse>('/api/ai/models/pull/status'),
  cancelPull: () => apiClient.post<PullStatusResponse>('/api/ai/models/pull/cancel', {}),
  deleteAIModel: (modelName: string) => apiClient.delete<{ success: boolean; model: string }>(`/api/ai/models/${encodeURIComponent(modelName)}`),
  getOllamaStorage: () => apiClient.get<OllamaStorageResponse>('/api/ai/models/storage'),
  getConversations: () => apiClient.get<AIConversation[]>('/api/ai/conversations'),
  createConversation: () => apiClient.post<AIConversation>('/api/ai/conversations', {}),
  getConversation: (id: string) => apiClient.get<AIConversation>(`/api/ai/conversations/${id}`),
  sendChat: (payload: { conversation_id?: string | null; message: string; client_message_id?: string }) =>
    apiClient.post<{ conversation: AIConversation; message: AIMessage }>('/api/ai/chat', payload, CHAT_TIMEOUT_MS),
};

