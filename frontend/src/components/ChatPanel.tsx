import { useState, useRef, useEffect, useMemo } from "react";
import { useStore } from "../store";
import { send } from "../ws";

export default function ChatPanel() {
  const selectedId = useStore((s) => s.selectedNodeId);
  const nodes = useStore((s) => s.nodes);
  const streaming = useStore((s) => s.streaming);
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const node = selectedId ? nodes[selectedId] : null;
  const isStreaming = selectedId ? streaming[selectedId] : false;

  // Walk root → selected, collect messages
  const messages = useMemo(() => {
    if (!node) return [];
    const path: typeof node[] = [];
    let cur = node;
    while (cur) {
      path.push(cur);
      cur = cur.parent_id ? nodes[cur.parent_id] : undefined!;
    }
    path.reverse();
    const msgs: { role: string; text: string; fromId: string }[] = [];
    for (const n of path) {
      if (n.user_message) msgs.push({ role: "user", text: n.user_message, fromId: n.id });
      if (n.assistant_response) msgs.push({ role: "assistant", text: n.assistant_response, fromId: n.id });
    }
    return msgs;
  }, [node, nodes]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, node?.assistant_response]);

  const handleSend = () => {
    if (!input.trim() || !selectedId || isStreaming) return;
    send({ type: "chat", node_id: selectedId, content: input.trim() });
    setInput("");
  };

  if (!node) {
    return <div className="chat-empty">Select a node to chat</div>;
  }

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <span className="chat-title">{node.label || "root"}</span>
        {isStreaming && <span className="chat-streaming">typing...</span>}
        <button
          className="branch-btn"
          onClick={() => send({ type: "branch", parent_id: selectedId })}
        >
          Branch
        </button>
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-placeholder">Send a message to start.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="msg-role">
              {m.role === "user" ? "You" : "Claude"}
              {m.fromId !== selectedId && (
                <span className="msg-from"> &middot; {nodes[m.fromId]?.label || m.fromId}</span>
              )}
            </div>
            <div className="msg-text">{m.text}</div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="chat-input">
        <textarea
          placeholder="Type a message..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          rows={2}
        />
        <button onClick={handleSend} disabled={!input.trim() || isStreaming}>
          Send
        </button>
      </div>
    </div>
  );
}
