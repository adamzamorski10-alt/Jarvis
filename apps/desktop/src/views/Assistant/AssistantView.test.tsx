/* @vitest-environment jsdom */
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantView } from './AssistantView';

const assistantMock = vi.hoisted(() => vi.fn());

vi.mock('../../hooks/useAI', () => ({ useAssistant: assistantMock }));

function baseAssistant(overrides = {}) {
  return {
    status: {
      provider: 'kimi',
      provider_status: 'ready',
      configured: true,
      provider_url: 'https://api.moonshot.ai/v1',
      provider_version: null,
      message: null,
      model: 'kimi-k3',
      model_status: 'ready',
      models_count: 1,
      ai_ready: true,
    },
    label: 'AI Ready',
    conversations: [],
    active: null,
    isLoading: false,
    isSending: false,
    error: null,
    load: vi.fn(),
    newConversation: vi.fn(),
    selectConversation: vi.fn(),
    send: vi.fn(),
    ...overrides,
  };
}

describe('AssistantView', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    assistantMock.mockReset();
  });

  it('sends text through the assistant hook', async () => {
    const send = vi.fn();
    assistantMock.mockReturnValue(baseAssistant({ send }));
    render(<AssistantView />);

    fireEvent.change(screen.getByPlaceholderText('Message Assistant'), { target: { value: 'hello kimi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(send).toHaveBeenCalledWith('hello kimi');
  });

  it('disables sending while the provider is not ready', () => {
    assistantMock.mockReturnValue(baseAssistant({
      label: 'Kimi Authentication Failed',
      status: {
        provider: 'kimi',
        provider_status: 'invalid_api_key',
        configured: true,
        provider_url: 'https://api.moonshot.ai/v1',
        provider_version: null,
        message: 'Kimi authentication failed',
        model: 'kimi-k3',
        model_status: 'not_selected',
        models_count: 0,
        ai_ready: false,
      },
    }));
    render(<AssistantView />);

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    expect(screen.getByPlaceholderText('Kimi Authentication Failed')).toBeInTheDocument();
  });

  it('shows sending state and keeps send disabled', () => {
    assistantMock.mockReturnValue(baseAssistant({ isSending: true }));
    render(<AssistantView />);

    expect(screen.getByText('AI is thinking...')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('renders OpenRouter provider and Free Router in header meta', () => {
    assistantMock.mockReturnValue(baseAssistant({
      status: {
        provider: 'openrouter',
        provider_status: 'ready',
        configured: true,
        provider_url: 'https://openrouter.ai/api/v1',
        provider_version: null,
        message: null,
        model: 'openrouter/free',
        model_status: 'ready',
        models_count: 5,
        ai_ready: true,
      },
    }));
    render(<AssistantView />);

    expect(screen.getByText('Provider: OpenRouter')).toBeInTheDocument();
    expect(screen.getByText('Model: openrouter/free (Free Router)')).toBeInTheDocument();
  });

  it('renders error banner with Retry button and invokes retry on click', () => {
    const retry = vi.fn();
    assistantMock.mockReturnValue(baseAssistant({
      error: 'OpenRouter rate limit reached. Please wait a moment and retry.',
      errorInfo: {
        message: 'OpenRouter rate limit reached. Please wait a moment and retry.',
        code: 'provider_rate_limited',
        provider: 'OpenRouter',
        retryable: true,
      },
      lastFailedPrompt: 'tell me a joke',
      retry,
    }));
    render(<AssistantView />);

    expect(screen.getByText('OpenRouter rate limit reached. Please wait a moment and retry.')).toBeInTheDocument();
    const retryBtn = screen.getByRole('button', { name: '↻ Retry' });
    expect(retryBtn).toBeInTheDocument();
    fireEvent.click(retryBtn);
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
