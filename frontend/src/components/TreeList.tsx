import { useState } from "react";
import { useStore, actions } from "../store";
import { send, WS } from "../ws";

export default function TreeList({ onCollapse }: { onCollapse: () => void }) {
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
        <span>RepoEvolve</span>
        <div style={{ display: "flex", gap: 4 }}>
          <button className="branch-btn" onClick={() => actions.toggleSettings()} title="Settings">
            ⚙
          </button>
          <button className="branch-btn" onClick={onCollapse} title="Collapse sidebar">
            ✕
          </button>
        </div>
      </div>
      <div className="tree-list-create">
        <input
          placeholder="New tree..."
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <button onClick={create}>+</button>
      </div>
      <div className="tree-list-items">
        {trees.map((t) => (
          <div
            key={t.id}
            className={`tree-item ${t.id === currentTreeId ? "active" : ""}`}
            onClick={() => {
              useStore.setState({ currentTreeId: t.id });
              send({ type: WS.LOAD_TREE, tree_id: t.id });
              send({ type: WS.SELECT_TREE, tree_id: t.id });
            }}
          >
            <span>{t.name}</span>
            <button
              className="delete-btn"
              onClick={(e) => {
                e.stopPropagation();
                send({ type: WS.DELETE_TREE, tree_id: t.id });
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
