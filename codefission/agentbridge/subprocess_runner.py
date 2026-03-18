"""Async subprocess runner for JSONL-based CLI tools."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator

log = logging.getLogger(__name__)

MAX_BUFFER_SIZE = 1024 * 1024  # 1 MB


class SubprocessRunner:
    """Spawn a CLI as a subprocess, read JSONL from stdout, write to stdin."""

    def __init__(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
    ):
        self._cmd = cmd
        self._cwd = cwd
        self._env = {**os.environ, **(env or {})}
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_lines: list[str] = []

    async def _drain_stderr(self) -> None:
        """Continuously read stderr to prevent pipe buffer deadlock."""
        assert self._process and self._process.stderr
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                self._stderr_lines.append(text)
                log.debug("[subprocess stderr] %s", text)
        except asyncio.CancelledError:
            pass

    async def start(self) -> None:
        log.info("[subprocess] starting: %s (cwd=%s)", self._cmd[0], self._cwd)
        self._process = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._cwd),
            env=self._env,
            limit=MAX_BUFFER_SIZE,  # raise default 64KB StreamReader limit
        )
        log.info("[subprocess] pid=%s started", self._process.pid)
        # Drain stderr concurrently to prevent pipe buffer deadlock
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def read_events(self) -> AsyncGenerator[dict, None]:
        """Yield parsed JSON objects from stdout, one per JSONL line.

        Buffers partial lines (the CLI may split a large JSON object across
        multiple writes) and speculatively tries json.loads until it succeeds.
        """
        if not self._process or not self._process.stdout:
            raise RuntimeError("Process not started")

        import time
        event_count = 0
        first_event_at: float | None = None
        t_start = time.monotonic()
        log.info("[subprocess] pid=%s waiting for first stdout line...", self._process.pid)

        buf = ""
        while True:
            raw = await self._process.stdout.readline()
            if not raw:
                elapsed = time.monotonic() - t_start
                log.info(
                    "[subprocess] pid=%s stdout EOF after %.1fs (%d events, stderr_lines=%d, rc=%s)",
                    self._process.pid, elapsed, event_count,
                    len(self._stderr_lines), self._process.returncode,
                )
                if self._stderr_lines:
                    log.info("[subprocess] stderr dump:\n%s", "\n".join(self._stderr_lines[-20:]))
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            buf += line
            if len(buf) > MAX_BUFFER_SIZE:
                log.warning("JSONL buffer overflow (%d bytes), discarding", len(buf))
                buf = ""
                continue

            try:
                data = json.loads(buf)
                buf = ""
                event_count += 1
                if event_count == 1:
                    first_event_at = time.monotonic()
                    log.info(
                        "[subprocess] pid=%s first event after %.1fs: type=%s",
                        self._process.pid, first_event_at - t_start,
                        data.get("type", "?"),
                    )
                yield data
            except json.JSONDecodeError:
                # Partial JSON — keep accumulating
                continue

    async def write_json(self, data: dict) -> None:
        """Write a single JSON line to stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Process not started or stdin closed")
        line = json.dumps(data) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def close_stdin(self) -> None:
        if self._process and self._process.stdin:
            self._process.stdin.close()
            await self._process.stdin.wait_closed()

    async def close(self) -> None:
        if not self._process:
            return
        # Cancel stderr drain
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
        # Close stdin to signal EOF
        if self._process.stdin and not self._process.stdin.is_closing():
            try:
                self._process.stdin.close()
            except Exception:
                pass
        # Terminate if still running
        if self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

    async def read_stderr(self) -> str:
        """Return buffered stderr (already drained by background task)."""
        return "\n".join(self._stderr_lines)

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process else None
