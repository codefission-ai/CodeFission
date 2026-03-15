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
  CANCEL: "cancel",
  DUPLICATE: "duplicate",
  GET_NODE: "get_node",
  GET_NODE_FILES: "get_node_files",
  GET_NODE_DIFF: "get_node_diff",
  GET_FILE_CONTENT: "get_file_content",
  SELECT_TREE: "select_tree",
  GET_SETTINGS: "get_settings",
  UPDATE_GLOBAL_SETTINGS: "update_global_settings",
  UPDATE_TREE_SETTINGS: "update_tree_settings",
  GET_NODE_PROCESSES: "get_node_processes",
  KILL_PROCESS: "kill_process",
  KILL_ALL_PROCESSES: "kill_all_processes",
  DELETE_NODE: "delete_node",
  GET_REPO_INFO: "get_repo_info",
  LIST_BRANCHES: "list_branches",
  MERGE_TO_BRANCH: "merge_to_branch",
  OPEN_REPO: "open_repo",
  UPDATE_BASE: "update_base",

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
  NODES_DELETED: "nodes_deleted",
  REPO_INFO: "repo_info",
  BRANCHES: "branches",
  MERGE_RESULT: "merge_result",
  REPO_OPENED: "repo_opened",
  BASE_UPDATED: "base_updated",
} as const;

let ws: WebSocket | null = null;

// ── Reconnect with exponential backoff + jitter ─────────────────────────
const BACKOFF_BASE = 500;    // start at 500ms
const BACKOFF_MAX = 30_000;  // cap at 30s
let backoffAttempt = 0;

function scheduleReconnect() {
  const delay = Math.min(BACKOFF_BASE * 2 ** backoffAttempt, BACKOFF_MAX);
  const jitter = delay * 0.5 * Math.random();
  backoffAttempt++;
  setTimeout(connectWs, delay + jitter);
}

// ── Heartbeat (client-initiated ping/pong) ──────────────────────────────
const PING_INTERVAL = 25_000;  // send ping every 25s
const PONG_TIMEOUT = 10_000;   // expect pong within 10s
let pingTimer: ReturnType<typeof setInterval> | null = null;
let pongTimer: ReturnType<typeof setTimeout> | null = null;
let awaitingPong = false;

function startHeartbeat() {
  stopHeartbeat();
  pingTimer = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    awaitingPong = true;
    ws.send(JSON.stringify({ type: "ping" }));
    pongTimer = setTimeout(() => {
      if (awaitingPong) {
        // Server didn't respond — connection is dead
        ws?.close();
      }
    }, PONG_TIMEOUT);
  }, PING_INTERVAL);
}

function stopHeartbeat() {
  if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
  if (pongTimer) { clearTimeout(pongTimer); pongTimer = null; }
  awaitingPong = false;
}

function receivedPong() {
  awaitingPong = false;
  if (pongTimer) { clearTimeout(pongTimer); pongTimer = null; }
}

// ── Message queue (buffer sends while disconnected) ─────────────────────
let sendQueue: Record<string, unknown>[] = [];

export function connectWs() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  // Clean up any lingering socket
  if (ws) {
    ws.onclose = null;
    ws.onerror = null;
    ws.onmessage = null;
    try { ws.close(); } catch {}
    ws = null;
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    actions.setConnected(true);
    backoffAttempt = 0;
    startHeartbeat();
    // Flush queued messages
    for (const msg of sendQueue) {
      ws!.send(JSON.stringify(msg));
    }
    sendQueue = [];

    // Check URL for ?repo_id= + ?head= + ?path= params
    const params = new URLSearchParams(window.location.search);
    const repoId = params.get("repo_id");
    const headCommit = params.get("head");
    const repoPath = params.get("path");

    if (repoId && headCommit && repoPath) {
      // Canvas-first: sidebar starts closed when opening from a repo
      actions.setSidebarOpen(false);
      send({ type: WS.OPEN_REPO, repo_id: repoId, head_commit: headCommit, repo_path: repoPath });
    } else {
      // No repo specified — sidebar open, list all trees
      actions.setSidebarOpen(true);
      send({ type: WS.LIST_TREES });
      send({ type: WS.LIST_BRANCHES });
    }
  };
  ws.onclose = () => {
    actions.setConnected(false);
    stopHeartbeat();
    scheduleReconnect();
  };
  ws.onerror = () => ws?.close();
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

export function send(msg: Record<string, unknown>) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  } else {
    // Queue messages that matter (skip ephemeral ones like select_tree)
    const t = msg.type as string;
    if (t !== WS.SELECT_TREE) {
      sendQueue.push(msg);
    }
  }
}

// ── Chunk batching ──────────────────────────────────────────────────────
const pendingChunks = new Map<string, string>();
let chunkRafId: number | null = null;

function flushChunks() {
  chunkRafId = null;
  for (const [nodeId, text] of pendingChunks) {
    actions.appendChunk(nodeId, text);
  }
  pendingChunks.clear();
}

function queueChunk(nodeId: string, text: string) {
  pendingChunks.set(nodeId, (pendingChunks.get(nodeId) || "") + text);
  if (chunkRafId === null) {
    chunkRafId = requestAnimationFrame(flushChunks);
  }
}

function handle(data: any) {
  switch (data.type) {
    case "pong":
      receivedPong();
      break;
    case WS.TREES:
      actions.setTrees(data.trees);
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
      actions.selectNode(data.root.id);
      actions.setExpanded(data.root.id, true);
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
      if (data.tree && data.staleness) {
        actions.setTreeStaleness(data.tree.id, data.staleness);
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
      queueChunk(data.node_id, data.text);
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
    case WS.NODES_DELETED:
      actions.commitDeleteNodes(data.deleted_ids || []);
      if (data.updated_nodes) {
        for (const n of data.updated_nodes) actions.upsertNode(n);
      }
      break;
    case WS.ERROR:
      actions.setNodeStatus(data.node_id, "error");
      actions.setStreaming(data.node_id, false);
      break;
    case WS.SETTINGS:
      if (data.global_defaults) actions.setGlobalDefaults(data.global_defaults);
      if (data.providers) actions.setProviders(data.providers);
      break;
    case WS.REPO_INFO:
      actions.setRepoContext({
        repo_path: data.path,
        repo_name: data.name,
        current_branch: data.current_branch,
        is_dirty: data.is_dirty,
      });
      break;
    case WS.BRANCHES:
      actions.setRepoBranches(data.branches || []);
      break;
    case WS.MERGE_RESULT:
      actions.setMergeResult({
        nodeId: data.node_id,
        ok: data.ok,
        commit: data.commit,
        error: data.error,
        conflicts: data.conflicts,
      });
      if (data.ok) {
        // Refresh repo info and branches after successful merge
        send({ type: WS.GET_REPO_INFO });
        send({ type: WS.LIST_BRANCHES });
      }
      break;
    case WS.REPO_OPENED:
      actions.setRepoContext({
        repo_path: data.path,
        repo_name: data.repo_name || data.name,
        current_branch: data.current_branch,
        is_dirty: data.is_dirty,
      });
      // Load tree + nodes directly from the response
      if (data.tree) {
        actions.setTrees([data.tree]);
        actions.selectTree(data.tree.id);
        if (data.nodes) actions.setNodes(data.nodes);
        if (data.staleness) actions.setTreeStaleness(data.tree.id, data.staleness);
      }
      if (data.branches) {
        actions.setRepoBranches(data.branches);
      } else {
        send({ type: WS.LIST_BRANCHES });
      }
      // Also fetch full tree list for sidebar
      send({ type: WS.LIST_TREES });
      break;
    case WS.BASE_UPDATED:
      if (data.existing_tree_id) {
        // A tree already exists for this (repo, commit) — switch to it
        actions.selectTree(data.existing_tree_id);
        send({ type: WS.LOAD_TREE, tree_id: data.existing_tree_id });
        send({ type: WS.SELECT_TREE, tree_id: data.existing_tree_id });
      }
      if (data.tree) {
        actions.updateTree(data.tree);
        actions.setTreeStaleness(data.tree.id, data.staleness || { stale: false, commits_behind: 0 });
        if (data.tree.root_node_id && data.tree.base_commit) {
          actions.updateNodeGit(data.tree.root_node_id, data.tree.base_commit);
        }
      }
      if (data.branches) {
        actions.setRepoBranches(data.branches);
      }
      break;
  }
}
