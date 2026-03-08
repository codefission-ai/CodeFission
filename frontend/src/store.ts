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
  type: "node" | "file" | "folder" | "diff";
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
  pendingInputText: string | null;
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
  pendingInputText: null,
  showSettings: false,
  globalDefaults: { provider: "claude-code", model: "claude-sonnet-4-6", max_turns: 25, auth_mode: "cli", api_key: "", sandbox: false },
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
  selectNode: (id: string | null) => useStore.setState((s) => ({
    selectedNodeId: id,
    // Only clear quotes when switching to a different non-null node;
    // deselecting (null) and reselecting preserves them.
    pendingQuotes: (id && s.selectedNodeId && id !== s.selectedNodeId) ? [] : s.pendingQuotes,
  })),

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
      return { pendingQuotes: [...s.pendingQuotes, q] };
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

  // ── Settings ─────────────────────────────────────────────────
  toggleSettings: () => useStore.setState((s) => ({ showSettings: !s.showSettings })),
  setGlobalDefaults: (d: GlobalDefaults) => useStore.setState({ globalDefaults: d }),
  setProviders: (p: ProviderInfo[]) => useStore.setState({ providers: p }),
};
