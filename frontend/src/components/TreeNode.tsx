import { memo, useState, useCallback, useRef, useLayoutEffect, useMemo, useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode, type ToolCall, type ProcessInfo, type FileQuote, isDagLeaf } from "../store";
import { send, WS } from "../ws";
import { renderMarkdown } from "../renderMarkdown";
import ToolCallLine from "./ToolCallLine";
import NodeModal from "./NodeModal";

// ── Drag & drop file upload helpers ──────────────────────────────────

interface DroppedFile {
  path: string;
  file: File;
}

function readEntry(entry: FileSystemEntry, basePath: string): Promise<DroppedFile[]> {
  return new Promise((resolve) => {
    if (entry.isFile) {
      (entry as FileSystemFileEntry).file((f) => {
        resolve([{ path: basePath + f.name, file: f }]);
      }, () => resolve([]));
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const results: DroppedFile[] = [];
      const readBatch = () => {
        reader.readEntries(async (entries) => {
          if (entries.length === 0) { resolve(results); return; }
          for (const e of entries) {
            const sub = await readEntry(e, basePath + entry.name + "/");
            results.push(...sub);
          }
          readBatch();
        }, () => resolve(results));
      };
      readBatch();
    } else {
      resolve([]);
    }
  });
}

interface DropResult {
  files: DroppedFile[];
  label: string;  // e.g. "my-project" for single folder, "3 files" for multiple
}

/** Collect all files from a drop event (supports folders via webkitGetAsEntry).
 *  Single folder drop: strip folder prefix so contents become workspace root. */
async function collectDroppedFiles(e: React.DragEvent): Promise<DropResult> {
  const items = e.dataTransfer?.items;
  if (!items) return { files: [], label: "" };

  const topEntries: FileSystemEntry[] = [];
  const looseFallback: DroppedFile[] = [];
  for (let i = 0; i < items.length; i++) {
    const entry = items[i].webkitGetAsEntry?.();
    if (entry) {
      topEntries.push(entry);
    } else {
      const f = items[i].getAsFile();
      if (f) looseFallback.push({ path: f.name, file: f });
    }
  }

  const nested = await Promise.all(topEntries.map((ent) => readEntry(ent, "")));
  const all = looseFallback.concat(...nested);

  // Single directory dropped → strip its name prefix so contents become workspace root
  const isSingleDir = topEntries.length === 1 && topEntries[0].isDirectory && looseFallback.length === 0;
  if (isSingleDir) {
    const prefix = topEntries[0].name + "/";
    for (const f of all) {
      if (f.path.startsWith(prefix)) f.path = f.path.slice(prefix.length);
    }
    return { files: all, label: topEntries[0].name };
  }

  const dirs = topEntries.filter((ent) => ent.isDirectory).length;
  const fileCount = topEntries.length - dirs + looseFallback.length;
  const parts: string[] = [];
  if (dirs > 0) parts.push(`${dirs} folder${dirs > 1 ? "s" : ""}`);
  if (fileCount > 0) parts.push(`${fileCount} file${fileCount > 1 ? "s" : ""}`);
  return { files: all, label: parts.join(", ") };
}

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

async function uploadFiles(treeId: string, nodeId: string, files: DroppedFile[]): Promise<{ count: number; git_commit: string } | null> {
  const totalSize = files.reduce((sum, f) => sum + f.file.size, 0);
  if (totalSize > MAX_UPLOAD_BYTES) {
    alert(`Upload too large (${(totalSize / 1024 / 1024).toFixed(1)}MB). Max is 50MB.`);
    return null;
  }
  const form = new FormData();
  for (const f of files) {
    form.append("files", f.file);
    form.append("paths", f.path);
  }
  const resp = await fetch(`/api/trees/${treeId}/nodes/${nodeId}/upload`, {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    console.error("Upload failed:", await resp.text());
    return null;
  }
  return resp.json();
}

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
  const isLeaf = useStore((s) => isDagLeaf(s.nodes, node.id, s.pendingDeleteNodes));
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

  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    const s = useStore.getState();
    if (s.streaming[node.id]) return;
    const ids = [node.id];
    actions.softDeleteNodes(ids);
    const prev = s.deleteToast;
    if (prev?.timer) clearTimeout(prev.timer);
    const timer = setTimeout(() => {
      actions.commitDeleteNodes(ids);
      send({ type: WS.DELETE_NODE, node_id: node.id });
    }, 10000);
    actions.setDeleteToast({ ids, label: "Deleted node", timer });
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
          <textarea
            ref={textareaRef}
            className="root-section-input nopan nodrag"
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
            disabled={loading}
          />
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
              <div className="tree-node-input">
                <textarea
                  ref={textareaRef}
                  className="tree-node-textarea nopan nodrag"
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
