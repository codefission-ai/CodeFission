import { useState, useRef, useEffect, useMemo } from "react";
import { marked } from "marked";
import { useStore, actions, type ToolCall } from "../store";
import { send, WS } from "../ws";

// Configure marked for chat
marked.setOptions({ breaks: true, gfm: true });

function renderMarkdown(text: string): string {
  try {
    return marked.parse(text) as string;
  } catch {
    return text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
}

// ── Tool call config (adapted from WhatTheBot) ─────────────────────

const TOOL_CONFIGS: Record<string, { icon: string; getValue: (a: Record<string, unknown>) => string; style: string }> = {
  Bash:       { icon: "$",     getValue: (a) => String(a.command || ""), style: "terminal" },
  bash:       { icon: "$",     getValue: (a) => String(a.command || ""), style: "terminal" },
  Read:       { icon: "Read",  getValue: (a) => String(a.file_path || a.path || ""), style: "file" },
  read_file:  { icon: "Read",  getValue: (a) => String(a.file_path || a.path || ""), style: "file" },
  Write:      { icon: "Write", getValue: (a) => String(a.file_path || ""), style: "file" },
  write_file: { icon: "Write", getValue: (a) => String(a.file_path || ""), style: "file" },
  Edit:       { icon: "Edit",  getValue: (a) => String(a.file_path || ""), style: "file" },
  edit_file:  { icon: "Edit",  getValue: (a) => String(a.file_path || ""), style: "file" },
  Glob:       { icon: "Glob",  getValue: (a) => String(a.pattern || ""), style: "search" },
  Grep:       { icon: "Grep",  getValue: (a) => String(a.pattern || ""), style: "search" },
};

function ToolCallLine({ tc }: { tc: ToolCall }) {
  const [open, setOpen] = useState(false);
  const cfg = TOOL_CONFIGS[tc.name];
  const st = tc.status;

  if (cfg) {
    // Configured tool: compact one-line display
    const value = cfg.getValue(tc.arguments);
    return (
      <div className={`tool-line ${cfg.style} ${st}`}>
        <span className="tool-line-label">{cfg.icon}</span>
        <span className="tool-line-value" title={value}>{value}</span>
        <span className={`tool-line-status ${st}`}>
          {st === "running" ? "⟳" : st === "done" ? "✓" : "✗"}
        </span>
        {tc.result && st !== "running" && (
          <div className="tool-line-details">
            <button className="tool-line-toggle" onClick={() => setOpen(!open)}>
              {open ? "▾ hide" : "▸ result"}
            </button>
            {open && (
              <pre className={`tool-line-body ${tc.is_error ? "error" : ""}`}>
                {tc.result.length > 2000 ? tc.result.slice(0, 2000) + "\n…" : tc.result}
              </pre>
            )}
          </div>
        )}
      </div>
    );
  }

  // Generic tool: collapsible details
  const argsStr = Object.keys(tc.arguments).length
    ? JSON.stringify(tc.arguments, null, 2)
    : "";

  return (
    <details className={`tool-generic ${st}`}>
      <summary className="tool-generic-summary">
        <span className="tool-generic-name">{tc.name || "tool"}</span>
        {argsStr && <span className="tool-generic-args">{argsStr.slice(0, 80)}</span>}
        <span className={`tool-line-status ${st}`}>
          {st === "running" ? "⟳" : st === "done" ? "✓" : "✗"}
        </span>
      </summary>
      {tc.result && (
        <pre className={`tool-generic-body ${tc.is_error ? "error" : ""}`}>
          {tc.result.length > 2000 ? tc.result.slice(0, 2000) + "\n…" : tc.result}
        </pre>
      )}
    </details>
  );
}

export default function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
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
        {node.git_commit && (
          <button
            className="branch-btn"
            onClick={() => {
              actions.openFilesPanel(node.id);
              send({ type: WS.GET_NODE_FILES, node_id: node.id });
            }}
          >
            Files
          </button>
        )}
        <button
          className="branch-btn"
          onClick={() => send({ type: WS.BRANCH, parent_id: selectedId })}
        >
          Branch
        </button>
        <button className="branch-btn" onClick={onCollapse} title="Collapse panel">
          ✕
        </button>
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
                dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }}
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
