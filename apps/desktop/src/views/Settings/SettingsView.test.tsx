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

  it('switches to Ollama fields and renders detailed Ollama diagnostics & model recommendations', async () => {
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
        selected_model: 'qwen2.5-coder:7b',
      },
      models: [{ name: 'qwen2.5-coder:7b', size: 4700000000, modified_at: '2026-08-20T10:00:00Z' }],
      status: {
        provider: 'ollama',
        provider_status: 'ready',
        configured: true,
        provider_url: 'http://127.0.0.1:11434',
        provider_version: '0.5.1',
        message: 'Ollama is ready with 1 model(s)',
        model: 'qwen2.5-coder:7b',
        model_status: 'ready',
        models_count: 1,
        ai_ready: true,
        installed: true,
        service_running: true,
        api_responding: true,
        models_available: true,
        executable_path: 'C:\\Users\\test\\AppData\\Local\\Programs\\Ollama\\ollama.exe',
      },
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Ollama Local AI Diagnostics')).toBeInTheDocument();
    expect(screen.getByText('Ollama is ready to use.')).toBeInTheDocument();
    expect(screen.getByText('Model Management')).toBeInTheDocument();
    expect(screen.getAllByText('qwen2.5-coder:7b')[0]).toBeInTheDocument();
    expect(screen.getByText('Recommended for your PC')).toBeInTheDocument();
    expect(screen.getByText('NVIDIA GTX 1080 Ti (11 GB VRAM) + 24 GB RAM')).toBeInTheDocument();
    expect(screen.queryByText('OpenRouter Cloud')).not.toBeInTheDocument();
  });

  it('renders Ollama stopped state with correct badge and message', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: {
        provider: 'ollama',
        ollama_url: 'http://127.0.0.1:11434',
        selected_model: null,
      },
      models: [],
      status: {
        provider: 'ollama',
        provider_status: 'stopped',
        configured: true,
        provider_url: 'http://127.0.0.1:11434',
        provider_version: null,
        message: 'Ollama executable detected, but service is not running.',
        model: null,
        model_status: 'not_selected',
        models_count: 0,
        ai_ready: false,
        installed: true,
        service_running: false,
        api_responding: false,
        models_available: false,
        executable_path: 'C:\\Program Files\\Ollama\\ollama.exe',
      },
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Ollama is installed, but its local service is not currently running.')).toBeInTheDocument();
    expect(screen.getByText('SERVICE STOPPED')).toBeInTheDocument();
  });

  it('renders Ollama not_installed state with correct badge and message', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: {
        provider: 'ollama',
        ollama_url: 'http://127.0.0.1:11434',
        selected_model: null,
      },
      models: [],
      status: {
        provider: 'ollama',
        provider_status: 'not_installed',
        configured: true,
        provider_url: 'http://127.0.0.1:11434',
        provider_version: null,
        message: 'Ollama is not installed',
        model: null,
        model_status: 'not_selected',
        models_count: 0,
        ai_ready: false,
        installed: false,
        service_running: false,
        api_responding: false,
        models_available: false,
        executable_path: null,
      },
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Ollama is not installed on this computer.')).toBeInTheDocument();
    expect(screen.getAllByText('NOT INSTALLED')[0]).toBeInTheDocument();
  });

  it('renders model storage info, model management cards, and opens confirmation modal on active model delete', async () => {
    const deleteModelMock = vi.fn();
    aiSettingsMock.mockReturnValue(baseAI({
      config: {
        provider: 'ollama',
        ollama_url: 'http://127.0.0.1:11434',
        selected_model: 'qwen3:8b',
      },
      models: [
        { name: 'qwen3:8b', size: 5200000000 },
        { name: 'nomic-embed-text:latest', size: 300000000 },
      ],
      storageInfo: {
        location: 'F:\\AI-Models\\.ollama\\models',
        drive: 'F:',
        available_bytes: 120000000000,
        used_bytes: 5500000000,
      },
      status: {
        provider: 'ollama',
        provider_status: 'ready',
        configured: true,
        provider_url: 'http://127.0.0.1:11434',
        model: 'qwen3:8b',
        model_status: 'ready',
        models_count: 2,
        ai_ready: true,
      },
      deleteModel: deleteModelMock,
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Model Management')).toBeInTheDocument();
    expect(screen.getByText('F:\\AI-Models\\.ollama\\models')).toBeInTheDocument();
    expect(screen.getByText('F:')).toBeInTheDocument();

    // Click Remove on active model -> should open confirmation modal
    const removeButtons = screen.getAllByRole('button', { name: 'Remove' });
    fireEvent.click(removeButtons[0]);

    expect(screen.getByText('Confirm Active Model Removal')).toBeInTheDocument();
    expect(screen.getByText('You are about to remove the currently active AI model.')).toBeInTheDocument();

    // Confirm deletion
    const confirmBtn = screen.getByRole('button', { name: 'Remove Model' });
    fireEvent.click(confirmBtn);

    expect(deleteModelMock).toHaveBeenCalledWith('qwen3:8b');
  });

  it('renders active download progress card and handles cancellation', async () => {
    const cancelPullMock = vi.fn();
    aiSettingsMock.mockReturnValue(baseAI({
      config: {
        provider: 'ollama',
        ollama_url: 'http://127.0.0.1:11434',
        selected_model: null,
      },
      pullStatus: {
        active: true,
        model: 'qwen3:8b',
        percentage: 62.5,
        completed: 3200000000,
        total: 5200000000,
        message: 'Downloading model layers...',
      },
      cancelPull: cancelPullMock,
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Downloading Model')).toBeInTheDocument();
    expect(screen.getByText('qwen3:8b')).toBeInTheDocument();
    expect(screen.getByText(/62\.5%/)).toBeInTheDocument();
    expect(screen.getByText('Downloading model layers...')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel Download' }));
    expect(cancelPullMock).toHaveBeenCalled();
  });

  it('renders insufficient disk space error panel', async () => {
    const clearErrorMock = vi.fn();
    aiSettingsMock.mockReturnValue(baseAI({
      config: {
        provider: 'ollama',
        ollama_url: 'http://127.0.0.1:11434',
        selected_model: null,
      },
      pullError: {
        message: 'Not enough free disk space to download qwen3:8b.',
        required_bytes: 5200000000,
        available_bytes: 2000000000,
        drive: 'F:',
      },
      clearPullError: clearErrorMock,
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Not enough disk space')).toBeInTheDocument();
    expect(screen.getByText('Not enough free disk space to download qwen3:8b.')).toBeInTheDocument();
    expect(screen.getByText('Model storage: F:')).toBeInTheDocument();
  });

  /* ---- STAGE 4.4 OLLAMA PROCESS CONTROL TESTS ---- */

  it('renders Ollama process status in STOPPED state with Start Service button', async () => {
    const startProcessMock = vi.fn();
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: null },
      processStatus: {
        state: 'stopped',
        running: false,
        managed: false,
        managed_pid: null,
        executable_path: 'C:\\Ollama\\ollama.exe',
        base_url: 'http://127.0.0.1:11434',
        storage_location: 'F:\\AI-Models\\.ollama\\models',
      },
      isProcessOperating: false,
      processActionState: null,
      startOllamaProcess: startProcessMock,
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Ollama Service Process')).toBeInTheDocument();
    expect(screen.getByText('STOPPED')).toBeInTheDocument();
    const startBtn = screen.getByRole('button', { name: 'Start Service' });
    expect(startBtn).toBeInTheDocument();
    expect(startBtn).not.toBeDisabled();

    fireEvent.click(startBtn);
    expect(startProcessMock).toHaveBeenCalled();
  });

  it('renders Ollama process status in RUNNING — MANAGED state with Stop and Restart Service buttons', async () => {
    const stopProcessMock = vi.fn();
    const restartProcessMock = vi.fn();
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: 'qwen3:8b' },
      processStatus: {
        state: 'running_managed',
        running: true,
        managed: true,
        managed_pid: 43210,
        executable_path: 'C:\\Ollama\\ollama.exe',
        base_url: 'http://127.0.0.1:11434',
        storage_location: 'F:\\AI-Models\\.ollama\\models',
      },
      isProcessOperating: false,
      processActionState: null,
      stopOllamaProcess: stopProcessMock,
      restartOllamaProcess: restartProcessMock,
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('RUNNING — MANAGED')).toBeInTheDocument();
    expect(screen.getByText('43210')).toBeInTheDocument();

    const stopBtn = screen.getByRole('button', { name: 'Stop Service' });
    const restartBtn = screen.getByRole('button', { name: 'Restart Service' });

    expect(stopBtn).toBeInTheDocument();
    expect(restartBtn).toBeInTheDocument();

    fireEvent.click(stopBtn);
    expect(stopProcessMock).toHaveBeenCalled();

    fireEvent.click(restartBtn);
    expect(restartProcessMock).toHaveBeenCalled();
  });

  it('renders Ollama process status in RUNNING — EXTERNAL state with disabled Stop button and safety notice', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: 'qwen3:8b' },
      processStatus: {
        state: 'running_external',
        running: true,
        managed: false,
        managed_pid: null,
        executable_path: 'C:\\Ollama\\ollama.exe',
        base_url: 'http://127.0.0.1:11434',
        storage_location: 'F:\\AI-Models\\.ollama\\models',
      },
      isProcessOperating: false,
      processActionState: null,
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('RUNNING — EXTERNAL')).toBeInTheDocument();
    expect(screen.getByText(/Ollama is running externally\. AI Operator will not stop or restart this process\./)).toBeInTheDocument();

    const disabledStopBtn = screen.getByRole('button', { name: 'Stop Service (External)' });
    expect(disabledStopBtn).toBeDisabled();
  });

  it('renders process action error message and clear button', async () => {
    const clearProcessErrorMock = vi.fn();
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: null },
      processStatus: {
        state: 'stopped',
        running: false,
        managed: false,
        managed_pid: null,
      },
      processError: 'Ollama is managed externally and cannot be stopped by AI Operator.',
      clearProcessError: clearProcessErrorMock,
    }));
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Ollama is managed externally and cannot be stopped by AI Operator.')).toBeInTheDocument();
  });

  /* ---- STAGE 4.5 AUTOSTART OLLAMA TOGGLE TESTS ---- */

  it('renders Autostart Ollama toggle in OFF state by default', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: null },
    }));
    // ai_ollama_autostart not set → defaults to false
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'manual', ai_ollama_autostart: 'false' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: vi.fn(),
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('Autostart Ollama')).toBeInTheDocument();
    const toggle = screen.getByRole('switch', { name: '' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('enables Autostart Ollama toggle when setting is true', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: null },
    }));
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'manual', ai_ollama_autostart: 'true' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: vi.fn(),
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    const toggle = screen.getByRole('switch', { name: '' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });

  it('calls updateSettings with false when Autostart toggle is clicked while ON', async () => {
    const updateSettingsMock = vi.fn().mockResolvedValue(true);
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: null },
    }));
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'manual', ai_ollama_autostart: 'true' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: updateSettingsMock,
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    const toggle = screen.getByRole('switch', { name: '' });
    fireEvent.click(toggle);
    expect(updateSettingsMock).toHaveBeenCalledWith({ ai_ollama_autostart: 'false' });
  });

  it('calls updateSettings with true when Autostart toggle is clicked while OFF', async () => {
    const updateSettingsMock = vi.fn().mockResolvedValue(true);
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: null },
    }));
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'manual', ai_ollama_autostart: 'false' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: updateSettingsMock,
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    const toggle = screen.getByRole('switch', { name: '' });
    fireEvent.click(toggle);
    expect(updateSettingsMock).toHaveBeenCalledWith({ ai_ollama_autostart: 'true' });
  });

  it('shows "next time AI Operator starts" info text below the Autostart toggle', async () => {
    aiSettingsMock.mockReturnValue(baseAI({
      config: { provider: 'ollama', ollama_url: 'http://127.0.0.1:11434', selected_model: null },
    }));
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Ai' }));

    expect(screen.getByText('This setting takes effect the next time AI Operator starts.')).toBeInTheDocument();
  });
});


describe('SettingsView Autonomy tab', () => {
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
      updateSettings: vi.fn().mockResolvedValue(true),
    });
    coreStatusMock.mockReturnValue({
      isOnline: true,
      status: { database: { status: 'connected' } },
      coreUrl: 'http://127.0.0.1:49152',
      diagnostics: null,
    });
  });

  it('renders only backend-supported autonomy modes', async () => {
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Autonomy' }));

    expect(screen.getByText('Manual')).toBeInTheDocument();
    expect(screen.getByText('Semi-Autonomous')).toBeInTheDocument();
    expect(screen.getByText('Autonomous')).toBeInTheDocument();
    expect(screen.queryByText('Supervised')).not.toBeInTheDocument();
    expect(screen.queryByText('Controlled')).not.toBeInTheDocument();
  });

  it('normalizes legacy supervised mode to semi_autonomous', async () => {
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'supervised' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: vi.fn().mockResolvedValue(true),
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Autonomy' }));

    expect(screen.getByText('Semi-Autonomous').closest('.autonomy-card')).toHaveClass('selected');
  });

  it('normalizes legacy controlled mode to autonomous', async () => {
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'controlled' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: vi.fn().mockResolvedValue(true),
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Autonomy' }));

    expect(screen.getByText('Autonomous').closest('.autonomy-card')).toHaveClass('selected');
  });

  it('persists a valid selected mode through updateSettings', async () => {
    const updateSettingsMock = vi.fn().mockResolvedValue(true);
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'manual' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: updateSettingsMock,
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Autonomy' }));

    fireEvent.click(screen.getByText('Autonomous'));
    fireEvent.click(screen.getByRole('button', { name: '✓ Save Changes' }));

    expect(updateSettingsMock).toHaveBeenCalledWith({ autonomy_mode: 'autonomous', theme: 'dark', language: 'en' });
  });

  it('does not save an unsupported mode value', async () => {
    const updateSettingsMock = vi.fn().mockResolvedValue(true);
    settingsMock.mockReturnValue({
      settings: { theme: 'dark', language: 'en', autonomy_mode: 'manual' },
      isLoading: false,
      isSaving: false,
      savedAt: null,
      error: null,
      updateSettings: updateSettingsMock,
    });
    render(<SettingsView />);
    fireEvent.click(screen.getByRole('button', { name: 'Autonomy' }));

    // The UI should not expose invalid modes at all, so this test asserts absence
    expect(screen.queryByText('Supervised')).not.toBeInTheDocument();
    expect(screen.queryByText('Controlled')).not.toBeInTheDocument();
  });
});


