"""Sandbox executor: applies Landlock restrictions then execs the real command.

Called as: python3 sandbox_exec.py <real_command> [args...]

Environment variables (consumed and removed before exec):
  _CODEFISSION_SANDBOX_PATHS   — JSON list of writable directory paths
  _CODEFISSION_SANDBOX_BACKEND — path to backend dir (for importing services.sandbox)
"""

import json
import os
import sys


def main():
    paths_json = os.environ.pop("_CODEFISSION_SANDBOX_PATHS", "")
    backend_dir = os.environ.pop("_CODEFISSION_SANDBOX_BACKEND", "")

    if paths_json:
        if backend_dir:
            sys.path.insert(0, backend_dir)
        from services.sandbox import apply_sandbox
        apply_sandbox(json.loads(paths_json))

    if len(sys.argv) < 2:
        print("Usage: sandbox_exec.py <command> [args...]", file=sys.stderr)
        sys.exit(1)

    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
