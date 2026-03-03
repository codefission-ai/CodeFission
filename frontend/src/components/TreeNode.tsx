import { memo, useState, useCallback } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode } from "../store";
import { send, WS } from "../ws";

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
  const [input, setInput] = useState("");

  const dot =
    isStreaming ? "#34d399" :
    node.status === "error" ? "#f87171" :
    node.status === "done" ? "#6366f1" :
    "#5c5c66";

  const handleSend = useCallback(() => {
    if (!input.trim() || isStreaming) return;
    send({ type: WS.CHAT, node_id: node.id, content: input.trim() });
    setInput("");
  }, [input, isStreaming, node.id]);

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
        <div className="tree-node-preview" onClick={(e) => e.stopPropagation()}>
          {node.user_message && (
            <div className="tree-node-user">{truncate(node.user_message, 150)}</div>
          )}
          {node.assistant_response && (
            <div className="tree-node-assistant">{truncate(node.assistant_response, 150)}</div>
          )}
          <div className="tree-node-input">
            <textarea
              className="tree-node-textarea"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="Follow up..."
              rows={1}
              disabled={isStreaming}
            />
            <button
              className="tree-node-send"
              onClick={handleSend}
              disabled={!input.trim() || isStreaming}
            >
              Send
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(TreeNode);
