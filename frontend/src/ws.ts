import { actions } from "./store";

// ── Wire protocol constants (mirror backend events.WS) ─────────────────

export const WS = {
  // Inbound (client → server)
  LIST_TREES: "list_trees",
  CREATE_TREE: "create_tree",
  LOAD_TREE: "load_tree",
  DELETE_TREE: "delete_tree",
  BRANCH: "branch",
  CHAT: "chat",
  GET_NODE: "get_node",

  // Outbound (server → client)
  TREES: "trees",
  TREE_CREATED: "tree_created",
  TREE_LOADED: "tree_loaded",
  TREE_DELETED: "tree_deleted",
  NODE_CREATED: "node_created",
  NODE_DATA: "node_data",
  STATUS: "status",
  CHUNK: "chunk",
  TOOL_START: "tool_start",
  TOOL_END: "tool_end",
  DONE: "done",
  ERROR: "error",
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
      break;
    case WS.TREE_CREATED:
      actions.addTree(data.tree);
      actions.selectTree(data.tree.id);
      actions.upsertNode(data.root);
      actions.selectNode(data.root.id);
      break;
    case WS.TREE_DELETED:
      actions.removeTree(data.tree_id);
      break;
    case WS.TREE_LOADED:
      actions.setNodes(data.nodes);
      const root = data.nodes.find((n: any) => !n.parent_id);
      if (root) actions.selectNode(root.id);
      break;
    case WS.NODE_CREATED:
      actions.upsertNode(data.node);
      break;
    case WS.NODE_DATA:
      actions.upsertNode(data.node);
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
      break;
    case WS.ERROR:
      actions.setNodeStatus(data.node_id, "error");
      actions.setStreaming(data.node_id, false);
      break;
  }
}
