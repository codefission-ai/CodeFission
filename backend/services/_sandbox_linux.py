"""Linux Landlock sandbox backend.

Applies filesystem restrictions using Landlock LSM (ABI v3+).
Read+exec allowed everywhere; writes restricted to specified paths.
Restrictions are permanent and inherited by all descendants (including across exec).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os

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


# ── Public API ────────────────────────────────────────────────────────

def apply_sandbox(writable_paths: list[str]):
    """Apply Landlock: read+exec everywhere, full write to writable_paths."""
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
