import { useState, useEffect } from "react";
import { useStore, actions, type CTree, type GlobalDefaults, type ProviderInfo } from "../store";
import { send, WS } from "../ws";

function GlobalSettings({ defaults, providers }: { defaults: GlobalDefaults; providers: ProviderInfo[] }) {
  const [provider, setProvider] = useState(defaults.provider);
  const [model, setModel] = useState(defaults.model);
  const [maxTurns, setMaxTurns] = useState(String(defaults.max_turns));
  const [authMode, setAuthMode] = useState(defaults.auth_mode);
  const [apiKey, setApiKey] = useState(defaults.api_key);
  const [sandbox, setSandbox] = useState(defaults.sandbox);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setProvider(defaults.provider);
    setModel(defaults.model);
    setMaxTurns(String(defaults.max_turns));
    setAuthMode(defaults.auth_mode);
    setApiKey(defaults.api_key);
    setSandbox(defaults.sandbox);
  }, [defaults]);

  const currentProvider = providers.find((p) => p.id === provider);
  const models = currentProvider?.models ?? [];
  const authModes = currentProvider?.auth_modes ?? [];

  // Reset model when provider changes
  const handleProviderChange = (newProvider: string) => {
    setProvider(newProvider);
    const p = providers.find((x) => x.id === newProvider);
    if (p) {
      setModel(p.default_model);
      setAuthMode(p.default_auth_mode);
    }
  };

  const handleSave = () => {
    send({
      type: WS.UPDATE_GLOBAL_SETTINGS,
      default_provider: provider,
      default_model: model,
      default_max_turns: parseInt(maxTurns) || 25,
      auth_mode: authMode,
      api_key: apiKey,
      sandbox,
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

      <label className="settings-label">Max Turns</label>
      <input
        className="settings-input"
        type="number"
        min={1}
        max={200}
        value={maxTurns}
        onChange={(e) => setMaxTurns(e.target.value)}
      />

      <label className="settings-label">Auth Mode</label>
      <select className="settings-select" value={authMode} onChange={(e) => setAuthMode(e.target.value)}>
        {authModes.map((m) => (
          <option key={m} value={m}>{m === "cli" ? "CLI (OAuth)" : m === "api_key" ? "API Key" : m}</option>
        ))}
      </select>

      {authMode === "api_key" && (
        <>
          <label className="settings-label">API Key</label>
          <input
            className="settings-input"
            type="password"
            placeholder="sk-..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </>
      )}

      <label className="settings-label settings-checkbox-label">
        <input
          type="checkbox"
          checked={sandbox}
          onChange={(e) => setSandbox(e.target.checked)}
        />
        Sandbox (restrict file writes to workspace)
      </label>
      <p className="settings-hint">When enabled, uses Landlock (Linux) to prevent the agent from writing files outside its workspace directory.</p>

      <button className="settings-save-btn" onClick={handleSave}>
        {saved ? "Saved" : "Save Global Settings"}
      </button>
    </div>
  );
}

function TreeSettings({ tree, defaults, providers }: { tree: CTree; defaults: GlobalDefaults; providers: ProviderInfo[] }) {
  const [provider, setProvider] = useState(tree.provider);
  const [model, setModel] = useState(tree.model);
  const [maxTurns, setMaxTurns] = useState(tree.max_turns !== null ? String(tree.max_turns) : "");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setProvider(tree.provider);
    setModel(tree.model);
    setMaxTurns(tree.max_turns !== null ? String(tree.max_turns) : "");
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
      max_turns: maxTurns ? parseInt(maxTurns) : null,
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

      <label className="settings-label">Max Turns</label>
      <input
        className="settings-input"
        type="number"
        min={1}
        max={200}
        placeholder={`Default (${defaults.max_turns})`}
        value={maxTurns}
        onChange={(e) => setMaxTurns(e.target.value)}
      />

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
          <button className="settings-close-btn" onClick={() => actions.toggleSettings()}>×</button>
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
