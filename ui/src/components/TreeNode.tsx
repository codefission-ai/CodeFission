import { memo, useState, useCallback, useRef, useLayoutEffect, useMemo, useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import { useStore, actions, type CNode, type CTree, type ToolCall, type ProcessInfo, type FileQuote, type MergeResult, isDetachable, getSubtreeIds } from "../store";
import { send, WS } from "../ws";
import { renderMarkdown } from "../renderMarkdown";
import ToolCallLine from "./ToolCallLine";
import NodeModal from "./NodeModal";
import { useFileAttach, formatFileSize } from "../fileAttach";

function truncate(text: string, max: number): string {
  if (!text || text.length <= max) return text || "";
  return text.slice(0, max) + "...";
}

function BranchSection({ tree, hasChildren }: { tree: CTree; hasChildren: boolean }) {
  const repoBranches = useStore((s) => s.repoBranches);
  const branch = tree.base_branch || "main";
  const shortSha = tree.base_commit ? tree.base_commit.slice(0, 7) : "";
  const locked = hasChildren;
  const [commitInput, setCommitInput] = useState("");
  const [editingCommit, setEditingCommit] = useState(false);
  const [pathInput, setPathInput] = useState("");
  const [editingPath, setEditingPath] = useState(false);

  const handleBranchChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    send({ type: WS.UPDATE_BASE, tree_id: tree.id, base_branch: e.target.value });
  }, [tree.id]);

  const handleCommitSubmit = useCallback(() => {
    const val = commitInput.trim();
    if (val && val !== tree.base_commit && val !== shortSha) {
      send({ type: WS.UPDATE_BASE, tree_id: tree.id, base_commit: val });
    }
    setEditingCommit(false);
    setCommitInput("");
  }, [commitInput, tree.id, tree.base_commit, shortSha]);

  const handlePathSubmit = useCallback(() => {
    const val = pathInput.trim();
    if (val && val !== tree.repo_path) {
      send({ type: WS.UPDATE_BASE, tree_id: tree.id, repo_path: val });
    }
    setEditingPath(false);
  }, [pathInput, tree.id, tree.repo_path]);

  return (
    <div className={`root-section ${locked ? "root-section-locked" : ""}`} onClick={(e) => e.stopPropagation()}>
      <label className="root-section-label">Base</label>
      {locked ? (
        tree.repo_path ? (
          <div className="branch-info-path" title={tree.repo_path}>
            {tree.repo_path}
          </div>
        ) : null
      ) : editingPath ? (
        <input
          className="branch-path-input nopan nodrag"
          value={pathInput}
          onChange={(e) => setPathInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handlePathSubmit(); if (e.key === "Escape") { setEditingPath(false); } }}
          onBlur={handlePathSubmit}
          placeholder="/path/to/repo"
          autoFocus
        />
      ) : (
        <div
          className="branch-info-path editable"
          title={tree.repo_path ? `${tree.repo_path} (click to change)` : "Click to set repo path"}
          onClick={() => { setEditingPath(true); setPathInput(tree.repo_path || ""); }}
        >
          {tree.repo_path || "no repo — click to set"}
        </div>
      )}
      <div className="branch-info-row">
        {locked ? (
          <span className="branch-info-name">{branch}</span>
        ) : (
          <select
            className="branch-select nopan nodrag"
            value={branch}
            onChange={handleBranchChange}
          >
            {!repoBranches.some((b) => b.name === branch) && (
              <option value={branch}>{branch}</option>
            )}
            {repoBranches.map((b) => (
              <option key={b.name} value={b.name}>{b.name}</option>
            ))}
          </select>
        )}
        {locked ? (
          shortSha ? (
            <span
              className="branch-info-sha"
              title={`Commit: ${tree.base_commit}`}
              onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(tree.base_commit || ""); }}
            >
              @{shortSha}
            </span>
          ) : null
        ) : editingCommit ? (
          <input
            className="commit-input nopan nodrag"
            value={commitInput}
            onChange={(e) => setCommitInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCommitSubmit(); if (e.key === "Escape") { setEditingCommit(false); setCommitInput(""); } }}
            onBlur={handleCommitSubmit}
            placeholder="sha or ref..."
            autoFocus
          />
        ) : (
          <span
            className="branch-info-sha editable"
            title={tree.base_commit ? `Commit: ${tree.base_commit} (click to change)` : "Click to set commit"}
            onClick={(e) => { e.stopPropagation(); setEditingCommit(true); setCommitInput(tree.base_commit || ""); }}
          >
            @{shortSha || "HEAD"}
          </span>
        )}
      </div>
    </div>
  );
}

function StalenessBanner({ treeId }: { treeId: string }) {
  const staleness = useStore((s) => s.treeStaleness[treeId]);
  const tree = useStore((s) => s.trees.find((t) => t.id === treeId));
  if (!staleness?.stale) return null;
  const branch = tree?.base_branch || "main";
  return (
    <div className="staleness-banner" onClick={(e) => e.stopPropagation()}>
      <span>{branch} has {staleness.commits_behind} new commit{staleness.commits_behind !== 1 ? "s" : ""} since this tree was created</span>
      <button
        className="staleness-update-btn"
        onClick={() => send({ type: WS.UPDATE_BASE, tree_id: treeId })}
      >
        Update base
      </button>
    </div>
  );
}

function MergeResultBanner({ result, onDismiss }: { result: MergeResult; onDismiss: () => void }) {
  return (
    <div className={`merge-result-banner ${result.ok ? "success" : "error"}`} onClick={(e) => e.stopPropagation()}>
      {result.ok ? (
        <span>Merged successfully{result.commit ? ` (${result.commit.slice(0, 7)})` : ""}</span>
      ) : (
        <div className="merge-result-error">
          <span>{result.error || "Merge failed"}</span>
          {result.conflicts && result.conflicts.length > 0 && (
            <ul className="merge-conflicts-list">
              {result.conflicts.map((f) => <li key={f}>{f}</li>)}
            </ul>
          )}
        </div>
      )}
      <button className="merge-result-dismiss" onClick={onDismiss}>&times;</button>
    </div>
  );
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

/** Collapsible tool calls block.
 *  During streaming: shows last 3 tool calls, collapses older into a summary.
 *  After streaming: all collapsed into a summary line, expandable on click. */
function ToolCallsBlock({ toolCalls, isStreaming }: { toolCalls: ToolCall[]; isStreaming: boolean }) {
  const [expandAll, setExpandAll] = useState(false);
  const VISIBLE_COUNT = 3;

  if (toolCalls.length === 0) return null;

  // After streaming: everything collapsed by default
  if (!isStreaming && !expandAll) {
    return (
      <div className="tool-calls-block">
        <button
          className="tool-calls-summary"
          onClick={(e) => { e.stopPropagation(); setExpandAll(true); }}
        >
          {toolCalls.length} tool call{toolCalls.length !== 1 ? "s" : ""}
        </button>
      </div>
    );
  }

  // After streaming, expanded: show all with option to collapse
  if (!isStreaming && expandAll) {
    return (
      <div className="tool-calls-block">
        <button
          className="tool-calls-summary"
          onClick={(e) => { e.stopPropagation(); setExpandAll(false); }}
        >
          {toolCalls.length} tool call{toolCalls.length !== 1 ? "s" : ""} (click to hide)
        </button>
        {toolCalls.map((tc) => (
          <ToolCallLine key={tc.tool_call_id} tc={tc} />
        ))}
      </div>
    );
  }

  // During streaming: show summary of older + last VISIBLE_COUNT
  const hiddenCount = Math.max(0, toolCalls.length - VISIBLE_COUNT);
  const visibleCalls = hiddenCount > 0 ? toolCalls.slice(-VISIBLE_COUNT) : toolCalls;

  return (
    <div className="tool-calls-block">
      {hiddenCount > 0 && !expandAll && (
        <button
          className="tool-calls-summary"
          onClick={(e) => { e.stopPropagation(); setExpandAll(true); }}
        >
          {hiddenCount} earlier tool call{hiddenCount !== 1 ? "s" : ""}
        </button>
      )}
      {hiddenCount > 0 && expandAll && (
        <>
          <button
            className="tool-calls-summary"
            onClick={(e) => { e.stopPropagation(); setExpandAll(false); }}
          >
            {hiddenCount} earlier tool call{hiddenCount !== 1 ? "s" : ""} (click to hide)
          </button>
          {toolCalls.slice(0, hiddenCount).map((tc) => (
            <ToolCallLine key={tc.tool_call_id} tc={tc} />
          ))}
        </>
      )}
      {visibleCalls.map((tc) => (
        <ToolCallLine key={tc.tool_call_id} tc={tc} />
      ))}
    </div>
  );
}

function TreeNode({ data }: { data: { node: CNode } }) {
  const { node } = data;
  const selectedId = useStore((s) => s.selectedNodeId);
  const isStreaming = useStore((s) => s.streaming[node.id]);
  const isExpanded = useStore((s) => s.expandedNodes[node.id]);
  const isLeaf = useStore((s) => isDetachable(s.nodes, node.id, s.pendingDeleteNodes));
  const hasVisibleChildren = useStore((s) => node.children_ids.some((cid) => !s.pendingDeleteNodes.has(cid)));
  const activeToolCalls = useStore((s) => s.toolCalls[node.id]) ?? EMPTY_TOOL_CALLS;
  const processes = useStore((s) => s.nodeProcesses[node.id]) ?? EMPTY_PROCESSES;
  const isUnread = useStore((s) => node.status === "done" && !s.seenNodes.has(node.id));
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
  // Get tree info for merge button (need base_branch for non-root nodes too)
  const treeForMerge = useStore((s) => s.trees.find((t) => t.id === node.tree_id));
  const mergeResult = useStore((s) => s.mergeResult?.nodeId === node.id ? s.mergeResult : null);
  const [merging, setMerging] = useState(false);
  const [input, setInput] = useState("");
  const [showModal, setShowModal] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const responseRef = useRef<HTMLDivElement>(null);
  const attach = useFileAttach({ treeId: node.tree_id, parentNodeId: node.id });

  // Block wheel events so ReactFlow doesn't pan when scrolling the response
  useEffect(() => {
    const el = responseRef.current;
    if (!el || !selected) return;
    const stop = (e: WheelEvent) => {
      if (e.metaKey || e.ctrlKey) return;
      if (el.scrollHeight > el.clientHeight) e.stopPropagation();
    };
    el.addEventListener("wheel", stop, { passive: false });
    return () => el.removeEventListener("wheel", stop);
  }, [selected]);

  // Consume pendingInputText from store
  const pendingInputText = useStore((s) => s.pendingInputText);
  useEffect(() => {
    if (pendingInputText && selected) {
      setInput((prev) => prev + pendingInputText);
      actions.clearPendingInput();
    }
  }, [pendingInputText, selected]);

  // Reset merging spinner when result arrives
  useEffect(() => {
    if (mergeResult) setMerging(false);
  }, [mergeResult]);

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

    // Build message
    let content = input.trim();
    if (fileQuotes.length > 0) {
      const paths = fileQuotes.map((q) => q.path);
      const fileLine = `[Attached files in workspace: ${paths.join(", ")}]`;
      content = content ? `${content}\n\n${fileLine}` : fileLine;
    }
    if (!content) return;
    const msg: Record<string, unknown> = { type: WS.CHAT, node_id: node.id, content };
    if (draftNodeId) msg.draft_node_id = draftNodeId;

    // Cross-branch quotes
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

  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    const s = useStore.getState();
    const ids = getSubtreeIds(s.nodes, node.id);
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

  const handleMerge = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (!treeForMerge || merging) return;
    setMerging(true);
    actions.setMergeResult(null);
    send({
      type: WS.MERGE_TO_BRANCH,
      node_id: node.id,
      target_branch: treeForMerge.base_branch || "main",
    });
  }, [node.id, treeForMerge, merging]);

  const hasChildren = node.children_ids.length > 0;
  const [skillInput, setSkillInput] = useState(tree?.skill ?? "");
  const skillTimerRef = useRef<ReturnType<typeof setTimeout>>(0 as never);
  const skillTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync skill input when tree data changes externally
  useEffect(() => {
    if (tree) setSkillInput(tree.skill);
  }, [tree?.skill]);

  // Auto-resize skill textarea
  useLayoutEffect(() => {
    const ta = skillTextareaRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }
  }, [skillInput, selected]);

  // Debounced save for skill
  const handleSkillChange = useCallback((val: string) => {
    setSkillInput(val);
    clearTimeout(skillTimerRef.current);
    skillTimerRef.current = setTimeout(() => {
      send({ type: WS.UPDATE_TREE_SETTINGS, tree_id: node.tree_id, skill: val });
    }, 500);
  }, [node.tree_id]);

  // Root hub: two-section input (Skill + Message), with branch badge
  if (isRoot && !node.user_message) {
    return (
      <div className={`tree-node tree-node-root ${selected ? "selected" : ""}`} onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); }}>
        {hasVisibleChildren && <Handle type="source" position={Position.Bottom} />}

        {/* Section 1: Instructions */}
        <div className={`root-section ${hasChildren ? "root-section-locked" : ""}`}>
          <label className="root-section-label">
            Instructions
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

        {/* Branch info (editable when no children, locked once children exist) */}
        {tree && <BranchSection tree={tree} hasChildren={hasChildren} />}

        {/* Staleness banner */}
        <StalenessBanner treeId={node.tree_id} />

        {/* Section 2: Message */}
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
      className={`tree-node ${selected ? "selected" : ""} ${isExpanded ? "expanded" : ""} ${hasCodeChange ? "has-code" : ""} ${isUnread ? "unread" : ""}`}
      onClick={() => {
        actions.selectNode(node.id);
        if (!isExpanded) {
          actions.setExpanded(node.id, true);
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
      {!isExpanded && isUnread && <span className="tree-node-unread" />}
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
      {isExpanded && (
        <div className="tree-node-preview" onClick={(e) => { e.stopPropagation(); actions.selectNode(node.id); }}>
          {/* Quote button */}
          {canShowQuote && (
            <button
              className={`quote-btn ${quotesFromThis > 0 ? "quoted" : ""}`}
              onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); e.nativeEvent.stopImmediatePropagation(); }}
              onClick={(e) => {
                e.stopPropagation();
                e.nativeEvent.stopImmediatePropagation();
                if (quotesFromThis > 0) {
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
          {isRoot && treeForMerge && (
            <div className="branch-info-row branch-info-compact" onClick={(e) => e.stopPropagation()}>
              <span className="branch-info-name">{treeForMerge.base_branch || "main"}</span>
              {treeForMerge.base_commit && (
                <span
                  className="branch-info-sha"
                  title={`Commit: ${treeForMerge.base_commit}`}
                  onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(treeForMerge.base_commit || ""); }}
                >
                  @{treeForMerge.base_commit.slice(0, 7)}
                </span>
              )}
            </div>
          )}
          {isRoot && <StalenessBanner treeId={node.tree_id} />}
          {node.user_message && (
            <div className="tree-node-user">
              {truncate(node.user_message, 150)}
              <button
                className="collapse-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  actions.setExpanded(node.id, false);
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
              onClick={(e) => {
                if (!selected || isStreaming) return;
                const target = e.target as HTMLElement;
                if (target.closest("a")) return;
                setShowModal(true);
              }}
            >
              <div
                className="tree-node-response-md"
                dangerouslySetInnerHTML={{ __html: assistantHtml }}
              />
              {isStreaming && <span className="stream-cursor" />}
            </div>
          )}

          {/* Tool calls — auto-collapse older calls */}
          {activeToolCalls.length > 0 && <ToolCallsBlock toolCalls={activeToolCalls} isStreaming={isStreaming} />}

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
                {hasCodeChange && treeForMerge && (
                  <button
                    className={`tree-node-action-btn merge-btn ${merging ? "merging" : ""}`}
                    onClick={handleMerge}
                    disabled={merging}
                    title={`Squash merge into ${treeForMerge.base_branch || "main"}`}
                  >
                    {merging ? "Merging..." : `Merge to ${treeForMerge.base_branch || "main"}`}
                  </button>
                )}
              </div>
              {mergeResult && (
                <MergeResultBanner result={mergeResult} onDismiss={() => actions.setMergeResult(null)} />
              )}
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
  prev.data.node === next.data.node
);
