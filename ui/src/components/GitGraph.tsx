import { useState, useEffect } from "react";
import { actions, useStore } from "../store";
import { send, WS } from "../ws";

interface GitCommit {
  sha: string;
  short_sha: string;
  parents: string[];
  message: string;
  author: string;
  date: string;
  refs: string[];
  graph: string;  // ASCII art from git log --graph (branch lines)
  trees: { tree_id: string; tree_name: string }[];
  nodes: { node_id: string; tree_id: string; label: string }[];
}

function timeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function GitCommitRow({
  commit,
  onClickTree,
  onClickNode,
  onPlantTree,
}: {
  commit: GitCommit;
  onClickTree: (treeId: string) => void;
  onClickNode: (nodeId: string, treeId: string) => void;
  onPlantTree: (sha: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasTrees = commit.trees.length > 0;
  const hasNodes = commit.nodes.length > 0;
  const hasCF = hasTrees || hasNodes;

  // Colorize the graph art: replace * with colored dot based on CF entities
  const graphArt = commit.graph || "*";

  return (
    <div className="git-graph-commit" onClick={() => setExpanded((e) => !e)}>
      <pre className={`git-graph-art ${hasTrees ? "has-tree" : hasNodes ? "has-node" : ""}`}>{graphArt}</pre>
      <div className="git-graph-content">
        <div className="git-graph-row-main">
          <span className="git-graph-sha">{commit.short_sha}</span>
          <span className="git-graph-message">{commit.message}</span>
          {commit.refs.length > 0 && (
            <span className="git-graph-refs">
              {commit.refs.map((ref) => (
                <span key={ref} className="git-graph-ref-badge">
                  {ref}
                </span>
              ))}
            </span>
          )}
          {hasCF && (
            <span className="git-graph-cf-badges">
              {hasTrees && (
                <span className="git-graph-cf-badge trees">
                  {commit.trees.length} tree{commit.trees.length !== 1 ? "s" : ""}
                </span>
              )}
              {hasNodes && (
                <span className="git-graph-cf-badge nodes">
                  {commit.nodes.length} node{commit.nodes.length !== 1 ? "s" : ""}
                </span>
              )}
            </span>
          )}
        </div>
        <div className="git-graph-meta">
          {commit.author} &middot; {timeAgo(commit.date)}
          {commit.parents.length > 1 && (
            <span className="git-graph-merge-badge">merge</span>
          )}
        </div>
        {expanded && (
          <div className="git-graph-expanded">
            {commit.trees.map((t) => (
              <button
                key={t.tree_id}
                className="git-graph-entity-btn tree"
                onClick={(e) => {
                  e.stopPropagation();
                  onClickTree(t.tree_id);
                }}
              >
                Tree: {t.tree_name}
              </button>
            ))}
            {commit.nodes.map((n) => (
              <button
                key={n.node_id}
                className="git-graph-entity-btn node"
                onClick={(e) => {
                  e.stopPropagation();
                  onClickNode(n.node_id, n.tree_id);
                }}
              >
                Node: {n.label || n.node_id.slice(0, 7)}
              </button>
            ))}
            {!hasCF && (
              <button
                className="git-graph-entity-btn plant"
                onClick={(e) => {
                  e.stopPropagation();
                  onPlantTree(commit.sha);
                }}
              >
                Plant new tree
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function GitGraph({
  project,
}: {
  project: { repoPath: string; repoName: string };
}) {
  const [commits, setCommits] = useState<GitCommit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const trees = useStore((s) => s.trees);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/git-graph/${encodeURIComponent(project.repoPath)}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => setCommits(data.commits || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [project.repoPath]);

  const handleClickTree = (treeId: string) => {
    actions.closeProjectView();
    actions.selectTree(treeId);
    send({ type: WS.LOAD_TREE, tree_id: treeId });
    send({ type: WS.SELECT_TREE, tree_id: treeId });
  };

  const handleClickNode = (nodeId: string, treeId: string) => {
    actions.closeProjectView();
    actions.selectTree(treeId);
    send({ type: WS.LOAD_TREE, tree_id: treeId });
    send({ type: WS.SELECT_TREE, tree_id: treeId });
    // Select the node after a short delay to ensure the tree is loaded
    setTimeout(() => {
      actions.selectNode(nodeId);
    }, 300);
  };

  const handlePlantTree = (sha: string) => {
    // Find the repo_id from existing trees in this project
    const projectTree = trees.find((t) => t.repo_path === project.repoPath);
    const repoId = projectTree?.repo_id || "";
    const defaultBranch = projectTree?.base_branch || "main";

    send({
      type: WS.CREATE_TREE,
      name: "Untitled",
      base_branch: defaultBranch,
      base_commit: sha,
      repo_id: repoId,
      repo_path: project.repoPath,
    });
    actions.closeProjectView();
  };

  return (
    <div className="git-graph">
      <div className="git-graph-header">
        <h2>{project.repoName}</h2>
        <button className="git-graph-close-btn" onClick={() => actions.closeProjectView()}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          >
            <line x1="3" y1="3" x2="11" y2="11" />
            <line x1="11" y1="3" x2="3" y2="11" />
          </svg>
        </button>
      </div>
      <div className="git-graph-list">
        {loading && (
          <div className="git-graph-status">Loading git history...</div>
        )}
        {error && (
          <div className="git-graph-status git-graph-error">Error: {error}</div>
        )}
        {!loading && !error && commits.length === 0 && (
          <div className="git-graph-status">No commits found.</div>
        )}
        {commits.map((commit) => (
          <GitCommitRow
            key={commit.sha}
            commit={commit}
            onClickTree={handleClickTree}
            onClickNode={handleClickNode}
            onPlantTree={handlePlantTree}
          />
        ))}
      </div>
    </div>
  );
}
