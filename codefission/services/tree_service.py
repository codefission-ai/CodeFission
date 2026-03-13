import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_project_path, get_project_dir
from db import get_db
from models import Node, Tree, DEFAULT_PROVIDER, DEFAULT_MODEL
from providers import PROVIDERS, DEFAULT_PROVIDER as FALLBACK_PROVIDER
from services.workspace_service import cleanup_tree_workspaces

log = logging.getLogger(__name__)


def _tree_from_row(row, root_id: str | None = None) -> Tree:
    """Build a Tree from a DB row."""
    return Tree(
        id=row["id"], name=row["name"], created_at=row["created_at"],
        root_node_id=root_id,
        provider=row["provider"], model=row["model"],
        max_turns=row["max_turns"],
        skill=row["skill"], notes=row["notes"],
        base_branch=row["base_branch"], base_commit=row["base_commit"],
        repo_id=row["repo_id"], repo_path=row["repo_path"], repo_name=row["repo_name"],
    )


async def create_tree(
    name: str,
    provider: str = "",
    model: str = "",
    base_branch: str = "main",
    base_commit: str | None = None,
    repo_id: str | None = None,
    repo_path: str | None = None,
    repo_name: str | None = None,
) -> tuple[Tree, Node]:
    tree_id = uuid.uuid4().hex[:12]
    root_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            """INSERT INTO trees (id, name, created_at, provider, model,
               base_branch, base_commit, repo_id, repo_path, repo_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tree_id, name, now, provider, model, base_branch, base_commit,
             repo_id, repo_path, repo_name),
        )
        await db.execute(
            "INSERT INTO nodes (id, tree_id, parent_id, user_message, assistant_response, label, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (root_id, tree_id, None, "", "", "root", "idle", now),
        )
        await db.commit()

    tree = Tree(id=tree_id, name=name, created_at=now, root_node_id=root_id,
                provider=provider, model=model, base_branch=base_branch, base_commit=base_commit,
                repo_id=repo_id, repo_path=repo_path, repo_name=repo_name)
    node = Node(id=root_id, tree_id=tree_id, label="root", created_at=now)
    return tree, node


async def list_trees(repo_id: str | None = None) -> list[Tree]:
    async with get_db() as db:
        if repo_id:
            cursor = await db.execute(
                "SELECT * FROM trees WHERE repo_id = ? ORDER BY created_at DESC",
                (repo_id,),
            )
        else:
            # Only return trees that have a repo_id (exclude pre-migration orphans)
            cursor = await db.execute(
                "SELECT * FROM trees WHERE repo_id IS NOT NULL ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
    return [_tree_from_row(r) for r in rows]


async def get_tree(tree_id: str) -> Tree | None:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM trees WHERE id = ?", (tree_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        cursor2 = await db.execute(
            "SELECT id FROM nodes WHERE tree_id = ? AND parent_id IS NULL", (tree_id,)
        )
        root = await cursor2.fetchone()
    root_id = root["id"] if root else None
    return _tree_from_row(row, root_id)


async def find_tree(repo_id: str, base_commit: str, current_repo_path: str | None = None) -> Tree | None:
    """Find a tree by repo_id + base_commit. Updates repo_path if it changed."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM trees WHERE repo_id = ? AND base_commit = ? ORDER BY created_at DESC",
            (repo_id, base_commit),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None

        # Prefer matching repo_path if multiple trees share the same repo_id + commit
        row = rows[0]
        if current_repo_path and len(rows) > 1:
            for r in rows:
                if r["repo_path"] == current_repo_path:
                    row = r
                    break

        # Update repo_path if it changed
        if current_repo_path and row["repo_path"] != current_repo_path:
            await db.execute(
                "UPDATE trees SET repo_path = ? WHERE id = ?",
                (current_repo_path, row["id"]),
            )
            await db.commit()

        cursor2 = await db.execute(
            "SELECT id FROM nodes WHERE tree_id = ? AND parent_id IS NULL", (row["id"],)
        )
        root = await cursor2.fetchone()
    root_id = root["id"] if root else None
    return _tree_from_row(row, root_id)


async def get_all_nodes(tree_id: str) -> list[Node]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM nodes WHERE tree_id = ? ORDER BY created_at", (tree_id,)
        )
        rows = await cursor.fetchall()

    # Filter out draft nodes — they are invisible until a message is sent
    visible_rows = [r for r in rows if r["status"] != "draft"]

    parent_to_children: dict[str, list[str]] = {}
    for r in visible_rows:
        pid = r["parent_id"]
        if pid:
            parent_to_children.setdefault(pid, []).append(r["id"])

    nodes = []
    for r in visible_rows:
        nodes.append(Node(
            id=r["id"],
            tree_id=r["tree_id"],
            parent_id=r["parent_id"],
            user_message=r["user_message"],
            assistant_response=r["assistant_response"],
            label=r["label"],
            status=r["status"],
            created_at=r["created_at"],
            children_ids=parent_to_children.get(r["id"], []),
            git_branch=r["git_branch"],
            git_commit=r["git_commit"],
            session_id=r["session_id"],
            created_by=r["created_by"],
            quoted_node_ids=json.loads(r["quoted_node_ids"]) if r["quoted_node_ids"] else [],
        ))
    return nodes


async def get_node(node_id: str) -> Node | None:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        cursor2 = await db.execute("SELECT id FROM nodes WHERE parent_id = ?", (node_id,))
        children = await cursor2.fetchall()
    return Node(
        id=row["id"],
        tree_id=row["tree_id"],
        parent_id=row["parent_id"],
        user_message=row["user_message"],
        assistant_response=row["assistant_response"],
        label=row["label"],
        status=row["status"],
        created_at=row["created_at"],
        children_ids=[c["id"] for c in children],
        git_branch=row["git_branch"],
        git_commit=row["git_commit"],
        session_id=row["session_id"],
        created_by=row["created_by"],
        quoted_node_ids=json.loads(row["quoted_node_ids"]) if row["quoted_node_ids"] else [],
    )


async def create_child_node(parent_id: str, label: str = "", created_by: str = "human") -> Node:
    parent = await get_node(parent_id)
    if not parent:
        raise ValueError(f"Parent node {parent_id} not found")

    node_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            "INSERT INTO nodes (id, tree_id, parent_id, user_message, assistant_response, label, status, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, parent.tree_id, parent_id, "", "", label, "idle", now, created_by),
        )
        await db.commit()

    return Node(
        id=node_id,
        tree_id=parent.tree_id,
        parent_id=parent_id,
        label=label,
        created_at=now,
        created_by=created_by,
    )


async def get_drafts_for_parent(parent_id: str) -> list[Node]:
    """Return all draft nodes under a given parent."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM nodes WHERE parent_id = ? AND status = 'draft'",
            (parent_id,),
        )
        rows = await cursor.fetchall()
    nodes = []
    for r in rows:
        n = await get_node(r["id"])
        if n:
            nodes.append(n)
    return nodes


async def delete_single_node(node_id: str) -> None:
    """Delete a single node from the database (no subtree, no cleanup)."""
    async with get_db() as db:
        await db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        await db.commit()


async def get_path_to_root(node_id: str) -> list[Node]:
    """Walk from node to root, return [root, ..., parent, node]."""
    path = []
    current_id: str | None = node_id
    while current_id:
        node = await get_node(current_id)
        if not node:
            break
        path.append(node)
        current_id = node.parent_id
    path.reverse()
    return path


async def update_node(node_id: str, **kwargs):
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k == "quoted_node_ids":
            sets.append(f"{k} = ?")
            vals.append(json.dumps(v))
        elif k in ("user_message", "assistant_response", "label", "status", "git_branch", "git_commit", "session_id", "created_by"):
            sets.append(f"{k} = ?")
            vals.append(v)
    if sets:
        vals.append(node_id)
        async with get_db() as db:
            await db.execute(
                f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", vals
            )
            await db.commit()


async def update_tree(tree_id: str, **kwargs):
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in ("name", "provider", "model", "max_turns", "skill", "notes",
                  "base_branch", "base_commit", "repo_id", "repo_path", "repo_name"):
            sets.append(f"{k} = ?")
            vals.append(v)
    if sets:
        vals.append(tree_id)
        async with get_db() as db:
            await db.execute(
                f"UPDATE trees SET {', '.join(sets)} WHERE id = ?", vals
            )
            await db.commit()


async def delete_subtree(node_id: str) -> tuple[list[str], list[Node]]:
    """Delete a non-root node and all its descendants.

    Returns (deleted_ids, updated_surviving_nodes) where updated_surviving_nodes
    are nodes whose quoted_node_ids were cleaned of references to deleted nodes.
    """
    node = await get_node(node_id)
    if not node:
        raise ValueError(f"Node {node_id} not found")
    if not node.parent_id:
        raise ValueError("Cannot delete root node")

    # Collect all descendant IDs via DFS
    deleted_ids = [node_id]
    stack = list(node.children_ids)
    while stack:
        cid = stack.pop()
        deleted_ids.append(cid)
        child = await get_node(cid)
        if child:
            stack.extend(child.children_ids)

    deleted_set = set(deleted_ids)

    async with get_db() as db:
        # Delete all nodes in subtree
        placeholders = ",".join("?" for _ in deleted_ids)
        await db.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", deleted_ids)
        await db.commit()

    # Clean quoted_node_ids on surviving nodes that reference any deleted ID
    all_nodes = await get_all_nodes(node.tree_id)
    updated_nodes: list[Node] = []
    for n in all_nodes:
        if not n.quoted_node_ids:
            continue
        cleaned = [qid for qid in n.quoted_node_ids if qid not in deleted_set]
        if len(cleaned) != len(n.quoted_node_ids):
            await update_node(n.id, quoted_node_ids=cleaned)
            n.quoted_node_ids = cleaned
            updated_nodes.append(n)

    return deleted_ids, updated_nodes


async def delete_tree(tree_id: str):
    # Collect node IDs before deletion for cleanup
    tree = await get_tree(tree_id)
    root_id = tree.root_node_id if tree else None
    all_nodes = await get_all_nodes(tree_id)
    node_ids = [n.id for n in all_nodes]

    # Resolve repo_path from tree record for worktree cleanup
    repo_path = None
    if tree and tree.repo_path:
        repo_path = Path(tree.repo_path)
    else:
        try:
            repo_path = get_project_path()
        except RuntimeError:
            pass

    async with get_db() as db:
        await db.execute("DELETE FROM nodes WHERE tree_id = ?", (tree_id,))
        await db.execute("DELETE FROM trees WHERE id = ?", (tree_id,))
        await db.commit()

    if root_id and repo_path:
        cleanup_tree_workspaces(repo_path, root_id, node_ids)


async def get_setting(key: str) -> str | None:
    async with get_db() as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else None


async def set_setting(key: str, value: str | None):
    async with get_db() as db:
        if value is None:
            await db.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, value, value),
            )
        await db.commit()


async def get_global_defaults() -> dict:
    """Return global default settings (from settings table + provider registry)."""
    provider = await get_setting("default_provider") or FALLBACK_PROVIDER
    p = PROVIDERS.get(provider)
    model = await get_setting("default_model") or (p.default_model if p else "claude-opus-4-6")
    max_turns_raw = await get_setting("default_max_turns")
    max_turns = int(max_turns_raw) if max_turns_raw else 0  # 0 = unlimited
    auth_mode = await get_setting("auth_mode") or (p.default_auth_mode if p else "cli")
    api_key = await get_setting("api_key") or ""
    sandbox = (await get_setting("sandbox")) == "true"
    summary_model = await get_setting("summary_model") or "claude-haiku-4-5-20251001"

    from config import get_global_db_path
    from services.sandbox import check_available as sandbox_check
    return {
        "provider": provider,
        "model": model,
        "max_turns": max_turns,
        "auth_mode": auth_mode,
        "api_key": api_key,
        "sandbox": sandbox,
        "sandbox_available": sandbox_check(),
        "summary_model": summary_model,
        "data_dir": str(get_global_db_path().parent),
    }


async def resolve_tree_settings(tree: Tree) -> dict:
    """Merge tree overrides with global defaults. Empty string / None = inherit."""
    defaults = await get_global_defaults()
    return {
        "provider": tree.provider if tree.provider else defaults["provider"],
        "model": tree.model if tree.model else defaults["model"],
        "max_turns": tree.max_turns if tree.max_turns is not None else defaults["max_turns"],
    }
