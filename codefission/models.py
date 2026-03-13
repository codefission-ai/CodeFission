from pydantic import BaseModel

# Default provider/model for new trees (empty = use global default)
DEFAULT_PROVIDER = ""
DEFAULT_MODEL = ""


class Node(BaseModel):
    id: str
    tree_id: str
    parent_id: str | None = None
    user_message: str = ""
    assistant_response: str = ""
    label: str = ""
    status: str = "idle"
    created_at: str = ""
    children_ids: list[str] = []
    git_branch: str | None = None
    git_commit: str | None = None
    session_id: str | None = None
    created_by: str = "human"
    quoted_node_ids: list[str] = []


class Tree(BaseModel):
    id: str
    name: str
    created_at: str = ""
    root_node_id: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    max_turns: int | None = None
    skill: str = ""
    notes: str = "[]"  # JSON array of {id, text, x, y, width, height}
    base_branch: str = "main"
    base_commit: str | None = None
    repo_id: str | None = None       # SHA of initial commit (repo identity)
    repo_path: str | None = None     # last known abs path (display + workspace resolution)
    repo_name: str | None = None     # display name (from git remote or dirname)
