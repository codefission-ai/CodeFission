import { create } from "zustand";

export interface CNode {
  id: string;
  tree_id: string;
  parent_id: string | null;
  user_message: string;
  assistant_response: string;
  label: string;
  status: string;
  children_ids: string[];
  git_branch: string | null;
  git_commit: string | null;
  created_by: string;
  quoted_node_ids: string[];
}

export interface CTree {
  id: string;
  name: string;
  created_at: string;
  root_node_id: string | null;
  provider: string;
  model: string;
  max_turns: number | null;
  repo_mode: string;
  repo_source: string | null;
  skill: string;
  notes: string;  // JSON array of {id, text, x, y, width, height}
}

export interface ProviderInfo {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  auth_modes: string[];
  default_auth_mode: string;
}

export interface GlobalDefaults {
  provider: string;
  model: string;
  max_turns: number;
  auth_mode: string;
  api_key: string;
  sandbox: boolean;
  sandbox_available: boolean;
  summary_model: string;
  data_dir: string;
}

export interface ToolCall {
  tool_call_id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "running" | "done" | "error";
  result: string;
  is_error: boolean;
}

export interface FileEntry {
  path: string;
}

export interface ProcessInfo {
  pid: number;
  command: string;
  ports: number[];
}

export interface FileQuote {
  id: string;
  nodeId: string;
  type: "node" | "file" | "folder" | "diff" | "note";
  path?: string;
  content?: string;
  label: string;
}

export interface FilesPanel {
  nodeId: string;
  tab: "files" | "diff";
  selectedFile: string | null;
}

interface Store {
  connected: boolean;
  trees: CTree[];
  currentTreeId: string | null;
  nodes: Record<string, CNode>;
  selectedNodeId: string | null;
  streaming: Record<string, boolean>;
  toolCalls: Record<string, ToolCall[]>;  // nodeId -> tool calls during streaming
  expandedNodes: Record<string, boolean>;
  collapsedSubtrees: Record<string, boolean>;
  nodeFiles: Record<string, string[]>;     // nodeId -> file paths
  nodeDiffs: Record<string, string>;       // nodeId -> diff text
  fileContents: Record<string, string>;    // "nodeId:filePath" -> content
  nodeProcesses: Record<string, ProcessInfo[]>;  // nodeId -> running processes
  filesPanel: FilesPanel | null;
  pendingQuotes: FileQuote[];
  pendingQuotesFor: string | null;  // which node these quotes target
  pendingInputText: string | null;
  pendingDeleteNodes: Set<string>;
  deleteToast: { ids: string[]; label: string; timer: ReturnType<typeof setTimeout> } | null;
  showSettings: boolean;
  globalDefaults: GlobalDefaults;
  providers: ProviderInfo[];
}

// Callback set by ws.ts to avoid circular imports
let _onExpandedChange: ((nodeId: string, expanded: boolean) => void) | null = null;
export function setExpandedCallback(cb: (nodeId: string, expanded: boolean) => void) {
  _onExpandedChange = cb;
}

let _onSubtreeCollapseChange: ((nodeId: string, collapsed: boolean) => void) | null = null;
export function setSubtreeCollapseCallback(cb: (nodeId: string, collapsed: boolean) => void) {
  _onSubtreeCollapseChange = cb;
}

function isDescendantOf(nodes: Record<string, CNode>, candidateId: string, ancestorId: string): boolean {
  let cur = nodes[candidateId];
  while (cur?.parent_id) {
    if (cur.parent_id === ancestorId) return true;
    cur = nodes[cur.parent_id];
  }
  return false;
}

/**
 * A node/note is a DAG leaf if nothing visible depends on it:
 * - No visible node has it as parent (tree edge)
 * - No visible node quotes it (quote edge via quoted_node_ids)
 */
export function isDagLeaf(
  nodes: Record<string, CNode>,
  id: string,
  pendingDeleteNodes: Set<string>,
): boolean {
  for (const n of Object.values(nodes)) {
    if (pendingDeleteNodes.has(n.id)) continue;
    if (n.parent_id === id) return false;         // tree child
    if (n.quoted_node_ids?.includes(id)) return false; // quote ref
  }
  return true;
}

export function getSubtreeIds(nodes: Record<string, CNode>, rootId: string): string[] {
  const ids = [rootId];
  const stack = [...(nodes[rootId]?.children_ids || [])];
  while (stack.length) {
    const cid = stack.pop()!;
    ids.push(cid);
    const child = nodes[cid];
    if (child) stack.push(...child.children_ids);
  }
  return ids;
}

export const useStore = create<Store>(() => ({
  connected: false,
  trees: [],
  currentTreeId: null,
  nodes: {},
  selectedNodeId: null,
  streaming: {},
  toolCalls: {},
  expandedNodes: {},
  collapsedSubtrees: {},
  nodeFiles: {},
  nodeDiffs: {},
  fileContents: {},
  nodeProcesses: {},
  filesPanel: null,
  pendingQuotes: [],
  pendingQuotesFor: null,
  pendingInputText: null,
  pendingDeleteNodes: new Set<string>(),
  deleteToast: null,
  showSettings: false,
  globalDefaults: { provider: "claude-code", model: "claude-opus-4-6", max_turns: 0, auth_mode: "cli", api_key: "", sandbox: false, sandbox_available: false, summary_model: "claude-haiku-4-5-20251001", data_dir: "" },
  providers: [],
}));

// Actions as plain functions (simpler than putting them in the store)
export const actions = {
  setConnected: (c: boolean) => useStore.setState({ connected: c }),

  setTrees: (trees: CTree[]) => useStore.setState({ trees }),
  addTree: (t: CTree) => useStore.setState((s) => ({ trees: [t, ...s.trees] })),
  removeTree: (id: string) =>
    useStore.setState((s) => ({
      trees: s.trees.filter((t) => t.id !== id),
      currentTreeId: s.currentTreeId === id ? null : s.currentTreeId,
      nodes: s.currentTreeId === id ? {} : s.nodes,
      selectedNodeId: s.currentTreeId === id ? null : s.selectedNodeId,
    })),
  selectTree: (id: string) => useStore.setState({ currentTreeId: id }),

  setNodes: (list: CNode[]) => {
    const nodes: Record<string, CNode> = {};
    for (const n of list) nodes[n.id] = n;
    useStore.setState({ nodes });
  },
  upsertNode: (node: CNode, afterId?: string) =>
    useStore.setState((s) => {
      const nodes = { ...s.nodes, [node.id]: node };
      if (node.parent_id && nodes[node.parent_id]) {
        const p = nodes[node.parent_id];
        if (!p.children_ids.includes(node.id)) {
          const ids = [...p.children_ids];
          const insertIdx = afterId ? ids.indexOf(afterId) : -1;
          if (insertIdx >= 0) {
            ids.splice(insertIdx + 1, 0, node.id);
          } else {
            ids.push(node.id);
          }
          nodes[node.parent_id] = { ...p, children_ids: ids };
        }
      }
      return { nodes };
    }),
  selectNode: (id: string | null) => useStore.setState((s) => {
    // Clear quotes when selecting a node that isn't the quotes' target
    const clear = id !== null && s.pendingQuotesFor !== null && id !== s.pendingQuotesFor;
    return {
      selectedNodeId: id,
      pendingQuotes: clear ? [] : s.pendingQuotes,
      pendingQuotesFor: clear ? null : s.pendingQuotesFor,
    };
  }),

  appendChunk: (nodeId: string, text: string) =>
    useStore.setState((s) => {
      const node = s.nodes[nodeId];
      if (!node) return s;
      return {
        nodes: {
          ...s.nodes,
          [nodeId]: { ...node, assistant_response: node.assistant_response + text },
        },
      };
    }),
  setNodeStatus: (nodeId: string, status: string) =>
    useStore.setState((s) => {
      const node = s.nodes[nodeId];
      if (!node) return s;
      return { nodes: { ...s.nodes, [nodeId]: { ...node, status } } };
    }),
  setStreaming: (nodeId: string, on: boolean) =>
    useStore.setState((s) => {
      const streaming = { ...s.streaming, [nodeId]: on };
      // Clear tool calls when streaming ends
      if (!on) {
        return { streaming, toolCalls: { ...s.toolCalls, [nodeId]: [] } };
      }
      return { streaming };
    }),

  // ── Tool call tracking ────────────────────────────────────────
  addToolCall: (nodeId: string, tc: ToolCall) =>
    useStore.setState((s) => {
      const existing = s.toolCalls[nodeId] || [];
      // Update if same tool_call_id exists, otherwise append
      const idx = existing.findIndex((t) => t.tool_call_id === tc.tool_call_id);
      let updated: ToolCall[];
      if (idx >= 0) {
        updated = [...existing];
        updated[idx] = { ...updated[idx], ...tc };
      } else {
        updated = [...existing, tc];
      }
      return { toolCalls: { ...s.toolCalls, [nodeId]: updated } };
    }),

  completeToolCall: (nodeId: string, toolCallId: string, result: string, isError: boolean) =>
    useStore.setState((s) => {
      const existing = s.toolCalls[nodeId] || [];
      const updated = existing.map((tc) =>
        tc.tool_call_id === toolCallId
          ? { ...tc, status: (isError ? "error" : "done") as ToolCall["status"], result, is_error: isError }
          : tc
      );
      return { toolCalls: { ...s.toolCalls, [nodeId]: updated } };
    }),

  updateNodeGit: (nodeId: string, gitCommit: string) =>
    useStore.setState((s) => {
      const node = s.nodes[nodeId];
      if (!node) return s;
      return { nodes: { ...s.nodes, [nodeId]: { ...node, git_commit: gitCommit } } };
    }),

  loadExpandedNodes: (map: Record<string, boolean>) =>
    useStore.setState({ expandedNodes: map }),
  toggleExpand: (id: string) =>
    useStore.setState((s) => {
      const v = !s.expandedNodes[id];
      _onExpandedChange?.(id, v);
      return { expandedNodes: { ...s.expandedNodes, [id]: v } };
    }),
  setExpanded: (id: string, v: boolean) =>
    useStore.setState((s) => {
      _onExpandedChange?.(id, v);
      return { expandedNodes: { ...s.expandedNodes, [id]: v } };
    }),

  loadCollapsedSubtrees: (map: Record<string, boolean>) =>
    useStore.setState({ collapsedSubtrees: map }),
  toggleSubtreeCollapsed: (id: string) =>
    useStore.setState((s) => {
      const collapsed = !s.collapsedSubtrees[id];
      _onSubtreeCollapseChange?.(id, collapsed);
      const next: Partial<Store> = {
        collapsedSubtrees: { ...s.collapsedSubtrees, [id]: collapsed },
      };
      // If collapsing and selected node is a descendant, move selection to collapsed node
      if (collapsed && s.selectedNodeId && isDescendantOf(s.nodes, s.selectedNodeId, id)) {
        next.selectedNodeId = id;
      }
      return next;
    }),

  // ── Tree updates ──────────────────────────────────────────────
  updateTree: (t: CTree) =>
    useStore.setState((s) => ({
      trees: s.trees.map((x) => (x.id === t.id ? t : x)),
    })),

  // ── File/diff panel ───────────────────────────────────────────
  setNodeFiles: (nodeId: string, files: string[]) =>
    useStore.setState((s) => ({
      nodeFiles: { ...s.nodeFiles, [nodeId]: files },
    })),
  setNodeDiff: (nodeId: string, diff: string) =>
    useStore.setState((s) => ({
      nodeDiffs: { ...s.nodeDiffs, [nodeId]: diff },
    })),
  setFileContent: (nodeId: string, filePath: string, content: string) =>
    useStore.setState((s) => ({
      fileContents: { ...s.fileContents, [`${nodeId}:${filePath}`]: content },
    })),
  setNodeProcesses: (nodeId: string, processes: ProcessInfo[]) =>
    useStore.setState((s) => ({
      nodeProcesses: { ...s.nodeProcesses, [nodeId]: processes },
    })),
  replaceAllNodeProcesses: (map: Record<string, ProcessInfo[]>) =>
    useStore.setState({ nodeProcesses: map }),

  openFilesPanel: (nodeId: string, tab: "files" | "diff" = "files") =>
    useStore.setState({ filesPanel: { nodeId, tab, selectedFile: null } }),
  closeFilesPanel: () => useStore.setState({ filesPanel: null }),
  setFilesPanelTab: (tab: "files" | "diff") =>
    useStore.setState((s) =>
      s.filesPanel ? { filesPanel: { ...s.filesPanel, tab } } : {}
    ),
  selectFile: (filePath: string | null) =>
    useStore.setState((s) =>
      s.filesPanel ? { filesPanel: { ...s.filesPanel, selectedFile: filePath } } : {}
    ),

  // ── Quote ────────────────────────────────────────────────────
  addFileQuote: (q: FileQuote) =>
    useStore.setState((s) => {
      // Prevent duplicate file/folder quotes (same node + type + path)
      if (q.type !== "diff") {
        const dup = s.pendingQuotes.some(
          (p) => p.nodeId === q.nodeId && p.type === q.type && p.path === q.path,
        );
        if (dup) return {};
      }
      return { pendingQuotes: [...s.pendingQuotes, q], pendingQuotesFor: s.selectedNodeId };
    }),
  removeFileQuote: (id: string) =>
    useStore.setState((s) => ({
      pendingQuotes: s.pendingQuotes.filter((q) => q.id !== id),
    })),
  appendToInput: (text: string) =>
    useStore.setState((s) => ({
      pendingInputText: (s.pendingInputText || "") + (s.pendingInputText ? "\n" : "") + "> " + text.replace(/\n/g, "\n> ") + "\n",
    })),
  clearPendingInput: () => useStore.setState({ pendingInputText: null }),

  // ── Soft delete / undo ───────────────────────────────────────
  softDeleteNodes: (ids: string[]) =>
    useStore.setState((s) => {
      const next = new Set(s.pendingDeleteNodes);
      for (const id of ids) next.add(id);
      // Move selection to parent if selected node is being deleted
      let selectedNodeId = s.selectedNodeId;
      if (selectedNodeId && next.has(selectedNodeId)) {
        const node = s.nodes[selectedNodeId];
        selectedNodeId = node?.parent_id || null;
      }
      return { pendingDeleteNodes: next, selectedNodeId };
    }),
  undoDeleteNodes: (ids: string[]) =>
    useStore.setState((s) => {
      const next = new Set(s.pendingDeleteNodes);
      for (const id of ids) next.delete(id);
      return { pendingDeleteNodes: next, deleteToast: null };
    }),
  commitDeleteNodes: (ids: string[]) =>
    useStore.setState((s) => {
      const idSet = new Set(ids);
      const nodes = { ...s.nodes };
      for (const id of ids) delete nodes[id];
      // Clean children_ids on surviving parents
      for (const id of ids) {
        const node = s.nodes[id];
        if (node?.parent_id && nodes[node.parent_id]) {
          const p = nodes[node.parent_id];
          const filtered = p.children_ids.filter((c) => !idSet.has(c));
          if (filtered.length !== p.children_ids.length) {
            nodes[node.parent_id] = { ...p, children_ids: filtered };
          }
        }
      }
      // Clean up associated state
      const pendingDeleteNodes = new Set(s.pendingDeleteNodes);
      const expandedNodes = { ...s.expandedNodes };
      const collapsedSubtrees = { ...s.collapsedSubtrees };
      const streaming = { ...s.streaming };
      const toolCalls = { ...s.toolCalls };
      const nodeFiles = { ...s.nodeFiles };
      const nodeDiffs = { ...s.nodeDiffs };
      const nodeProcesses = { ...s.nodeProcesses };
      const pendingQuotes = s.pendingQuotes.filter((q) => !idSet.has(q.nodeId));
      for (const id of ids) {
        pendingDeleteNodes.delete(id);
        delete expandedNodes[id];
        delete collapsedSubtrees[id];
        delete streaming[id];
        delete toolCalls[id];
        delete nodeFiles[id];
        delete nodeDiffs[id];
        delete nodeProcesses[id];
      }
      // Clean fileContents keys
      const fileContents = { ...s.fileContents };
      for (const key of Object.keys(fileContents)) {
        const nid = key.split(":")[0];
        if (idSet.has(nid)) delete fileContents[key];
      }
      const filesPanel = s.filesPanel && idSet.has(s.filesPanel.nodeId) ? null : s.filesPanel;
      const selectedNodeId = s.selectedNodeId && idSet.has(s.selectedNodeId) ? null : s.selectedNodeId;
      return {
        nodes, pendingDeleteNodes, expandedNodes, collapsedSubtrees, streaming,
        toolCalls, nodeFiles, nodeDiffs, nodeProcesses, fileContents, filesPanel,
        selectedNodeId, pendingQuotes, deleteToast: null,
      };
    }),
  setDeleteToast: (toast: Store["deleteToast"]) =>
    useStore.setState({ deleteToast: toast }),

  // ── Settings ─────────────────────────────────────────────────
  toggleSettings: () => useStore.setState((s) => ({ showSettings: !s.showSettings })),
  setGlobalDefaults: (d: GlobalDefaults) => useStore.setState({ globalDefaults: d }),
  setProviders: (p: ProviderInfo[]) => useStore.setState({ providers: p }),
};
