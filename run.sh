#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$DIR/venv/bin/python"
FRONTEND="$DIR/frontend"
DIST="$FRONTEND/dist"

# Rebuild frontend if dist is missing or source is newer
if [ ! -d "$DIST" ] || [ -n "$(find "$FRONTEND/src" "$FRONTEND/index.html" -newer "$DIST/index.html" 2>/dev/null)" ]; then
  echo "Building frontend..."
  (cd "$FRONTEND" && npm run build)
fi

PORT="${1:-8080}"
exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --app-dir "$DIR/backend"
