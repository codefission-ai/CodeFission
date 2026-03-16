import { useState, useEffect } from "react";
import { actions } from "../store";
import { send, WS } from "../ws";

interface BrowseEntry {
  name: string;
  path: string;
  is_git: boolean;
}

interface BrowseResult {
  current: string;
  parent: string | null;
  entries: BrowseEntry[];
}

function FolderBrowser({ onSelect }: { onSelect: (path: string) => void }) {
  const [currentPath, setCurrentPath] = useState("~");
  const [resolvedPath, setResolvedPath] = useState("");
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [entries, setEntries] = useState<BrowseEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    fetch(`/api/browse?path=${encodeURIComponent(currentPath)}`)
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load directory");
        return r.json();
      })
      .then((data: BrowseResult) => {
        setEntries(data.entries);
        setResolvedPath(data.current);
        setParentPath(data.parent);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [currentPath]);

  return (
    <div className="folder-browser">
      <div className="folder-browser-header">
        <span className="folder-browser-path" title={resolvedPath}>
          {resolvedPath || currentPath}
        </span>
      </div>
      <div className="folder-browser-toolbar">
        {parentPath && (
          <button
            className="folder-browser-up"
            onClick={() => setCurrentPath(parentPath)}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="7 3 7 11" />
              <polyline points="3 7 7 3 11 7" />
            </svg>
            Parent
          </button>
        )}
      </div>
      {error && <div className="folder-browser-error">{error}</div>}
      <div className="folder-browser-list">
        {loading && <div className="folder-browser-loading">Loading...</div>}
        {!loading && entries.length === 0 && !error && (
          <div className="folder-browser-empty">No subdirectories</div>
        )}
        {!loading &&
          entries.map((e) => (
            <div
              key={e.path}
              className="folder-browser-item"
              onClick={() => setCurrentPath(e.path)}
            >
              <span className="folder-browser-icon">
                {e.is_git ? (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="var(--accent)" strokeWidth="1.5">
                    <path d="M2 4h5l1.5 2H14v8H2V4z" />
                    <circle cx="11" cy="10" r="1.5" fill="var(--accent)" stroke="none" />
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M2 4h5l1.5 2H14v8H2V4z" />
                  </svg>
                )}
              </span>
              <span className="folder-browser-name">{e.name}</span>
              {e.is_git && <span className="folder-browser-git-badge">git</span>}
            </div>
          ))}
      </div>
      <button
        className="project-setup-btn primary folder-browser-select"
        onClick={() => onSelect(resolvedPath || currentPath)}
      >
        Select This Folder
      </button>
    </div>
  );
}

type Source = "local" | "github" | "empty";

export default function ProjectSetup() {
  const [source, setSource] = useState<Source | null>(null);
  const [folderPath, setFolderPath] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [cloneName, setCloneName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleCancel = () => {
    actions.stopCreatingProject();
  };

  const handleBack = () => {
    setSource(null);
    setError("");
    setLoading(false);
  };

  // Flow 1 & 2: Open local folder (auto-inits git if needed)
  const handleOpenLocal = () => {
    const path = folderPath.trim();
    if (!path) return;
    send({ type: WS.OPEN_REPO, repo_path: path });
    actions.stopCreatingProject();
  };

  // Flow 3: Empty project
  const handleCreateEmpty = async () => {
    const name = projectName.trim();
    if (!name) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        `/api/create-empty-project?name=${encodeURIComponent(name)}`,
        { method: "POST" }
      );
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to create project");
      }
      const data = await res.json();
      send({ type: WS.OPEN_REPO, repo_path: data.path });
      actions.stopCreatingProject();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // Flow 4: Clone from GitHub
  const deriveCloneName = () => {
    if (cloneName.trim()) return cloneName.trim();
    if (githubUrl.trim()) {
      const match = githubUrl.trim().match(/\/([^/]+?)(?:\.git)?$/);
      return match ? match[1] : "";
    }
    return "";
  };

  const handleClone = async () => {
    const url = githubUrl.trim();
    if (!url) return;
    const name = deriveCloneName();
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ url });
      if (name) params.set("name", name);
      const res = await fetch(`/api/clone?${params.toString()}`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Clone failed");
      }
      const data = await res.json();
      send({ type: WS.OPEN_REPO, repo_path: data.path });
      actions.stopCreatingProject();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="project-setup">
      {/* Step 1: Choose source */}
      {source === null && (
        <div className="project-setup-choose">
          <h2>New Project</h2>
          <p className="project-setup-hint">Choose how to start your project</p>

          <div className="project-setup-options">
            <div className="project-setup-option" onClick={() => setSource("local")}>
              <div className="project-setup-option-icon">
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M4 8h10l2 3h12v15H4V8z" />
                </svg>
              </div>
              <div className="project-setup-option-label">Local Folder</div>
              <div className="project-setup-option-desc">Open an existing git repo or folder</div>
            </div>

            <div className="project-setup-option" onClick={() => setSource("github")}>
              <div className="project-setup-option-icon">
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="16" cy="16" r="12" />
                  <path d="M12 22c0-3 2-4 4-4s4 1 4 4" />
                  <circle cx="16" cy="13" r="3" />
                </svg>
              </div>
              <div className="project-setup-option-label">GitHub Repo</div>
              <div className="project-setup-option-desc">Clone from a GitHub URL</div>
            </div>

            <div className="project-setup-option" onClick={() => setSource("empty")}>
              <div className="project-setup-option-icon">
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="6" y="6" width="20" height="20" rx="2" />
                  <line x1="16" y1="11" x2="16" y2="21" />
                  <line x1="11" y1="16" x2="21" y2="16" />
                </svg>
              </div>
              <div className="project-setup-option-label">Empty Project</div>
              <div className="project-setup-option-desc">Start from scratch</div>
            </div>
          </div>

          <button className="project-setup-btn secondary" onClick={handleCancel}>
            Cancel
          </button>
        </div>
      )}

      {/* Flow 1 & 2: Local Folder */}
      {source === "local" && (
        <div className="project-setup-local">
          <h2>Open Local Folder</h2>
          <p className="project-setup-hint">
            Type a path directly or browse to select a folder.
            Non-git folders will be automatically initialized.
          </p>

          <div className="project-setup-folder-input">
            <input
              autoFocus
              type="text"
              placeholder="/path/to/your/project..."
              value={folderPath}
              onChange={(e) => setFolderPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && folderPath.trim()) handleOpenLocal();
                if (e.key === "Escape") handleBack();
              }}
            />
            <button
              className="project-setup-btn primary"
              disabled={!folderPath.trim()}
              onClick={handleOpenLocal}
            >
              Open
            </button>
          </div>

          <FolderBrowser onSelect={(path) => {
            setFolderPath(path);
          }} />

          <div className="project-setup-actions">
            <button className="project-setup-btn secondary" onClick={handleBack}>
              Back
            </button>
          </div>
        </div>
      )}

      {/* Flow 4: GitHub Clone */}
      {source === "github" && (
        <div className="project-setup-github">
          <h2>Clone GitHub Repo</h2>
          <p className="project-setup-hint">Enter the repository URL to clone</p>

          {error && <div className="project-setup-error">{error}</div>}

          <div className="project-setup-name-input">
            <label>Repository URL</label>
            <input
              autoFocus
              type="text"
              placeholder="https://github.com/user/repo.git"
              value={githubUrl}
              onChange={(e) => setGithubUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && githubUrl.trim()) handleClone();
                if (e.key === "Escape") handleBack();
              }}
            />
          </div>

          <div className="project-setup-name-input">
            <label>Project Name (optional)</label>
            <input
              type="text"
              placeholder={deriveCloneName() || "auto-derived from URL"}
              value={cloneName}
              onChange={(e) => setCloneName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && githubUrl.trim()) handleClone();
                if (e.key === "Escape") handleBack();
              }}
            />
            {githubUrl.trim() && (
              <p className="project-setup-name-hint">
                Will be cloned as: <strong>{deriveCloneName() || "cloned-repo"}</strong>
              </p>
            )}
          </div>

          <div className="project-setup-actions">
            <button className="project-setup-btn secondary" onClick={handleBack}>
              Back
            </button>
            <button
              className="project-setup-btn primary"
              disabled={!githubUrl.trim() || loading}
              onClick={handleClone}
            >
              {loading ? "Cloning..." : "Clone"}
            </button>
          </div>
        </div>
      )}

      {/* Flow 3: Empty Project */}
      {source === "empty" && (
        <div className="project-setup-empty">
          <h2>Create Empty Project</h2>
          <p className="project-setup-hint">Start with a new empty git repository</p>

          {error && <div className="project-setup-error">{error}</div>}

          <div className="project-setup-name-input">
            <label>Project Name</label>
            <input
              autoFocus
              type="text"
              placeholder="my-project"
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && projectName.trim()) handleCreateEmpty();
                if (e.key === "Escape") handleBack();
              }}
            />
          </div>

          <div className="project-setup-actions">
            <button className="project-setup-btn secondary" onClick={handleBack}>
              Back
            </button>
            <button
              className="project-setup-btn primary"
              disabled={!projectName.trim() || loading}
              onClick={handleCreateEmpty}
            >
              {loading ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
