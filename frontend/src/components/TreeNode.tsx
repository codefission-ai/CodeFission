import { memo, useState, useCallback, useRef, useLayoutEffect, useMemo, useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode, type ToolCall, type ProcessInfo, type FileQuote, isDetachable, getSubtreeIds } from "../store";
import { send, WS } from "../ws";
import { renderMarkdown } from "../renderMarkdown";
import ToolCallLine from "./ToolCallLine";
import NodeModal from "./NodeModal";
import { collectDroppedFiles, uploadFiles, useFileAttach, formatFileSize } from "../fileAttach";

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

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="root-section-copy"
      onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
      onMouseDown={(e) => e.preventDefault()}
    >
      {copied ? "ok" : "cp"}
    </button>
  );
}

function TreeNode({ data }: { data: { node: CNode; descendantCount?: number } }) {
  const { node, descendantCount } = data;
  const selectedId = useStore((s) => s.selectedNodeId);
  const isStreaming = useStore((s) => s.streaming[node.id]);
  const isExpanded = useStore((s) => s.expandedNodes[node.id]);
  const isSubtreeCollapsed = useStore((s) => s.collapsedSubtrees[node.id]);
  const isLeaf = useStore((s) => isDetachable(s.nodes, node.id, s.pendingDeleteNodes));
  const hasVisibleChildren = useStore((s) => node.children_ids.some((cid) => !s.pendingDeleteNodes.has(cid)));
  const activeToolCalls = useStore((s) => s.toolCalls[node.id]) ?? EMPTY_TOOL_CALLS;
  const processes = useStore((s) => s.nodeProcesses[node.id]) ?? EMPTY_PROCESSES;
  const tree = useStore((s) => !node.parent_id ? s.trees.find((t) => t.id === node.tree_id) : undefined);
  const pendingQuotes = useStore((s) => s.pendingQuotes);
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
  const attach = useFileAttach({ treeId: node.tree_id, parentNodeId: node.id });

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

  const handleSend = useCallback(async () => {
    if (attach.uploading || isStreaming) return;
    if (!input.trim() && attach.pendingFiles.length === 0) return;

    // Consume draft (files already uploaded to draft workspace)
    const { draftNodeId, uploadedQuotes } = attach.consumeDraft();
    let fileQuotes = uploadedQuotes;

    // Fallback: if no draft was used, upload to parent (legacy path)
    if (!draftNodeId && attach.pendingFiles.length > 0) {
      const result = await attach.uploadAndQuote(node.tree_id, node.id);
      if (!result) return;
      fileQuotes = result.quotes;
      actions.updateNodeGit(node.id, result.git_commit);
    }

    // Build message: append uploaded file paths so the LLM knows they exist in the workspace
    let content = input.trim();
    if (fileQuotes.length > 0) {
      const paths = fileQuotes.map((q) => q.path);
      const fileLine = `[Attached files in workspace: ${paths.join(", ")}]`;
      content = content ? `${content}\n\n${fileLine}` : fileLine;
    }
    if (!content) return;
    const msg: Record<string, unknown> = { type: WS.CHAT, node_id: node.id, content };
    if (draftNodeId) msg.draft_node_id = draftNodeId;

    // Cross-branch quotes (node, file, folder references — NOT uploaded files)
    const pendingQ = useStore.getState().pendingQuotes;
    const allQuotes = pendingQ.map((q: FileQuote) => ({
      node_id: q.nodeId,
      type: q.type,
      ...(q.path ? { path: q.path } : {}),
      ...(q.content ? { content: q.content } : {}),
    }));
    if (allQuotes.length > 0) msg.file_quotes = allQuotes;

    send(msg);
    setInput("");
    if (pendingQ.length > 0) useStore.setState({ pendingQuotes: [], pendingQuotesFor: null });
  }, [input, isStreaming, node.id, node.tree_id, attach]);

  const handleOpenFiles = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    actions.openFilesPanel(node.id);
    send({ type: WS.GET_NODE_FILES, node_id: node.id });
  }, [node.id]);

  const handleBrowseRepo = useCallback(() => {
    actions.openFilesPanel(node.id);
    send({ type: WS.GET_NODE_FILES, node_id: node.id });
  }, [node.id]);

  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    const s = useStore.getState();
    // Collect the full subtree rooted at this node
    const ids = getSubtreeIds(s.nodes, node.id);
    // Block if any node in the subtree is streaming
    if (ids.some((id) => s.streaming[id])) return;
    actions.softDeleteNodes(ids);
    const prev = s.deleteToast;
    if (prev?.timer) clearTimeout(prev.timer);
    const label = ids.length > 1 ? `Deleted subtree (${ids.length} nodes)` : "Deleted node";
    const timer = setTimeout(() => {
      actions.commitDeleteNodes(ids);
      send({ type: WS.DELETE_NODE, node_id: node.id });
    }, 10000);
    actions.setDeleteToast({ ids, label, timer });
  }, [node.id]);

  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadInfo, setUploadInfo] = useState<{ label: string; count: number } | null>(null);

  const handleFileDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const { files, label } = await collectDroppedFiles(e);
    if (!files.length) return;
    setUploading(true);
    const result = await uploadFiles(node.tree_id, node.id, files);
    setUploading(false);
    if (result) {
      actions.updateNodeGit(node.id, result.git_commit);
      send({ type: WS.GET_NODE_FILES, node_id: node.id });
      setUploadInfo((prev) => ({
        label: prev ? prev.label + ", " + label : label,
        count: (prev?.count || 0) + result.count,
      }));
    }
  }, [node.tree_id, node.id]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const [loading, setLoading] = useState(false);
  const hasRepo = tree && tree.repo_mode !== "new";
  const hasChildren = node.children_ids.length > 0;
  const [repoInput, setRepoInput] = useState("");
  const repoInputType = detectInputType(repoInput);
  const [skillInput, setSkillInput] = useState(tree?.skill ?? "");
  const skillTimerRef = useRef<ReturnType<typeof setTimeout>>(0 as never);
  const skillTextareaRef = useRef<HTMLTextAreaElement>(null);
  const repoTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync skill input when tree data changes externally
  useEffect(() => {
    if (tree) setSkillInput(tree.skill);
  }, [tree?.skill]);

  // Auto-resize skill and repo textareas
  useLayoutEffect(() => {
    const ta = skillTextareaRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }
  }, [skillInput, selected]);
  useLayoutEffect(() => {
    const ta = repoTextareaRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }
  }, [repoInput, selected]);

  // Debounced save for skill
  const handleSkillChange = useCallback((val: string) => {
    setSkillInput(val);
    clearTimeout(skillTimerRef.current);
    skillTimerRef.current = setTimeout(() => {
      send({ type: WS.UPDATE_TREE_SETTINGS, tree_id: node.tree_id, skill: val });
    }, 500);
  }, [node.tree_id]);

  const handleRepoSubmit = useCallback(() => {
    if (!repoInput.trim() || loading) return;
    const t = repoInput.trim();
    const type = detectInputType(t);
    if (type === "url" || type === "local") {
      setLoading(true);
      send({
        type: WS.SET_REPO,
        tree_id: node.tree_id,
        repo_mode: type === "url" ? "url" : "local",
        repo_source: t,
      });
      setRepoInput("");
      setTimeout(() => setLoading(false), 30000);
    }
  }, [repoInput, loading, node.tree_id]);

  // Clear loading when tree updates (repo set succeeded)
  const treeRepoMode = tree?.repo_mode;
  useEffect(() => {
    if (treeRepoMode && treeRepoMode !== "new") setLoading(false);
  }, [treeRepoMode]);

  // Root hub: three-section input
  if (isRoot && !node.user_message) {
    return (
      <div className={`tree-node tree-node-root ${selected ? "selected" : ""}`} onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); }}>
        {hasVisibleChildren && <Handle type="source" position={Position.Bottom} />}

        {/* Section 1: Skill */}
        <div className={`root-section ${hasChildren ? "root-section-locked" : ""}`}>
          <label className="root-section-label">
            Skill
            {hasChildren && skillInput && <CopyBtn text={skillInput} />}
          </label>
          <textarea
            ref={skillTextareaRef}
            className="root-section-input nopan nodrag"
            value={skillInput}
            onChange={(e) => handleSkillChange(e.target.value)}
            onFocus={() => actions.selectNode(node.id)}
            placeholder="System instructions for all conversations..."
            rows={1}
            disabled={hasChildren}
          />
        </div>

        {/* Section 2: Repo */}
        <div className={`root-section ${hasChildren ? "root-section-locked" : ""}`}>
          <label className="root-section-label">
            Repo
            {hasChildren && hasRepo && tree?.repo_source && <CopyBtn text={tree.repo_source} />}
          </label>
          {hasRepo && tree ? (
            <RepoBadge tree={tree} onBrowse={!hasChildren ? handleBrowseRepo : undefined} />
          ) : uploadInfo ? (
            <div
              className={`drop-zone ${dragOver ? "drop-zone-active" : ""}`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleFileDrop}
            >
              <div className="upload-badge" onClick={(e) => e.stopPropagation()}>
                <span
                  className="upload-badge-text"
                  onClick={handleBrowseRepo}
                  title="Browse files"
                >
                  {uploadInfo.label} ({uploadInfo.count} file{uploadInfo.count !== 1 ? "s" : ""})
                </span>
                {uploading && <span className="upload-badge-hint">uploading...</span>}
              </div>
              {dragOver && <div className="drop-zone-overlay">Drop more files</div>}
            </div>
          ) : (
            <>
              <div
                className={`drop-zone ${dragOver ? "drop-zone-active" : ""}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleFileDrop}
              >
                <textarea
                  ref={repoTextareaRef}
                  className="root-section-input nopan nodrag"
                  value={repoInput}
                  onChange={(e) => setRepoInput(e.target.value)}
                  onFocus={() => actions.selectNode(node.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleRepoSubmit();
                    }
                  }}
                  placeholder={uploading ? "Uploading..." : "Drop a folder, paste a path or GitHub URL..."}
                  rows={2}
                  disabled={hasChildren || loading || uploading}
                />
                {dragOver && <div className="drop-zone-overlay">Drop files here</div>}
              </div>
              {repoInput.trim() && (
                <div className="root-input-hint">
                  {repoInputType === "url" ? "⏎ clone repo" :
                   repoInputType === "local" ? "⏎ copy from path" :
                   "Enter a path or URL"}
                </div>
              )}
              {loading && <div className="root-input-hint">Setting up repo...</div>}
            </>
          )}
        </div>

        {/* Section 3: Message */}
        <div className="root-section">
          <label className="root-section-label">Message</label>
          {pendingQuotes.length > 0 && selected && (
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
          {attach.pendingFiles.length > 0 && (
            <div className="file-chips nodrag nopan" onClick={(e) => e.stopPropagation()}>
              {attach.pendingFiles.map((f, i) => (
                <span key={i} className="file-chip">
                  <span className="file-chip-name" title={f.path}>{f.path.split("/").pop()}</span>
                  <button className="file-chip-remove" onClick={() => attach.removeFile(i)}>&times;</button>
                </span>
              ))}
              <span className="file-chips-size">{formatFileSize(attach.totalSize)}</span>
            </div>
          )}
          <div
            className={`tree-node-input-wrap ${attach.dragOver ? "drag-over" : ""}`}
            onDragOver={attach.onDragOver}
            onDragLeave={attach.onDragLeave}
            onDrop={attach.addFromDrop}
          >
            <textarea
              ref={textareaRef}
              className="root-section-input nopan nodrag"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onFocus={() => actions.selectNode(node.id)}
              onPaste={attach.addFromPaste}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="Type a message..."
              rows={1}
              disabled={loading}
            />
            <button
              className="attach-btn nopan nodrag"
              onClick={(e) => { e.stopPropagation(); attach.fileInputRef.current?.click(); }}
              title="Attach files"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            </button>
            <input
              ref={attach.fileInputRef}
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={attach.addFromInput}
            />
            {attach.dragOver && <div className="drop-overlay nopan nodrag">Drop files here</div>}
          </div>
        </div>

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
      {hasVisibleChildren && <Handle type="source" position={Position.Bottom} />}
      {!isRoot && isLeaf && (
        <div className="delete-circle-zone">
          <button className="delete-circle" title="Delete" onMouseDown={(e) => e.preventDefault()} onClick={handleDelete}>×</button>
        </div>
      )}
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
              {pendingQuotes.length > 0 && (
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
              {attach.pendingFiles.length > 0 && (
                <div className="file-chips nodrag nopan" onClick={(e) => e.stopPropagation()}>
                  {attach.pendingFiles.map((f, i) => (
                    <span key={i} className="file-chip">
                      <span className="file-chip-name" title={f.path}>{f.path.split("/").pop()}</span>
                      <button className="file-chip-remove" onClick={() => attach.removeFile(i)}>&times;</button>
                    </span>
                  ))}
                  <span className="file-chips-size">{formatFileSize(attach.totalSize)}</span>
                </div>
              )}
              <div
                className={`tree-node-input ${attach.dragOver ? "drag-over" : ""}`}
                onDragOver={attach.onDragOver}
                onDragLeave={attach.onDragLeave}
                onDrop={attach.addFromDrop}
              >
                <textarea
                  ref={textareaRef}
                  className="tree-node-textarea nopan nodrag"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onFocus={() => actions.selectNode(node.id)}
                  onPaste={attach.addFromPaste}
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
                  className="attach-btn nopan nodrag"
                  onClick={(e) => { e.stopPropagation(); attach.fileInputRef.current?.click(); }}
                  title="Attach files"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
                </button>
                <input
                  ref={attach.fileInputRef}
                  type="file"
                  multiple
                  style={{ display: "none" }}
                  onChange={attach.addFromInput}
                />
                {attach.dragOver && <div className="drop-overlay nopan nodrag">Drop files here</div>}
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
