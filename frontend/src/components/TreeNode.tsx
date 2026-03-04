import { memo, useState, useCallback, useRef, type ChangeEvent } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode } from "../store";
import { send, WS } from "../ws";
import NodeModal from "./NodeModal";

/** Auto-resize a textarea to fit its content */
function autoResize(e: ChangeEvent<HTMLTextAreaElement>) {
  const ta = e.target;
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + "px";
}

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
  const isRoot = !node.parent_id;
  const [input, setInput] = useState("");
  const [showModal, setShowModal] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const dot =
    isStreaming ? "#34d399" :
    node.status === "error" ? "#f87171" :
    node.status === "done" ? "#6366f1" :
    "#5c5c66";

  const handleSend = useCallback(() => {
    if (!input.trim() || isStreaming) return;
    send({ type: WS.CHAT, node_id: node.id, content: input.trim() });
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "";
  }, [input, isStreaming, node.id]);

  // Root with no message yet: just a textbox
  if (isRoot && !node.user_message) {
    return (
      <div className="tree-node tree-node-root" onClick={(e) => e.stopPropagation()}>
        <Handle type="source" position={Position.Bottom} />
        <textarea
          ref={textareaRef}
          className="tree-node-root-input"
          value={input}
          onChange={(e) => { setInput(e.target.value); autoResize(e); }}
          onFocus={() => actions.selectNode(node.id)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Ask a question..."
          rows={1}
        />
      </div>
    );
  }

  // All nodes (including root once it has a message): collapsible
  return (
    <div
      className={`tree-node ${selected ? "selected" : ""} ${isExpanded ? "expanded" : ""}`}
      onClick={() => {
        actions.selectNode(node.id);
        actions.toggleExpand(node.id);
      }}
    >
      {!isRoot && <Handle type="target" position={Position.Top} />}
      <Handle type="source" position={Position.Bottom} />
      {!isExpanded && <span className="tree-node-dot" style={{ background: dot }} />}
      {!isExpanded && (
        <span className="tree-node-label">
          {isRoot ? truncate(node.user_message, 40) : (node.label || "...")}
        </span>
      )}
      {isExpanded && (
        <div className="tree-node-preview" onClick={(e) => e.stopPropagation()}>
          {node.user_message && (
            <div className="tree-node-user">{truncate(node.user_message, 150)}</div>
          )}
          {(node.assistant_response || isStreaming) && (
            <div
              className={`tree-node-assistant ${!isStreaming && node.assistant_response ? "clickable" : ""}`}
              onClick={() => {
                if (!isStreaming && node.assistant_response) setShowModal(true);
              }}
            >
              {truncate(node.assistant_response, 150)}
              {isStreaming && <span className="stream-cursor" />}
            </div>
          )}
          {!isStreaming && (
            <div className="tree-node-input">
              <textarea
                ref={textareaRef}
                className="tree-node-textarea"
                value={input}
                onChange={(e) => { setInput(e.target.value); autoResize(e); }}
                onFocus={() => actions.selectNode(node.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="Follow up..."
                rows={1}
              />
              <button
                className="tree-node-send"
                onClick={handleSend}
                disabled={!input.trim() || isStreaming}
              >
                Send
              </button>
            </div>
          )}
        </div>
      )}
      {showModal && (
        <NodeModal
          userMessage={node.user_message}
          assistantResponse={node.assistant_response}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  );
}

export default memo(TreeNode);
