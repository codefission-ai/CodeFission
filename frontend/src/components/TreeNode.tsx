import { memo, useState, useCallback, useRef, useLayoutEffect, useMemo, useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode, type ToolCall } from "../store";
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

function RepoSelector({ treeId, locked, onBrowse }: {
  treeId: string;
  locked?: boolean;
  onBrowse?: () => void;
}) {
  const tree = useStore((s) => s.trees.find((t) => t.id === treeId));
  const [mode, setMode] = useState("new");
  const [source, setSource] = useState("");
  const [setting, setSetting] = useState(false);

  if (!tree) return null;

  // Locked once children exist, or already configured with a source
  if (locked || tree.repo_mode !== "new") {
    return <RepoBadge tree={tree} onBrowse={onBrowse} />;
  }

  const needsSource = mode === "local" || mode === "url";

  const handleSet = () => {
    if (setting) return;
    if (needsSource && !source.trim()) return;
    setSetting(true);
    send({
      type: WS.SET_REPO,
      tree_id: treeId,
      repo_mode: mode,
      repo_source: needsSource ? source.trim() : undefined,
    });
  };

  return (
    <div className="repo-selector" onClick={(e) => e.stopPropagation()}>
      <div className="repo-selector-row">
        <select
          className="repo-selector-select"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          disabled={setting}
        >
          <option value="new">Empty repo</option>
          <option value="local">Local path</option>
          <option value="url">Git URL</option>
        </select>
        <button className="repo-selector-btn" onClick={handleSet} disabled={setting}>
          {setting ? "..." : "Set"}
        </button>
      </div>
      {needsSource && (
        <>
          <input
            className="repo-selector-input"
            placeholder={mode === "local" ? "/home/user/project" : "https://github.com/user/repo"}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSet()}
            disabled={setting}
            autoFocus
          />
          {mode === "local" && source && !source.startsWith("/") && (
            <div className="repo-selector-hint">Path should start with /</div>
          )}
        </>
      )}
    </div>
  );
}

const EMPTY_TOOL_CALLS: ToolCall[] = [];

function TreeNode({ data }: { data: { node: CNode } }) {
  const { node } = data;
  const selectedId = useStore((s) => s.selectedNodeId);
  const isStreaming = useStore((s) => s.streaming[node.id]);
  const isExpanded = useStore((s) => s.expandedNodes[node.id]);
  const activeToolCalls = useStore((s) => s.toolCalls[node.id]) ?? EMPTY_TOOL_CALLS;
  const tree = useStore((s) => !node.parent_id ? s.trees.find((t) => t.id === node.tree_id) : undefined);
  const selected = selectedId === node.id;
  const isRoot = !node.parent_id;
  const [input, setInput] = useState("");
  const [showModal, setShowModal] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const responseRef = useRef<HTMLDivElement>(null);

  // Block wheel events so ReactFlow doesn't pan when scrolling the response
  useEffect(() => {
    const el = responseRef.current;
    if (!el) return;
    const stop = (e: WheelEvent) => {
      if (el.scrollHeight > el.clientHeight) e.stopPropagation();
    };
    el.addEventListener("wheel", stop, { passive: false });
    return () => el.removeEventListener("wheel", stop);
  });

  const assistantHtml = useMemo(
    () => node.assistant_response ? renderMarkdown(node.assistant_response) : "",
    [node.assistant_response]
  );

  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = ta.scrollHeight + "px";
    }
  }, [input]);

  const dot =
    isStreaming ? "#16a34a" :
    node.status === "error" ? "#dc2626" :
    node.status === "done" ? "#8a8a96" :
    "#b0b0ba";

  const handleSend = useCallback(() => {
    if (!input.trim() || isStreaming) return;
    send({ type: WS.CHAT, node_id: node.id, content: input.trim() });
    setInput("");
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

  // Root hub: repo selector (locks once children exist) + textbox
  if (isRoot && !node.user_message) {
    const hasChildren = node.children_ids.length > 0;
    return (
      <div className="tree-node tree-node-root" onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); }}>
        <Handle type="source" position={Position.Bottom} />
        <RepoSelector treeId={node.tree_id} locked={hasChildren} onBrowse={handleBrowseRepo} />
        <textarea
          ref={textareaRef}
          className="tree-node-root-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => actions.selectNode(node.id)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Type a message..."
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
        if (!isExpanded) actions.setExpanded(node.id, true);
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
        <div className="tree-node-preview" onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); }}>
          {isRoot && tree && (
            <RepoBadge tree={tree} onBrowse={handleBrowseRepo} />
          )}
          {node.user_message && (
            <div className="tree-node-user">
              {truncate(node.user_message, 150)}
              <button
                className="collapse-btn"
                onClick={(e) => { e.stopPropagation(); actions.setExpanded(node.id, false); }}
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

          {!isStreaming && selected && (
            <>
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
              {node.git_commit && (
                <button className="tree-node-files-btn" onClick={handleOpenFiles}>
                  Files
                </button>
              )}
            </>
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
