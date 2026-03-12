"""CLI entry point for CodeFission."""

import shutil
import sys

import uvicorn


def _check_prerequisites():
    missing = []
    if not shutil.which("git"):
        missing.append(
            "git - install from https://git-scm.com/downloads"
            "\n      macOS: xcode-select --install"
            "\n      Ubuntu/Debian: sudo apt install git"
            "\n      Windows: https://git-scm.com/download/win"
        )
    if not shutil.which("claude"):
        missing.append(
            "Claude Code CLI - install with: npm install -g @anthropic-ai/claude-code"
            "\n      Then authenticate: claude login"
        )
    if missing:
        print("CodeFission requires the following:\n")
        for m in missing:
            print(f"  * {m}\n")
        sys.exit(1)


def main():
    _check_prerequisites()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    uvicorn.run(
        "codefission.main:app",
        host="0.0.0.0",
        port=port,
        ws_ping_interval=30,
        ws_ping_timeout=10,
    )


if __name__ == "__main__":
    main()
