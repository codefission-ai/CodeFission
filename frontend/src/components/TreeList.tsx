import { useState } from "react";
import { useStore } from "../store";
import { send, WS } from "../ws";

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
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <line x1="3" y1="3" x2="9" y2="9" />
                <line x1="9" y1="3" x2="3" y2="9" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
