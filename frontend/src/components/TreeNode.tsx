import { memo, useState, useCallback, useRef, useLayoutEffect, useMemo, useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode, type ToolCall, type ProcessInfo, type FileQuote } from "../store";
import { send, WS } from "../ws";
import { renderMarkdown } from "../renderMarkdown";
import ToolCallLine from "./ToolCallLine";
import NodeModal from "./NodeModal";

function truncate(text: string, max: number): string {
  if (!text || text.length <= max) return text || "";
  return text.slice(0, max) + "...";
}

function RepoBadge({ tree, onBrowse }: {
  tree: { repo_mode: string; repo_source: string | null };
  onBrowse?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const label = tree.repo_mode === "new"
    ? "empty repo"
    : tree.repo_source || tree.repo_mode;

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(label);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="repo-badge" onClick={(e) => e.stopPropagation()}>
      <span
        className={`repo-badge-text ${onBrowse ? "repo-badge-browsable" : ""}`}
        title={label}
        onClick={onBrowse}
      >
        {label}
      </span>
      <button className="repo-badge-copy" onClick={handleCopy} title="Copy path">
        {copied ? "ok" : "cp"}
      </button>
    </div>
  );
}

/** Detect input type: github/git URL, local absolute path, or plain message */
function detectInputType(text: string): "url" | "local" | "message" {
  const t = text.trim();
  if (/^https?:\/\//i.test(t) || /^git@/i.test(t) || t.endsWith(".git")) return "url";
  if (t.startsWith("/") || t.startsWith("~")) return "local";
  return "message";
}

const EMPTY_TOOL_CALLS: ToolCall[] = [];
const EMPTY_PROCESSES: ProcessInfo[] = [];

function ProcessList({ nodeId, processes }: { nodeId: string; processes: ProcessInfo[] }) {
  return (
    <div className="node-processes" onClick={(e) => e.stopPropagation()}>
      <div className="node-processes-header">
        <span>Processes ({processes.length})</span>
        <div className="node-processes-actions">
          {processes.length > 1 && (
            <button
              className="node-process-action"
              onClick={() => send({ type: WS.KILL_ALL_PROCESSES, node_id: nodeId })}
              title="Kill all"
            >
              Kill all
            </button>
          )}
          <button
            className="node-process-action"
            onClick={() => send({ type: WS.GET_NODE_PROCESSES, node_id: nodeId })}
            title="Refresh"
          >
            ↻
          </button>
        </div>
      </div>
      {processes.map((p) => (
        <div key={p.pid} className="node-process-item">
          <span className="node-process-cmd" title={p.command}>
            {p.command.length > 60 ? p.command.slice(0, 60) + "…" : p.command}
          </span>
          {p.ports.length > 0 && (
            <span className="node-process-ports">
              {p.ports.map((port) => (
                <span key={port} className="node-process-port">:{port}</span>
              ))}
            </span>
          )}
          <button
            className="node-process-kill"
            onClick={() => send({ type: WS.KILL_PROCESS, node_id: nodeId, pid: p.pid })}
            title={`Kill PID ${p.pid}`}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

function TreeNode({ data }: { data: { node: CNode; descendantCount?: number } }) {
  const { node, descendantCount } = data;
  const selectedId = useStore((s) => s.selectedNodeId);
  const isStreaming = useStore((s) => s.streaming[node.id]);
  const isExpanded = useStore((s) => s.expandedNodes[node.id]);
  const isSubtreeCollapsed = useStore((s) => s.collapsedSubtrees[node.id]);
  const activeToolCalls = useStore((s) => s.toolCalls[node.id]) ?? EMPTY_TOOL_CALLS;
  const processes = useStore((s) => s.nodeProcesses[node.id]) ?? EMPTY_PROCESSES;
  const tree = useStore((s) => !node.parent_id ? s.trees.find((t) => t.id === node.tree_id) : undefined);
  const pendingQuotes = useStore((s) => s.pendingQuotes);
  const quotesForThis = useStore((s) => s.pendingQuotesFor === node.id);
  const selectedHasInput = useStore((s) => {
    if (!s.selectedNodeId) return false;
    const sel = s.nodes[s.selectedNodeId];
    if (!sel) return false;
    // Root hub always has input
    if (!sel.parent_id && !sel.user_message) return true;
    // Expanded + not streaming = has follow-up textarea
    return !!s.expandedNodes[s.selectedNodeId] && !s.streaming[s.selectedNodeId];
  });
  const selected = selectedId === node.id;
  const quotesFromThis = pendingQuotes.filter((q) => q.nodeId === node.id).length;
  const canShowQuote = selectedHasInput && !selected && isExpanded;
  const isRoot = !node.parent_id;
  const parentCommit = useStore((s) => node.parent_id ? s.nodes[node.parent_id]?.git_commit : null);
  const hasCodeChange = !isRoot && !!node.git_commit && node.git_commit !== parentCommit;
  const [input, setInput] = useState("");
  const [showModal, setShowModal] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const responseRef = useRef<HTMLDivElement>(null);

  // Block wheel events so ReactFlow doesn't pan when scrolling the response,
  // but only when this node is selected (focused).
  useEffect(() => {
    const el = responseRef.current;
    if (!el || !selected) return;
    const stop = (e: WheelEvent) => {
      // Let Cmd/Ctrl+Scroll through for canvas zoom
      if (e.metaKey || e.ctrlKey) return;
      if (el.scrollHeight > el.clientHeight) e.stopPropagation();
    };
    el.addEventListener("wheel", stop, { passive: false });
    return () => el.removeEventListener("wheel", stop);
  }, [selected]);

  // Consume pendingInputText from store (e.g. from FilesPanel text selection quotes)
  const pendingInputText = useStore((s) => s.pendingInputText);
  useEffect(() => {
    if (pendingInputText && selected) {
      setInput((prev) => prev + pendingInputText);
      actions.clearPendingInput();
    }
  }, [pendingInputText, selected]);

  const assistantHtml = useMemo(
    () => node.assistant_response ? renderMarkdown(node.assistant_response, node.id) : "",
    [node.assistant_response, node.id]
  );

  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = ta.scrollHeight + "px";
    }
  }, [input, selected]);

  const dot =
    isStreaming ? "#16a34a" :
    node.status === "error" ? "#dc2626" :
    node.status === "done" ? "#8a8a96" :
    "#b0b0ba";

  const handleSend = useCallback(() => {
    if (!input.trim() || isStreaming) return;
    const msg: Record<string, unknown> = { type: WS.CHAT, node_id: node.id, content: input.trim() };
    const quotes = useStore.getState().pendingQuotes;
    if (quotes.length > 0) {
      msg.file_quotes = quotes.map((q: FileQuote) => ({
        node_id: q.nodeId,
        type: q.type,
        ...(q.path ? { path: q.path } : {}),
        ...(q.content ? { content: q.content } : {}),
      }));
    }
    send(msg);
    setInput("");
    if (quotes.length > 0) useStore.setState({ pendingQuotes: [], pendingQuotesFor: null });
  }, [input, isStreaming, node.id]);

  const handleOpenFiles = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    actions.openFilesPanel(node.id);
    send({ type: WS.GET_NODE_FILES, node_id: node.id });
  }, [node.id]);

  const handleBrowseRepo = useCallback(() => {
    actions.openFilesPanel(node.id);
    send({ type: WS.GET_NODE_FILES, node_id: node.id });
  }, [node.id]);

  const [loading, setLoading] = useState(false);
  const inputType = detectInputType(input);
  const hasRepo = tree && tree.repo_mode !== "new";

  const handleRootSend = useCallback(() => {
    if (!input.trim() || isStreaming || loading) return;
    const t = input.trim();
    const type = detectInputType(t);

    if (type === "url" || type === "local") {
      // Set repo, then clear input (chat comes from the next message)
      setLoading(true);
      send({
        type: WS.SET_REPO,
        tree_id: node.tree_id,
        repo_mode: type === "url" ? "url" : "local",
        repo_source: t,
      });
      setInput("");
      // Loading clears when tree_updated comes back (via store)
      setTimeout(() => setLoading(false), 30000); // safety timeout
    } else {
      // Plain message — send as chat (empty repo auto-created)
      handleSend();
    }
  }, [input, isStreaming, loading, node.tree_id, handleSend]);

  // Clear loading when tree updates (repo set succeeded)
  const treeRepoMode = tree?.repo_mode;
  useEffect(() => {
    if (treeRepoMode && treeRepoMode !== "new") setLoading(false);
  }, [treeRepoMode]);

  // Root hub: unified input
  if (isRoot && !node.user_message) {
    return (
      <div className={`tree-node tree-node-root ${selected ? "selected" : ""}`} onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); }}>
        <Handle type="source" position={Position.Bottom} />
        {hasRepo && tree && (
          <RepoBadge tree={tree} onBrowse={handleBrowseRepo} />
        )}
        {pendingQuotes.length > 0 && selected && quotesForThis && (
          <div className="quote-chips">
            {pendingQuotes.map((q) => (
              <span key={q.id} className="quote-chip">
                <span className="quote-chip-label">{q.label}</span>
                <button
                  className="quote-chip-remove"
                  onClick={(e) => { e.stopPropagation(); actions.removeFileQuote(q.id); }}
                  onMouseDown={(e) => e.preventDefault()}
                >&times;</button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={textareaRef}
          className="tree-node-root-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => actions.selectNode(node.id)}
          onDrop={(e) => {
            const files = e.dataTransfer?.files;
            if (files?.length) {
              e.preventDefault();
              // Use the first dropped item's path (webkitRelativePath or name)
              const path = (files[0] as any).path || files[0].name;
              if (path) setInput(path);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleRootSend();
            }
          }}
          placeholder={hasRepo ? "Type a message..." : "Path, URL, or message..."}
          rows={1}
          disabled={loading}
        />
        {input.trim() && !hasRepo && (
          <div className="root-input-hint">
            {inputType === "url" ? "⏎ clone repo" :
             inputType === "local" ? "⏎ copy from path" :
             "⏎ start with empty repo"}
          </div>
        )}
        {loading && <div className="root-input-hint">Setting up repo...</div>}
        <span className="tree-node-worktree-id">{node.id.slice(0, 8)}</span>
      </div>
    );
  }

  // All nodes (including root once it has a message): collapsible
  return (
    <div
      className={`tree-node ${selected ? "selected" : ""} ${isExpanded ? "expanded" : ""} ${isSubtreeCollapsed ? "subtree-collapsed" : ""} ${hasCodeChange ? "has-code" : ""}`}
      onClick={() => {
        actions.selectNode(node.id);
        if (!isExpanded) {
          actions.setExpanded(node.id, true);
          if (isSubtreeCollapsed) actions.toggleSubtreeCollapsed(node.id);
        }
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
      {!isExpanded && processes.length > 0 && (
        <span className="tree-node-proc-badge" title={`${processes.length} running process${processes.length > 1 ? "es" : ""}`}>
          ⚡{processes.length}
        </span>
      )}
      {!isExpanded && isSubtreeCollapsed && descendantCount! > 0 && (
        <span className="subtree-badge subtree-badge-active" title={`${descendantCount} hidden nodes`}>
          +{descendantCount}
        </span>
      )}
      {isExpanded && (
        <div className="tree-node-preview" onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); }}>
          {/* Quote button: hover to quote this node into your message */}
          {canShowQuote && (
            <button
              className={`quote-btn ${quotesFromThis > 0 ? "quoted" : ""}`}
              onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); e.nativeEvent.stopImmediatePropagation(); }}
              onClick={(e) => {
                e.stopPropagation();
                e.nativeEvent.stopImmediatePropagation();
                if (quotesFromThis > 0) {
                  // Remove all quotes from this node
                  const toRemove = useStore.getState().pendingQuotes.filter((q) => q.nodeId === node.id);
                  toRemove.forEach((q) => actions.removeFileQuote(q.id));
                } else {
                  actions.addFileQuote({
                    id: `fq-${Date.now()}`,
                    nodeId: node.id,
                    type: "node",
                    label: node.label || node.id.slice(0, 8),
                  });
                }
              }}
            >
              {quotesFromThis > 0 ? "Quoted \u2713" : "Quote"}
            </button>
          )}
          {isRoot && tree && (
            <RepoBadge tree={tree} onBrowse={handleBrowseRepo} />
          )}
          {node.user_message && (
            <div className="tree-node-user">
              {truncate(node.user_message, 150)}
              <button
                className="collapse-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  actions.setExpanded(node.id, false);
                  if (node.children_ids.length > 0 && !isSubtreeCollapsed) actions.toggleSubtreeCollapsed(node.id);
                }}
                title="Collapse"
              >&#x25B2;</button>
            </div>
          )}
          {/* Assistant response with markdown */}
          {node.assistant_response && (
            <div
              ref={responseRef}
              className={`tree-node-assistant ${selected && !isStreaming ? "clickable" : ""}`}
              onClick={() => { if (selected && !isStreaming) setShowModal(true); }}
            >
              <div
                className="tree-node-response-md"
                dangerouslySetInnerHTML={{ __html: assistantHtml }}
              />
              {isStreaming && <span className="stream-cursor" />}
            </div>
          )}

          {/* Tool calls during streaming */}
          {isStreaming && activeToolCalls.length > 0 && (
            <div className="tool-calls-block">
              {activeToolCalls.map((tc) => (
                <ToolCallLine key={tc.tool_call_id} tc={tc} />
              ))}
            </div>
          )}

          {/* Streaming dots - waiting state */}
          {isStreaming && !node.assistant_response && activeToolCalls.length === 0 && (
            <div className="tree-node-assistant">
              <div className="stream-dots">···</div>
            </div>
          )}

          {/* Action buttons while streaming */}
          {isStreaming && (
            <div className="tree-node-actions">
              <button
                className="tree-node-action-btn cancel"
                onClick={(e) => { e.stopPropagation(); send({ type: WS.CANCEL, node_id: node.id }); }}
              >
                Cancel
              </button>
              {node.user_message && (
                <button
                  className="tree-node-action-btn"
                  onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); send({ type: WS.DUPLICATE, node_id: node.id }); }}
                  title="Re-run this prompt as a sibling"
                >
                  Duplicate
                </button>
              )}
            </div>
          )}

          {!isStreaming && selected && (
            <>
              {pendingQuotes.length > 0 && quotesForThis && (
                <div className="quote-chips">
                  {pendingQuotes.map((q) => (
                    <span key={q.id} className="quote-chip">
                      <span className="quote-chip-label">{q.label}</span>
                      <button
                        className="quote-chip-remove"
                        onClick={(e) => { e.stopPropagation(); actions.removeFileQuote(q.id); }}
                        onMouseDown={(e) => e.preventDefault()}
                      >&times;</button>
                    </span>
                  ))}
                </div>
              )}
              <div className="tree-node-input">
                <textarea
                  ref={textareaRef}
                  className="tree-node-textarea"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
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
              </div>
              <div className="tree-node-actions">
                {node.user_message && (
                  <button
                    className="tree-node-action-btn"
                    onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); send({ type: WS.DUPLICATE, node_id: node.id }); }}
                    title="Re-run this prompt as a sibling"
                  >
                    Duplicate
                  </button>
                )}
                {node.git_commit && (
                  <button className={`tree-node-action-btn ${hasCodeChange ? "files-btn" : ""}`} onClick={handleOpenFiles}>
                    Files
                  </button>
                )}
              </div>
            </>
          )}

          {/* Running processes */}
          {!isStreaming && processes.length > 0 && isExpanded && (
            <ProcessList nodeId={node.id} processes={processes} />
          )}

        </div>
      )}
      {showModal && (
        <NodeModal
          nodeId={node.id}
          userMessage={node.user_message}
          assistantResponse={node.assistant_response}
          onClose={() => setShowModal(false)}
          onQuoteText={(text) => setInput((prev) => prev + (prev ? "\n" : "") + "> " + text.replace(/\n/g, "\n> ") + "\n")}
        />
      )}
      <span className="tree-node-worktree-id">{node.id.slice(0, 8)}</span>
    </div>
  );
}

export default memo(TreeNode, (prev, next) =>
  prev.data.node === next.data.node &&
  prev.data.descendantCount === next.data.descendantCount
);
