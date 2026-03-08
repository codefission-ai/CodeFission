import { actions, setExpandedCallback, setSubtreeCollapseCallback } from "./store";

setExpandedCallback((nodeId, expanded) => {
  send({ type: WS.SET_EXPANDED, node_id: nodeId, expanded });
});

setSubtreeCollapseCallback((nodeId, collapsed) => {
  send({ type: WS.SET_SUBTREE_COLLAPSED, node_id: nodeId, collapsed });
});

// ── Wire protocol constants (mirror backend events.WS) ─────────────────

export const WS = {
  // Inbound (client → server)
  LIST_TREES: "list_trees",
  CREATE_TREE: "create_tree",
  LOAD_TREE: "load_tree",
  DELETE_TREE: "delete_tree",
  BRANCH: "branch",
  CHAT: "chat",
  CANCEL: "cancel",
  DUPLICATE: "duplicate",
  GET_NODE: "get_node",
  SET_REPO: "set_repo",
  GET_NODE_FILES: "get_node_files",
  GET_NODE_DIFF: "get_node_diff",
  GET_FILE_CONTENT: "get_file_content",
  SELECT_TREE: "select_tree",
  SET_EXPANDED: "set_expanded",
  SET_SUBTREE_COLLAPSED: "set_subtree_collapsed",
  GET_SETTINGS: "get_settings",
  UPDATE_GLOBAL_SETTINGS: "update_global_settings",
  UPDATE_TREE_SETTINGS: "update_tree_settings",
  GET_NODE_PROCESSES: "get_node_processes",
  KILL_PROCESS: "kill_process",
  KILL_ALL_PROCESSES: "kill_all_processes",

  // Outbound (server → client)
  TREES: "trees",
  TREE_CREATED: "tree_created",
  TREE_LOADED: "tree_loaded",
  TREE_DELETED: "tree_deleted",
  TREE_UPDATED: "tree_updated",
  NODE_CREATED: "node_created",
  NODE_DATA: "node_data",
  NODE_FILES: "node_files",
  NODE_DIFF: "node_diff",
  FILE_CONTENT: "file_content",
  STATUS: "status",
  CHUNK: "chunk",
  TOOL_START: "tool_start",
  TOOL_END: "tool_end",
  DONE: "done",
  ERROR: "error",
  SETTINGS: "settings",
  NODE_PROCESSES: "node_processes",
} as const;

let ws: WebSocket | null = null;

export function connectWs() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    actions.setConnected(true);
    send({ type: WS.LIST_TREES });
  };
  ws.onclose = () => {
    actions.setConnected(false);
    setTimeout(connectWs, 2000);
  };
  ws.onerror = () => ws?.close();
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

export function send(msg: Record<string, unknown>) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function handle(data: any) {
  switch (data.type) {
    case WS.TREES:
      actions.setTrees(data.trees);
      if (data.expanded_nodes) actions.loadExpandedNodes(data.expanded_nodes);
      if (data.collapsed_subtrees) actions.loadCollapsedSubtrees(data.collapsed_subtrees);
      if (data.global_defaults) actions.setGlobalDefaults(data.global_defaults);
      if (data.providers) actions.setProviders(data.providers);
      // Auto-load last active tree on reconnect/refresh
      if (data.last_tree_id && data.trees.some((t: any) => t.id === data.last_tree_id)) {
        actions.selectTree(data.last_tree_id);
        send({ type: WS.LOAD_TREE, tree_id: data.last_tree_id });
      }
      break;
    case WS.TREE_CREATED:
      actions.addTree(data.tree);
      actions.selectTree(data.tree.id);
      send({ type: WS.SELECT_TREE, tree_id: data.tree.id });
      actions.setNodes([data.root]);
      break;
    case WS.TREE_DELETED:
      actions.removeTree(data.tree_id);
      break;
    case WS.TREE_UPDATED:
      actions.updateTree(data.tree);
      break;
    case WS.TREE_LOADED:
      actions.setNodes(data.nodes);
      if (data.node_processes) {
        for (const [nodeId, procs] of Object.entries(data.node_processes)) {
          actions.setNodeProcesses(nodeId, procs as any[]);
        }
      }
      // Don't auto-select any node — let user click to focus
      break;
    case WS.NODE_CREATED:
      actions.upsertNode(data.node, data.after_id);
      break;
    case WS.NODE_DATA:
      actions.upsertNode(data.node);
      break;
    case WS.NODE_FILES:
      actions.setNodeFiles(data.node_id, data.files);
      break;
    case WS.NODE_DIFF:
      actions.setNodeDiff(data.node_id, data.diff);
      break;
    case WS.FILE_CONTENT:
      actions.setFileContent(data.node_id, data.file_path, data.content);
      break;
    case WS.STATUS:
      actions.setNodeStatus(data.node_id, "active");
      actions.setStreaming(data.node_id, true);
      actions.setExpanded(data.node_id, true);
      break;
    case WS.CHUNK:
      actions.appendChunk(data.node_id, data.text);
      break;
    case WS.TOOL_START:
      actions.addToolCall(data.node_id, {
        tool_call_id: data.tool_call_id,
        name: data.name,
        arguments: data.arguments || {},
        status: "running",
        result: "",
        is_error: false,
      });
      break;
    case WS.TOOL_END:
      actions.completeToolCall(
        data.node_id,
        data.tool_call_id,
        data.result || "",
        data.is_error || false,
      );
      break;
    case WS.DONE:
      actions.setNodeStatus(data.node_id, "done");
      actions.setStreaming(data.node_id, false);
      if (data.git_commit) {
        actions.updateNodeGit(data.node_id, data.git_commit);
      }
      if (data.processes) {
        actions.setNodeProcesses(data.node_id, data.processes);
      }
      break;
    case WS.NODE_PROCESSES:
      actions.setNodeProcesses(data.node_id, data.processes || []);
      break;
    case "tree_node_processes":
      actions.replaceAllNodeProcesses(data.tree_node_processes || {});
      break;
    case WS.ERROR:
      actions.setNodeStatus(data.node_id, "error");
      actions.setStreaming(data.node_id, false);
      break;
    case WS.SETTINGS:
      if (data.global_defaults) actions.setGlobalDefaults(data.global_defaults);
      if (data.providers) actions.setProviders(data.providers);
      break;
  }
}
