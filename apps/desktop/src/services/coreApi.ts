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

export interface OllamaProcessOperationResponse {
  success: boolean;
  already_running?: boolean | null;
  state: string;
  managed: boolean;
  managed_pid?: number | null;
  code?: string | null;
  message: string;
}

export interface OllamaProcessStatusResponse {
  state: string;
  running: boolean;
  managed: boolean;
  managed_pid?: number | null;
  executable_path?: string | null;
  base_url: string;
  storage_location?: string | null;
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
  startOllamaProcess: () => apiClient.post<OllamaProcessOperationResponse>('/api/ai/ollama/start', {}),
  stopOllamaProcess: () => apiClient.post<OllamaProcessOperationResponse>('/api/ai/ollama/stop', {}),
  restartOllamaProcess: () => apiClient.post<OllamaProcessOperationResponse>('/api/ai/ollama/restart', {}),
  getOllamaProcessStatus: () => apiClient.get<OllamaProcessStatusResponse>('/api/ai/ollama/process'),
  getConversations: () => apiClient.get<AIConversation[]>('/api/ai/conversations'),
  createConversation: () => apiClient.post<AIConversation>('/api/ai/conversations', {}),
  getConversation: (id: string) => apiClient.get<AIConversation>(`/api/ai/conversations/${id}`),
  sendChat: (payload: { conversation_id?: string | null; message: string; client_message_id?: string }) =>
    apiClient.post<{
      conversation: AIConversation;
      message: AIMessage;
      approval_required?: boolean;
      pending_tool_call?: Record<string, unknown> | null;
      approval_id?: string | null;
      agent_loop?: Record<string, unknown> | null;
    }>('/api/ai/chat', payload, CHAT_TIMEOUT_MS),
  approveTool: (approvalId: string, reason?: string) =>
    apiClient.post<{
      approval_id: string;
      status: string;
      tool_id: string;
      action: string;
      tool_call_id: string;
      arguments: Record<string, unknown>;
      conversation_id: string;
      request_id?: string | null;
      created_at: string;
      expires_at: string;
      executed_at?: string | null;
      decision_reason?: string | null;
      execution_result?: Record<string, unknown> | null;
    }>(`/api/ai/approvals/${encodeURIComponent(approvalId)}/approve`, { decision_reason: reason }),
  rejectTool: (approvalId: string, reason?: string) =>
    apiClient.post<{
      approval_id: string;
      status: string;
      tool_id: string;
      action: string;
      tool_call_id: string;
      arguments: Record<string, unknown>;
      conversation_id: string;
      request_id?: string | null;
      created_at: string;
      expires_at: string;
      executed_at?: string | null;
      decision_reason?: string | null;
    }>(`/api/ai/approvals/${encodeURIComponent(approvalId)}/reject`, { decision_reason: reason }),
};

