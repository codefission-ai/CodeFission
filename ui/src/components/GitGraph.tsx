import type { ReactElement } from "react";
import { useState, useEffect } from "react";
import { actions, useStore } from "../store";
import { send, WS } from "../ws";

// ── Types ───────────────────────────────────────────────────────────────

interface Connection {
  from_lane: number;
  to_lane: number;
  type: "straight" | "merge";
}

interface GitCommit {
  sha: string;
  short_sha: string;
  parents: string[];
  message: string;
  author: string;
  date: string;
  refs: string[];
  lane: number;
  branch_id: number;
  connections: Connection[];
  pass_through: number[];
  trees: { tree_id: string; tree_name: string }[];
  nodes: { node_id: string; tree_id: string; label: string }[];
}

// ── Constants ───────────────────────────────────────────────────────────

const LANE_WIDTH = 16;
const ROW_HEIGHT = 40;
const DOT_RADIUS = 5;
const LANE_COLORS = [
  "#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6",
  "#ec4899", "#06b6d4", "#f97316", "#14b8a6", "#6366f1",
];

function laneColor(id: number): string {
  return LANE_COLORS[id % LANE_COLORS.length];
}

/** Map from lane (column) index to the branch_id that owns it at a given row. */
function buildLaneBranchMap(commits: GitCommit[]): Map<number, number>[] {
  // For each row, map lane -> branch_id so pass-through lines can be colored
  // by the branch that occupies that lane.
  const maps: Map<number, number>[] = [];
  for (let i = 0; i < commits.length; i++) {
    const m = new Map<number, number>();
    // The commit itself occupies its lane
    m.set(commits[i].lane, commits[i].branch_id);
    maps.push(m);
  }
  // Fill in pass-through lanes by scanning which branch occupies each lane.
  // We do this by tracking branch_id per lane across rows.
  const laneOwner = new Map<number, number>(); // lane -> branch_id
  for (let i = 0; i < commits.length; i++) {
    const c = commits[i];
    laneOwner.set(c.lane, c.branch_id);
    for (const pt of c.pass_through) {
      if (laneOwner.has(pt)) {
        maps[i].set(pt, laneOwner.get(pt)!);
      }
    }
  }
  return maps;
}

function laneX(lane: number): number {
  return lane * LANE_WIDTH + LANE_WIDTH / 2;
}

function rowY(rowIndex: number): number {
  return rowIndex * ROW_HEIGHT + ROW_HEIGHT / 2;
}

// ── Helpers ─────────────────────────────────────────────────────────────

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

// ── SVG sub-components ──────────────────────────────────────────────────

function CommitLines({
  commit,
  rowIndex,
  laneBranchMap,
}: {
  commit: GitCommit;
  rowIndex: number;
  laneBranchMap: Map<number, number>;
}) {
  const y = rowY(rowIndex);
  const nextY = y + ROW_HEIGHT;
  const elements: ReactElement[] = [];

  // 1. Draw pass-through lines (active lanes that aren't this commit)
  for (const ptLane of commit.pass_through) {
    const x = laneX(ptLane);
    const branchId = laneBranchMap.get(ptLane) ?? ptLane;
    elements.push(
      <line
        key={`pt-${ptLane}`}
        x1={x} y1={y - ROW_HEIGHT / 2}
        x2={x} y2={y + ROW_HEIGHT / 2}
        stroke={laneColor(branchId)}
        strokeWidth={2}
        strokeLinecap="round"
      />
    );
  }

  // 2. Draw connection lines from this commit toward parents (below)
  for (let ci = 0; ci < commit.connections.length; ci++) {
    const conn = commit.connections[ci];
    const fromX = laneX(conn.from_lane);
    const toX = laneX(conn.to_lane);

    if (conn.type === "straight") {
      elements.push(
        <line
          key={`s-${ci}-${conn.from_lane}-${conn.to_lane}`}
          x1={fromX} y1={y}
          x2={toX} y2={nextY}
          stroke={laneColor(commit.branch_id)}
          strokeWidth={2}
          strokeLinecap="round"
        />
      );
    } else if (conn.type === "merge") {
      // Curved line from this commit down to the merge-source lane.
      // Spread control points wider for bigger lane gaps.
      const gap = Math.abs(conn.to_lane - conn.from_lane);
      const spread = Math.min(0.7, 0.4 + gap * 0.08);
      elements.push(
        <path
          key={`m-${ci}-${conn.from_lane}-${conn.to_lane}`}
          d={`M ${fromX} ${y} C ${fromX} ${y + ROW_HEIGHT * spread}, ${toX} ${nextY - ROW_HEIGHT * spread}, ${toX} ${nextY}`}
          fill="none"
          stroke={laneColor(laneBranchMap.get(conn.to_lane) ?? conn.to_lane)}
          strokeWidth={2}
          strokeLinecap="round"
        />
      );
    }
  }

  return <g>{elements}</g>;
}

function CommitDot({ commit, rowIndex }: { commit: GitCommit; rowIndex: number }) {
  const x = laneX(commit.lane);
  const y = rowY(rowIndex);
  const color = laneColor(commit.branch_id);
  const hasTrees = commit.trees.length > 0;
  const r = hasTrees ? DOT_RADIUS + 2 : DOT_RADIUS;

  return (
    <circle
      cx={x}
      cy={y}
      r={r}
      fill={color}
      stroke="var(--bg-deep, #1a1a2e)"
      strokeWidth={2}
    />
  );
}

// ── Commit info row ─────────────────────────────────────────────────────

function CommitInfoRow({
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

  return (
    <div
      className="git-graph-info-row"
      style={{ height: ROW_HEIGHT }}
      onClick={() => setExpanded((e) => !e)}
    >
      <div className="git-graph-row-main">
        <span className="git-graph-sha">{commit.short_sha}</span>
        <span className="git-graph-message">{commit.message}</span>
        {commit.refs.length > 0 && (
          <span className="git-graph-refs">
            {commit.refs.map((ref) => (
              <span key={ref} className="git-graph-ref-badge">{ref}</span>
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
        <span className="git-graph-time">{timeAgo(commit.date)}</span>
      </div>
      {expanded && (
        <div className="git-graph-expanded">
          {commit.trees.map((t) => (
            <button
              key={t.tree_id}
              className="git-graph-entity-btn tree"
              onClick={(e) => { e.stopPropagation(); onClickTree(t.tree_id); }}
            >
              Tree: {t.tree_name}
            </button>
          ))}
          {commit.nodes.map((n) => (
            <button
              key={n.node_id}
              className="git-graph-entity-btn node"
              onClick={(e) => { e.stopPropagation(); onClickNode(n.node_id, n.tree_id); }}
            >
              Node: {n.label || n.node_id.slice(0, 7)}
            </button>
          ))}
          <button
            className="git-graph-entity-btn plant"
            onClick={(e) => { e.stopPropagation(); onPlantTree(commit.sha); }}
          >
            Plant new tree
          </button>
        </div>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────

export default function GitGraph({
  project,
}: {
  project: { repoPath: string; repoName: string };
}) {
  const [commits, setCommits] = useState<GitCommit[]>([]);
  const [maxLanes, setMaxLanes] = useState(1);
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
      .then((data) => {
        setCommits(data.commits || []);
        setMaxLanes(data.max_lanes || 1);
      })
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
    setTimeout(() => { actions.selectNode(nodeId); }, 300);
  };

  const handlePlantTree = (sha: string) => {
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

  const svgWidth = (maxLanes + 1) * LANE_WIDTH;
  const svgHeight = commits.length * ROW_HEIGHT;
  const laneBranchMaps = commits.length > 0 ? buildLaneBranchMap(commits) : [];

  return (
    <div className="git-graph">
      <div className="git-graph-header">
        <h2>{project.repoName}</h2>
        <button className="git-graph-close-btn" onClick={() => actions.closeProjectView()}>
          <svg
            width="14" height="14" viewBox="0 0 14 14"
            fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
          >
            <line x1="3" y1="3" x2="11" y2="11" />
            <line x1="11" y1="3" x2="3" y2="11" />
          </svg>
        </button>
      </div>
      <div className="git-graph-body">
        {loading && (
          <div className="git-graph-status">Loading git history...</div>
        )}
        {error && (
          <div className="git-graph-status git-graph-error">Error: {error}</div>
        )}
        {!loading && !error && commits.length === 0 && (
          <div className="git-graph-status">No commits found.</div>
        )}
        {!loading && !error && commits.length > 0 && (
          <>
            <svg
              className="git-graph-svg"
              width={svgWidth}
              height={svgHeight}
              style={{ minWidth: svgWidth }}
            >
              {/* Lines behind dots */}
              {commits.map((commit, i) => (
                <CommitLines key={`lines-${commit.sha}`} commit={commit} rowIndex={i} laneBranchMap={laneBranchMaps[i]} />
              ))}
              {/* Dots on top */}
              {commits.map((commit, i) => (
                <CommitDot key={`dot-${commit.sha}`} commit={commit} rowIndex={i} />
              ))}
            </svg>
            <div className="git-graph-info">
              {commits.map((commit) => (
                <CommitInfoRow
                  key={commit.sha}
                  commit={commit}
                  onClickTree={handleClickTree}
                  onClickNode={handleClickNode}
                  onPlantTree={handlePlantTree}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
