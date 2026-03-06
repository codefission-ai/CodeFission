"""Landlock-based filesystem sandbox for node agent subprocesses.

Each node's agent (Claude CLI subprocess) is sandboxed so it can only write
to the node's tree workspace, /tmp, ~/.claude, etc. Reads are allowed everywhere.

Uses Linux Landlock LSM (ABI v3+). Falls back to no-op on unsupported systems.

Integration: install_hook() once at startup, then set_sandbox() per chat task.
The asyncio.create_subprocess_exec monkey-patch wraps the command with a sandbox
executor script that applies Landlock before exec'ing the real command. This works
with any event loop implementation (asyncio, uvloop, etc.).
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import json
import logging
import os
import sys
from contextvars import ContextVar
from pathlib import Path

log = logging.getLogger(__name__)

# ── Landlock constants (x86_64, ABI v3) ──────────────────────────────

_SYS_create_ruleset = 444
_SYS_add_rule = 445
_SYS_restrict_self = 446

_EXECUTE     = 1 << 0
_WRITE_FILE  = 1 << 1
_READ_FILE   = 1 << 2
_READ_DIR    = 1 << 3
_REMOVE_DIR  = 1 << 4
_REMOVE_FILE = 1 << 5
_MAKE_CHAR   = 1 << 6
_MAKE_DIR    = 1 << 7
_MAKE_REG    = 1 << 8
_MAKE_SOCK   = 1 << 9
_MAKE_FIFO   = 1 << 10
_MAKE_BLOCK  = 1 << 11
_MAKE_SYM    = 1 << 12
_REFER       = 1 << 13
_TRUNCATE    = 1 << 14

_RULE_PATH_BENEATH = 1

_ALL_ACCESS = (
    _EXECUTE | _WRITE_FILE | _READ_FILE | _READ_DIR |
    _REMOVE_DIR | _REMOVE_FILE | _MAKE_CHAR | _MAKE_DIR |
    _MAKE_REG | _MAKE_SOCK | _MAKE_FIFO | _MAKE_BLOCK |
    _MAKE_SYM | _REFER | _TRUNCATE
)

_READ_EXEC = _EXECUTE | _READ_FILE | _READ_DIR


# ── ctypes structures & syscall wrapper ──────────────────────────────

class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _syscall(nr, *args):
    ret = _libc.syscall(nr, *args)
    if ret < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return ret


def _create_ruleset(handled: int) -> int:
    attr = _RulesetAttr(handled_access_fs=handled)
    return _syscall(
        _SYS_create_ruleset,
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint32(0),
    )


def _add_path_rule(ruleset_fd: int, path: str, access: int):
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        attr = _PathBeneathAttr(allowed_access=access, parent_fd=fd)
        _syscall(
            _SYS_add_rule,
            ctypes.c_int(ruleset_fd),
            ctypes.c_uint32(_RULE_PATH_BENEATH),
            ctypes.byref(attr),
            ctypes.c_uint32(0),
        )
    finally:
        os.close(fd)


def _restrict_self(ruleset_fd: int):
    _libc.prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
    _syscall(
        _SYS_restrict_self,
        ctypes.c_int(ruleset_fd),
        ctypes.c_uint32(0),
    )


# ── High-level API ───────────────────────────────────────────────────

def apply_sandbox(writable_paths: list[str]):
    """Apply Landlock: read+exec everywhere, full write to writable_paths.

    Restrictions are permanent and inherited by all descendants (including
    across exec). Call from the process that should be sandboxed.
    """
    fd = _create_ruleset(_ALL_ACCESS)
    try:
        _add_path_rule(fd, "/", _READ_EXEC)
        for p in writable_paths:
            if os.path.exists(p):
                _add_path_rule(fd, p, _ALL_ACCESS)
        _restrict_self(fd)
    finally:
        os.close(fd)


def check_available() -> bool:
    """Check if Landlock is supported on this kernel."""
    try:
        fd = _create_ruleset(_READ_FILE)
        os.close(fd)
        return True
    except OSError:
        return False


def default_writable_paths(tree_workspace_dir: str) -> list[str]:
    """Standard writable paths for an agent sandbox."""
    home = str(Path.home())
    return [
        tree_workspace_dir,
        "/tmp",
        "/dev",
        f"{home}/.claude",
        f"{home}/.cache",
        f"{home}/.local",
    ]


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

    Returns True if installed, False if Landlock is not available.
    """
    global _original_create_subprocess_exec, _installed
    if _installed:
        return True
    if not check_available():
        log.warning("Landlock not available — agent sandboxing disabled")
        return False
    _original_create_subprocess_exec = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = _patched_create_subprocess_exec
    _installed = True
    log.info("Landlock sandbox hook installed")
    return True


def set_sandbox(writable_paths: list[str]):
    """Set sandbox paths for the current asyncio task context.

    All asyncio.create_subprocess_exec calls in this task (and child tasks)
    will have the subprocess wrapped with Landlock restrictions.
    """
    if not _installed:
        return
    _sandbox_paths.set(writable_paths)


def clear_sandbox():
    """Clear sandbox from current context."""
    _sandbox_paths.set(None)
