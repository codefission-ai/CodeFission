import { useState, useMemo } from "react";
import { useStore } from "../store";
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

  return (
    <div
      className={`tree-item ${isActive ? "active" : ""}`}
      onClick={() => {
        useStore.setState({ currentTreeId: treeId });
        send({ type: WS.LOAD_TREE, tree_id: treeId });
        send({ type: WS.SELECT_TREE, tree_id: treeId });
      }}
    >
      <span className="tree-item-name">
        {name}
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
  const [name, setName] = useState("");

  const create = () => {
    send({
      type: WS.CREATE_TREE,
      name: name.trim() || "Untitled",
    });
    setName("");
  };

  return (
    <div className="tree-list">
      <div className="tree-list-header">
        <span>Clawtree</span>
      </div>
      <div className="tree-list-create">
        <input
          placeholder="New tree..."
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <button onClick={create} title="Create tree">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="7" y1="2" x2="7" y2="12" />
            <line x1="2" y1="7" x2="12" y2="7" />
          </svg>
        </button>
      </div>
      <div className="tree-list-items">
        {trees.map((t) => (
          <TreeItem key={t.id} treeId={t.id} name={t.name} isActive={t.id === currentTreeId} />
        ))}
      </div>
    </div>
  );
}
