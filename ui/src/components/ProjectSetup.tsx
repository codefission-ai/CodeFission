import { useState } from "react";
import { actions } from "../store";
import { send, WS } from "../ws";

type Step = "choose" | "name";

export default function ProjectSetup() {
  const [step, setStep] = useState<Step>("choose");
  const [source, setSource] = useState<"local" | "github" | "empty">("local");
  const [folderPath, setFolderPath] = useState("");
  const [githubUrl, _setGithubUrl] = useState("");
  const [projectName, setProjectName] = useState("");

  const handleCancel = () => {
    actions.stopCreatingProject();
  };

  const handleChooseLocal = () => {
    setSource("local");
    setStep("name");
  };

  const handleChooseGithub = () => {
    setSource("github");
    setStep("name");
  };

  const handleChooseEmpty = () => {
    setSource("empty");
    setStep("name");
  };

  const deriveName = () => {
    if (projectName.trim()) return projectName.trim();
    if (source === "local" && folderPath.trim()) {
      const parts = folderPath.trim().replace(/\/+$/, "").split("/");
      return parts[parts.length - 1] || "Untitled";
    }
    if (source === "github" && githubUrl.trim()) {
      const match = githubUrl.trim().match(/\/([^/]+?)(?:\.git)?$/);
      return match ? match[1] : "Untitled";
    }
    return "Untitled";
  };

  const handleCreate = () => {
    if (source === "local") {
      const path = folderPath.trim();
      if (!path) return;
      send({ type: WS.OPEN_REPO, repo_path: path });
    } else if (source === "github") {
      // TODO: clone github repo first, then open
      // For now, show the URL in a message
      const url = githubUrl.trim();
      if (!url) return;
      alert("GitHub clone not yet implemented. Clone manually and use Local Folder.");
      return;
    } else {
      // Empty project — create a temp folder
      // For now, just send open_repo with empty path to trigger the backend
      alert("Empty project not yet implemented. Use Local Folder with an existing directory.");
      return;
    }
    actions.stopCreatingProject();
  };

  return (
    <div className="project-setup">
      {step === "choose" && (
        <div className="project-setup-choose">
          <h2>New Project</h2>
          <p className="project-setup-hint">Choose how to start your project</p>

          <div className="project-setup-options">
            <div className="project-setup-option" onClick={() => { setSource("local"); }}>
              <div className="project-setup-option-icon">
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M4 8h10l2 3h12v15H4V8z" />
                </svg>
              </div>
              <div className="project-setup-option-label">Local Folder</div>
              <div className="project-setup-option-desc">Open an existing git repo or folder</div>
            </div>

            <div className="project-setup-option" onClick={handleChooseGithub}>
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

            <div className="project-setup-option" onClick={handleChooseEmpty}>
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

          {/* Local folder inline input — shown when "Local Folder" is clicked */}
          {source === "local" && (
            <div className="project-setup-folder-input">
              <input
                autoFocus
                type="text"
                placeholder="/path/to/your/project..."
                value={folderPath}
                onChange={(e) => setFolderPath(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && folderPath.trim()) handleChooseLocal();
                  if (e.key === "Escape") handleCancel();
                }}
              />
              <button
                className="project-setup-btn primary"
                disabled={!folderPath.trim()}
                onClick={handleChooseLocal}
              >
                Continue
              </button>
            </div>
          )}

          <button className="project-setup-btn secondary" onClick={handleCancel}>
            Cancel
          </button>
        </div>
      )}

      {step === "name" && (
        <div className="project-setup-name">
          <h2>Name Your Project</h2>
          <p className="project-setup-hint">
            {source === "local" && `Opening: ${folderPath}`}
            {source === "github" && `Cloning: ${githubUrl}`}
            {source === "empty" && "Starting a new empty project"}
          </p>

          <div className="project-setup-name-input">
            <label>Project Name</label>
            <input
              autoFocus
              type="text"
              placeholder={deriveName()}
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
                if (e.key === "Escape") setStep("choose");
              }}
            />
            <p className="project-setup-name-hint">
              Auto-detected: <strong>{deriveName()}</strong> — edit above to change
            </p>
          </div>

          <div className="project-setup-actions">
            <button className="project-setup-btn secondary" onClick={() => setStep("choose")}>
              Back
            </button>
            <button className="project-setup-btn primary" onClick={handleCreate}>
              Create Project
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
