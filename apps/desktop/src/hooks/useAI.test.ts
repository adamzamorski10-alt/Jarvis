import { describe, expect, it } from 'vitest';
import { formatAIStatus } from './useAI';
import { AIStatusResponse } from '../services/coreApi';

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
});
