"""Compatibility shim — re-exports from services.workspace.

This file exists so old imports like `from services.workspace_service import ...`
continue to work. New code should import from `services.workspace` instead.
"""

from services.workspace import *  # noqa: F401,F403
from services.workspace import _run_git, _GIT_ENV, _worktrees_dir, _artifacts_dir  # noqa: F401
