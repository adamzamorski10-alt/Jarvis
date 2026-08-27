/**
 * Settings View — General, Autonomy, and System tabs.
 * All changes persist via Python Core API.
 */
import { useState, useEffect } from 'react';
import { useSettings } from '../../hooks/useSettings';
import { useCoreStatus } from '../../hooks/useCoreStatus';
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
    key: 'supervised',
    title: 'Supervised',
    desc: 'AI can perform low-risk actions automatically. Medium and high-risk actions require approval.',
  },
  {
    key: 'controlled',
    title: 'Controlled',
    desc: 'AI can execute most pre-approved actions. Critical actions still require confirmation.',
  },
  {
    key: 'autonomous',
    title: 'Autonomous',
    desc: 'AI works independently within defined policies. All actions remain logged and auditable.',
  },
];

function SystemInfoRow({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="info-row">
      <span className="info-key">{label}</span>
      <span className={`info-value${highlight ? ' status-value success' : ''}`}>{value}</span>
    </div>
  );
}

function AISettingsSection() {
  const ai = useAISettings();
  const [provider, setProvider] = useState('ollama');
  const [ollamaUrl, setOllamaUrl] = useState('');
  const [kimiBaseUrl, setKimiBaseUrl] = useState('');
  const [kimiApiKey, setKimiApiKey] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    if (ai.config) {
      setProvider(ai.config.provider);
      setOllamaUrl(ai.config.ollama_url);
      setKimiBaseUrl(ai.config.kimi_base_url);
      setSelectedModel(ai.config.selected_model ?? '');
      setKimiApiKey('');
    }
  }, [ai.config]);

  const save = async () => {
    await ai.save({
      provider,
      ollama_url: ollamaUrl,
      kimi_base_url: kimiBaseUrl,
      kimi_api_key: kimiApiKey || undefined,
      selected_model: selectedModel || null,
    });
    setKimiApiKey('');
    setResult('Configuration saved');
  };

  const testConnection = async () => {
    await save();
    await ai.testConnection();
    setResult(null);
  };

  const providerName = provider === 'kimi' ? 'Kimi' : 'Ollama';
  const connected = ai.status?.ai_ready;

  if (ai.isLoading && !ai.config) {
    return <div className="spinner" />;
  }

  return (
    <div className="settings-section fade-in">
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

      {provider === 'kimi' ? (
        <div className="card">
          <div className="card-header"><span className="card-title">Kimi Cloud</span></div>
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
        <div className="card">
          <div className="card-header"><span className="card-title">Ollama Connection</span></div>
          <div className="settings-row">
            <div className="settings-row-info">
              <div className="settings-row-title">URL</div>
              <div className="settings-row-desc">Local Ollama API endpoint.</div>
            </div>
            <div className="settings-row-control">
              <input className="form-input" value={ollamaUrl} onChange={event => setOllamaUrl(event.target.value)} />
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <span className="card-title">Model</span>
          <button className="btn btn-secondary btn-sm" onClick={ai.refreshModels}>Refresh Models</button>
        </div>
        <div className="settings-row">
          <div className="settings-row-info">
            <div className="settings-row-title">Selected Model</div>
            <div className="settings-row-desc">{ai.models.length ? `${ai.models.length} models available for ${providerName}.` : 'No models detected yet.'}</div>
          </div>
          <div className="settings-row-control">
            <select className="form-select" value={selectedModel} onChange={event => setSelectedModel(event.target.value)}>
              <option value="">No model selected</option>
              {ai.models.map(model => <option key={model.name} value={model.name}>{model.name}</option>)}
            </select>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <span className="card-title">AI Status</span>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <button className="btn btn-secondary btn-sm" onClick={testConnection}>Test Connection</button>
            <button className="btn btn-primary btn-sm" onClick={save}>Save Configuration</button>
          </div>
        </div>
        <SystemInfoRow label="Provider" value={providerName} />
        <SystemInfoRow label="Connection" value={ai.status?.provider_status ?? 'unknown'} highlight={!!connected} />
        <SystemInfoRow label="Selected Model" value={ai.status?.model ?? 'None'} />
        <SystemInfoRow label="Ready Status" value={formatAIStatus(ai.status)} highlight={!!connected} />
        {result && <div style={{ color: 'var(--color-success)' }}>{result}</div>}
        {ai.error && <div style={{ color: 'var(--color-error)' }}>{ai.error}</div>}
      </div>
    </div>
  );
}

export function SettingsView() {
  const [activeTab, setActiveTab] = useState<Tab>('general');
  const { settings, isLoading, isSaving, savedAt, error, updateSettings } = useSettings();
  const { isOnline, status, coreUrl, diagnostics } = useCoreStatus();

  const [localTheme, setLocalTheme] = useState('dark');
  const [localLanguage, setLocalLanguage] = useState('en');
  const [localAutonomy, setLocalAutonomy] = useState('manual');
  const [isDirty, setIsDirty] = useState(false);

  // Sync local state when settings load
  useEffect(() => {
    if (!isLoading && settings) {
      setLocalTheme(settings.theme ?? 'dark');
      setLocalLanguage(settings.language ?? 'en');
      setLocalAutonomy(settings.autonomy_mode ?? 'manual');
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
                    Permission Engine — Phase 3
                  </div>
                  <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
                    The full autonomy system with risk levels (GREEN/YELLOW/ORANGE/RED), 
                    Approval Center, and Policy Engine will be implemented in Phase 3.
                    Setting your preferred mode now ensures a smooth transition.
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'ai' && <AISettingsSection />}

        {/* System Tab */}
        {activeTab === 'system' && (
          <div className="settings-section fade-in">
            <div className="card">
              <div className="card-header">
                <span className="card-title">Application</span>
              </div>
              <SystemInfoRow label="Name" value="AI Operator" />
              <SystemInfoRow label="Version" value="0.1.0" />
              <SystemInfoRow label="Phase" value="1 — Foundation MVP" />
              <SystemInfoRow label="Stack" value="Tauri + React + TypeScript" />
            </div>

            <div className="card">
              <div className="card-header">
                <span className="card-title">Core Service</span>
              </div>
              <SystemInfoRow
                label="Status"
                value={isOnline ? 'Running' : 'Offline'}
                highlight={isOnline}
              />
              <SystemInfoRow label="URL" value={coreUrl ?? 'Unknown'} />
              <SystemInfoRow label="Framework" value="Python + FastAPI" />
            </div>

            <div className="card">
              <div className="card-header">
                <span className="card-title">Database</span>
              </div>
              <SystemInfoRow
                label="Status"
                value={
                  !isOnline ? 'Unknown'
                  : status?.database.status === 'connected' ? 'Connected'
                  : 'Error'
                }
                highlight={isOnline && status?.database.status === 'connected'}
              />
              <SystemInfoRow label="Engine" value="SQLite (aiosqlite)" />
              <SystemInfoRow label="Location" value="Application local data directory" />
            </div>

            <div className="card">
              <div className="card-header">
                <span className="card-title">AI Provider</span>
              </div>
              <SystemInfoRow label="Status" value="Not Configured" />
              <SystemInfoRow label="Planned" value="Ollama (Phase 2)" />
              <SystemInfoRow label="Models" value="Qwen3 8B, Qwen2.5-Coder 7B, Qwen3-VL 8B" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
