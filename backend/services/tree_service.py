import uuid
from datetime import datetime, timezone

from db import get_db
from models import Node, Tree, DEFAULT_PROVIDER, DEFAULT_MODEL
from services.workspace_service import cleanup_tree_workspace


async def create_tree(
    name: str,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    repo_mode: str = "none",
    repo_source: str | None = None,
) -> tuple[Tree, Node]:
    tree_id = str(uuid.uuid4())[:8]
    root_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            "INSERT INTO trees (id, name, created_at, provider, model, repo_mode, repo_source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tree_id, name, now, provider, model, repo_mode, repo_source),
        )
        await db.execute(
            "INSERT INTO nodes (id, tree_id, parent_id, user_message, assistant_response, label, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (root_id, tree_id, None, "", "", "root", "idle", now),
        )
        await db.commit()

    tree = Tree(id=tree_id, name=name, created_at=now, root_node_id=root_id,
                provider=provider, model=model, repo_mode=repo_mode, repo_source=repo_source)
    node = Node(id=root_id, tree_id=tree_id, label="root", created_at=now)
    return tree, node


async def list_trees() -> list[Tree]:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM trees ORDER BY created_at DESC")
        rows = await cursor.fetchall()
    return [
        Tree(id=r["id"], name=r["name"], created_at=r["created_at"],
             provider=r["provider"], model=r["model"],
             repo_mode=r["repo_mode"], repo_source=r["repo_source"])
        for r in rows
    ]


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
    return Tree(
        id=row["id"], name=row["name"], created_at=row["created_at"],
        root_node_id=root_id,
        provider=row["provider"], model=row["model"],
        repo_mode=row["repo_mode"], repo_source=row["repo_source"],
    )


async def get_all_nodes(tree_id: str) -> list[Node]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM nodes WHERE tree_id = ? ORDER BY created_at", (tree_id,)
        )
        rows = await cursor.fetchall()

    parent_to_children: dict[str, list[str]] = {}
    for r in rows:
        pid = r["parent_id"]
        if pid:
            parent_to_children.setdefault(pid, []).append(r["id"])

    nodes = []
    for r in rows:
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
    )


async def create_child_node(parent_id: str, label: str = "") -> Node:
    parent = await get_node(parent_id)
    if not parent:
        raise ValueError(f"Parent node {parent_id} not found")

    node_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            "INSERT INTO nodes (id, tree_id, parent_id, user_message, assistant_response, label, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, parent.tree_id, parent_id, "", "", label, "idle", now),
        )
        await db.commit()

    return Node(
        id=node_id,
        tree_id=parent.tree_id,
        parent_id=parent_id,
        label=label,
        created_at=now,
    )


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
        if k in ("user_message", "assistant_response", "label", "status", "git_branch", "git_commit"):
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
        if k in ("repo_mode", "repo_source"):
            sets.append(f"{k} = ?")
            vals.append(v)
    if sets:
        vals.append(tree_id)
        async with get_db() as db:
            await db.execute(
                f"UPDATE trees SET {', '.join(sets)} WHERE id = ?", vals
            )
            await db.commit()


async def delete_tree(tree_id: str):
    async with get_db() as db:
        await db.execute("DELETE FROM nodes WHERE tree_id = ?", (tree_id,))
        await db.execute("DELETE FROM trees WHERE id = ?", (tree_id,))
        await db.commit()
    cleanup_tree_workspace(tree_id)
