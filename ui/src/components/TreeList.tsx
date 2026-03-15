import { useState, useMemo } from "react";
import { useStore, actions } from "../store";
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

function TreeItem({ treeId, name, repoName, isActive }: { treeId: string; name: string; repoName: string | null; isActive: boolean }) {
  const { streamingCount, processCount, hasError } = useTreeActivity(treeId);
  const tree = useStore((s) => s.trees.find((t) => t.id === treeId));
  const staleness = useStore((s) => s.treeStaleness[treeId]);

  return (
    <div
      className={`tree-item ${isActive ? "active" : ""}`}
      onClick={() => {
        // Set context for this tree's repo before loading
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
        {repoName && (
          <span className="tree-repo-name" title={repoName}>
            {repoName}
          </span>
        )}
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
            ⚡{processCount}
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

export default function TreeList() {
  const trees = useStore((s) => s.trees);
  const currentTreeId = useStore((s) => s.currentTreeId);
  const repoContext = useStore((s) => s.repoContext);
  const repoBranches = useStore((s) => s.repoBranches);
  const [name, setName] = useState("");
  const [selectedBranch, setSelectedBranch] = useState("");

  // Default to current branch or first branch
  const defaultBranch = repoBranches.find((b) => b.current)?.name || repoBranches[0]?.name || "main";

  const create = () => {
    const branch = selectedBranch || defaultBranch;
    send({
      type: WS.CREATE_TREE,
      name: name.trim() || "Untitled",
      base_branch: branch,
    });
    setName("");
    setSelectedBranch("");
  };

  // Group trees by repo_name for display
  const grouped = useMemo(() => {
    const groups: Record<string, typeof trees> = {};
    for (const t of trees) {
      const key = t.repo_name || "(no repo)";
      if (!groups[key]) groups[key] = [];
      groups[key].push(t);
    }
    return groups;
  }, [trees]);

  const repoNames = Object.keys(grouped).sort();
  const multiRepo = repoNames.length > 1;

  return (
    <div className="tree-list">
      <div className="tree-list-header">
        <span>{repoContext?.repo_name || "CodeFission"}</span>
      </div>

      {/* Tree creation — only available with a repo context */}
      {repoContext && (
        <div className="tree-list-create">
          <input
            placeholder="New tree..."
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
          {repoBranches.length > 1 && (
            <select
              className="branch-picker"
              value={selectedBranch || defaultBranch}
              onChange={(e) => setSelectedBranch(e.target.value)}
              title="Base branch"
            >
              {repoBranches.map((b) => (
                <option key={b.name} value={b.name}>
                  {b.name}{b.current ? " *" : ""}
                </option>
              ))}
            </select>
          )}
          <button onClick={create} title="Create tree">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="7" y1="2" x2="7" y2="12" />
              <line x1="2" y1="7" x2="12" y2="7" />
            </svg>
          </button>
        </div>
      )}

      {/* Flat list of all trees, optionally grouped by repo */}
      <div className="tree-list-items">
        {multiRepo ? (
          repoNames.map((repoName) => (
            <div key={repoName}>
              <div className="tree-list-header" style={{ marginTop: 8, fontSize: "0.75rem", opacity: 0.6 }}>
                <span>{repoName}</span>
              </div>
              {grouped[repoName].map((t) => (
                <TreeItem
                  key={t.id}
                  treeId={t.id}
                  name={t.name}
                  repoName={null}
                  isActive={t.id === currentTreeId}
                />
              ))}
            </div>
          ))
        ) : (
          trees.map((t) => (
            <TreeItem
              key={t.id}
              treeId={t.id}
              name={t.name}
              repoName={multiRepo ? t.repo_name : null}
              isActive={t.id === currentTreeId}
            />
          ))
        )}
      </div>

      {/* Home dir mode hint */}
      {!repoContext && trees.length === 0 && (
        <div style={{ padding: "16px", opacity: 0.5, fontSize: "0.8rem", textAlign: "center" }}>
          Run <code>fission</code> from a git repo to create trees.
        </div>
      )}
    </div>
  );
}
