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
}

export interface CTree {
  id: string;
  name: string;
  created_at: string;
  root_node_id: string | null;
  provider: string;
  model: string;
}

interface Store {
  connected: boolean;
  trees: CTree[];
  currentTreeId: string | null;
  nodes: Record<string, CNode>;
  selectedNodeId: string | null;
  streaming: Record<string, boolean>;
}

export const useStore = create<Store>(() => ({
  connected: false,
  trees: [],
  currentTreeId: null,
  nodes: {},
  selectedNodeId: null,
  streaming: {},
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
  upsertNode: (node: CNode) =>
    useStore.setState((s) => {
      const nodes = { ...s.nodes, [node.id]: node };
      // Update parent's children_ids
      if (node.parent_id && nodes[node.parent_id]) {
        const p = nodes[node.parent_id];
        if (!p.children_ids.includes(node.id)) {
          nodes[node.parent_id] = { ...p, children_ids: [...p.children_ids, node.id] };
        }
      }
      return { nodes };
    }),
  selectNode: (id: string | null) => useStore.setState({ selectedNodeId: id }),

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
    useStore.setState((s) => ({ streaming: { ...s.streaming, [nodeId]: on } })),
};
