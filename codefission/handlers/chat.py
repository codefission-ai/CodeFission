"""Chat handlers — send message, stream AI response, cancel, duplicate.

Consumes orchestrator.chat() async generator and forwards each domain event
(TextDelta, ToolStart, ToolEnd, ChatCompleted) as a WS message to the browser.
Also handles auto-naming untitled trees from the first message.
"""

import asyncio
import logging

from agentbridge import TextDelta, ToolStart, ToolEnd, SessionInit
from events import bus, WS, STREAM_START, STREAM_DELTA, STREAM_END, STREAM_ERROR
from models import StreamState

log = logging.getLogger(__name__)


class ChatMixin:

    async def handle_chat(self, data: dict):
        node_id = data["node_id"]
        content = data["content"]
        after_id = data.get("after_id")
        file_quotes = data.get("file_quotes") or []
        draft_node_id = data.get("draft_node_id")

        # Set context for the tree this node belongs to
        node = await self.orch.get_node(node_id)
        if node:
            await self._set_context_for_tree(node.tree_id)

        task = asyncio.create_task(self._run_chat(node_id, content, after_id, file_quotes, draft_node_id))
        self.tasks[node_id] = task

    async def _run_chat(self, node_id: str, msg: str, after_id: str | None = None, file_quotes: list[dict] | None = None, draft_node_id: str | None = None):
        """Thin WS consumer of Orchestrator.chat() async generator."""
        from handlers import _active_streams
        from models import ChatNodeCreated, ChatCompleted

        nid = node_id
        tool_names: dict[str, str] = {}

        log.info("_run_chat START parent=%s msg=%s", node_id[:8], msg[:50])

        try:
            async for event in self.orch.chat(
                node_id, msg,
                after_id=after_id,
                file_quotes=file_quotes,
                draft_node_id=draft_node_id,
            ):
                if isinstance(event, ChatNodeCreated):
                    nid = event.node.id
                    log.info("ChatNodeCreated nid=%s, self.send=%s", nid[:8], id(self.send))
                    created_payload = {"node": event.node.model_dump()}
                    if event.after_id:
                        created_payload["after_id"] = event.after_id
                    await self.send(WS.NODE_CREATED, **created_payload)

                    # Re-key task under child id so cancel can find it
                    self.tasks[nid] = self.tasks.pop(node_id, asyncio.current_task())

                    # Send node data (now has user_message, status=active)
                    await self.send(WS.NODE_DATA, node=event.node.model_dump())

                    # Auto-name tree on first message
                    tree = await self.orch.get_tree(event.node.tree_id)
                    if tree and tree.name == "Untitled":
                        asyncio.create_task(self._auto_name_tree(tree.id, msg, tree))

                    # Init streaming state for reconnect + cancel support
                    state = StreamState(node_id=nid, tree_id=event.node.tree_id, send_fn=self.send)
                    state.stream_task = self.tasks.get(nid)  # the _run_chat task
                    self.streams[nid] = state
                    _active_streams[nid] = state
                    await bus.emit(STREAM_START, node_id=nid)
                    await self.send(WS.STATUS, node_id=nid, status="active")

                elif isinstance(event, SessionInit):
                    pass  # Orchestrator handles session_id saving

                elif isinstance(event, TextDelta):
                    if nid in self.streams:
                        self.streams[nid].text += event.text
                    log.debug("TextDelta nid=%s len=%d", nid[:8], len(event.text))
                    await bus.emit(STREAM_DELTA, node_id=nid, text=event.text)
                    await self.send(WS.CHUNK, node_id=nid, text=event.text)

                elif isinstance(event, ToolStart):
                    if event.name:
                        tool_names[event.tool_call_id] = event.name
                    await self.send(WS.TOOL_START,
                        node_id=nid,
                        tool_call_id=event.tool_call_id,
                        name=event.name,
                        arguments=event.arguments,
                    )

                elif isinstance(event, ToolEnd):
                    name = event.name or tool_names.get(event.tool_call_id, "")
                    await self.send(WS.TOOL_END,
                        node_id=nid,
                        tool_call_id=event.tool_call_id,
                        name=name,
                        result=event.result,
                        is_error=event.is_error,
                    )

                elif isinstance(event, ChatCompleted):
                    if nid in self.streams:
                        self.streams[nid].status = "done"
                    full_response = event.result.full_response
                    await bus.emit(STREAM_END, node_id=nid, full_response=full_response)
                    done_payload = {"node_id": nid, "full_response": full_response}
                    if event.result.git_commit:
                        done_payload["git_commit"] = event.result.git_commit

                    # Brief delay to let SDK/tool subprocesses fully exit
                    await asyncio.sleep(0.15)

                    # Post-chat process scan and worktree cleanup
                    proc_result = await self.orch.post_chat_cleanup(nid, event.result.files_changed)
                    if proc_result:
                        done_payload["processes"] = proc_result["processes"]

                    await self.send(WS.DONE, **done_payload)
                    await self._send_tree_processes()

        except (asyncio.CancelledError, KeyboardInterrupt):
            # Cancelled by user — save partial response
            if nid in self.streams:
                self.streams[nid].status = "error"
            partial = self.streams.get(nid)
            partial_text = partial.text if partial else ""
            active_tools = list(tool_names.values())
            try:
                from store.git import resolve_workspace
                tree = await self.orch.get_tree(self.streams[nid].tree_id if nid in self.streams else "")
                workspace = resolve_workspace(tree.root_node_id, nid) if tree else None
                result = await self.orch.cancel_chat(nid, partial_text, active_tools, workspace)
                await self.send(WS.CHUNK, node_id=nid, text=result.saved_text)
            except Exception:
                log.debug("Cancel cleanup failed for %s", nid, exc_info=True)
            await self.send(WS.ERROR, node_id=nid, error="Cancelled")
            await self._send_tree_processes()

        except Exception as e:
            import traceback
            traceback.print_exc()
            if nid in self.streams:
                self.streams[nid].status = "error"
            await bus.emit(STREAM_ERROR, node_id=nid, error=str(e))
            await self.send(WS.ERROR, node_id=nid, error=str(e))
            try:
                await self.orch.post_error_cleanup(nid)
                await self._send_tree_processes()
            except Exception:
                log.debug("Ephemeral worktree removal after error failed for %s", nid, exc_info=True)

        finally:
            self.streams.pop(nid, None)
            _active_streams.pop(nid, None)
            self.tasks.pop(nid, None)

    async def _auto_name_tree(self, tree_id: str, first_message: str, tree):
        """Background task: generate a short name for a tree and push it to the client."""
        try:
            name = await self.orch.auto_name_tree(tree_id, first_message, tree)
            if name:
                updated = await self.orch.get_tree(tree_id)
                if updated:
                    await self.send(WS.TREE_UPDATED, tree=updated.model_dump())
        except Exception:
            log.debug("Auto-name tree failed for %s", tree_id, exc_info=True)

    async def handle_cancel(self, data: dict):
        from handlers import _active_streams

        node_id = data["node_id"]

        # Mark as cancelled in the stream registry
        info = _active_streams.get(node_id)
        if info:
            info.cancelled = True

        # Cancel the task — this propagates through:
        # _run_chat → orch.chat() → stream_chat() → create_session() → SubprocessRunner.close() → kill process
        task = self.tasks.get(node_id)
        if task and not task.done():
            task.cancel()
        elif info and info.stream_task and not info.stream_task.done():
            info.stream_task.cancel()

    async def handle_duplicate(self, data: dict):
        """Re-run the same user message from the same parent, creating a sibling."""
        node_id = data["node_id"]
        node = await self.orch.get_node(node_id)
        if not node or not node.user_message or not node.parent_id:
            await self.send(WS.ERROR, error="Cannot duplicate this node")
            return
        await self.handle_chat({
            "node_id": node.parent_id,
            "content": node.user_message,
            "after_id": node_id,
        })
