import { useState, useMemo } from "react";
import { useStore, actions, type CTree } from "../store";
import { send, WS } from "../ws";

/** Derive per-tree activity summary from global state. */
function useTreeActivity(treeId: string) {
  const nodes = useStore((s) => s.nodes);
  const streaming = useStore((s) => s.streaming);
  const nodeProcesses = useStore((s) => s.nodeProcesses);

  return useMemo(() => {
    let streamingCount = 0;
    let processCount = 0;
    let hasError = false;

    for (const node of Object.values(nodes)) {
      if (node.tree_id !== treeId) continue;
      if (streaming[node.id]) streamingCount++;
      const procs = nodeProcesses[node.id];
      if (procs?.length) processCount += procs.length;
      if (node.status === "error") hasError = true;
    }

    return { streamingCount, processCount, hasError };
  }, [nodes, streaming, nodeProcesses, treeId]);
}

function TreeItem({ treeId, name, isActive }: { treeId: string; name: string; isActive: boolean }) {
  const { streamingCount, processCount, hasError } = useTreeActivity(treeId);
  const tree = useStore((s) => s.trees.find((t) => t.id === treeId));
  const staleness = useStore((s) => s.treeStaleness[treeId]);

  return (
    <div
      className={`tree-item ${isActive ? "active" : ""}`}
      onClick={() => {
        if (tree?.repo_path) {
          send({ type: WS.OPEN_REPO, repo_id: tree.repo_id, head_commit: tree.base_commit, repo_path: tree.repo_path });
          actions.setSidebarOpen(false);
        }
        useStore.setState({ currentTreeId: treeId });
        send({ type: WS.LOAD_TREE, tree_id: treeId });
        send({ type: WS.SELECT_TREE, tree_id: treeId });
      }}
    >
      <span className="tree-item-name">
        <span>{name}</span>
        {tree?.base_branch && (
          <span className="tree-branch-badge" title={`Base: ${tree.base_branch}`}>
            {tree.base_branch}
          </span>
        )}
        {staleness?.stale && (
          <span
            className="tree-activity-dot stale"
            title={`${staleness.commits_behind} new commit${staleness.commits_behind !== 1 ? "s" : ""} on ${tree?.base_branch || "main"}`}
          />
        )}
        {streamingCount > 0 && (
          <span className="tree-activity-dot streaming" title={`${streamingCount} node${streamingCount > 1 ? "s" : ""} streaming`} />
        )}
        {!streamingCount && hasError && (
          <span className="tree-activity-dot error" title="Error" />
        )}
        {processCount > 0 && (
          <span className="tree-activity-badge" title={`${processCount} process${processCount > 1 ? "es" : ""} running`}>
            {processCount}
          </span>
        )}
      </span>
      <button
        className="delete-btn"
        onClick={(e) => {
          e.stopPropagation();
          send({ type: WS.DELETE_TREE, tree_id: treeId });
        }}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <line x1="3" y1="3" x2="9" y2="9" />
          <line x1="9" y1="3" x2="3" y2="9" />
        </svg>
      </button>
    </div>
  );
}

interface ProjectGroup {
  repoId: string;
  repoName: string;
  repoPath: string | null;
  trees: CTree[];
  latestCreatedAt: string;
}

function ProjectSection({ group, isActiveProject, currentTreeId }: {
  group: ProjectGroup;
  isActiveProject: boolean;
  currentTreeId: string | null;
}) {
  const [collapsed, setCollapsed] = useState(!isActiveProject);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  // Auto-expand when this project becomes active
  const wasActive = useState(isActiveProject)[0];
  if (isActiveProject && !wasActive && collapsed) {
    setCollapsed(false);
  }

  const handleCreate = () => {
    if (!group.repoPath) return;
    // Use the first tree's base_branch as default (handles repos where default is "master" etc.)
    const defaultBranch = group.trees[0]?.base_branch || "main";
    send({
      type: WS.CREATE_TREE,
      name: newName.trim() || "Untitled",
      base_branch: defaultBranch,
      repo_id: group.repoId,
      repo_path: group.repoPath,
    });
    setNewName("");
    setCreating(false);
  };

  return (
    <div className="project-section">
      <div
        className={`project-header ${isActiveProject ? "active" : ""}`}
        onClick={() => setCollapsed((c) => !c)}
      >
        <span className="project-chevron">{collapsed ? "\u25B6" : "\u25BC"}</span>
        <span className="project-name" title={group.repoPath || undefined}>{group.repoName}</span>
        <span className="project-count">{group.trees.length}</span>
        {group.repoPath && (
          <button
            className="project-add-btn"
            onClick={(e) => {
              e.stopPropagation();
              setCollapsed(false);
              setCreating(true);
            }}
            title="New tree in this project"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="6" y1="1" x2="6" y2="11" />
              <line x1="1" y1="6" x2="11" y2="6" />
            </svg>
          </button>
        )}
      </div>
      {!collapsed && (
        <div className="project-trees">
          {creating && (
            <div className="project-create-inline">
              <input
                autoFocus
                placeholder="New tree..."
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreate();
                  if (e.key === "Escape") setCreating(false);
                }}
                onBlur={() => { if (!newName.trim()) setCreating(false); }}
              />
              <button onClick={handleCreate} title="Create">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <line x1="6" y1="1" x2="6" y2="11" />
                  <line x1="1" y1="6" x2="11" y2="6" />
                </svg>
              </button>
            </div>
          )}
          {group.trees.map((t) => (
            <TreeItem
              key={t.id}
              treeId={t.id}
              name={t.name}
              isActive={t.id === currentTreeId}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function NewProjectInput() {
  const [adding, setAdding] = useState(false);
  const [folderPath, setFolderPath] = useState("");

  const handleSubmit = () => {
    const path = folderPath.trim();
    if (!path) {
      setAdding(false);
      return;
    }
    send({ type: WS.OPEN_REPO, repo_path: path });
    setFolderPath("");
    setAdding(false);
  };

  if (!adding) {
    return (
      <div className="new-project-bar">
        <button
          className="new-project-btn"
          onClick={() => setAdding(true)}
          title="Open a project folder"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="6" y1="1" x2="6" y2="11" />
            <line x1="1" y1="6" x2="11" y2="6" />
          </svg>
          <span>New Project</span>
        </button>
      </div>
    );
  }

  return (
    <div className="tree-list-create">
      <input
        autoFocus
        placeholder="/path/to/project..."
        value={folderPath}
        onChange={(e) => setFolderPath(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleSubmit();
          if (e.key === "Escape") { setAdding(false); setFolderPath(""); }
        }}
        onBlur={() => { if (!folderPath.trim()) setAdding(false); }}
      />
      <button onClick={handleSubmit} title="Open project">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="2 7 6 11 12 3" />
        </svg>
      </button>
    </div>
  );
}

export default function TreeList() {
  const trees = useStore((s) => s.trees);
  const currentTreeId = useStore((s) => s.currentTreeId);

  // Find the active tree's repo_id
  const activeRepoId = useStore((s) => {
    if (!s.currentTreeId) return null;
    const t = s.trees.find((t) => t.id === s.currentTreeId);
    return t?.repo_id || null;
  });

  // Group trees by repo_id, sorted by recency
  const projectGroups = useMemo(() => {
    const groups: Record<string, ProjectGroup> = {};
    for (const t of trees) {
      const key = t.repo_id || "(no-repo)";
      if (!groups[key]) {
        groups[key] = {
          repoId: t.repo_id || "",
          repoName: t.repo_name || "(no repo)",
          repoPath: t.repo_path,
          trees: [],
          latestCreatedAt: t.created_at,
        };
      }
      groups[key].trees.push(t);
      if (t.created_at > groups[key].latestCreatedAt) {
        groups[key].latestCreatedAt = t.created_at;
      }
    }
    // Sort trees within each group by recency (newest first)
    for (const g of Object.values(groups)) {
      g.trees.sort((a, b) => b.created_at.localeCompare(a.created_at));
    }
    // Sort groups by recency (most recent tree first)
    return Object.values(groups).sort((a, b) =>
      b.latestCreatedAt.localeCompare(a.latestCreatedAt)
    );
  }, [trees]);

  return (
    <div className="tree-list">
      <div className="tree-list-header">
        <span>CodeFission</span>
      </div>

      <NewProjectInput />

      <div className="tree-list-items">
        {projectGroups.map((group) => (
          <ProjectSection
            key={group.repoId || group.repoName}
            group={group}
            isActiveProject={group.repoId === activeRepoId}
            currentTreeId={currentTreeId}
          />
        ))}
      </div>

      {trees.length === 0 && (
        <div style={{ padding: "16px", opacity: 0.5, fontSize: "0.8rem", textAlign: "center" }}>
          Run <code>fission</code> from a git repo to create trees.
        </div>
      )}
    </div>
  );
}
