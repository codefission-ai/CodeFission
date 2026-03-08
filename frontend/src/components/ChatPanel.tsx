import { useState, useRef, useEffect, useMemo } from "react";
import { useStore, actions, type FileQuote } from "../store";
import { send, WS } from "../ws";
import { renderMarkdown } from "../renderMarkdown";
import ToolCallLine from "./ToolCallLine";

function quotePreview(q: FileQuote, nodes: Record<string, any>): string {
  const qnode = nodes[q.nodeId];
  const nodeLabel = qnode?.label || q.nodeId.slice(0, 8);
  const branch = qnode?.git_branch || `ct-${q.nodeId}`;
  const commit = qnode?.git_commit?.slice(0, 12) || "no commit";
  const gitInfo = `branch: ${branch}, commit: ${commit}`;

  switch (q.type) {
    case "node": {
      const parts: string[] = [];
      if (qnode?.user_message) parts.push(`User: ${qnode.user_message.slice(0, 200)}`);
      if (qnode?.assistant_response) parts.push(`Assistant: ${qnode.assistant_response.slice(0, 200)}`);
      return `--- Node: "${nodeLabel}" (${gitInfo}) ---\n${parts.join("\n\n") || "(empty)"}`;
    }
    case "file":
      if (q.content) {
        return `--- File selection: ${q.path} (from "${nodeLabel}", ${gitInfo}) ---\n${q.content.slice(0, 500)}`;
      }
      return `--- File: ${q.path} (from "${nodeLabel}", ${gitInfo}) ---\n[full file contents]`;
    case "folder":
      return `--- Folder: ${q.path}/ (from "${nodeLabel}", ${gitInfo}) ---\n[all files in folder]`;
    case "diff":
      return `--- Diff selection (from "${nodeLabel}", ${gitInfo}) ---\n${(q.content || "").slice(0, 500)}`;
    default:
      return q.label;
  }
}

export default function ChatPanel() {
  const selectedId = useStore((s) => s.selectedNodeId);
  const nodes = useStore((s) => s.nodes);
  const streaming = useStore((s) => s.streaming);
  const toolCalls = useStore((s) => s.toolCalls);
  const pendingQuotes = useStore((s) => selectedId ? (s.pendingQuotes[selectedId] || []) : []);
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
    if (pendingQuotes.length > 0) {
      msg.file_quotes = pendingQuotes.map((q) => ({
        node_id: q.nodeId,
        type: q.type,
        ...(q.path ? { path: q.path } : {}),
        ...(q.content ? { content: q.content } : {}),
      }));
    }
    send(msg);
    setInput("");
    if (pendingQuotes.length > 0) {
      const { [selectedId]: _, ...rest } = useStore.getState().pendingQuotes;
      useStore.setState({ pendingQuotes: rest });
    }
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
            {pendingQuotes.map((q) => (
              <span key={q.id} className="quote-chip" title={quotePreview(q, nodes)}>
                <span className="quote-chip-label">{q.label}</span>
                <button
                  className="quote-chip-remove"
                  onClick={() => actions.removeFileQuote(q.id)}
                  onMouseDown={(e) => e.preventDefault()}
                >&times;</button>
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
