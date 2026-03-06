import { useState, useRef, useEffect, useMemo } from "react";
import { useStore, actions } from "../store";
import { send, WS } from "../ws";
import { renderMarkdown } from "../renderMarkdown";
import ToolCallLine from "./ToolCallLine";

export default function ChatPanel() {
  const selectedId = useStore((s) => s.selectedNodeId);
  const nodes = useStore((s) => s.nodes);
  const streaming = useStore((s) => s.streaming);
  const toolCalls = useStore((s) => s.toolCalls);
  const pendingQuotes = useStore((s) => s.pendingQuotes);
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const node = selectedId ? nodes[selectedId] : null;
  const isStreaming = selectedId ? streaming[selectedId] : false;
  const activeToolCalls = selectedId ? toolCalls[selectedId] || [] : [];

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
  }, [messages.length, node?.assistant_response, activeToolCalls.length]);

  const handleSend = () => {
    if (!input.trim() || !selectedId || isStreaming) return;
    const msg: Record<string, unknown> = { type: WS.CHAT, node_id: selectedId, content: input.trim() };
    if (pendingQuotes.length > 0) msg.quoted_node_ids = pendingQuotes;
    send(msg);
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
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="msg-role">
              {m.role === "user" ? "You" : "Assistant"}
              {m.fromId !== selectedId && (
                <span className="msg-from"> · {nodes[m.fromId]?.label || m.fromId}</span>
              )}
            </div>
            {m.role === "assistant" ? (
              <div
                className="msg-text"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text, m.fromId) }}
              />
            ) : (
              <div className="msg-text">{m.text}</div>
            )}
          </div>
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
        {pendingQuotes.length > 0 && (
          <div className="quote-chips chat-quote-chips">
            {pendingQuotes.map((qid) => (
              <span key={qid} className="quote-chip">
                <span className="quote-chip-label">{nodes[qid]?.label || qid}</span>
                <button
                  className="quote-chip-remove"
                  onClick={() => actions.removeQuote(qid)}
                  onMouseDown={(e) => e.preventDefault()}
                >×</button>
              </span>
            ))}
          </div>
        )}
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
