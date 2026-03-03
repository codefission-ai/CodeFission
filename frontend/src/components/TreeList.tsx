import { useState } from "react";
import { useStore } from "../store";
import { send } from "../ws";

export default function TreeList() {
  const trees = useStore((s) => s.trees);
  const currentTreeId = useStore((s) => s.currentTreeId);
  const [name, setName] = useState("");

  const create = () => {
    send({ type: "create_tree", name: name.trim() || "Untitled" });
    setName("");
  };

  return (
    <div className="tree-list">
      <div className="tree-list-header">clawtree</div>
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
              send({ type: "load_tree", tree_id: t.id });
            }}
          >
            <span>{t.name}</span>
            <button
              className="delete-btn"
              onClick={(e) => {
                e.stopPropagation();
                send({ type: "delete_tree", tree_id: t.id });
              }}
            >
              &times;
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
