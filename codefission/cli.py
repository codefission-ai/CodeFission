"""CLI entry point for CodeFission."""

import sys

import uvicorn


def main():
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
