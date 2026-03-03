#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$DIR/venv/bin/python"

PORT="${1:-8080}"
exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --app-dir "$DIR/backend"
