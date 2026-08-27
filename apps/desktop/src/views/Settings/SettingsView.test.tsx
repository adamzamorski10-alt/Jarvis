/* @vitest-environment jsdom */
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsView } from './SettingsView';

const settingsMock = vi.hoisted(() => vi.fn());
const coreStatusMock = vi.hoisted(() => vi.fn());
const aiSettingsMock = vi.hoisted(() => vi.fn());

vi.mock('../../hooks/useSettings', () => ({ useSettings: settingsMock }));
vi.mock('../../hooks/useCoreStatus', () => ({ useCoreStatus: coreStatusMock }));
vi.mock('../../hooks/useAI', async () => {
  const actual = await vi.importActual<typeof import('../../hooks/useAI')>('../../hooks/useAI');
  return { ...actual, useAISettings: aiSettingsMock };
});

function baseAI(overrides = {}) {
  return {
    providers: [
      { id: 'ollama', label: 'Ollama', available: true },
      { id: 'openrouter', label: 'OpenRouter', available: true },
      { id: 'kimi', label: 'Kimi', available: true },
    ],
    config: {
      provider: 'openrouter',
      ollama_url: 'http://127.0.0.1:11434',
      openrouter_base_url: 'https://openrouter.ai/api/v1',
      openrouter_api_key_configured: true,
      openrouter_api_key_hint: '****5678',
      kimi_base_url: 'https://api.moonshot.ai/v1',
      kimi_api_key_configured: true,
      kimi_api_key_hint: '****1234',
      selected_model: 'openrouter/free',
    },
    models: [{ name: 'openrouter/free' }, { name: 'meta-llama/llama-3.3-70b-instruct:free' }],
    status: {
      provider: 'openrouter',
      provider_status: 'ready',
      configured: true,
      provider_url: 'https://openrouter.ai/api/v1',
      provider_version: null,
      message: null,
      model: 'openrouter/free',
      model_status: 'ready',
      models_count: 2,
      ai_ready: true,
    },
    isLoading: false,
    error: null,
    load: vi.fn(),
    save: vi.fn(),
    testConnection: vi.fn(),
    refreshModels: vi.fn(),
    ...overrides,
  };
}

describe('SettingsView AI tab', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'manual' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: vi.fn(),
    });
    coreStatusMock.mockReturnValue({
      isOnline: true,
      status: { database: { status: 'connected' } },
      coreUrl: 'http://127.0.0.1:49152',
      diagnostics: null,
    });
  });

  it('shows OpenRouter fields, masks the stored API key, and renders Free Router controls', async () => {
    aiSettingsMock.mockReturnValue(baseAI());
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('OpenRouter Cloud')).toBeInTheDocument();
    expect(screen.getByText('Stored securely (****5678)')).toBeInTheDocument();
    expect(screen.queryByText('sk-or-v1-secret')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('Leave blank to keep existing key')).toBeInTheDocument();
    expect(screen.getByText('Use Free Router (openrouter/free)')).toBeInTheDocument();
    expect(screen.getByText('Free Router Active (openrouter/free)')).toBeInTheDocument();
  });

  it('shows Kimi fields and masks the stored API key', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: {
        provider: 'kimi',
        ollama_url: 'http://127.0.0.1:11434',
        openrouter_base_url: 'https://openrouter.ai/api/v1',
        openrouter_api_key_configured: false,
        openrouter_api_key_hint: null,
        kimi_base_url: 'https://api.moonshot.ai/v1',
        kimi_api_key_configured: true,
        kimi_api_key_hint: '****1234',
        selected_model: 'kimi-k3',
      },
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
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Kimi Cloud')).toBeInTheDocument();
    expect(screen.getByText('Stored securely (****1234)')).toBeInTheDocument();
    expect(screen.queryByText('sk-secret-1234')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('Leave blank to keep existing key')).toBeInTheDocument();
  });

  it('switches to Ollama fields without rendering cloud API key inputs', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: {
        provider: 'ollama',
        ollama_url: 'http://127.0.0.1:11434',
        openrouter_base_url: 'https://openrouter.ai/api/v1',
        openrouter_api_key_configured: false,
        openrouter_api_key_hint: null,
        kimi_base_url: 'https://api.moonshot.ai/v1',
        kimi_api_key_configured: false,
        kimi_api_key_hint: null,
        selected_model: null,
      },
      status: {
        provider: 'ollama',
        provider_status: 'online',
        configured: true,
        provider_url: 'http://127.0.0.1:11434',
        provider_version: '0.1.0',
        message: null,
        model: null,
        model_status: 'not_selected',
        models_count: 0,
        ai_ready: false,
      },
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Ollama Connection')).toBeInTheDocument();
    expect(screen.queryByText('OpenRouter Cloud')).not.toBeInTheDocument();
    expect(screen.queryByText('Kimi Cloud')).not.toBeInTheDocument();
  });
});
