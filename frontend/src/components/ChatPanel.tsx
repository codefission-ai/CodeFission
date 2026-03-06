import { useState, useRef, useEffect, useMemo, memo } from "react";
import { useStore, type CNode } from "../store";
import { send, WS } from "../ws";
import { renderMarkdown } from "../renderMarkdown";
import ToolCallLine from "./ToolCallLine";

// Memoized message bubble — only re-renders when its text changes
const MessageBubble = memo(function MessageBubble({
  role, text, fromId, selectedId, label,
}: {
  role: string; text: string; fromId: string; selectedId: string; label: string;
}) {
  const html = useMemo(() => role === "assistant" ? renderMarkdown(text, fromId) : "", [text, fromId, role]);

  return (
    <div className={`msg ${role}`}>
      <div className="msg-role">
        {role === "user" ? "You" : "Assistant"}
        {fromId !== selectedId && (
          <span className="msg-from"> · {label || fromId}</span>
        )}
      </div>
      {role === "assistant" ? (
        <div className="msg-text" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <div className="msg-text">{text}</div>
      )}
    </div>
  );
});

export default function ChatPanel() {
  const selectedId = useStore((s) => s.selectedNodeId);
  const nodes = useStore((s) => s.nodes);
  const streaming = useStore((s) => s.streaming);
  const toolCalls = useStore((s) => s.toolCalls);
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const node = selectedId ? nodes[selectedId] : null;
  const isStreaming = selectedId ? streaming[selectedId] : false;
  const activeToolCalls = selectedId ? toolCalls[selectedId] || [] : [];

  // Walk root → selected, collect messages
  const messages = useMemo(() => {
    if (!node) return [];
    const path: CNode[] = [];
    let cur: CNode | undefined = node;
    while (cur) {
      path.push(cur);
      cur = cur.parent_id ? nodes[cur.parent_id] : undefined;
    }
    path.reverse();
    const msgs: { role: string; text: string; fromId: string }[] = [];
    for (const n of path) {
      if (n.user_message) msgs.push({ role: "user", text: n.user_message, fromId: n.id });
      if (n.assistant_response) msgs.push({ role: "assistant", text: n.assistant_response, fromId: n.id });
    }
    return msgs;
  }, [node, nodes]);

  // Only auto-scroll when new messages appear or streaming starts, not on every chunk
  const scrollTrigger = messages.length + (isStreaming ? 1 : 0);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [scrollTrigger]);

  const handleSend = () => {
    if (!input.trim() || !selectedId || isStreaming) return;
    send({ type: WS.CHAT, node_id: selectedId, content: input.trim() });
    setInput("");
  };

  if (!node) {
    return <div className="chat-empty">Select a node to chat</div>;
  }

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <span className="chat-title">{node.label || "root"}</span>
        {isStreaming && <span className="chat-streaming">streaming</span>}
      </div>

      <div className="chat-messages">
        {messages.length === 0 && !isStreaming && (
          <div className="chat-placeholder">Send a message to start.</div>
        )}
        {messages.map((m) => (
          <MessageBubble
            key={`${m.fromId}-${m.role}`}
            role={m.role}
            text={m.text}
            fromId={m.fromId}
            selectedId={selectedId!}
            label={nodes[m.fromId]?.label || m.fromId}
          />
        ))}

        {/* Active tool calls during streaming */}
        {isStreaming && activeToolCalls.length > 0 && (
          <div className="tool-calls-block">
            {activeToolCalls.map((tc) => (
              <ToolCallLine key={tc.tool_call_id} tc={tc} />
            ))}
          </div>
        )}

        {/* Streaming dots when waiting for first content */}
        {isStreaming && !node.assistant_response && activeToolCalls.length === 0 && (
          <div className="msg assistant">
            <div className="msg-role">Assistant</div>
            <div className="stream-dots">···</div>
          </div>
        )}

        <div ref={endRef} />
      </div>

      <div className="chat-input">
        <textarea
          ref={textareaRef}
          placeholder="Type a message..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          rows={1}
        />
        <button onClick={handleSend} disabled={!input.trim() || isStreaming}>
          Send
        </button>
      </div>
    </div>
  );
}
