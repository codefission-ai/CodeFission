import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode } from "../store";

function truncate(text: string, max: number): string {
  if (!text || text.length <= max) return text || "";
  return text.slice(0, max) + "...";
}

function TreeNode({ data }: { data: { node: CNode } }) {
  const { node } = data;
  const selectedId = useStore((s) => s.selectedNodeId);
  const isStreaming = useStore((s) => s.streaming[node.id]);
  const isExpanded = useStore((s) => s.expandedNodes[node.id]);
  const selected = selectedId === node.id;

  const dot =
    isStreaming ? "#34d399" :
    node.status === "error" ? "#f87171" :
    node.status === "done" ? "#6366f1" :
    "#5c5c66";

  return (
    <div
      className={`tree-node ${selected ? "selected" : ""} ${isExpanded ? "expanded" : ""}`}
      onClick={() => {
        actions.selectNode(node.id);
        actions.toggleExpand(node.id);
      }}
    >
      {node.parent_id && <Handle type="target" position={Position.Top} />}
      <Handle type="source" position={Position.Bottom} />
      <span className="tree-node-dot" style={{ background: dot }} />
      <span className="tree-node-label">{node.label || "root"}</span>
      {isExpanded && (
        <div className="tree-node-preview">
          {node.user_message && (
            <div className="tree-node-user">{truncate(node.user_message, 150)}</div>
          )}
          {node.assistant_response && (
            <div className="tree-node-assistant">{truncate(node.assistant_response, 150)}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(TreeNode);
