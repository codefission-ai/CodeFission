"""Audit log — append-only record of every mutation for `fission log` and debugging.

Not event sourcing — trees/nodes tables remain the source of truth for state.
The actions table captures what happened, when, and who triggered it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from db import get_db
from models import Action


class ActionLog:
    """Append-only audit log backed by the `actions` table."""

    async def record(
        self,
        kind: str,
        tree_id: str | None = None,
        node_id: str | None = None,
        params: dict | None = None,
        result: dict | None = None,
        source: str = "gui",
    ) -> Action:
        """Record an action and return it with its assigned seq number."""
        action_id = uuid.uuid4().hex[:12]
        ts = datetime.now(timezone.utc).isoformat()
        params_json = json.dumps(params or {})
        result_json = json.dumps(result or {})

        async with get_db() as db:
            # Compute next seq atomically
            cursor = await db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM actions")
            row = await cursor.fetchone()
            seq = row[0]

            await db.execute(
                """INSERT INTO actions (id, seq, ts, tree_id, node_id, kind, params, result, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (action_id, seq, ts, tree_id, node_id, kind, params_json, result_json, source),
            )
            await db.commit()

        return Action(
            id=action_id,
            seq=seq,
            ts=ts,
            tree_id=tree_id,
            node_id=node_id,
            kind=kind,
            params=params or {},
            result=result or {},
            source=source,
        )

    async def update_result(self, action_id: str, result: dict) -> None:
        """Update the result field of an existing action."""
        result_json = json.dumps(result)
        async with get_db() as db:
            await db.execute(
                "UPDATE actions SET result = ? WHERE id = ?",
                (result_json, action_id),
            )
            await db.commit()

    async def list_actions(
        self,
        tree_id: str | None = None,
        limit: int = 100,
    ) -> list[Action]:
        """List actions, optionally filtered by tree_id, ordered by seq."""
        async with get_db() as db:
            if tree_id:
                cursor = await db.execute(
                    "SELECT * FROM actions WHERE tree_id = ? ORDER BY seq LIMIT ?",
                    (tree_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM actions ORDER BY seq LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()

        return [self._row_to_action(row) for row in rows]

    async def replay(self, tree_id: str) -> list[Action]:
        """Return all actions for a tree in seq order (no limit)."""
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM actions WHERE tree_id = ? ORDER BY seq",
                (tree_id,),
            )
            rows = await cursor.fetchall()

        return [self._row_to_action(row) for row in rows]

    @staticmethod
    def _row_to_action(row) -> Action:
        """Convert a DB row to an Action dataclass."""
        params = row["params"]
        result = row["result"]
        return Action(
            id=row["id"],
            seq=row["seq"],
            ts=row["ts"],
            tree_id=row["tree_id"],
            node_id=row["node_id"],
            kind=row["kind"],
            params=json.loads(params) if isinstance(params, str) else (params or {}),
            result=json.loads(result) if isinstance(result, str) else (result or {}),
            source=row["source"],
        )
