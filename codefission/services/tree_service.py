"""Compatibility shim — re-exports from services.trees.

This file exists so old imports like `from services.tree_service import ...`
continue to work. New code should import from `services.trees` instead.
"""

from services.trees import *  # noqa: F401,F403
from services.trees import (  # explicit re-exports for type checkers
    create_tree,
    get_tree,
    get_node,
    get_all_nodes,
    create_child_node,
    update_node,
    update_tree,
    delete_subtree,
    delete_tree,
    list_trees,
    get_global_defaults,
    get_setting,
    set_setting,
    resolve_tree_settings,
    find_tree,
    get_ancestor_chain,
    get_drafts_for_parent,
    delete_single_node,
)
