"""Filesystem sandbox for node agent subprocesses.

Each node's agent (Claude CLI subprocess) is sandboxed so it can only write
to the node's tree workspace, /tmp, ~/.claude, etc. Reads are allowed everywhere.

Platform backends:
  - Linux: Landlock LSM (ABI v3+)        → _sandbox_linux.py
  - macOS: (not yet implemented)          → _sandbox_darwin.py
  - Other: sandbox disabled, logs warning

Integration: install_hook() once at startup, then set_sandbox() per chat task.
The asyncio.create_subprocess_exec monkey-patch wraps the command with a sandbox
executor script that applies restrictions before exec'ing the real command. This
works with any event loop implementation (asyncio, uvloop, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextvars import ContextVar
from pathlib import Path

log = logging.getLogger(__name__)


# ── Platform dispatch ─────────────────────────────────────────────────

def _load_backend():
    """Load the platform-specific sandbox backend.

    Returns (apply_sandbox, check_available) or (None, None) if unsupported.
    """
    if sys.platform == "linux":
        from services._sandbox_linux import apply_sandbox, check_available
        return apply_sandbox, check_available
    elif sys.platform == "darwin":
        try:
            from services._sandbox_darwin import apply_sandbox, check_available
            return apply_sandbox, check_available
        except ImportError:
            return None, None
    return None, None


_backend_apply, _backend_check = _load_backend()


def apply_sandbox(writable_paths: list[str]):
    """Apply filesystem sandbox: read+exec everywhere, write to writable_paths only.

    Restrictions are permanent and inherited by all descendants (including
    across exec). Call from the process that should be sandboxed.
    """
    if _backend_apply is None:
        raise RuntimeError(f"Sandbox not available on {sys.platform}")
    _backend_apply(writable_paths)


def check_available() -> bool:
    """Check if sandboxing is supported on this platform/kernel."""
    if _backend_check is None:
        return False
    return _backend_check()


# ── Common helpers ────────────────────────────────────────────────────

def default_writable_paths(tree_workspace_dir: str) -> list[str]:
    """Standard writable paths for an agent sandbox."""
    home = str(Path.home())
    paths = [
        tree_workspace_dir,
        "/tmp",
        f"{home}/.claude",
        f"{home}/.cache",
        f"{home}/.local",
    ]
    if sys.platform == "linux":
        paths.append("/dev")
    elif sys.platform == "darwin":
        paths.append("/dev")
        paths.append("/private/tmp")
        paths.append("/private/var/folders")  # macOS temp dirs
    return paths


# ── Sandbox wrapper paths ────────────────────────────────────────────

_SANDBOX_EXEC = Path(__file__).resolve().parent.parent / "sandbox_exec.py"
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)


# ── ContextVar + asyncio.create_subprocess_exec patch ─────────────────
#
# We patch asyncio.create_subprocess_exec (not subprocess.Popen) because
# uvloop's event loop bypasses subprocess.Popen entirely, using libuv's
# uv_spawn instead. Patching at the asyncio level works regardless of
# whether the standard asyncio loop or uvloop is used.

_sandbox_paths: ContextVar[list[str] | None] = ContextVar(
    "_sandbox_paths", default=None,
)

_original_create_subprocess_exec = None
_installed = False


async def _patched_create_subprocess_exec(program, *args, **kwargs):
    """Wraps the subprocess command with sandbox_exec.py when sandbox is active."""
    paths = _sandbox_paths.get()
    if paths is not None:
        env = dict(kwargs.get("env") or os.environ)
        env["_CLAWTREE_SANDBOX_PATHS"] = json.dumps(paths)
        env["_CLAWTREE_SANDBOX_BACKEND"] = _BACKEND_DIR
        kwargs["env"] = env
        return await _original_create_subprocess_exec(
            sys.executable, str(_SANDBOX_EXEC), str(program), *args, **kwargs
        )
    return await _original_create_subprocess_exec(program, *args, **kwargs)


def install_hook() -> bool:
    """Patch asyncio.create_subprocess_exec to inject sandbox. Call once at startup.

    Returns True if installed, False if sandboxing is not available.
    """
    global _original_create_subprocess_exec, _installed
    if _installed:
        return True
    if not check_available():
        log.warning("Sandbox not available on %s — agent sandboxing disabled", sys.platform)
        return False
    _original_create_subprocess_exec = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = _patched_create_subprocess_exec
    _installed = True
    log.info("Sandbox hook installed (platform: %s)", sys.platform)
    return True


def set_sandbox(writable_paths: list[str]):
    """Set sandbox paths for the current asyncio task context.

    All asyncio.create_subprocess_exec calls in this task (and child tasks)
    will have the subprocess wrapped with sandbox restrictions.
    """
    if not _installed:
        return
    _sandbox_paths.set(writable_paths)


def clear_sandbox():
    """Clear sandbox from current context."""
    _sandbox_paths.set(None)
