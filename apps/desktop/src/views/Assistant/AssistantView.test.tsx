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

    expect(screen.getByText('Waiting for model response...')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });
});
