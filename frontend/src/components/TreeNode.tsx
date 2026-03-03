import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, type CNode } from "../store";

function TreeNode({ data }: { data: { node: CNode } }) {
  const { node } = data;
  const selectedId = useStore((s) => s.selectedNodeId);
  const isStreaming = useStore((s) => s.streaming[node.id]);
  const selected = selectedId === node.id;

  const dot =
    isStreaming ? "#22c55e" :
    node.status === "error" ? "#ef4444" :
    node.status === "done" ? "#333" :
    "#bbb";

  return (
    <div
      className={`tree-node ${selected ? "selected" : ""}`}
      onClick={() => useStore.setState({ selectedNodeId: node.id })}
    >
      {node.parent_id && <Handle type="target" position={Position.Top} />}
      <Handle type="source" position={Position.Bottom} />
      <span className="tree-node-dot" style={{ background: dot }} />
      <span className="tree-node-label">{node.label || "root"}</span>
    </div>
  );
}

export default memo(TreeNode);
