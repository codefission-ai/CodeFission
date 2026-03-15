"""Phase 2A — Test ActionLog service.

Tests for the audit log: record, list, update_result, replay.
The ActionLog writes to the 'actions' table in the global DB.

Written against the PLANNED interface from backend-rewrite-plan.md.
Will fail until services/actions.py is implemented.
"""

import json

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_action_log():
    """Import and return an ActionLog instance."""
    from store.actions import ActionLog
    return ActionLog()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestActionLog:

    @pytest.mark.asyncio
    async def test_record_returns_action_with_seq(self, tmp_db):
        """record() returns an Action with id, seq, ts, and kind."""
        log = await _create_action_log()
        action = await log.record(
            "create_tree", tree_id="tree1", node_id=None,
            params={"name": "T"},
        )

        assert action.id is not None
        assert action.seq is not None
        assert action.seq > 0
        assert action.ts is not None
        assert action.kind == "create_tree"

    @pytest.mark.asyncio
    async def test_seq_auto_increments(self, tmp_db):
        """Second action's seq is greater than first's."""
        log = await _create_action_log()
        a1 = await log.record("create_tree", "t1", None, {"name": "T1"})
        a2 = await log.record("branch", "t1", "n1", {"parent_id": "root"})

        assert a2.seq > a1.seq

    @pytest.mark.asyncio
    async def test_list_actions_by_tree(self, tmp_db):
        """list_actions filters by tree_id."""
        log = await _create_action_log()
        await log.record("create_tree", "tree1", None, {"name": "T1"})
        await log.record("create_tree", "tree2", None, {"name": "T2"})
        await log.record("branch", "tree1", "n1", {"parent_id": "root"})

        actions = await log.list_actions("tree1")
        assert len(actions) == 2
        assert all(a.tree_id == "tree1" for a in actions)

    @pytest.mark.asyncio
    async def test_list_actions_ordered_by_seq(self, tmp_db):
        """list_actions returns actions in seq order."""
        log = await _create_action_log()
        for i in range(5):
            await log.record("chat", "tree1", f"n{i}", {"message": f"msg{i}"})

        actions = await log.list_actions("tree1")
        seqs = [a.seq for a in actions]
        assert seqs == sorted(seqs)

    @pytest.mark.asyncio
    async def test_list_actions_limit(self, tmp_db):
        """list_actions respects the limit parameter."""
        log = await _create_action_log()
        for i in range(10):
            await log.record("chat", "tree1", f"n{i}", {"message": f"msg{i}"})

        actions = await log.list_actions("tree1", limit=3)
        assert len(actions) == 3

    @pytest.mark.asyncio
    async def test_update_result(self, tmp_db):
        """update_result adds result data to an existing action."""
        log = await _create_action_log()
        action = await log.record(
            "chat", "tree1", "n1",
            params={"message": "hello"},
        )
        assert action.result == {} or action.result is None

        await log.update_result(action.id, {"cost_usd": 0.05, "files_changed": 2})

        # Fetch the action to verify
        actions = await log.list_actions("tree1")
        updated = [a for a in actions if a.id == action.id][0]
        assert updated.result["cost_usd"] == 0.05
        assert updated.result["files_changed"] == 2

    @pytest.mark.asyncio
    async def test_source_field_default_gui(self, tmp_db):
        """Default source is 'gui'."""
        log = await _create_action_log()
        action = await log.record(
            "create_tree", "tree1", None, {"name": "T"},
        )
        assert action.source == "gui"

    @pytest.mark.asyncio
    async def test_source_field_cli(self, tmp_db):
        """source='cli' when explicitly set."""
        log = await _create_action_log()
        action = await log.record(
            "create_tree", "tree1", None, {"name": "T"},
            source="cli",
        )
        assert action.source == "cli"

    @pytest.mark.asyncio
    async def test_action_params_stored_as_json(self, tmp_db):
        """Params with nested structures are stored and decoded correctly."""
        log = await _create_action_log()
        params = {"key": "value", "nested": [1, 2, 3], "deep": {"a": True}}
        action = await log.record("chat", "tree1", "n1", params)

        # Fetch back and verify params
        actions = await log.list_actions("tree1")
        fetched = [a for a in actions if a.id == action.id][0]
        assert fetched.params == params
        assert fetched.params["nested"] == [1, 2, 3]
        assert fetched.params["deep"]["a"] is True

    @pytest.mark.asyncio
    async def test_replay_returns_all_actions(self, tmp_db):
        """replay() returns all actions for a tree in seq order."""
        log = await _create_action_log()
        for i in range(10):
            await log.record("chat", "tree1", f"n{i}", {"message": f"msg{i}"})

        replayed = await log.replay("tree1")
        assert len(replayed) == 10
        seqs = [a.seq for a in replayed]
        assert seqs == sorted(seqs)
