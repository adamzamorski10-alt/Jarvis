/**
 * Settings View — General, Autonomy, AI, and System/Diagnostics tabs.
 * All changes persist via Python Core API.
 */
import { useState, useEffect } from 'react';
import { useSettings } from '../../hooks/useSettings';
import { useCoreStatus } from '../../hooks/useCoreStatus';
import { useDiagnostics } from '../../hooks/useDiagnostics';
import { useAISettings, formatAIStatus } from '../../hooks/useAI';
import { ErrorState } from '../../components/Common/ErrorState';

type Tab = 'general' | 'autonomy' | 'ai' | 'system';

const AUTONOMY_MODES = [
  {
    key: 'manual',
    title: 'Manual',
    desc: 'AI analyzes and proposes actions. Nothing is executed without your explicit confirmation.',
  },
  {
    key: 'semi_autonomous',
    title: 'Semi-Autonomous',
    desc: 'AI can perform safe and low-risk actions automatically. Medium, high, and critical-risk actions require approval.',
  },
  {
    key: 'autonomous',
    title: 'Autonomous',
    desc: 'AI can execute safe, low, and medium-risk actions automatically. High and critical-risk actions require approval.',
  },
];

const AUTONOMY_MODE_ALIASES: Record<string, 'manual' | 'semi_autonomous' | 'autonomous'> = {
  supervised: 'semi_autonomous',
  controlled: 'autonomous',
};

function normalizeAutonomyMode(raw: string | undefined | null): 'manual' | 'semi_autonomous' | 'autonomous' {
  if (!raw) return 'manual';
  const normalized = AUTONOMY_MODE_ALIASES[raw];
  if (normalized) return normalized;
  return ['manual', 'semi_autonomous', 'autonomous'].includes(raw) ? (raw as 'manual' | 'semi_autonomous' | 'autonomous') : 'manual';
}

function SystemInfoRow({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="info-row">
      <span className="info-key">{label}</span>
      <span className={`info-value${highlight ? ' status-value success' : ''}`}>{value}</span>
    </div>
  );
}

function formatBytes(bytes?: number | null): string {
  if (!bytes) return 'Unknown size';
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

interface RecommendedModel {
  name: string;
  title: string;
  description: string;
  vramReq: string;
  category: string;
}

const RECOMMENDED_OLLAMA_MODELS: RecommendedModel[] = [
  {
    name: 'qwen2.5-coder:7b',
    title: 'Qwen2.5 Coder 7B',
    description: 'Optimal for code analysis, programming, and AI Operator agent workflows.',
    vramReq: '~4.7 GB VRAM',
    category: 'Code & Development',
  },
  {
    name: 'qwen3:8b',
    title: 'Qwen3 8B',
    description: 'High-quality general reasoning and dialogue assistant.',
    vramReq: '~4.9 GB VRAM',
    category: 'General Assistant',
  },
  {
    name: 'llama3.2:3b',
    title: 'Llama 3.2 3B',
    description: 'Ultra-fast lightweight assistant with low latency for quick tasks.',
    vramReq: '~2.0 GB VRAM',
    category: 'Fast & Lightweight',
  },
  {
    name: 'mistral:7b',
    title: 'Mistral 7B',
    description: 'Balanced general-purpose 7B model.',
    vramReq: '~4.1 GB VRAM',
    category: 'General Knowledge',
  },
];

function AISettingsSection() {
  const ai = useAISettings();
  const { settings, updateSettings } = useSettings();
  const [provider, setProvider] = useState('ollama');
  const [ollamaUrl, setOllamaUrl] = useState('');
  const [openrouterBaseUrl, setOpenrouterBaseUrl] = useState('');
  const [openrouterApiKey, setOpenrouterApiKey] = useState('');
  const [kimiBaseUrl, setKimiBaseUrl] = useState('');
  const [kimiApiKey, setKimiApiKey] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [ollamaAutostart, setOllamaAutostart] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [deletingModel, setDeletingModel] = useState<string | null>(null);

  const handleRemoveClick = (modelName: string) => {
    if (selectedModel && (selectedModel === modelName || selectedModel.startsWith(`${modelName}:`))) {
      setDeletingModel(modelName);
    } else {
      ai.deleteModel(modelName);
    }
  };

  const confirmDeleteActiveModel = async () => {
    if (!deletingModel) return;
    const target = deletingModel;
    setDeletingModel(null);
    await ai.deleteModel(target);
  };

  useEffect(() => {
    if (ai.config) {
      setProvider(ai.config.provider);
      setOllamaUrl(ai.config.ollama_url);
      setOpenrouterBaseUrl(ai.config.openrouter_base_url ?? 'https://openrouter.ai/api/v1');
      setKimiBaseUrl(ai.config.kimi_base_url);
      setSelectedModel(ai.config.selected_model ?? '');
      setKimiApiKey('');
      setOpenrouterApiKey('');
    }
  }, [ai.config]);

  useEffect(() => {
    if (settings.ai_ollama_autostart !== undefined) {
      setOllamaAutostart(settings.ai_ollama_autostart === 'true');
    }
  }, [settings.ai_ollama_autostart]);

  const handleToggleAutostart = async (enabled: boolean) => {
    setOllamaAutostart(enabled);
    await updateSettings({ ai_ollama_autostart: enabled ? 'true' : 'false' });
  };

  const save = async () => {
    await ai.save({
      provider,
      ollama_url: ollamaUrl,
      openrouter_base_url: openrouterBaseUrl,
      openrouter_api_key: openrouterApiKey !== '' ? openrouterApiKey : undefined,
      kimi_base_url: kimiBaseUrl,
      kimi_api_key: kimiApiKey !== '' ? kimiApiKey : undefined,
      selected_model: selectedModel || null,
    });
    setKimiApiKey('');
    setOpenrouterApiKey('');
    setResult('Configuration saved');
  };

  const selectAndSaveModel = async (modelName: string) => {
    setSelectedModel(modelName);
    await ai.save({
      provider,
      ollama_url: ollamaUrl,
      openrouter_base_url: openrouterBaseUrl,
      kimi_base_url: kimiBaseUrl,
      selected_model: modelName,
    });
    setResult(`Active model set to ${modelName}`);
  };

  const handleClearKey = async (targetProvider: 'openrouter' | 'kimi') => {
    if (targetProvider === 'openrouter') {
      await ai.save({
        provider,
        ollama_url: ollamaUrl,
        openrouter_base_url: openrouterBaseUrl,
        openrouter_api_key: '',
        kimi_base_url: kimiBaseUrl,
        selected_model: selectedModel || null,
      });
      setOpenrouterApiKey('');
      setResult('OpenRouter API key cleared');
    } else {
      await ai.save({
        provider,
        ollama_url: ollamaUrl,
        kimi_base_url: kimiBaseUrl,
        kimi_api_key: '',
        openrouter_base_url: openrouterBaseUrl,
        selected_model: selectedModel || null,
      });
      setKimiApiKey('');
      setResult('Kimi API key cleared');
    }
  };

  const testConnection = async () => {
    await save();
    await ai.testConnection();
    setResult(null);
  };

  const providerNames: Record<string, string> = {
    ollama: 'Ollama',
    openrouter: 'OpenRouter',
    kimi: 'Kimi',
  };
  const providerName = providerNames[provider] ?? provider;
  const connected = ai.status?.ai_ready;
  const isFreeRouter = provider === 'openrouter' && (selectedModel === 'openrouter/free' || ai.status?.model === 'openrouter/free');

  // Ollama status computation
  const providerStatus = ai.status?.provider_status ?? 'unknown';
  const isOllamaInstalled = ai.status?.installed ?? (providerStatus !== 'not_installed');
  const isOllamaRunning = ai.status?.service_running ?? (providerStatus === 'online' || providerStatus === 'ready' || providerStatus === 'no_models');
  const isApiResponding = ai.status?.api_responding ?? isOllamaRunning;
  const areModelsAvailable = ai.status?.models_available ?? (ai.models.length > 0);

  const ollamaStateMessage =
    providerStatus === 'not_installed'
      ? 'Ollama is not installed on this computer.'
      : providerStatus === 'stopped'
      ? 'Ollama is installed, but its local service is not currently running.'
      : providerStatus === 'online'
      ? 'Ollama is running, but model information could not be fully loaded.'
      : providerStatus === 'no_models'
      ? 'Ollama is running correctly, but no local AI models are installed.'
      : providerStatus === 'ready'
      ? 'Ollama is ready to use.'
      : formatAIStatus(ai.status);

  const isSelectedModelMissing =
    selectedModel &&
    (providerStatus === 'ready' || providerStatus === 'online') &&
    !ai.models.some(m => m.name === selectedModel || m.name.startsWith(`${selectedModel}:`));

  if (ai.isLoading && !ai.config) {
    return <div className="spinner" />;
  }

  return (
    <div className="settings-section fade-in">
      {/* Provider Selector */}
      <div className="card">
        <div className="card-header"><span className="card-title">AI Provider</span></div>
        <div className="settings-row">
          <div className="settings-row-info">
            <div className="settings-row-title">Provider</div>
            <div className="settings-row-desc">Choose the model runtime used by Assistant.</div>
          </div>
          <div className="settings-row-control">
            <select className="form-select" value={provider} onChange={event => setProvider(event.target.value)}>
              {ai.providers.map(item => (
                <option key={item.id} value={item.id} disabled={!item.available}>
                  {item.label}{item.available ? '' : ' - Coming Soon'}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Provider Specific Configuration Card */}
      {provider === 'openrouter' ? (
        <div className="card">
          <div className="card-header">
            <span className="card-title">OpenRouter Cloud</span>
            {ai.config?.openrouter_api_key_configured && (
              <button className="btn btn-ghost btn-sm" style={{ color: 'var(--color-error)' }} onClick={() => handleClearKey('openrouter')}>
                Clear API Key
              </button>
            )}
          </div>
          <div className="settings-row">
            <div className="settings-row-info">
              <div className="settings-row-title">API Key</div>
              <div className="settings-row-desc">
                {ai.config?.openrouter_api_key_configured
                  ? `Stored securely (${ai.config.openrouter_api_key_hint ?? '****'})`
                  : 'No API key stored.'}
              </div>
            </div>
            <div className="settings-row-control">
              <input
                className="form-input"
                type="password"
                value={openrouterApiKey}
                placeholder={ai.config?.openrouter_api_key_configured ? 'Leave blank to keep existing key' : 'Paste OpenRouter API key'}
                onChange={event => setOpenrouterApiKey(event.target.value)}
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row-info">
              <div className="settings-row-title">Base URL</div>
              <div className="settings-row-desc">OpenRouter OpenAI-compatible endpoint.</div>
            </div>
            <div className="settings-row-control">
              <input className="form-input" value={openrouterBaseUrl} onChange={event => setOpenrouterBaseUrl(event.target.value)} />
            </div>
          </div>
        </div>
      ) : provider === 'kimi' ? (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Kimi Cloud</span>
            {ai.config?.kimi_api_key_configured && (
              <button className="btn btn-ghost btn-sm" style={{ color: 'var(--color-error)' }} onClick={() => handleClearKey('kimi')}>
                Clear API Key
              </button>
            )}
          </div>
          <div className="settings-row">
            <div className="settings-row-info">
              <div className="settings-row-title">API Key</div>
              <div className="settings-row-desc">
                {ai.config?.kimi_api_key_configured ? `Stored securely (${ai.config.kimi_api_key_hint ?? '****'})` : 'No API key stored.'}
              </div>
            </div>
            <div className="settings-row-control">
              <input
                className="form-input"
                type="password"
                value={kimiApiKey}
                placeholder={ai.config?.kimi_api_key_configured ? 'Leave blank to keep existing key' : 'Paste Kimi API key'}
                onChange={event => setKimiApiKey(event.target.value)}
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row-info">
              <div className="settings-row-title">Base URL</div>
              <div className="settings-row-desc">Moonshot OpenAI-compatible endpoint.</div>
            </div>
            <div className="settings-row-control">
              <input className="form-input" value={kimiBaseUrl} onChange={event => setKimiBaseUrl(event.target.value)} />
            </div>
          </div>
        </div>
      ) : (
        /* OLLAMA LOCAL AI CARD */
        <div className="card">
          <div className="card-header">
            <span className="card-title">Ollama Local AI Diagnostics</span>
            <StatusBadge status={providerStatus} />
          </div>

          <div style={{
            padding: 'var(--space-3)',
            marginBottom: 'var(--space-4)',
            borderRadius: 6,
            background: providerStatus === 'ready' ? 'rgba(52,211,153,0.08)' : providerStatus === 'stopped' ? 'rgba(251,191,36,0.08)' : 'rgba(239,68,68,0.08)',
            borderLeft: `4px solid ${providerStatus === 'ready' ? 'var(--color-success)' : providerStatus === 'stopped' ? 'var(--color-warning)' : 'var(--color-error)'}`,
            fontSize: 'var(--text-sm)',
            color: 'var(--color-text-primary)',
          }}>
            <div style={{ fontWeight: 600, marginBottom: 2 }}>
              {ollamaStateMessage}
            </div>
            {isSelectedModelMissing && (
              <div style={{ color: 'var(--color-error)', marginTop: 4, fontSize: 'var(--text-xs)' }}>
                ⚠ Selected local model &quot;{selectedModel}&quot; is not installed.
              </div>
            )}
          </div>

          <SystemInfoRow label="Installed" value={isOllamaInstalled ? 'Yes' : 'No'} highlight={isOllamaInstalled} />
          <SystemInfoRow label="Service Running" value={isOllamaRunning ? 'Yes (127.0.0.1:11434)' : 'No'} highlight={isOllamaRunning} />
          <SystemInfoRow label="API Responding" value={isApiResponding ? 'Yes' : 'No'} highlight={isApiResponding} />
          <SystemInfoRow label="Models Available" value={areModelsAvailable ? `Yes (${ai.models.length} installed)` : 'No models'} highlight={areModelsAvailable} />
          {ai.status?.executable_path && (
            <SystemInfoRow label="Executable Path" value={ai.status.executable_path} />
          )}
          {ai.status?.provider_version && (
            <SystemInfoRow label="Ollama Version" value={ai.status.provider_version} />
          )}
          {ai.storageInfo?.location && (
            <SystemInfoRow label="Model Storage Location" value={ai.storageInfo.location} />
          )}
          {ai.storageInfo?.drive && (
            <SystemInfoRow label="Storage Drive" value={ai.storageInfo.drive} />
          )}
          {ai.storageInfo?.available_bytes !== undefined && (
            <SystemInfoRow label="Available Space" value={formatBytes(ai.storageInfo.available_bytes)} highlight={true} />
          )}
          {ai.storageInfo?.used_bytes !== undefined && (
            <SystemInfoRow label="Used Space by Models" value={formatBytes(ai.storageInfo.used_bytes)} />
          )}

          <div className="settings-row" style={{ marginTop: 'var(--space-3)' }}>
            <div className="settings-row-info">
              <div className="settings-row-title">Ollama API URL</div>
              <div className="settings-row-desc">Local Ollama endpoint address.</div>
            </div>
            <div className="settings-row-control">
              <input className="form-input" value={ollamaUrl} onChange={event => setOllamaUrl(event.target.value)} />
            </div>
          </div>

          {/* Ollama Process Service Control Block */}
          <div style={{
            marginTop: 'var(--space-4)',
            padding: 'var(--space-3)',
            borderRadius: 6,
            background: 'var(--color-bg-subtle)',
            border: '1px solid var(--color-border-subtle)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-3)' }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', color: 'var(--color-text-primary)' }}>
                  Ollama Service Process
                </div>
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginTop: 2 }}>
                  Background process management for local AI service (ollama serve).
                </div>
              </div>
              <span className={`process-badge process-badge-${ai.processActionState || (ai.processStatus?.state ?? (isOllamaRunning ? 'running_external' : 'stopped'))}`} style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '3px 10px', borderRadius: 4,
                fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.04em',
                background: ai.isProcessOperating || (ai.processStatus?.state === 'starting' || ai.processStatus?.state === 'stopping' || ai.processStatus?.state === 'restarting')
                  ? 'rgba(251,191,36,0.15)'
                  : (ai.processStatus?.state === 'running_managed')
                  ? 'rgba(52,211,153,0.15)'
                  : (ai.processStatus?.state === 'running_external' || (!ai.processStatus?.state && isOllamaRunning))
                  ? 'rgba(59,130,246,0.15)'
                  : (ai.processStatus?.state === 'failed')
                  ? 'rgba(239,68,68,0.15)'
                  : 'rgba(156,163,175,0.15)',
                color: ai.isProcessOperating || (ai.processStatus?.state === 'starting' || ai.processStatus?.state === 'stopping' || ai.processStatus?.state === 'restarting')
                  ? 'var(--color-warning)'
                  : (ai.processStatus?.state === 'running_managed')
                  ? 'var(--color-success)'
                  : (ai.processStatus?.state === 'running_external' || (!ai.processStatus?.state && isOllamaRunning))
                  ? 'var(--color-primary)'
                  : (ai.processStatus?.state === 'failed')
                  ? 'var(--color-error)'
                  : 'var(--color-text-muted)',
              }}>
                {ai.processActionState === 'starting' ? 'STARTING...' :
                 ai.processActionState === 'stopping' ? 'STOPPING...' :
                 ai.processActionState === 'restarting' ? 'RESTARTING...' :
                 ai.processStatus?.state === 'running_managed' ? 'RUNNING — MANAGED' :
                 ai.processStatus?.state === 'running_external' || (!ai.processStatus?.state && isOllamaRunning) ? 'RUNNING — EXTERNAL' :
                 ai.processStatus?.state === 'stopped' || (!ai.processStatus?.state && providerStatus === 'stopped') ? 'STOPPED' :
                 ai.processStatus?.state === 'failed' ? 'START FAILED' :
                 (ai.processStatus?.state ? ai.processStatus.state.toUpperCase() : 'STOPPED')}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 'var(--space-3)', fontSize: 'var(--text-xs)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--color-text-secondary)' }}>Managed by AI Operator:</span>
                <span style={{ fontWeight: 600, color: ai.processStatus?.managed ? 'var(--color-success)' : 'var(--color-text-primary)' }}>
                  {ai.processStatus?.managed ? 'Yes' : 'No'}
                </span>
              </div>
              {ai.processStatus?.managed_pid && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--color-text-secondary)' }}>Managed Process PID:</span>
                  <code style={{ fontWeight: 600, background: 'var(--color-bg-muted)', padding: '1px 5px', borderRadius: 3 }}>
                    {ai.processStatus.managed_pid}
                  </code>
                </div>
              )}
              {ai.processStatus?.executable_path && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--color-text-secondary)' }}>Executable Path:</span>
                  <span style={{ color: 'var(--color-text-muted)', wordBreak: 'break-all', textAlign: 'right' }}>
                    {ai.processStatus.executable_path}
                  </span>
                </div>
              )}
              {ai.processStatus?.storage_location && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--color-text-secondary)' }}>Storage Location:</span>
                  <span style={{ color: 'var(--color-text-muted)', wordBreak: 'break-all', textAlign: 'right' }}>
                    {ai.processStatus.storage_location}
                  </span>
                </div>
              )}
            </div>

            {(ai.processStatus?.state === 'running_external' || (!ai.processStatus?.state && isOllamaRunning && !ai.processStatus?.managed)) && (
              <div style={{
                padding: 'var(--space-2) var(--space-3)',
                borderRadius: 4,
                marginBottom: 'var(--space-3)',
                background: 'rgba(59,130,246,0.08)',
                border: '1px solid rgba(59,130,246,0.2)',
                fontSize: 'var(--text-xs)',
                color: 'var(--color-text-primary)',
              }}>
                ℹ Ollama is running externally. AI Operator will not stop or restart this process.
              </div>
            )}

            {ai.processError && (
              <div style={{
                padding: 'var(--space-2) var(--space-3)',
                borderRadius: 4,
                marginBottom: 'var(--space-3)',
                background: 'rgba(239,68,68,0.08)',
                border: '1px solid var(--color-error)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: 'var(--text-xs)',
                color: 'var(--color-error)',
              }}>
                <span>{ai.processError}</span>
                <button className="btn btn-ghost btn-sm" style={{ padding: '0 4px', height: 'auto' }} onClick={ai.clearProcessError}>
                  ✕
                </button>
              </div>
            )}

            <div style={{ display: 'flex', gap: 'var(--space-2)', justifyContent: 'flex-end' }}>
              {ai.processStatus?.state === 'running_external' || (!ai.processStatus?.state && isOllamaRunning && !ai.processStatus?.managed) ? (
                <button className="btn btn-secondary btn-sm" disabled title="Cannot stop external process">
                  Stop Service (External)
                </button>
              ) : ai.processStatus?.managed ? (
                <>
                  <button
                    className="btn btn-secondary btn-sm"
                    disabled={ai.isProcessOperating}
                    onClick={ai.restartOllamaProcess}
                  >
                    {ai.processActionState === 'restarting' ? 'Restarting...' : 'Restart Service'}
                  </button>
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ color: 'var(--color-error)' }}
                    disabled={ai.isProcessOperating}
                    onClick={ai.stopOllamaProcess}
                  >
                    {ai.processActionState === 'stopping' ? 'Stopping...' : 'Stop Service'}
                  </button>
                </>
              ) : (
                <button
                  className="btn btn-primary btn-sm"
                  disabled={ai.isProcessOperating}
                  onClick={ai.startOllamaProcess}
                >
                  {ai.processActionState === 'starting' ? 'Starting...' : 'Start Service'}
                </button>
              )}
            </div>

            {/* Autostart Ollama toggle */}
            <div style={{
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'space-between',
              marginTop: 'var(--space-3)',
              paddingTop: 'var(--space-3)',
              borderTop: '1px solid var(--color-border-subtle)',
            }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', color: 'var(--color-text-primary)' }}>
                  Autostart Ollama
                </div>
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginTop: 2 }}>
                  Automatically start Ollama when AI Operator starts.
                </div>
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', marginTop: 4, fontStyle: 'italic' }}>
                  This setting takes effect the next time AI Operator starts.
                </div>
              </div>
              <button
                id="ollama-autostart-toggle"
                role="switch"
                aria-checked={ollamaAutostart}
                onClick={() => handleToggleAutostart(!ollamaAutostart)}
                style={{
                  flexShrink: 0,
                  marginLeft: 'var(--space-4)',
                  position: 'relative',
                  width: 40,
                  height: 22,
                  borderRadius: 11,
                  border: 'none',
                  cursor: 'pointer',
                  background: ollamaAutostart ? 'var(--color-primary)' : 'var(--color-border)',
                  transition: 'background 0.2s ease',
                  padding: 0,
                }}
              >
                <span style={{
                  position: 'absolute',
                  top: 3,
                  left: ollamaAutostart ? 21 : 3,
                  width: 16,
                  height: 16,
                  borderRadius: '50%',
                  background: '#fff',
                  transition: 'left 0.2s ease',
                  display: 'block',
                }} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Live Download Progress Card */}
      {provider === 'ollama' && ai.pullStatus?.active && (
        <div className="card" style={{ border: '1px solid var(--color-primary)', background: 'rgba(59,130,246,0.04)' }}>
          <div className="card-header">
            <span className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <span className="spinner" style={{ width: 14, height: 14 }} />
              Downloading Model
            </span>
            <button className="btn btn-secondary btn-sm" style={{ color: 'var(--color-error)' }} onClick={ai.cancelPull}>
              Cancel Download
            </button>
          </div>
          <div style={{ marginBottom: 'var(--space-2)', fontWeight: 600, fontSize: 'var(--text-sm)', color: 'var(--color-text-primary)' }}>
            {ai.pullStatus.model}
          </div>
          <div style={{ width: '100%', height: 10, background: 'var(--color-bg-muted)', borderRadius: 5, overflow: 'hidden', marginBottom: 'var(--space-2)' }}>
            <div style={{
              height: '100%',
              width: typeof ai.pullStatus.percentage === 'number' ? `${ai.pullStatus.percentage}%` : '100%',
              background: 'var(--color-primary)',
              transition: 'width 0.3s ease',
            }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>
            <span>{ai.pullStatus.message || 'Downloading...'}</span>
            <span>
              {typeof ai.pullStatus.percentage === 'number' ? `${ai.pullStatus.percentage}%` : 'Downloading...'}
              {ai.pullStatus.completed ? ` (${formatBytes(ai.pullStatus.completed)}${ai.pullStatus.total ? ` / ${formatBytes(ai.pullStatus.total)}` : ''})` : ''}
            </span>
          </div>
        </div>
      )}

      {/* Disk Space Error Alert */}
      {provider === 'ollama' && ai.pullError && (
        <div className="card" style={{ border: '1px solid var(--color-error)', background: 'rgba(239,68,68,0.06)' }}>
          <div className="card-header">
            <span className="card-title" style={{ color: 'var(--color-error)' }}>Not enough disk space</span>
            <button className="btn btn-ghost btn-sm" onClick={ai.clearPullError}>✕</button>
          </div>
          <p style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-primary)', marginBottom: 'var(--space-2)' }}>
            {ai.pullError.message}
          </p>
          {ai.pullError.required_bytes !== undefined && (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', display: 'flex', flexDirection: 'column', gap: 2 }}>
              <div>Required: ~{formatBytes(ai.pullError.required_bytes)}</div>
              <div>Available: ~{formatBytes(ai.pullError.available_bytes)}</div>
              <div>Model storage: {ai.pullError.drive || 'F:'}</div>
            </div>
          )}
        </div>
      )}

      {/* Selected Model Card */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Active Model Selection</span>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            {provider === 'openrouter' && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setSelectedModel('openrouter/free')}
                title="Select OpenRouter Free Router"
              >
                Use Free Router (openrouter/free)
              </button>
            )}
            <button className="btn btn-secondary btn-sm" onClick={ai.refreshModels}>⟳ Refresh Models</button>
          </div>
        </div>
        <div className="settings-row">
          <div className="settings-row-info">
            <div className="settings-row-title">Selected Model</div>
            <div className="settings-row-desc">
              {ai.models.length
                ? `${ai.models.length} models available for ${providerName}.`
                : 'No models detected yet.'}
              {isFreeRouter ? ' (Using OpenRouter Free Router — rate limits apply)' : ''}
            </div>
          </div>
          <div className="settings-row-control">
            <select className="form-select" value={selectedModel} onChange={event => setSelectedModel(event.target.value)}>
              <option value="">No model selected</option>
              {provider === 'openrouter' && !ai.models.some(m => m.name === 'openrouter/free') && (
                <option value="openrouter/free">openrouter/free (Free Router)</option>
              )}
              {ai.models.map(model => (
                <option key={model.name} value={model.name}>
                  {model.name === 'openrouter/free' ? 'openrouter/free (Free Router)' : model.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Available Local Models Section (Model Management) */}
      {provider === 'ollama' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Model Management</span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
              {ai.models.length} installed in Ollama
            </span>
          </div>
          {ai.models.length === 0 ? (
            <div style={{ padding: 'var(--space-3) 0', fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)' }}>
              No local AI models installed. Choose a recommended model below to download.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
              {ai.models.map(model => {
                const isSelected = selectedModel === model.name;
                return (
                  <div key={model.name} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: 'var(--space-3)', borderRadius: 6,
                    background: isSelected ? 'rgba(59,130,246,0.08)' : 'var(--color-bg-subtle)',
                    border: `1px solid ${isSelected ? 'var(--color-primary)' : 'var(--color-border-subtle)'}`,
                  }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', color: 'var(--color-text-primary)' }}>
                        {model.name}
                      </div>
                      <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)', marginTop: 2 }}>
                        Size: {formatBytes(model.size)} | Modified: {model.modified_at ? new Date(model.modified_at).toLocaleDateString() : 'Unknown'}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                      <button
                        className={`btn btn-sm ${isSelected ? 'btn-primary' : 'btn-secondary'}`}
                        disabled={isSelected}
                        onClick={() => selectAndSaveModel(model.name)}
                      >
                        {isSelected ? '✓ ACTIVE MODEL' : 'Select as Active'}
                      </button>
                      <button
                        className="btn btn-secondary btn-sm"
                        style={{ color: 'var(--color-error)' }}
                        onClick={() => handleRemoveClick(model.name)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Recommended Models Section for User Hardware (Ollama specific) */}
      {provider === 'ollama' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Recommended for your PC</span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
              NVIDIA GTX 1080 Ti (11 GB VRAM) + 24 GB RAM
            </span>
          </div>
          <p style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-3)' }}>
            These models are optimized for your GTX 1080 Ti GPU (11 GB VRAM). Click Download Model to pull automatically.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {RECOMMENDED_OLLAMA_MODELS.map(rec => {
              const isInstalled = ai.models.some(m => m.name === rec.name || m.name.startsWith(`${rec.name}:`));
              const isSelected = selectedModel === rec.name || selectedModel.startsWith(`${rec.name}:`);

              return (
                <div key={rec.name} style={{
                  padding: 'var(--space-3)', borderRadius: 6,
                  background: 'var(--color-bg-subtle)',
                  border: '1px solid var(--color-border-subtle)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                      <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)', color: 'var(--color-text-primary)' }}>
                        {rec.title}
                      </span>
                      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>({rec.name})</span>
                      <span style={{
                        padding: '1px 6px', borderRadius: 4, fontSize: 10, fontWeight: 700,
                        background: isInstalled ? 'rgba(52,211,153,0.15)' : 'rgba(156,163,175,0.15)',
                        color: isInstalled ? 'var(--color-success)' : 'var(--color-text-muted)',
                      }}>
                        {isInstalled ? 'INSTALLED' : 'NOT INSTALLED'}
                      </span>
                    </div>
                    <span style={{ fontSize: 'var(--text-xs)', fontWeight: 600, color: 'var(--color-primary)' }}>
                      {rec.vramReq}
                    </span>
                  </div>
                  <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 6 }}>
                    {rec.description}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <code style={{ fontSize: 'var(--text-xs)', background: 'var(--color-bg-muted)', padding: '2px 6px', borderRadius: 4 }}>
                      ollama pull {rec.name}
                    </code>
                    {isInstalled ? (
                      <button
                        className={`btn btn-sm ${isSelected ? 'btn-primary' : 'btn-secondary'}`}
                        disabled={isSelected}
                        onClick={() => selectAndSaveModel(rec.name)}
                      >
                        {isSelected ? '✓ ACTIVE MODEL' : 'Select as Active'}
                      </button>
                    ) : (
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={ai.pullStatus?.active}
                        onClick={() => ai.startPull(rec.name)}
                      >
                        Download Model
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* AI Status Footer & Actions */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">AI Status Summary</span>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <button className="btn btn-secondary btn-sm" onClick={testConnection}>Test Connection</button>
            <button className="btn btn-primary btn-sm" onClick={save}>Save Configuration</button>
          </div>
        </div>
        <SystemInfoRow label="Provider" value={providerName} />
        <SystemInfoRow label="Connection" value={ai.status?.provider_status ?? 'unknown'} highlight={!!connected} />
        <SystemInfoRow
          label="Selected Model"
          value={ai.status?.model ? (ai.status.model === 'openrouter/free' ? 'openrouter/free (Free Router)' : ai.status.model) : 'None'}
        />
        {provider === 'openrouter' && (
          <SystemInfoRow
            label="Router Mode"
            value={isFreeRouter ? 'Free Router Active (openrouter/free)' : 'Specific Model Mode'}
            highlight={isFreeRouter}
          />
        )}
        <SystemInfoRow label="Ready Status" value={formatAIStatus(ai.status)} highlight={!!connected} />
        {result && <div style={{ color: 'var(--color-success)', marginTop: 'var(--space-2)' }}>{result}</div>}
        {ai.error && <div style={{ color: 'var(--color-error)', marginTop: 'var(--space-2)' }}>{ai.error}</div>}
      </div>

      {/* Confirmation Modal for Active Model Deletion */}
      {deletingModel && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div className="card" style={{ maxWidth: 460, width: '90%', border: '1px solid var(--color-error)' }}>
            <div className="card-header">
              <span className="card-title" style={{ color: 'var(--color-error)' }}>Confirm Active Model Removal</span>
            </div>
            <p style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-primary)', margin: 'var(--space-3) 0' }}>
              You are about to remove the currently active AI model.
            </p>
            <div style={{ padding: 'var(--space-2) var(--space-3)', background: 'var(--color-bg-subtle)', borderRadius: 6, marginBottom: 'var(--space-4)' }}>
              <strong>Model:</strong> <code>{deletingModel}</code>
            </div>
            <p style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-4)' }}>
              The model will no longer be available to the Assistant.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)' }}>
              <button className="btn btn-secondary btn-sm" onClick={() => setDeletingModel(null)}>Cancel</button>
              <button className="btn btn-sm" style={{ background: 'var(--color-error)', color: '#fff' }} onClick={confirmDeleteActiveModel}>
                Remove Model
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const isGood = status === 'running' || status === 'connected' || status === 'ready';
  const isWarn = status === 'loading' || status === 'not_configured' || status === 'no_models' || status === 'stopped';
  const labelMap: Record<string, string> = {
    stopped: 'SERVICE STOPPED',
    not_installed: 'NOT INSTALLED',
    no_models: 'NO MODELS',
    ready: 'READY',
    online: 'OLLAMA RUNNING',
  };
  const label = labelMap[status] ?? status.toUpperCase();
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 8px', borderRadius: 4,
      fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.04em',
      background: isGood ? 'rgba(52,211,153,0.12)' : isWarn ? 'rgba(251,191,36,0.12)' : 'rgba(239,68,68,0.12)',
      color: isGood ? 'var(--color-success)' : isWarn ? 'var(--color-warning)' : 'var(--color-error)',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: isGood ? 'var(--color-success)' : isWarn ? 'var(--color-warning)' : 'var(--color-error)',
        display: 'inline-block',
      }} />
      {label}
    </span>
  );
}

function DiagRow({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div className="info-row">
      <span className="info-key">{label}</span>
      <span className="info-value" style={mono ? { fontFamily: 'monospace', fontSize: 'var(--text-xs)' } : {}}>
        {value ?? '—'}
      </span>
    </div>
  );
}

function fmtDate(d: Date | null): string {
  if (!d) return '—';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtUptime(seconds: number | null): string {
  if (seconds === null) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function DiagnosticsSection() {
  const { diagnostics, refresh } = useDiagnostics();
  const d = diagnostics;

  return (
    <div className="settings-section fade-in">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-4)' }}>
        <div>
          <h2 style={{ fontSize: 'var(--text-lg)', fontWeight: 700, color: 'var(--color-text-primary)', margin: 0 }}>System Diagnostics</h2>
          <p style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', margin: 0 }}>Live status of all core components</p>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={refresh}>⟳ Refresh</button>
      </div>

      {/* Core */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Core Service</span>
          <StatusBadge status={d.coreStatus} />
        </div>
        <DiagRow label="Status" value={d.coreStatus} />
        <DiagRow label="Version" value={d.coreVersion} />
        <DiagRow label="Framework" value="Python + FastAPI (asyncio)" />
        <DiagRow label="URL" value={d.coreUrl} mono />
        <DiagRow label="PID" value={d.corePid !== null ? String(d.corePid) : null} />
        <DiagRow label="Uptime" value={fmtUptime(d.coreUptimeSeconds)} />
        <DiagRow label="Start Time" value={d.coreStartTime ?? null} mono />
        <DiagRow label="Last Health Check" value={fmtDate(d.coreLastCheck)} />
        <DiagRow label="Last Successful Check" value={fmtDate(d.lastSuccessfulHealthCheck)} />
        {d.coreLastError && (
          <div className="info-row">
            <span className="info-key">Last Error</span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-error)', fontFamily: 'monospace' }}>
              {d.coreLastError}
            </span>
          </div>
        )}
        {d.lastNetworkError && (
          <div className="info-row">
            <span className="info-key">Last Network Error</span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-error)', fontFamily: 'monospace' }}>
              {fmtDate(d.lastNetworkError.timestamp)} — {d.lastNetworkError.message}
            </span>
          </div>
        )}
      </div>

      {/* Database */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Database</span>
          <StatusBadge status={d.dbStatus} />
        </div>
        <DiagRow label="Status" value={d.dbStatus} />
        <DiagRow label="Engine" value={d.dbEngine} />
        <DiagRow label="Migration Status" value={d.dbMigrationStatus} />
        <DiagRow label="Schema Version" value={d.dbSchemaVersion} />
        {d.dbLastError && (
          <div className="info-row">
            <span className="info-key">Last Error</span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-error)', fontFamily: 'monospace' }}>
              {d.dbLastError}
            </span>
          </div>
        )}
      </div>

      {/* AI Provider */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">AI Provider</span>
          <StatusBadge status={d.aiStatus} />
        </div>
        <DiagRow label="Status" value={d.aiStatus} />
        <DiagRow label="Provider" value={d.aiProvider} />
        <DiagRow label="Configured" value={d.aiConfigured ? 'Yes' : 'No — set key in AI tab'} />
        <DiagRow label="Selected Model" value={d.aiSelectedModel} />
        <DiagRow label="Message" value={d.aiMessage} />
        {d.lastProviderError && (
          <>
            <DiagRow label="Last Error Code" value={d.lastProviderError.code} mono />
            <DiagRow label="Provider" value={d.lastProviderError.provider} />
            <DiagRow label="Retryable" value={d.lastProviderError.retryable ? 'Yes' : 'No'} />
            <DiagRow label="Error Time" value={fmtDate(d.lastProviderError.timestamp)} />
          </>
        )}
      </div>

      {/* Application */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Application</span>
        </div>
        <DiagRow label="Name" value="AI Operator" />
        <DiagRow label="Version" value={d.appVersion} />
        <DiagRow label="Phase" value="1 — Foundation MVP" />
        <DiagRow label="Stack" value="Tauri v2 + React + TypeScript + Vite" />
      </div>

      {/* Diagnostic Log */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Recent Diagnostic Events</span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }}>
            Last {d.recentLogs.length} events (max 50)
          </span>
        </div>
        {d.recentLogs.length === 0 ? (
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-muted)', padding: 'var(--space-3) 0' }}>
            No diagnostic events recorded yet.
          </div>
        ) : (
          <div style={{ maxHeight: 280, overflowY: 'auto' }}>
            {d.recentLogs.map(log => (
              <div key={log.id} style={{
                display: 'grid', gridTemplateColumns: '80px 70px 70px 1fr', gap: 'var(--space-2)',
                padding: 'var(--space-1) 0', borderBottom: '1px solid var(--color-border-subtle)',
                fontSize: 'var(--text-xs)', fontFamily: 'monospace', alignItems: 'center',
              }}>
                <span style={{ color: 'var(--color-text-muted)' }}>
                  {new Date(log.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
                <span style={{
                  color: log.level === 'error' ? 'var(--color-error)' : log.level === 'warn' ? 'var(--color-warning)' : 'var(--color-success)',
                  fontWeight: 600,
                }}>
                  {log.level.toUpperCase()}
                </span>
                <span style={{ color: 'var(--color-text-muted)' }}>{log.component}</span>
                <span style={{ color: 'var(--color-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {log.endpoint ? `${log.endpoint} ` : ''}{log.message}
                  {log.errorCode ? ` [${log.errorCode}]` : ''}
                  {log.httpStatus ? ` HTTP ${log.httpStatus}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function SettingsView() {
  const [activeTab, setActiveTab] = useState<Tab>('general');
  const { settings, isLoading, isSaving, savedAt, error, updateSettings } = useSettings();
  const { diagnostics } = useCoreStatus();

  const [localTheme, setLocalTheme] = useState('dark');
  const [localLanguage, setLocalLanguage] = useState('en');
  const [localAutonomy, setLocalAutonomy] = useState('manual');
  const [isDirty, setIsDirty] = useState(false);

  // Sync local state when settings load
  useEffect(() => {
    if (!isLoading && settings) {
      setLocalTheme(settings.theme ?? 'dark');
      setLocalLanguage(settings.language ?? 'en');
      setLocalAutonomy(normalizeAutonomyMode(settings.autonomy_mode));
      setIsDirty(false);
    }
  }, [settings, isLoading]);

  const markDirty = () => setIsDirty(true);

  const handleSave = async () => {
    const success = await updateSettings({
      theme: localTheme,
      language: localLanguage,
      autonomy_mode: localAutonomy,
    });
    if (success) setIsDirty(false);
  };

  if (isLoading) {
    return (
      <div className="view-container">
        <div className="view-header">
          <h1 className="view-title">Settings</h1>
        </div>
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 'var(--space-12)' }}>
          <div className="spinner" />
        </div>
      </div>
    );
  }

  if (error && !settings.theme) {
    return (
      <div className="view-container">
        <div className="view-header">
          <h1 className="view-title">Settings</h1>
          <p className="view-subtitle">Configure AI Operator preferences and behavior</p>
        </div>
        <ErrorState title="Settings unavailable" description={error} diagnostics={diagnostics} onRetry={() => window.location.reload()} />
      </div>
    );
  }

  return (
    <div className="view-container fade-in">
      <div className="view-header" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <h1 className="view-title">Settings</h1>
          <p className="view-subtitle">Configure AI Operator preferences and behavior</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', paddingTop: 'var(--space-1)' }}>
          {savedAt && !isDirty && (
            <div className="save-feedback">
              ✓ Saved {savedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </div>
          )}
          {error && (
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-error)' }}>{error}</div>
          )}
          {isDirty && (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSave}
              disabled={isSaving}
            >
              {isSaving ? 'Saving…' : '✓ Save Changes'}
            </button>
          )}
        </div>
      </div>

      <div className="view-body">
        {/* Tabs */}
        <div className="settings-tabs">
          {(['general', 'autonomy', 'ai', 'system'] as Tab[]).map(tab => (
            <button
              key={tab}
              className={`settings-tab${activeTab === tab ? ' active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        {/* General Tab */}
        {activeTab === 'general' && (
          <div className="settings-section fade-in">
            <div className="settings-row">
              <div className="settings-row-info">
                <div className="settings-row-title">Theme</div>
                <div className="settings-row-desc">Choose the application appearance</div>
              </div>
              <div className="settings-row-control">
                <select
                  className="form-select"
                  value={localTheme}
                  onChange={e => { setLocalTheme(e.target.value); markDirty(); }}
                >
                  <option value="dark">Dark</option>
                  <option value="light">Light (coming soon)</option>
                  <option value="system">System Default (coming soon)</option>
                </select>
              </div>
            </div>

            <div className="settings-row">
              <div className="settings-row-info">
                <div className="settings-row-title">Language</div>
                <div className="settings-row-desc">Interface language (more languages coming soon)</div>
              </div>
              <div className="settings-row-control">
                <select
                  className="form-select"
                  value={localLanguage}
                  onChange={e => { setLocalLanguage(e.target.value); markDirty(); }}
                >
                  <option value="en">English</option>
                  <option value="pl">Polski</option>
                </select>
              </div>
            </div>
          </div>
        )}

        {/* Autonomy Tab */}
        {activeTab === 'autonomy' && (
          <div className="settings-section fade-in">
            <div className="card" style={{ marginBottom: 'var(--space-2)' }}>
              <div className="card-header">
                <span className="card-title">Autonomy Mode</span>
              </div>
              <p style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-4)' }}>
                Controls how much AI Operator can act independently. This setting will gate the Permission Engine in a future phase.
                Your choice is saved immediately and will be enforced when agent capabilities are unlocked.
              </p>
              <div className="autonomy-grid">
                {AUTONOMY_MODES.map(mode => (
                  <div
                    key={mode.key}
                    className={`autonomy-card${localAutonomy === mode.key ? ' selected' : ''}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => { setLocalAutonomy(mode.key); markDirty(); }}
                    onKeyDown={event => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        setLocalAutonomy(mode.key);
                        markDirty();
                      }
                    }}
                  >
                    <div className="autonomy-card-header">
                      <div className="autonomy-card-title">{mode.title}</div>
                      <div className="autonomy-card-check">
                        {localAutonomy === mode.key && (
                          <span style={{ color: 'white', fontSize: 10, fontWeight: 700 }}>✓</span>
                        )}
                      </div>
                    </div>
                    <div className="autonomy-card-desc">{mode.desc}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="card" style={{ borderColor: 'var(--color-warning-dim)' }}>
              <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-start' }}>
                <span style={{ fontSize: 20, color: 'var(--color-warning)' }}>⚠</span>
                <div>
                  <div style={{ fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-1)' }}>
                    Autonomy Mode
                  </div>
                  <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
                    Your chosen mode controls how the Permission Engine evaluates tool requests:
                    Manual asks for confirmation on every non-safe action; Semi-Autonomous allows safe and low-risk actions automatically; Autonomous additionally allows medium-risk actions. High and critical-risk actions always require approval.
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'ai' && <AISettingsSection />}

        {/* System / Diagnostics Tab */}
        {activeTab === 'system' && <DiagnosticsSection />}
      </div>
    </div>
  );
}
