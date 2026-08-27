import { describe, expect, it } from 'vitest';
import { formatAIStatus, formatAssistantError } from './useAI';
import { AIStatusResponse, ApiError, ApiTimeoutError, CoreOfflineError } from '../services/coreApi';

function status(overrides: Partial<AIStatusResponse>): AIStatusResponse {
  return {
    provider: 'ollama',
    provider_status: 'online',
    configured: true,
    provider_url: 'http://127.0.0.1:11434',
    provider_version: null,
    message: null,
    model: null,
    model_status: 'not_selected',
    models_count: 0,
    ai_ready: false,
    ...overrides,
  };
}

describe('formatAIStatus', () => {
  it('shows ready state', () => {
    expect(formatAIStatus(status({ ai_ready: true, model: 'qwen:test', model_status: 'ready' }))).toBe('AI Ready');
  });

  it('distinguishes no selected model from offline', () => {
    expect(formatAIStatus(status({ provider_status: 'online', model_status: 'not_selected' }))).toBe('Ollama Connected - No Model Selected');
  });

  it('shows offline when provider is unreachable', () => {
    expect(formatAIStatus(status({ provider_status: 'unreachable' }))).toBe('Ollama Offline');
  });

  it('shows Kimi authentication failures', () => {
    expect(formatAIStatus(status({ provider: 'kimi', provider_status: 'invalid_api_key' }))).toBe('Kimi Authentication Failed');
  });

  it('shows Kimi rate limit state', () => {
    expect(formatAIStatus(status({ provider: 'kimi', provider_status: 'rate_limited' }))).toBe('Kimi Rate Limited');
  });

  it('shows OpenRouter connected without model', () => {
    expect(formatAIStatus(status({ provider: 'openrouter', provider_status: 'ready', model_status: 'not_selected' }))).toBe('OpenRouter Connected - No Model Selected');
  });

  it('shows OpenRouter authentication failures', () => {
    expect(formatAIStatus(status({ provider: 'openrouter', provider_status: 'invalid_api_key' }))).toBe('OpenRouter Authentication Failed');
  });

  it('shows OpenRouter rate limit state', () => {
    expect(formatAIStatus(status({ provider: 'openrouter', provider_status: 'rate_limited' }))).toBe('OpenRouter Rate Limited');
  });

  it('shows OpenRouter not configured', () => {
    expect(formatAIStatus(status({ provider: 'openrouter', provider_status: 'not_configured' }))).toBe('OpenRouter Not Configured');
  });
});

describe('formatAssistantError', () => {
  it('formats timeout as a retryable timeout error', () => {
    const res = formatAssistantError(new ApiTimeoutError(), 'openrouter');
    expect(res.message).toContain('took too long to respond');
    expect(res.retryable).toBe(true);
    expect(res.code).toBe('core_timeout');
  });

  it('formats actual Core offline error', () => {
    const res = formatAssistantError(new CoreOfflineError(), 'openrouter');
    expect(res.message).toContain('AI Core is not running');
    expect(res.retryable).toBe(true);
    expect(res.code).toBe('core_unreachable');
  });

  it('formats OpenRouter rate limit error as retryable', () => {
    const err = new ApiError(409, 'Rate limited', { code: 'rate_limited', provider: 'openrouter', retryable: true });
    const res = formatAssistantError(err, 'openrouter');
    expect(res.message).toContain('rate limited');
    expect(res.retryable).toBe(true);
    expect(res.code).toBe('rate_limited');
  });

  it('formats OpenRouter model unavailable / overloaded error as retryable', () => {
    const err = new ApiError(409, 'Overloaded', { code: 'model_unavailable', provider: 'openrouter', retryable: true });
    const res = formatAssistantError(err, 'openrouter');
    expect(res.message).toContain('temporarily unavailable');
    expect(res.retryable).toBe(true);
  });

  it('formats OpenRouter invalid API key as non-retryable', () => {
    const err = new ApiError(401, 'Unauthorized', { code: 'invalid_api_key', provider: 'openrouter', retryable: false });
    const res = formatAssistantError(err, 'openrouter');
    expect(res.message).toContain('OpenRouter API key is invalid');
    expect(res.retryable).toBe(false);
  });
});
