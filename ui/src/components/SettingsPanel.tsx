import { useState, useEffect } from "react";
import { useStore, actions, type CTree, type GlobalDefaults, type ProviderInfo } from "../store";
import { send, WS } from "../ws";

function ProviderAuthStatus({ providers }: { providers: ProviderInfo[] }) {
  return (
    <div className="settings-auth-status">
      {providers.map((p) => (
        <div key={p.id} className="settings-provider-status">
          <div className="settings-provider-header">
            <span className="settings-provider-name">{p.name} {p.version && `v${p.version}`}</span>
            <span className={`settings-tag ${p.installed ? "settings-tag-ok" : "settings-tag-warn"}`}>
              {p.installed ? "\u2713 Installed" : "\u2717 Not installed"}
            </span>
          </div>
          {p.auth.map((a, i) => (
            <div key={i} className="settings-auth-row">
              <span className={a.authenticated ? "settings-auth-ok" : "settings-auth-missing"}>
                {a.authenticated ? "\u2713" : "\u2717"} {a.method}
              </span>
              <span className="settings-auth-detail">{a.detail}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function ProviderApiKeys({ providers }: { providers: ProviderInfo[] }) {
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [savedProvider, setSavedProvider] = useState<string | null>(null);

  const handleSave = (providerId: string) => {
    send({
      type: WS.UPDATE_GLOBAL_SETTINGS,
      provider_api_keys: { [providerId]: keys[providerId] || "" },
    });
    setSavedProvider(providerId);
    setTimeout(() => setSavedProvider(null), 1500);
  };

  return (
    <div className="settings-api-keys">
      <label className="settings-label">CodeFission API Key (optional override)</label>
      <p className="settings-hint">Per-provider API keys override environment variables.</p>
      {providers.map((p) => (
        <div key={p.id} className="settings-api-key-row">
          <span className="settings-api-key-label">{p.name}:</span>
          <input
            className="settings-input settings-api-key-input"
            type="password"
            placeholder="sk-..."
            value={keys[p.id] ?? ""}
            onChange={(e) => setKeys({ ...keys, [p.id]: e.target.value })}
          />
          <button
            className="settings-save-btn settings-save-btn-small"
            onClick={() => handleSave(p.id)}
          >
            {savedProvider === p.id ? "Saved" : "Save"}
          </button>
        </div>
      ))}
    </div>
  );
}

function GlobalSettings({ defaults, providers }: { defaults: GlobalDefaults; providers: ProviderInfo[] }) {
  const [provider, setProvider] = useState(defaults.provider);
  const [model, setModel] = useState(defaults.model);
  const [summaryModel, setSummaryModel] = useState(defaults.summary_model);
  const [dataDir, setDataDir] = useState(defaults.data_dir);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setProvider(defaults.provider);
    setModel(defaults.model);
    setSummaryModel(defaults.summary_model);
    setDataDir(defaults.data_dir);
  }, [defaults]);

  const currentProvider = providers.find((p) => p.id === provider);
  const models = currentProvider?.models ?? [];

  // Reset model when provider changes
  const handleProviderChange = (newProvider: string) => {
    setProvider(newProvider);
    const p = providers.find((x) => x.id === newProvider);
    if (p) {
      setModel(p.default_model);
    }
  };

  const handleSave = () => {
    send({
      type: WS.UPDATE_GLOBAL_SETTINGS,
      default_provider: provider,
      default_model: model,
      summary_model: summaryModel,
      data_dir: dataDir !== defaults.data_dir ? dataDir : undefined,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="settings-section">
      <h3 className="settings-section-title">Global Defaults</h3>
      <p className="settings-hint">Applies to all trees unless overridden.</p>

      <label className="settings-label">Provider</label>
      <select className="settings-select" value={provider} onChange={(e) => handleProviderChange(e.target.value)}>
        {providers.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>

      <label className="settings-label">Model</label>
      <select className="settings-select" value={model} onChange={(e) => setModel(e.target.value)}>
        {models.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>

      <label className="settings-label">Auth Status</label>
      <ProviderAuthStatus providers={providers} />

      <ProviderApiKeys providers={providers} />

      <label className="settings-label">Auto-name Model</label>
      <select className="settings-select" value={summaryModel} onChange={(e) => setSummaryModel(e.target.value)}>
        <option value="">Disabled</option>
        <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5</option>
        <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
        <option value="claude-opus-4-6">Claude Opus 4.6</option>
      </select>
      <p className="settings-hint">Small model used to auto-name new trees. Defaults to cheapest available model. "Disabled" turns off auto-naming.</p>

      <label className="settings-label">Data Directory</label>
      <input
        className="settings-input"
        type="text"
        value={dataDir}
        onChange={(e) => setDataDir(e.target.value)}
      />
      <p className="settings-hint">Where DB and workspaces are stored. Requires restart to take effect.</p>

      <button className="settings-save-btn" onClick={handleSave}>
        {saved ? "Saved" : "Save Global Settings"}
      </button>
    </div>
  );
}

function TreeSettings({ tree, defaults, providers }: { tree: CTree; defaults: GlobalDefaults; providers: ProviderInfo[] }) {
  const [provider, setProvider] = useState(tree.provider);
  const [model, setModel] = useState(tree.model);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setProvider(tree.provider);
    setModel(tree.model);
  }, [tree]);

  const effectiveProvider = provider || defaults.provider;
  const currentProvider = providers.find((p) => p.id === effectiveProvider);
  const models = currentProvider?.models ?? [];

  const handleProviderChange = (val: string) => {
    setProvider(val);
    if (val) {
      const p = providers.find((x) => x.id === val);
      if (p) setModel(p.default_model);
    } else {
      setModel("");
    }
  };

  const handleSave = () => {
    send({
      type: WS.UPDATE_TREE_SETTINGS,
      tree_id: tree.id,
      provider,
      model,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="settings-section">
      <h3 className="settings-section-title">Tree: {tree.name}</h3>
      <p className="settings-hint">Override global defaults for this tree. Leave as "Default" to inherit.</p>

      <label className="settings-label">Provider</label>
      <select className="settings-select" value={provider} onChange={(e) => handleProviderChange(e.target.value)}>
        <option value="">Default ({defaults.provider})</option>
        {providers.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>

      <label className="settings-label">Model</label>
      <select className="settings-select" value={model} onChange={(e) => setModel(e.target.value)}>
        <option value="">Default ({defaults.model})</option>
        {models.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>

      <button className="settings-save-btn" onClick={handleSave}>
        {saved ? "Saved" : "Save Tree Settings"}
      </button>
    </div>
  );
}

export default function SettingsPanel() {
  const show = useStore((s) => s.showSettings);
  const defaults = useStore((s) => s.globalDefaults);
  const providers = useStore((s) => s.providers);
  const tree = useStore((s) => s.trees.find((t) => t.id === s.currentTreeId));

  if (!show) return null;

  return (
    <div className="settings-overlay" onClick={() => actions.toggleSettings()}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <h2>Settings</h2>
          <button className="settings-close-btn" onClick={() => actions.toggleSettings()}>&times;</button>
        </div>
        <div className="settings-body">
          {tree && <TreeSettings tree={tree} defaults={defaults} providers={providers} />}
          {!tree && <p className="settings-hint">Select a tree to configure tree-specific settings.</p>}
          <GlobalSettings defaults={defaults} providers={providers} />
        </div>
      </div>
    </div>
  );
}
