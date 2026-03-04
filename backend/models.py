from pydantic import BaseModel
from datetime import datetime

# Default provider/model for new trees
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-sonnet-4-6"


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


class Tree(BaseModel):
    id: str
    name: str
    created_at: str = ""
    root_node_id: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    repo_mode: str = "new"
    repo_source: str | None = None
